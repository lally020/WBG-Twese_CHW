import json, tempfile, time, unittest
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient
from eval_dashboard import install_eval_routes, ReviewIn

class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        (self.root/'evaluation/baseline_rules').mkdir(parents=True)
        (self.root/'static').mkdir();(self.root/'static/eval-demo.html').write_text('Evaluation lab')
        (self.root/'evals.yaml').write_text('mode: rules')
        (self.root/'evaluation/messages.jsonl').write_text(json.dumps({'id':'m01','text':'fictional text','expected_flag':'urgent'})+'\n')
        self.app=FastAPI();self.svc=install_eval_routes(self.app,self.root);self.client=TestClient(self.app)
    def tearDown(self):self.svc.stop();self.tmp.cleanup()
    def test_report_and_page_routes(self):
        (self.root/'evaluation/baseline_rules/report.json').write_text(json.dumps({'passed':False,'source_hashes':{},'summary':{}}))
        self.assertEqual(self.client.get('/eval-demo').status_code,200)
        self.assertFalse(self.client.get('/api/evals/status').json()['report']['passed'])
    def test_partial_run_not_shown_as_completed(self):
        path=self.root/'evaluation/runs/partial';path.mkdir(parents=True)
        (path/'report.json').write_text('{"summary":{}}')
        self.assertIsNone(self.svc.status()['report'])
    def test_review_separate_from_test_labels(self):
        path=self.root/'evaluation/messages.jsonl';before=path.read_bytes()
        r=self.svc.review(ReviewIn(suite='messages',case_id='m01',expected='nonurgent',reviewer='Demo reviewer',rationale='Fictional review rationale'))
        self.assertEqual(path.read_bytes(),before);self.assertFalse(r['model_changed']);self.assertEqual(self.svc.status()['review_count'],1)
    def test_unknown_case_rejected(self):
        with self.assertRaises(ValueError):self.svc.review(ReviewIn(suite='messages',case_id='../other',expected='urgent',reviewer='Demo',rationale='Not a known case'))
    def test_duplicate_job_blocked(self):
        (self.root/'run_evals.py').write_text("import time\ntime.sleep(.2)\nprint('Report: synthetic')\nraise SystemExit(1)\n")
        self.assertTrue(self.svc.start_run());self.assertFalse(self.svc.start_run())
        deadline=time.monotonic()+3
        while self.svc.running and time.monotonic()<deadline:time.sleep(.02)
        self.assertFalse(self.svc.running);self.assertIsNone(self.svc.error)
    def test_settings_cannot_change_during_cycle(self):
        self.svc.running=True
        self.assertEqual(self.client.post('/api/evals/auto',json={'enabled':False}).status_code,409)
        self.assertEqual(self.client.post('/api/evals/rollback',json={}).status_code,409)
        self.svc.running=False
    def test_watch_detects_changes(self):
        calls=[];self.svc.start_run=lambda reason:(calls.append(reason) or True)
        self.svc.watch=True;self.svc.start()
        (self.root/'new_rule.py').write_text('# changed candidate')
        deadline=time.monotonic()+9
        while not calls and time.monotonic()<deadline:time.sleep(.05)
        self.assertEqual(len(calls),1)

if __name__=='__main__':unittest.main()
