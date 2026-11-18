import base64
import csv
import hashlib
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
    """Stands in for pyserial's port: answers like the trial program, can fail and can stall mid-transfer."""
    def __init__(self,first_state=None,lose_after=None,after_stop=b'',drip=0,stall_after=None,stall_seconds=0.0):
        self.first_state=first_state or {};self.lose_after=lose_after;self.after_stop=after_stop;self.drip=drip
        self.stall_after=stall_after;self.stall_seconds=stall_seconds;self.stall_left=None;self.stalled_at=None
        self.written=[];self.outgoing=bytearray();self.reads=0;self.closed=False;self.stops=0;self.lock=threading.Lock()
    def open(self):pass
    def write(self,data):
        command=data.decode('ascii').strip()
        with self.lock:
            self.written.append(command)
            if command=='STATUS' and len(self.written)==1:self.outgoing+=status_line('STATUS',**self.first_state)
            elif command=='STOP':
                self.stops+=1;self.outgoing+=status_line('STOP')
                if self.stops>1:
                    # With stall_after, the closing STOP's reply and that many bytes of after_stop go out,
                    # then nothing for stall_seconds, then the rest.
                    if self.stall_after is not None:self.stall_left=len(self.outgoing)+self.stall_after
                    self.outgoing+=self.after_stop
            else:self.outgoing+=status_line(command)
    def read(self,size):
        self.reads+=1
        if self.lose_after is not None and self.reads>self.lose_after:
            raise SerialException('device reports readiness to read but returned no data')
        with self.lock:
            size=min(size,self.drip) if self.drip else size
            if self.stall_left==0:
                if self.stalled_at is None:self.stalled_at=time.monotonic()
                if time.monotonic()-self.stalled_at<self.stall_seconds:size=0
                else:self.stall_left=None
            elif self.stall_left is not None:size=min(size,self.stall_left)
            chunk=bytes(self.outgoing[:size]);del self.outgoing[:size]
            if self.stall_left:self.stall_left-=len(chunk)
        if not chunk:time.sleep(0.002)
        return chunk
    def close(self):self.closed=True

def serial_module(port):
    module=types.ModuleType('serial')
    module.SerialException=SerialException
    def factory(**kwargs):return port
    module.Serial=factory
    return module

def run_live(test,port,commands,*extra,hold=0.0):
    """Runs record_classifier.main() over a stand-in port with scripted console input."""
    temp=tempfile.TemporaryDirectory();test.addCleanup(temp.cleanup)
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

class LiveRecorderTests(unittest.TestCase):
    """main() over a stand-in port: start state, a lost port, QUIT and the serial import."""
    def run_main(self,port,commands,*extra,hold=0.0):
        return run_live(self,port,commands,*extra,hold=hold)
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
        # The port falls silent for 0.6 s halfway through the frame, longer than the 0.2 s quiet time,
        # so QUIT closes the session with the whole frame only if it waits for the open transfer.
        raw,jpeg,tensor=fixture(schema=2)
        port=FakePort(after_stop=raw,drip=700,stall_after=len(raw)//2,stall_seconds=0.6)
        code,out,rows,manifest,summary=self.run_main(port,[])
        self.assertIsNotNone(port.stalled_at)
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
    def test_output_test_is_recorded_apart_from_frames(self):
        r,root,s=self.record(self.output_event('started')+self.output_event('completed'))
        self.assertFalse(r.errors);self.assertEqual(s['frames'],0);self.assertEqual(s['complete_valid_pairs'],0)
        self.assertEqual(s['output_tests_completed'],1);self.assertIn('Output tests (injected alarm trigger)',(root/'index.html').read_text())
    def test_output_cancellation_and_missing_terminal(self):
        r,_,s=self.record(self.output_event('started')+self.output_event('cancelled'))
        self.assertFalse(r.errors);self.assertEqual(s['output_tests_cancelled'],1);self.assertEqual(s['output_tests_completed'],0)
        r,_,s=self.record(self.output_event('started'))
        self.assertTrue(r.errors);self.assertEqual(s['output_tests_incomplete'],1);self.assertEqual(s['output_tests_completed'],0)
    def test_orphan_and_duplicate_output_events(self):
        r,_,s=self.record(self.output_event('completed'));self.assertTrue(r.errors);self.assertEqual(s['output_tests_completed'],0)
        r,_,s=self.record(self.output_event('started')*2+self.output_event('cancelled')*2)
        self.assertTrue(r.errors);self.assertEqual(s['output_tests_started'],1)
    def test_firmware_output_test_lines_parse(self):
        # Fill the trial program's own OUTPUT_TEST format string and feed the result.
        source=(Path(__file__).resolve().parents[1]/'experiments/deer-classifier/src/app_main.cpp').read_text()
        formats=[m.group(1).encode().decode('unicode_escape') for m in re.finditer(r'line\("(OUTPUT_TEST [^"\\]*(?:\\.[^"\\]*)*)"',source)]
        self.assertEqual(len(formats),1)
        def filled(test,event,observed):
            return formats[0].replace('%s','0123456789abcdef',1).replace('%u',str(test),1).replace('%s',event,1).replace('%lld',str(observed),1)
        lines=[filled(1,'started',5000000),filled(1,'completed',10519000),filled(2,'started',12000000),filled(2,'cancelled',13000000)]
        r,root,s=self.record(('\n'.join(lines)+'\n').encode())
        self.assertFalse(r.errors);self.assertEqual(s['output_tests_started'],2)
        self.assertEqual(s['output_tests_completed'],1);self.assertEqual(s['output_tests_cancelled'],1)
        rows=[json.loads(x) for x in (root/'events.jsonl').read_text().splitlines()]
        self.assertEqual([row['output_test']['event'] for row in rows if 'output_test' in row],['started','completed','started','cancelled'])
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
    def test_gallery_draws_boxes_and_lists_trials(self):
        raw,_,_=fixture(schema=2,boxes=[dict(category=0,score=.3,box=[32,24,95,71])])
        r,root,s=self.record(raw)
        page=(root/'index.html').read_text()
        self.assertIn('left:10.00%;top:10.00%;width:20.00%;height:20.00%',page);self.assertIn('<span>0.300</span>',page)
        with (root/'trials.csv').open() as stream:rows=list(csv.DictReader(stream))
        self.assertEqual([(x['trial'],x['frames'],x['max_score'],x['confirmed']) for x in rows],[('1','1','0.2','False')])
        self.assertEqual(s['trials'][0]['recording'],'full')
    def test_markers_are_tied_to_the_frame_before_them(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);root=Path(temp.name)
        r=Receiver(root,context=dict(label='D1 at 5 in',notes='white light'));r.mark('before any frame')
        raw,_,_=fixture(schema=2);r.feed(raw);r.mark('print removed');r.finish()
        rows=[json.loads(x) for x in (root/'events.jsonl').read_text().splitlines() if 'marker' in x]
        self.assertEqual([(x['marker'],x['after_frame']) for x in rows],[('before any frame',None),('print removed','0123456789abcdef-trial-000001-frame-000001')])
        page=(root/'index.html').read_text()
        self.assertLess(page.index('Marker: before any frame'),page.index('Trial 1, frame 1'));self.assertLess(page.index('Trial 1, frame 1'),page.index('Marker: print removed'))
        self.assertIn('D1 at 5 in',page)
    def test_live_manifest_closes_with_hashes_and_firmware(self):
        boot=('BOOT deer_recorded_trials schema=2 boot=0123456789abcdef model_bytes=499712 sha256='+'a'*64+' firmware='+json.dumps(dict(project='deer_recorded_trials',version='1',idf='v5.4.2',compiled='Nov 11 2026 10:00:00',elf_sha256='b'*64),separators=(',',':'))+'\n').encode()
        raw,_,_=fixture(schema=2)
        port=FakePort(after_stop=boot+raw,drip=900)
        code,out,rows,manifest,summary=run_live(self,port,[],'--label','D1')
        self.assertEqual(manifest['firmware']['0123456789abcdef']['elf_sha256'],'b'*64);self.assertEqual(manifest['label'],'D1')
        for name,entry in manifest['artifacts'].items():
            self.assertEqual(entry['sha256'],hashlib.sha256((out/name).read_bytes()).hexdigest())
        self.assertIn('serial.raw',manifest['artifacts']);self.assertNotIn('run_manifest.json',manifest['artifacts'])
    def test_sensor_state_lines_are_not_errors(self):
        line='PIR_STATE '+json.dumps(dict(boot='0123456789abcdef',mode='pir',pir=1,warmed_up=True,paused=False,pause_remaining_s=0,armed=True,at_us=61000000))
        r,root,s=self.record((line+'\n').encode());self.assertFalse(r.errors)
        self.assertIn('PIR_STATE',(root/'events.jsonl').read_text())
    def test_lighting_is_recorded_and_validated(self):
        raw,_,_=fixture();raw=raw.replace(b'"lighting": "ambient"',b'"lighting": "white"')
        r,root,s=self.record(raw);self.assertFalse(r.errors);self.assertEqual(s['complete_valid_pairs'],1)
        self.assertIn('lighting white',(root/'index.html').read_text())
        r,_,s=self.record(raw.replace(b'"capture_lighting_ok": true',b'"capture_lighting_ok": false'))
        self.assertTrue(r.errors);self.assertEqual(s['complete_valid_pairs'],0)
    def test_command_boundaries(self):
        for text in ['LIGHT AMBIENT','LIGHT WHITE','RECORD FULL','RECORD JPEG','TEST OUTPUT','CAPTURE 600','MODE PIR','STOP']:
            self.assertTrue(valid_command(text),text)
        for text in ['TEST','TEST OUTPUT extra','TEST  OUTPUT','test output','LIGHT BLUE','CAPTURE 601','CAPTURE 0','LIGHT WHITE\nARM','RECORD','RECORD MIXED']:
            self.assertFalse(valid_command(text),text)

if __name__=='__main__':unittest.main()
