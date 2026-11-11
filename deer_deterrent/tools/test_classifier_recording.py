import base64
import io
import json
import re
import tempfile
import unittest
from pathlib import Path
from PIL import Image
import sys
import threading
import time
import types
from unittest import mock
import record_classifier
from record_classifier import Receiver, fnv1a, INPUT_BYTES, valid_command

def fixture(schema=1,recording='full',boxes=()):
    photo=io.BytesIO();Image.new('RGB',(320,240),(60,100,150)).save(photo,format='JPEG')
    jpeg=photo.getvalue();tensor=bytes([0,32,127])*(224*224)
    result=dict(schema=schema,boot='0123456789abcdef',trial=1,frame=1,within=1,trigger='manual',model_sha256='a'*64,
                valid=True,committed=True,present=False,armed=False,alarm_started=False,score=.2,lighting='ambient',capture_lighting_ok=True,
                jpeg_bytes=len(jpeg),camera_width=320,camera_height=240,input_bytes=INPUT_BYTES,
                input_dtype='int8',input_layout='NHWC',input_shape=[1,224,224,3],input_exponent=-7,boxes=list(boxes))
    if schema==2:
        result.update(recording=recording,input_sent=recording=='full',candidate_floor=.1,nms_iou=.7,max_boxes=10,jpeg_quality=12,frame_interval_ms=500,
                      camera_xclk_mhz=20,hmirror=False,vflip=False,build=dict(project='deer_recorded_trials',version='1',idf='v5.4.2',compiled='Nov 11 2026 10:00:00',elf_sha256='b'*64))
    lines=['TRIAL_FRAME '+json.dumps(result)]
    for kind,data in [('jpeg',jpeg),('input',tensor)] if schema==1 or recording=='full' else [('jpeg',jpeg)]:
        header=dict(boot=result['boot'],trial=1,frame=1,kind=kind,bytes=len(data),fnv1a=fnv1a(data))
        lines.append('TRIAL_BLOB '+json.dumps(header))
        for offset in range(0,len(data),180):
            lines.append(f'TRIAL_DATA 1 {kind} {offset} '+base64.b64encode(data[offset:offset+180]).decode())
        lines.append('TRIAL_BLOB_END 1 '+kind)
    lines.append('TRIAL_END '+json.dumps(dict(boot=result['boot'],trial=1,frame=1,transfer_complete=True,transfer_us=123)))
    return ('\n'.join(lines)+'\n').encode(),jpeg,tensor

class SerialException(OSError):
    pass

def status_line(command,**state):
    item=dict(boot='0123456789abcdef',command=command,accepted=True,mode='manual',lighting='ambient',recording='full',armed=False,busy=False,
              in_flight=False,alarm=False,alarm_source='none',trial=0,attempted=0,requested=0,pir=0,warmup_remaining_s=0,pause_remaining_s=0)
    item.update(state)
    return ('TRIAL_STATUS '+json.dumps(item)+'\n').encode()

class FakePort:
    """Stands in for pyserial's port: answers like the trial program and can fail."""
    def __init__(self,first_state=None,lose_after=None,after_stop=b'',drip=0):
        self.first_state=first_state or {};self.lose_after=lose_after;self.after_stop=after_stop;self.drip=drip
        self.written=[];self.outgoing=bytearray();self.reads=0;self.closed=False;self.stops=0;self.lock=threading.Lock()
    def open(self):pass
    def write(self,data):
        command=data.decode('ascii').strip()
        with self.lock:
            self.written.append(command)
            if command=='STATUS' and len(self.written)==1:self.outgoing+=status_line('STATUS',**self.first_state)
            elif command=='STOP':
                self.stops+=1;self.outgoing+=status_line('STOP')
                if self.stops>1:self.outgoing+=self.after_stop
            else:self.outgoing+=status_line(command)
    def read(self,size):
        self.reads+=1
        if self.lose_after is not None and self.reads>self.lose_after:
            raise SerialException('device reports readiness to read but returned no data')
        with self.lock:
            size=min(size,self.drip) if self.drip else size
            chunk=bytes(self.outgoing[:size]);del self.outgoing[:size]
        if not chunk:time.sleep(0.002)
        return chunk
    def close(self):self.closed=True

def serial_module(port):
    module=types.ModuleType('serial')
    module.SerialException=SerialException
    def factory(**kwargs):return port
    module.Serial=factory
    return module

class LiveRecorderTests(unittest.TestCase):
    """main() over a stand-in port: start state, a lost port, QUIT and the serial import."""
    def run_main(self,port,commands,*extra,hold=0.0):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        out=Path(temp.name)/'session';queue=list(commands)
        def console():
            if queue:
                time.sleep(0.05);return queue.pop(0)
            time.sleep(hold)
            raise EOFError
        with mock.patch.dict(sys.modules,{'serial':serial_module(port)}),mock.patch('builtins.input',console),mock.patch('builtins.print'):
            code=record_classifier.main(['--port','fake','--output',str(out),'--quiet-seconds','0.2','--drain-seconds','5',*extra])
        rows=[json.loads(x) for x in (out/'events.jsonl').read_text().splitlines()]
        return code,out,rows,json.loads((out/'run_manifest.json').read_text()),json.loads((out/'summary.json').read_text())
    def test_opening_state_is_recorded_then_normalized(self):
        port=FakePort(first_state=dict(mode='pir',armed=True))
        code,out,rows,manifest,summary=self.run_main(port,['CAPTURE 1'])
        self.assertEqual(code,0);self.assertEqual(port.written,['STATUS','STOP','CAPTURE 1','STOP'])
        self.assertEqual(manifest['initial_status']['mode'],'pir');self.assertTrue(manifest['initial_status']['armed'])
        self.assertEqual(manifest['start_status']['command'],'STOP');self.assertEqual(manifest['close'],'normal')
        self.assertEqual([r['host_command'] for r in rows if 'host_command' in r],['STATUS','STOP','CAPTURE 1','STOP'])
    def test_keep_state_sends_no_stop_at_start(self):
        port=FakePort(first_state=dict(mode='pir',armed=True))
        code,_,_,manifest,_=self.run_main(port,[],'--keep-state')
        self.assertEqual(port.written,['STATUS','STOP']);self.assertNotIn('start_status',manifest)
    def test_lost_port_is_an_error_and_no_stop_is_logged(self):
        port=FakePort(lose_after=40)
        code,out,rows,manifest,summary=self.run_main(port,[],hold=2.0)
        self.assertEqual(code,1);self.assertEqual(manifest['close'],'serial_lost')
        self.assertTrue(any('serial connection lost' in e for e in summary['errors']))
        sent=[r['host_command'] for r in rows if 'host_command' in r]
        self.assertEqual(sent,port.written);self.assertEqual(sent,['STATUS','STOP'])
        self.assertTrue((out/'index.html').exists())
    def test_quit_waits_for_the_outstanding_frame(self):
        raw,jpeg,tensor=fixture(schema=2)
        port=FakePort(after_stop=raw,drip=700)
        code,out,rows,manifest,summary=self.run_main(port,[])
        self.assertEqual(code,0);self.assertEqual(summary['complete_valid_pairs'],1)
        self.assertEqual(next(out.glob('*.i8')).read_bytes(),tensor)
    def test_missing_pyserial_leaves_no_session_folder(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);out=Path(temp.name)/'session'
        with mock.patch.dict(sys.modules,{'serial':None}),mock.patch('sys.stderr'),self.assertRaises(SystemExit):
            record_classifier.main(['--port','fake','--output',str(out)])
        self.assertFalse(out.exists())

class RecordingTests(unittest.TestCase):
    def record(self,raw,chunk=4096):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        root=Path(temp.name);r=Receiver(root)
        for offset in range(0,len(raw),chunk):r.feed(raw[offset:offset+chunk])
        r.finish();return r,root,json.loads((root/'summary.json').read_text())
    def test_exact_pair_and_preview(self):
        raw,jpeg,tensor=fixture();r,root,s=self.record(raw,37)
        self.assertEqual(s['complete_valid_pairs'],1);self.assertFalse(r.errors)
        self.assertEqual(next(root.glob('*.jpg')).read_bytes(),jpeg)
        self.assertEqual(next(root.glob('*.i8')).read_bytes(),tensor)
        self.assertEqual((root/'serial.raw').read_bytes(),raw)
        with Image.open(next(root.glob('*.png'))) as image:self.assertEqual(image.getpixel((0,0)),(0,64,253))
    def test_checksum_corruption(self):
        raw,_,_=fixture();raw=raw.replace(b'TRIAL_DATA 1 input 0 ACB/',b'TRIAL_DATA 1 input 0 BCB/',1)
        r,root,s=self.record(raw);self.assertTrue(r.errors);self.assertEqual(s['complete_valid_pairs'],0)
        self.assertFalse(list(root.glob('*.i8')))
    def test_missing_end(self):
        raw,_,_=fixture();r,_,s=self.record(raw[:raw.index(b'TRIAL_END ')]);self.assertEqual(s['incomplete_frames'],1)
    def test_wrong_frame_identity(self):
        raw,_,_=fixture();raw=raw.replace(b'TRIAL_DATA 1 input 0 ',b'TRIAL_DATA 2 input 0 ',1)
        r,_,s=self.record(raw);self.assertTrue(r.errors);self.assertEqual(s['complete_valid_pairs'],0)
    def test_duplicate_chunk(self):
        raw,_,_=fixture();line=next(x for x in raw.splitlines(keepends=True) if x.startswith(b'TRIAL_DATA 1 input 180 '))
        r,_,s=self.record(raw.replace(line,line+line));self.assertTrue(r.errors);self.assertEqual(s['complete_valid_pairs'],0)
    def test_duplicate_result_does_not_overwrite(self):
        raw,_,_=fixture();r,root,s=self.record(raw+raw);self.assertTrue(r.errors);self.assertEqual(len(list(root.glob('*.jpg'))),1)
    def test_partial_line_and_reboot(self):
        raw,_,_=fixture();stop=raw.index(b'TRIAL_DATA 1 input 180 ')
        r,_,s=self.record(raw[:stop]+b'BOOT restarted\nTRIAL_DATA broken');self.assertTrue(r.errors);self.assertEqual(s['incomplete_frames'],1)
    def test_invalid_input_contract(self):
        raw,_,_=fixture();r,_,s=self.record(raw.replace(b'"input_layout": "NHWC"',b'"input_layout": "NCHW"'))
        self.assertTrue(r.errors);self.assertEqual(s['complete_valid_pairs'],0)
    def test_corrupt_jpeg(self):
        raw,_,_=fixture();raw=raw.replace(b'TRIAL_DATA 1 jpeg 0 ',b'TRIAL_DATA 1 jpeg 1 ',1)
        r,_,s=self.record(raw);self.assertTrue(r.errors);self.assertEqual(s['complete_valid_pairs'],0)
    def test_matching_checksum_does_not_make_invalid_jpeg_valid(self):
        raw,jpeg,_=fixture();broken=b'NOTJPEG'+jpeg[7:]
        old_line=next(x for x in raw.splitlines() if x.startswith(b'TRIAL_DATA 1 jpeg 0 '))
        new_line=b'TRIAL_DATA 1 jpeg 0 '+base64.b64encode(broken[:180])
        raw=raw.replace(old_line,new_line,1).replace(fnv1a(jpeg).encode(),fnv1a(broken).encode(),1)
        r,root,s=self.record(raw);self.assertTrue(r.errors);self.assertEqual(s['complete_valid_pairs'],0)
        self.assertFalse(list(root.glob('*.jpg')))
    def test_invalid_json_and_limit(self):
        r,_,s=self.record(b'TRIAL_FRAME {"score":NaN}\n'+b'x'*70000+b'\n')
        self.assertTrue(r.errors);self.assertEqual(s['complete_valid_pairs'],0)
    def output_event(self,event,test=1):
        return ('OUTPUT_TEST '+json.dumps(dict(schema=1,boot='0123456789abcdef',test=test,event=event,source='manual_command'))+'\n').encode()
    def test_output_test_is_not_a_recognition_result(self):
        r,root,s=self.record(self.output_event('started')+self.output_event('completed'))
        self.assertFalse(r.errors);self.assertEqual(s['frames'],0);self.assertEqual(s['complete_valid_pairs'],0)
        self.assertEqual(s['output_tests_completed'],1);self.assertIn('Independent output tests',(root/'index.html').read_text())
    def test_output_cancellation_and_missing_terminal(self):
        r,_,s=self.record(self.output_event('started')+self.output_event('cancelled'))
        self.assertFalse(r.errors);self.assertEqual(s['output_tests_cancelled'],1);self.assertEqual(s['output_tests_completed'],0)
        r,_,s=self.record(self.output_event('started'))
        self.assertTrue(r.errors);self.assertEqual(s['output_tests_incomplete'],1);self.assertEqual(s['output_tests_completed'],0)
    def test_orphan_and_duplicate_output_events(self):
        r,_,s=self.record(self.output_event('completed'));self.assertTrue(r.errors);self.assertEqual(s['output_tests_completed'],0)
        r,_,s=self.record(self.output_event('started')*2+self.output_event('cancelled')*2)
        self.assertTrue(r.errors);self.assertEqual(s['output_tests_started'],1)
    def injection_event(self,event,number=1,start=5000000,ended=None,**extra):
        item=dict(schema=1,boot='0123456789abcdef',injection=number,event=event,detection='confirmed_deer',source='usb_command',alarm_start_us=start,observed_us=start+20000)
        if event!='started':item['ended_us']=ended
        item.update(extra)
        return ('INJECTION '+json.dumps(item)+'\n').encode()
    def test_injected_detection_is_recorded_apart_from_frames(self):
        r,root,s=self.record(self.injection_event('started')+self.injection_event('completed',ended=10500000))
        self.assertFalse(r.errors);self.assertEqual(s['frames'],0);self.assertEqual(s['complete_valid_pairs'],0)
        self.assertEqual(s['injections_started'],1);self.assertEqual(s['injections_completed'],1);self.assertEqual(s['output_tests_started'],0)
        page=(root/'index.html').read_text();self.assertIn('Injected detections',page);self.assertIn('completed after 5.500 s',page)
        rows=[json.loads(x) for x in (root/'events.jsonl').read_text().splitlines()]
        self.assertEqual([row['injection']['event'] for row in rows if 'injection' in row],['started','completed'])
    def test_injection_cancellation_and_missing_terminal(self):
        r,_,s=self.record(self.injection_event('started')+self.injection_event('cancelled'))
        self.assertFalse(r.errors);self.assertEqual(s['injections_cancelled'],1);self.assertEqual(s['injections_completed'],0)
        r,_,s=self.record(self.injection_event('started'))
        self.assertTrue(r.errors);self.assertEqual(s['injections_incomplete'],1)
    def test_orphan_duplicate_and_malformed_injection_events(self):
        r,_,s=self.record(self.injection_event('completed',ended=10500000));self.assertTrue(r.errors);self.assertEqual(s['injections_started'],0)
        r,_,s=self.record(self.injection_event('started')*2);self.assertTrue(r.errors);self.assertEqual(s['injections_started'],1)
        r,_,s=self.record(self.injection_event('started')+self.injection_event('completed',ended=None));self.assertTrue(r.errors);self.assertEqual(s['injections_completed'],0)
        r,_,s=self.record(self.injection_event('started')+self.injection_event('completed',start=1,ended=10500000));self.assertTrue(r.errors);self.assertEqual(s['injections_completed'],0)
        r,_,s=self.record(self.injection_event('started',detection='deer'));self.assertTrue(r.errors);self.assertEqual(s['injections_started'],0)
    def test_firmware_injection_lines_parse(self):
        # Fill the trial program's own INJECTION format strings and feed the result.
        source=(Path(__file__).resolve().parents[1]/'experiments/deer-classifier/src/app_main.cpp').read_text()
        formats=[m.group(1).encode().decode('unicode_escape') for m in re.finditer(r'line\("(INJECTION [^"\\]*(?:\\.[^"\\]*)*)"',source)]
        self.assertEqual(len(formats),2)
        started=formats[0].replace('%s','0123456789abcdef',1).replace('%u','1').replace('%lld','5000000',1).replace('%lld','5020000',1)
        ended=formats[1].replace('%s','0123456789abcdef',1).replace('%u','1').replace('%s','completed',1).replace('%lld','5000000',1).replace('%s','10500000',1).replace('%lld','10520000',1)
        cancelled=formats[1].replace('%s','0123456789abcdef',1).replace('%u','2').replace('%s','cancelled',1).replace('%lld','6000000',1).replace('%s','null',1).replace('%lld','6020000',1)
        started2=started.replace('"injection":1','"injection":2').replace('5000000','6000000')
        r,_,s=self.record((started+'\n'+ended+'\n'+started2+'\n'+cancelled+'\n').encode())
        self.assertFalse(r.errors);self.assertEqual(s['injections_completed'],1);self.assertEqual(s['injections_cancelled'],1)
    def test_schema2_full_frame_pairs_picture_and_input(self):
        raw,jpeg,tensor=fixture(schema=2);r,root,s=self.record(raw,53)
        self.assertFalse(r.errors);self.assertEqual(s['complete_valid_pairs'],1);self.assertEqual(s['frames_without_input'],0)
        self.assertEqual(next(root.glob('*.i8')).read_bytes(),tensor)
    def test_jpeg_only_frame_is_complete_without_input(self):
        raw,jpeg,_=fixture(schema=2,recording='jpeg');r,root,s=self.record(raw)
        self.assertFalse(r.errors);self.assertEqual(s['complete_valid_frames'],1);self.assertEqual(s['complete_valid_pairs'],0)
        self.assertEqual(s['frames_without_input'],1);self.assertFalse(list(root.glob('*.i8')))
        self.assertEqual(next(root.glob('*.jpg')).read_bytes(),jpeg)
    def test_unannounced_input_blob_is_rejected(self):
        full,_,_=fixture(schema=2);jpeg_only=full.replace(b'"recording": "full", "input_sent": true',b'"recording": "jpeg", "input_sent": false')
        r,root,s=self.record(jpeg_only);self.assertTrue(r.errors);self.assertFalse(list(root.glob('*.i8')))
        r,_,_=self.record(full.replace(b'"input_sent": true',b'"input_sent": "yes"'));self.assertTrue(r.errors)
    def test_cancelled_input_transfer_is_counted_not_an_error(self):
        raw,jpeg,_=fixture(schema=2);lines=raw.decode().splitlines()
        start=next(i for i,x in enumerate(lines) if x.startswith('TRIAL_BLOB ') and '"input"' in x)
        cut=lines[:start+3]+['TRIAL_BLOB_CANCELLED 1 input 360',
            'TRIAL_END '+json.dumps(dict(boot='0123456789abcdef',trial=1,frame=1,transfer_complete=False,transfer_cancelled=True,transfer_us=5))]
        r,root,s=self.record(('\n'.join(cut)+'\n').encode())
        self.assertFalse(r.errors);self.assertEqual(s['cancelled_transfers'],1);self.assertEqual(s['complete_valid_pairs'],0)
        self.assertEqual(next(root.glob('*.jpg')).read_bytes(),jpeg);self.assertFalse(list(root.glob('*.i8')))
        r,_,_=self.record(('\n'.join(lines[:start+3]+['TRIAL_BLOB_CANCELLED 1 input 180'])+'\n').encode());self.assertTrue(r.errors)
    def test_sdk_log_lines_are_diagnostics_not_errors(self):
        raw,_,_=fixture(schema=2)
        r,root,s=self.record(b'E (1234) gdma: transfer stalled\n\x1b[0;33mW (1240) cam_hal: FB-OVF\x1b[0m\n'+raw+b'ERROR camera_init code=0x106 name=ESP_ERR_NOT_SUPPORTED\n')
        self.assertEqual(s['errors'],['device: ERROR camera_init code=0x106 name=ESP_ERR_NOT_SUPPORTED'])
        self.assertEqual(s['device_diagnostics'],['E (1234) gdma: transfer stalled','W (1240) cam_hal: FB-OVF'])
        self.assertEqual(s['complete_valid_pairs'],1)
    def test_box_outside_the_picture_keeps_picture_and_input(self):
        raw,jpeg,tensor=fixture(schema=2,boxes=[dict(category=0,score=.3,box=[10,10,320,100])])
        r,root,s=self.record(raw)
        self.assertFalse(r.errors);self.assertEqual(len(s['warnings']),1);self.assertEqual(s['complete_valid_pairs'],1)
        self.assertEqual(next(root.glob('*.jpg')).read_bytes(),jpeg);self.assertEqual(next(root.glob('*.i8')).read_bytes(),tensor)
    def test_one_error_per_failed_transfer(self):
        raw,_,_=fixture();bad=raw.replace(b'TRIAL_DATA 1 input 0 ',b'TRIAL_DATA 2 input 0 ',1)
        r,_,s=self.record(bad);self.assertEqual(s['complete_valid_pairs'],0)
        self.assertEqual(r.errors,['data chunk identity/offset mismatch','incomplete frame: 0123456789abcdef-trial-000001-frame-000001'])
        line=next(x for x in raw.splitlines(keepends=True) if x.startswith(b'TRIAL_DATA 1 input 180 '))
        r,_,s=self.record(raw.replace(line,line+line));self.assertEqual(len(r.errors),2)
        joined=raw[raw.index(b'TRIAL_DATA 1 input 3600 '):]
        r,_,s=self.record(joined);self.assertEqual(len(r.errors),2)
    def test_sensor_state_lines_are_not_errors(self):
        line='PIR_STATE '+json.dumps(dict(boot='0123456789abcdef',mode='pir',pir=1,warmed_up=True,paused=False,pause_remaining_s=0,armed=True,at_us=61000000))
        r,root,s=self.record((line+'\n').encode());self.assertFalse(r.errors)
        self.assertIn('PIR_STATE',(root/'events.jsonl').read_text())
    def test_lighting_is_recorded_and_validated(self):
        raw,_,_=fixture();raw=raw.replace(b'"lighting": "ambient"',b'"lighting": "white"')
        r,root,s=self.record(raw);self.assertFalse(r.errors);self.assertEqual(s['complete_valid_pairs'],1)
        self.assertIn('Capture lighting: white',(root/'index.html').read_text())
        r,_,s=self.record(raw.replace(b'"capture_lighting_ok": true',b'"capture_lighting_ok": false'))
        self.assertTrue(r.errors);self.assertEqual(s['complete_valid_pairs'],0)
    def test_command_boundaries(self):
        for text in ['LIGHT AMBIENT','LIGHT WHITE','RECORD FULL','RECORD JPEG','TEST OUTPUT','INJECT DEER','CAPTURE 600','MODE PIR','STOP']:
            self.assertTrue(valid_command(text),text)
        for text in ['TEST','TEST OUTPUT extra','TEST  OUTPUT','LIGHT BLUE','CAPTURE 601','CAPTURE 0','LIGHT WHITE\nARM','INJECT','INJECT DEER now','INJECT  DEER','inject deer','RECORD','RECORD MIXED']:
            self.assertFalse(valid_command(text),text)

if __name__=='__main__':unittest.main()
