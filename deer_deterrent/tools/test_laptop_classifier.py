import io
import json
import sys
import tempfile
import threading
import time
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit
from urllib.error import HTTPError, URLError
from PIL import Image
import laptop_classifier
from laptop_classifier import Device, process_manifest, fnv1a, validate_manifest, strict_json, BurstGates, EspDetPredictor

BOOT='0123456789abcdef'

def photo(shade=50):
    stream=io.BytesIO();Image.new('RGB',(320,240),(70,100,shade)).save(stream,format='JPEG');return stream.getvalue()

def fixture(count=1):
    jpegs=[photo(40+i) for i in range(count)]
    manifest=dict(protocol=3,boot=BOOT,burst=2,accepting_results=True,
                  images=[dict(id=3+i,bytes=len(j),fnv1a=fnv1a(j),url='https://untrusted.example/image') for i,j in enumerate(jpegs)])
    return jpegs,manifest

class Predictor:
    identity={'test_fixture':True}
    def __init__(self,*scores):self.scores=list(scores) or [.9];self.inputs=[]
    def predict(self,jpeg):
        self.inputs.append(jpeg);score=self.scores[min(len(self.inputs),len(self.scores))-1]
        return {'score':score,'boxes':[]}

def refused(code,message):
    """A refusal as Device.request raises it, its reply already released."""
    error=HTTPError('http://device/classification',code,message,{},None);error.close();return error

class FakeDevice:
    """Serves the manifest's photos and answers verdicts with the board's two-of-three rule."""
    def __init__(self,jpegs):
        self.jpegs=jpegs;self.posts=[];self.paths=[];self.fail=False;self.history=[];self.alarm=False
    def request(self,path,fields=None,image=False):
        self.paths.append(path)
        if image:
            index=int(parse_qs(urlsplit(path).query)['id'][0])-3
            return self.jpegs[index]
        self.posts.append(fields)
        if self.fail:raise refused(409,'expired')
        self.history=(self.history+[fields['deer']=='true'])[-3:]
        present=sum(self.history)>=2;start=present and not self.alarm;self.alarm|=start
        return dict(accepted=True,duplicate=False,present=present,alarm_started=start)

class LaptopClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
    def records(self):
        return [json.loads(p.read_text()) for p in sorted(self.root.glob('*.json'))]
    def test_two_of_three_confirms_and_pairs_exact_jpegs(self):
        jpegs,manifest=fixture(3);device=FakeDevice(jpegs);model=Predictor(.9,.1,.9);seen=set()
        rows=process_manifest(device,model,manifest,self.root,seen,.85)
        self.assertEqual(model.inputs,jpegs);self.assertEqual([r['deer'] for r in rows],[True,False,True])
        self.assertEqual([r['confirmed'] for r in rows],[False,False,True])
        self.assertEqual([r['alarm_receipt']['alarm_started'] for r in rows],[False,False,True])
        self.assertEqual(sorted(p.read_bytes() for p in self.root.glob('*.jpg')),sorted(jpegs))
        self.assertEqual(device.posts[0],dict(boot=BOOT,burst=2,id=3,fnv1a=fnv1a(jpegs[0]),deer='true'))
        self.assertEqual(process_manifest(device,model,manifest,self.root,seen,.85),[]);self.assertEqual(len(device.posts),3)
        self.assertTrue(all(p.startswith('/photo.jpg?') and 'untrusted' not in p for p in device.paths if p.startswith('/photo')))
    def test_one_deer_picture_does_not_confirm(self):
        jpegs,manifest=fixture(2);rows=process_manifest(FakeDevice(jpegs),Predictor(.9,.1),manifest,self.root,set(),.85)
        self.assertEqual([r['confirmed'] for r in rows],[False,False]);self.assertFalse(any(r['alarm_receipt']['alarm_started'] for r in rows))
    def test_negative_returns_real_negative(self):
        jpegs,manifest=fixture(1);rows=process_manifest(FakeDevice(jpegs),Predictor(.2),manifest,self.root,set(),.85)
        self.assertFalse(rows[0]['deer']);self.assertFalse(rows[0]['alarm_receipt']['alarm_started'])
    def test_failed_photo_is_recorded_and_the_next_one_is_processed(self):
        jpegs,manifest=fixture(3);device=FakeDevice([jpegs[0][:-5],jpegs[1],jpegs[2]]);model=Predictor(.9,.9)
        rows=process_manifest(device,model,manifest,self.root,set(),.85)
        self.assertIn('error',rows[0]);self.assertIsNone(rows[0]['alarm_receipt']);self.assertEqual(len(device.posts),2)
        self.assertEqual(len(model.inputs),2);self.assertEqual([r.get('confirmed') for r in rows],[None,False,True])
    def test_nonfinite_inference_sends_no_verdict(self):
        jpegs,manifest=fixture(1);device=FakeDevice(jpegs)
        rows=process_manifest(device,Predictor(float('nan')),manifest,self.root,set(),.85)
        self.assertFalse(device.posts);self.assertIn('invalid model score',rows[0]['error'])
        self.assertIsNone(self.records()[0]['alarm_receipt'])
    def test_rejected_response_retains_score_and_error(self):
        jpegs,manifest=fixture(1);device=FakeDevice(jpegs);device.fail=True
        rows=process_manifest(device,Predictor(.9),manifest,self.root,set(),.85)
        row=self.records()[0]
        self.assertEqual(row['prediction']['score'],.9);self.assertIsNone(row['alarm_receipt']);self.assertIn('409',row['error'])
    def test_closed_window_does_not_reprocess_prior_images(self):
        jpegs,manifest=fixture(1);manifest['accepting_results']=False;device=FakeDevice(jpegs);model=Predictor()
        self.assertEqual(process_manifest(device,model,manifest,self.root,set(),.85),[])
        self.assertFalse(model.inputs);self.assertFalse(device.paths)
    def test_bad_manifest_and_duplicate_json_rejected(self):
        _,manifest=fixture(1);manifest['images']*=2
        with self.assertRaises(ValueError):validate_manifest(manifest)
        _,manifest=fixture(1);manifest['protocol']=2
        with self.assertRaises(ValueError):validate_manifest(manifest)
        with self.assertRaises(ValueError):strict_json('{"deer":true,"deer":false}')
        with self.assertRaises(ValueError):strict_json('{"score":NaN}')
    def test_unexpected_receipt_does_not_become_success(self):
        jpegs,manifest=fixture(1);device=FakeDevice(jpegs);request=device.request
        def contradictory(path,fields=None,image=False):
            return request(path,fields,image) if image else dict(accepted=True,duplicate=True,present=True,alarm_started=True)
        device.request=contradictory
        rows=process_manifest(device,Predictor(.9),manifest,self.root,set(),.85)
        self.assertIn('acknowledgment',rows[0]['error']);self.assertIsNone(self.records()[0]['alarm_receipt'])
    def test_disagreement_with_the_device_decision_is_an_error(self):
        jpegs,manifest=fixture(1);device=FakeDevice(jpegs);request=device.request
        def early(path,fields=None,image=False):
            return request(path,fields,image) if image else dict(accepted=True,duplicate=False,present=True,alarm_started=False)
        device.request=early
        rows=process_manifest(device,Predictor(.9),manifest,self.root,set(),.85)
        self.assertIn('disagree',rows[0]['error'])
    def test_gate_is_the_repository_rule(self):
        gates=BurstGates(.85);gate=gates.for_burst(BOOT,2)
        self.assertIs(gate,gates.for_burst(BOOT,2));self.assertIsNot(gate,gates.for_burst(BOOT,3))
        self.assertEqual((gate.window,gate.required,gate.threshold),(3,2,.85))
        self.assertEqual(type(gate).__module__,'deer_temporal_gate')
    def test_invalid_origin_rejected(self):
        for url in ('file:///tmp/image','http://host/path','http://user:pass@host','http://host?url=elsewhere'):
            with self.assertRaises(ValueError):Device(url)

class EmulatedBoard:
    """The laptop-interface program's HTTP routes, protocol 3, served on the loopback address."""
    def __init__(self,jpegs,pause_seconds=0.0,refuse_first_capture=False):
        self.jpegs=jpegs;self.lock=threading.Lock();self.armed=False;self.burst=0;self.retained=[];self.history=[]
        self.alarms=0;self.pause_until=time.monotonic()+pause_seconds;self.refuse_next=refuse_first_capture
        self.captures_in_pause=0;self.refusals=0;self.verdicts=[];self.stops=0
        board=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def reply(self,code,body,kind='application/json'):
                data=body.encode() if isinstance(body,str) else body
                self.send_response(code);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
            def do_GET(self):
                parts=urlsplit(self.path);query=parse_qs(parts.query)
                with board.lock:
                    if parts.path=='/status':
                        return self.reply(200,json.dumps(dict(protocol=3,state='ready',pir_ready=True,pir=False,photos=len(board.retained),boot=BOOT,
                            burst=board.burst,burst_active=False,attempts=0,confirmed=False,armed=board.armed,alarm_active=False,
                            cooldown_seconds=1 if time.monotonic()<board.pause_until else 0)))
                    if parts.path=='/burst':
                        images=[dict(id=i,bytes=len(j),fnv1a=fnv1a(j),url='/x') for i,j in board.retained]
                        return self.reply(200,json.dumps(dict(protocol=3,boot=BOOT,burst=board.burst,accepting_results=bool(board.burst),images=images)))
                    if parts.path=='/photo.jpg':
                        if query.get('boot')!=[BOOT] or query.get('burst')!=[str(board.burst)]:return self.reply(409,'stale','text/plain')
                        for i,j in board.retained:
                            if query.get('id')==[str(i)]:return self.reply(200,j,'image/jpeg')
                        return self.reply(404,'none','text/plain')
                self.reply(404,'none','text/plain')
            def do_POST(self):
                fields=parse_qs(self.rfile.read(int(self.headers.get('Content-Length',0))).decode())
                with board.lock:
                    if self.path=='/stop':board.armed=False;board.stops+=1;return self.reply(200,'{"armed":false,"alarm_active":false}')
                    if self.path=='/arm':board.armed=True;return self.reply(200,'{"armed":true}')
                    if self.path=='/capture':
                        if time.monotonic()<board.pause_until:board.captures_in_pause+=1
                        if time.monotonic()<board.pause_until or board.refuse_next:
                            board.refuse_next=False;board.refusals+=1
                            return self.reply(409,'Wait for PIR warmup, active burst or cooldown to finish.','text/plain')
                        board.burst+=1;board.retained=[(10+i,j) for i,j in enumerate(board.jpegs)];board.history=[]
                        return self.reply(202,'Manual burst started.','text/plain')
                    if self.path=='/classification':
                        board.verdicts.append(fields['deer'][0])
                        board.history=(board.history+[fields['deer'][0]=='true'])[-3:]
                        present=sum(board.history)>=2;start=present and board.armed and board.alarms==0;board.alarms+=start
                        return self.reply(200,json.dumps(dict(accepted=True,duplicate=False,present=present,alarm_started=start)))
                self.reply(404,'none','text/plain')
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
    def __enter__(self):self.thread.start();return self
    def __exit__(self,*exc):self.server.shutdown();self.server.server_close();self.thread.join()
    @property
    def url(self):return f'http://127.0.0.1:{self.server.server_port}'

class MainLoopTests(unittest.TestCase):
    """main() against an emulated board over real HTTP, with a stand-in predictor."""
    def run_client(self,board,scores,*extra):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);out=Path(temp.name)/'session'
        def factory(*args):return Predictor(*scores)
        with mock.patch('builtins.print'):
            laptop_classifier.main(['--checkpoint','x.pt','--model-project','m','--esp-detection','e','--url',board.url,
                                    '--output',str(out),'--seconds','1.5','--poll','0.05',*extra],predictor_factory=factory)
        rows=[json.loads(x) for x in (out/'events.jsonl').read_text().splitlines()]
        return out,rows
    def test_armed_capture_confirms_on_the_second_deer_picture(self):
        jpegs=[photo(40+i) for i in range(3)]
        with EmulatedBoard(jpegs) as board:
            out,rows=self.run_client(board,[.9,.95,.2],'--arm','--capture')
        self.assertEqual(board.verdicts,['true','true','false']);self.assertEqual(board.alarms,1)
        records=[json.loads(p.read_text()) for p in sorted(out.glob('*-photo-*.json'))]
        self.assertEqual([r['confirmed'] for r in records],[False,True,True])
        self.assertEqual([r['alarm_receipt']['alarm_started'] for r in records],[False,True,False])
        self.assertTrue(json.loads((out/'run.json').read_text())['armed']);self.assertGreaterEqual(board.stops,2)
    def test_capture_waits_out_a_pause_instead_of_ending(self):
        with EmulatedBoard([photo(60)],pause_seconds=0.4) as board:
            out,rows=self.run_client(board,[.1],'--capture')
        self.assertEqual(board.captures_in_pause,0);self.assertEqual(board.verdicts,['false'])
        self.assertFalse(any('error' in r for r in rows))
    def test_refused_capture_is_retried(self):
        with EmulatedBoard([photo(61)],refuse_first_capture=True) as board:
            out,rows=self.run_client(board,[.1],'--capture')
        self.assertEqual(board.refusals,1);self.assertEqual(board.verdicts,['false'])
        self.assertTrue(any('capture_deferred' in r for r in rows))
    def test_ten_seconds_without_a_good_poll_end_the_session(self):
        # The device answers the opening status and one poll, then stops answering. The clock advances 4 s
        # at every reading, so the first failed poll comes more than 10 s after the last good one.
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);out=Path(temp.name)/'session'
        state=dict(protocol=3,boot=BOOT,pir_ready=False,burst_active=False,alarm_active=False,cooldown_seconds=0)
        class SilentDevice:
            polls=0
            def __init__(self,base):pass
            def status(self):
                SilentDevice.polls+=1
                if SilentDevice.polls>2:raise URLError('no route to the device')
                return dict(state)
            def request(self,path,fields=None,image=False):
                if path=='/burst':return dict(protocol=3,boot=BOOT,burst=0,accepting_results=False,images=[])
                return dict(armed=False,alarm_active=False)
        clock=iter(range(0,100000,4))
        with mock.patch('builtins.print'),mock.patch.object(laptop_classifier,'Device',SilentDevice),\
             mock.patch.object(laptop_classifier.time,'monotonic',lambda:float(next(clock))),\
             mock.patch.object(laptop_classifier.time,'sleep',lambda seconds:None):
            with self.assertRaises(RuntimeError) as caught:
                laptop_classifier.main(['--checkpoint','x.pt','--model-project','m','--esp-detection','e','--url','http://device',
                                        '--output',str(out),'--seconds','600','--poll','0.25'],predictor_factory=lambda *a:Predictor())
        self.assertEqual(str(caught.exception),'device unreachable for 10 s');self.assertEqual(SilentDevice.polls,3)
        rows=[json.loads(x) for x in (out/'events.jsonl').read_text().splitlines()]
        self.assertEqual(sum('poll_error' in r for r in rows),1);self.assertIn('stop',rows[-1])
    def test_refused_request_releases_its_reply(self):
        with EmulatedBoard([photo(62)],refuse_first_capture=True) as board:
            with self.assertRaises(HTTPError) as caught:Device(board.url).request('/capture',{},image=True)
        self.assertEqual(caught.exception.code,409);self.assertTrue(caught.exception.fp.closed)

class StandInModel:
    """Minimal stand-ins for torch and ultralytics, to run EspDetPredictor's own code."""
    def __init__(self,names,boxes,warn=False):
        self.names,self.boxes,self.warn=names,boxes,warn
        self.built=self.build()
    def modules(self):
        return self.built
    def build(self):
        model=self
        class Tensor:
            def __init__(self,values):self.values=values
            def cpu(self):return self
            def tolist(self):return self.values
        class Boxes:
            def __init__(self,rows):
                self.xyxy=Tensor([r[0] for r in rows]);self.conf=Tensor([r[1] for r in rows]);self.cls=Tensor([r[2] for r in rows])
        class Result:
            def __init__(self,rows):self.boxes=Boxes(rows)
        logger=types.SimpleNamespace(handlers=[])
        logger.addHandler=logger.handlers.append;logger.removeHandler=logger.handlers.remove
        class YOLO:
            def __init__(self,path):self.path=path;self.names=model.names
            def predict(self,**settings):
                self.settings=settings
                if model.warn:
                    import logging
                    record=logging.LogRecord('ultralytics',30,'',0,'WARNING NMS time limit 2.0s exceeded',None,None)
                    for handler in list(logger.handlers):handler.emit(record)
                return [Result(model.boxes)]
        ultralytics=types.ModuleType('ultralytics');ultralytics.YOLO=YOLO;ultralytics.__version__='stand-in'
        utils=types.ModuleType('ultralytics.utils');utils.LOGGER=logger;ultralytics.utils=utils
        torch=types.ModuleType('torch');torch.__version__='stand-in'
        return {'ultralytics':ultralytics,'ultralytics.utils':utils,'torch':torch}

class PredictorTests(unittest.TestCase):
    """EspDetPredictor's checks and score contract, run with stand-in model libraries."""
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);root=Path(temp.name)
        self.checkpoint=root/'best.pt';self.checkpoint.write_bytes(b'weights')
        self.project=root/'deer_classifier';self.project.mkdir()
        (self.project/'espdet_compat.py').write_text('LOADED=[]\ndef load_esp_detection(path):\n    LOADED.append(path)\n')
        self.espdet=root/'esp-detection';self.espdet.mkdir();(self.espdet/'train.py').write_text('')
    def predictor(self,model):
        with mock.patch.dict(sys.modules,model.modules()):
            p=EspDetPredictor(self.checkpoint,self.project,self.espdet)
        return p
    def predict(self,model,p):
        with mock.patch.dict(sys.modules,model.modules()):
            return p.predict(photo())
    def test_maximum_deer_box_is_the_score(self):
        model=StandInModel({0:'deer'},[([1,2,30,40],.31,0),([5,5,9,9],.62,0)])
        p=self.predictor(model);result=self.predict(model,p)
        self.assertEqual(result['score'],.62);self.assertEqual(len(result['boxes']),2)
        self.assertEqual(p.identity['checkpoint_sha256'],laptop_classifier.digest(self.checkpoint))
        self.assertEqual(p.identity['prediction_floor'],.001)
    def test_no_box_is_a_zero_score(self):
        model=StandInModel({0:'deer'},[]);p=self.predictor(model)
        self.assertEqual(self.predict(model,p)['score'],0.0)
    def test_truncated_nms_sends_no_verdict(self):
        model=StandInModel({0:'deer'},[([1,2,30,40],.9,0)],warn=True);p=self.predictor(model)
        with self.assertRaises(RuntimeError):self.predict(model,p)
    def test_other_classes_and_bad_scores_are_refused(self):
        model=StandInModel({0:'deer'},[([1,2,30,40],.4,1)]);p=self.predictor(model)
        with self.assertRaises(ValueError):self.predict(model,p)
        model=StandInModel({0:'deer'},[([1,2,30,40],1.5,0)]);p=self.predictor(model)
        with self.assertRaises(ValueError):self.predict(model,p)
    def test_wrong_model_or_paths_are_refused(self):
        with self.assertRaises(ValueError):self.predictor(StandInModel({0:'deer',1:'bird'},[]))
        self.checkpoint.rename(self.checkpoint.with_suffix('.onnx'));self.checkpoint=self.checkpoint.with_suffix('.onnx')
        with self.assertRaises(ValueError):self.predictor(StandInModel({0:'deer'},[]))

if __name__=='__main__':unittest.main()
