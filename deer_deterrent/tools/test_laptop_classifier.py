import io
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from urllib.error import HTTPError
from PIL import Image
from laptop_classifier import Device, process_manifest, fnv1a, validate_manifest, strict_json

def fixture():
    stream=io.BytesIO();Image.new('RGB',(320,240),(70,100,50)).save(stream,format='JPEG')
    jpeg=stream.getvalue()
    manifest=dict(protocol=2,boot='0123456789abcdef',burst=2,accepting_results=True,
                  images=[dict(id=3,bytes=len(jpeg),fnv1a=fnv1a(jpeg),url='https://untrusted.example/image')])
    return jpeg,manifest

class Predictor:
    identity={'test_fixture':True}
    def __init__(self,score=.9):self.score=score;self.inputs=[]
    def predict(self,jpeg):self.inputs.append(jpeg);return {'score':self.score,'boxes':[]}

class FakeDevice:
    def __init__(self,jpeg):self.jpeg=jpeg;self.posts=[];self.paths=[];self.fail=False
    def request(self,path,fields=None,image=False):
        self.paths.append(path)
        if image:return self.jpeg
        self.posts.append(fields)
        if self.fail:raise HTTPError('http://device/classification',409,'expired',{},None)
        return dict(accepted=True,duplicate=False,alarm_started=fields['deer']=='true')

class LaptopClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.jpeg,self.manifest=fixture()
        self.device=FakeDevice(self.jpeg);self.model=Predictor()
    def test_positive_pairs_exact_jpeg_and_acknowledgment(self):
        seen=set();rows=process_manifest(self.device,self.model,self.manifest,self.root,seen,.85)
        self.assertEqual(self.model.inputs,[self.jpeg]);self.assertEqual(next(self.root.glob('*.jpg')).read_bytes(),self.jpeg)
        self.assertTrue(rows[0]['alarm_receipt']['alarm_started'])
        self.assertEqual(self.device.posts,[dict(boot=self.manifest['boot'],burst=2,id=3,fnv1a=fnv1a(self.jpeg),deer='true')])
        self.assertEqual(process_manifest(self.device,self.model,self.manifest,self.root,seen,.85),[])
        self.assertEqual(len(self.device.posts),1)
        self.assertTrue(self.device.paths[0].startswith('/photo.jpg?'))
        self.assertNotIn('untrusted',self.device.paths[0])
    def test_negative_returns_real_negative(self):
        rows=process_manifest(self.device,Predictor(.2),self.manifest,self.root,set(),.85)
        self.assertFalse(rows[0]['deer']);self.assertFalse(rows[0]['alarm_receipt']['alarm_started'])
    def test_corrupt_capture_sends_no_verdict(self):
        self.device.jpeg=self.jpeg[:-5]
        with self.assertRaises(ValueError):process_manifest(self.device,self.model,self.manifest,self.root,set(),.85)
        self.assertFalse(self.model.inputs);self.assertFalse(self.device.posts)
        self.assertIn('error',json.loads(next(self.root.glob('*.json')).read_text()))
    def test_nonfinite_inference_sends_no_verdict(self):
        with self.assertRaises(ValueError):process_manifest(self.device,Predictor(float('nan')),self.manifest,self.root,set(),.85)
        self.assertFalse(self.device.posts)
        self.assertIsNone(json.loads(next(self.root.glob('*.json')).read_text())['alarm_receipt'])
    def test_rejected_response_retains_score_and_error(self):
        self.device.fail=True
        with self.assertRaises(HTTPError):process_manifest(self.device,self.model,self.manifest,self.root,set(),.85)
        row=json.loads(next(self.root.glob('*.json')).read_text())
        self.assertEqual(row['prediction']['score'],.9);self.assertIsNone(row['alarm_receipt']);self.assertIn('409',row['error'])
    def test_closed_window_does_not_reprocess_prior_images(self):
        self.manifest['accepting_results']=False
        self.assertEqual(process_manifest(self.device,self.model,self.manifest,self.root,set(),.85),[])
        self.assertFalse(self.model.inputs);self.assertFalse(self.device.paths)
    def test_bad_manifest_and_duplicate_json_rejected(self):
        self.manifest['images']*=2
        with self.assertRaises(ValueError):validate_manifest(self.manifest)
        with self.assertRaises(ValueError):strict_json('{"deer":true,"deer":false}')
        with self.assertRaises(ValueError):strict_json('{"score":NaN}')
    def test_unexpected_receipt_does_not_become_success(self):
        request=self.device.request
        def contradictory(path,fields=None,image=False):
            return request(path,fields,image) if image else dict(accepted=True,duplicate=True,alarm_started=True)
        self.device.request=contradictory
        with self.assertRaises(ValueError):process_manifest(self.device,self.model,self.manifest,self.root,set(),.85)
        self.assertIsNone(json.loads(next(self.root.glob('*.json')).read_text())['alarm_receipt'])
    def test_invalid_origin_rejected(self):
        for url in ('file:///tmp/image','http://host/path','http://user:pass@host','http://host?url=elsewhere'):
            with self.assertRaises(ValueError):Device(url)
    def test_real_http_download_and_return(self):
        jpeg,manifest=fixture();posts=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                query=parse_qs(urlsplit(self.path).query)
                if urlsplit(self.path).path!='/photo.jpg' or query!={'boot':[manifest['boot']],'burst':['2'],'id':['3']}:
                    self.send_error(400);return
                self.send_response(200);self.end_headers();self.wfile.write(jpeg)
            def do_POST(self):
                data=parse_qs(self.rfile.read(int(self.headers['Content-Length'])).decode())
                posts.append(data);self.send_response(200);self.end_headers()
                self.wfile.write(b'{"accepted":true,"duplicate":false,"alarm_started":true}')
        server=HTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            device=Device(f'http://127.0.0.1:{server.server_port}')
            rows=process_manifest(device,self.model,manifest,self.root,set(),.85)
            self.assertTrue(rows[0]['alarm_receipt']['alarm_started'])
            self.assertEqual(posts[0]['deer'],['true']);self.assertEqual(posts[0]['fnv1a'],[fnv1a(jpeg)])
        finally:
            server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
