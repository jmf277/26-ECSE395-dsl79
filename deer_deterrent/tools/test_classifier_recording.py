import base64
import io
import json
import re
import tempfile
import unittest
from pathlib import Path
from PIL import Image
from record_classifier import Receiver, fnv1a, INPUT_BYTES, valid_command

def fixture():
    photo=io.BytesIO();Image.new('RGB',(320,240),(60,100,150)).save(photo,format='JPEG')
    jpeg=photo.getvalue();tensor=bytes([0,32,127])*(224*224)
    result=dict(schema=1,boot='0123456789abcdef',trial=1,frame=1,within=1,trigger='manual',model_sha256='a'*64,
                valid=True,committed=True,present=False,armed=False,alarm_started=False,score=.2,lighting='ambient',capture_lighting_ok=True,
                jpeg_bytes=len(jpeg),camera_width=320,camera_height=240,input_bytes=INPUT_BYTES,
                input_dtype='int8',input_layout='NHWC',input_shape=[1,224,224,3],input_exponent=-7,boxes=[])
    lines=['TRIAL_FRAME '+json.dumps(result)]
    for kind,data in [('jpeg',jpeg),('input',tensor)]:
        header=dict(boot=result['boot'],trial=1,frame=1,kind=kind,bytes=len(data),fnv1a=fnv1a(data))
        lines.append('TRIAL_BLOB '+json.dumps(header))
        for offset in range(0,len(data),180):
            lines.append(f'TRIAL_DATA 1 {kind} {offset} '+base64.b64encode(data[offset:offset+180]).decode())
        lines.append('TRIAL_BLOB_END 1 '+kind)
    lines.append('TRIAL_END '+json.dumps(dict(boot=result['boot'],trial=1,frame=1,transfer_complete=True,transfer_us=123)))
    return ('\n'.join(lines)+'\n').encode(),jpeg,tensor

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
    def test_lighting_is_recorded_and_validated(self):
        raw,_,_=fixture();raw=raw.replace(b'"lighting": "ambient"',b'"lighting": "white"')
        r,root,s=self.record(raw);self.assertFalse(r.errors);self.assertEqual(s['complete_valid_pairs'],1)
        self.assertIn('Capture lighting: white',(root/'index.html').read_text())
        r,_,s=self.record(raw.replace(b'"capture_lighting_ok": true',b'"capture_lighting_ok": false'))
        self.assertTrue(r.errors);self.assertEqual(s['complete_valid_pairs'],0)
    def test_command_boundaries(self):
        for text in ['LIGHT AMBIENT','LIGHT WHITE','TEST OUTPUT','INJECT DEER','CAPTURE 600','MODE PIR','STOP']:
            self.assertTrue(valid_command(text),text)
        for text in ['TEST','TEST OUTPUT extra','TEST  OUTPUT','LIGHT BLUE','CAPTURE 601','CAPTURE 0','LIGHT WHITE\nARM','INJECT','INJECT DEER now','INJECT  DEER','inject deer']:
            self.assertFalse(valid_command(text),text)

if __name__=='__main__':unittest.main()
