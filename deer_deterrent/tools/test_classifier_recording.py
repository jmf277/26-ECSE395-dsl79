import base64
import io
import json
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
    def test_lighting_is_recorded_and_validated(self):
        raw,_,_=fixture();raw=raw.replace(b'"lighting": "ambient"',b'"lighting": "white"')
        r,root,s=self.record(raw);self.assertFalse(r.errors);self.assertEqual(s['complete_valid_pairs'],1)
        self.assertIn('Capture lighting: white',(root/'index.html').read_text())
        r,_,s=self.record(raw.replace(b'"capture_lighting_ok": true',b'"capture_lighting_ok": false'))
        self.assertTrue(r.errors);self.assertEqual(s['complete_valid_pairs'],0)
    def test_command_boundaries(self):
        for text in ['LIGHT AMBIENT','LIGHT WHITE','TEST OUTPUT','CAPTURE 600','MODE PIR','STOP']:
            self.assertTrue(valid_command(text),text)
        for text in ['TEST','TEST OUTPUT extra','TEST  OUTPUT','LIGHT BLUE','CAPTURE 601','CAPTURE 0','LIGHT WHITE\nARM']:
            self.assertFalse(valid_command(text),text)

if __name__=='__main__':unittest.main()
