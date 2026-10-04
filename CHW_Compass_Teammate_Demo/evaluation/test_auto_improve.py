import copy,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from urgency_ai import UrgencyClassifier
from auto_improve import activation_checks,atomic,rollback,cycle_lock

class AutoLearningTests(unittest.TestCase):
    def test_existing_classifier_reloads_atomic_policy(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'active.json'
            with patch.dict(os.environ,{'COMPASS_URGENCY_POLICY':str(p)}):m=UrgencyClassifier()
            self.assertEqual(m.predict('Hello there')['version'],'synthetic-urgency-v1')
            spec=UrgencyClassifier.base_spec();spec['version']='synthetic-test-candidate';atomic(p,spec)
            self.assertEqual(m.predict('Hello there')['version'],'synthetic-test-candidate')
            p.write_text('{invalid')
            self.assertEqual(m.predict('Hello there')['version'],'synthetic-test-candidate')
            self.assertIsNotNone(m.registry_error)
    def test_invalid_policy_rejected(self):
        spec=UrgencyClassifier.base_spec();spec['urgent_threshold']=.1
        with self.assertRaises(ValueError):UrgencyClassifier(spec,use_registry=False)
    def test_language_abstention_retained(self):
        m=UrgencyClassifier(UrgencyClassifier.base_spec(),use_registry=False)
        self.assertEqual(m.predict('siwezi kupumua','Kiswahili')['label'],'uncertain')
    def fixture(self):
        old={'metrics':{'fn':1,'fp':0,'f1':.5},'rows':[{'expected':'urgent','predicted':'urgent','reply_ok':True,'elapsed':.01,'review':True,'urgent_task':True}]}
        new=copy.deepcopy(old);new['metrics']['fn']=0;new['metrics']['f1']=1
        return old,new
    def test_gate_accepts_only_improvement(self):
        b,a=self.fixture();self.assertTrue(all(activation_checks(b,a,b,a,b,a,[True],[True]).values()))
        a['metrics']['fp']=1
        self.assertFalse(all(activation_checks(b,a,b,a,b,a,[True],[True]).values()))
    def test_new_miss_blocked_despite_aggregate_gain(self):
        b,a=self.fixture();a['rows'][0]['predicted']='nonurgent'
        self.assertFalse(activation_checks(b,a,b,a,b,a,[True],[True])['no_new_missed_urgent_classifier_cases'])
    def test_no_improvement_keeps_incumbent(self):
        b,a=self.fixture()
        self.assertFalse(all(activation_checks(b,b,b,b,b,b,[True],[True]).values()))
    def test_rollback_restores_previous_policy(self):
        with tempfile.TemporaryDirectory() as td:
            registry=Path(td)/'evaluation/model_registry';s=UrgencyClassifier.base_spec()
            atomic(registry/'previous.json',s)
            self.assertEqual(rollback(td)['restored'],s['version'])
            self.assertEqual(json.loads((registry/'active.json').read_text()),s)
    def test_concurrent_cycles_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            with cycle_lock(Path(td)):
                with self.assertRaises(RuntimeError):
                    with cycle_lock(Path(td)):pass

if __name__=='__main__':unittest.main()
