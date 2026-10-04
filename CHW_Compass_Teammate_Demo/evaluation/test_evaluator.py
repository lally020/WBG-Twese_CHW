"""Test measurement logic independently of app expectations."""
import unittest
from run_evals import binary_metrics, summarize

class MetricsTests(unittest.TestCase):
    def test_hand_calculated_confusion(self):
        pairs=[('urgent','urgent'),('urgent','nonurgent'),('urgent','uncertain'),('nonurgent','urgent'),('nonurgent','nonurgent')]
        m=binary_metrics([dict(expected=a,predicted=b) for a,b in pairs])
        self.assertEqual((m['tp'],m['fp'],m['fn'],m['tn']),(1,1,2,1))
        self.assertEqual(m['precision'],.5);self.assertAlmostEqual(m['recall'],1/3)
        self.assertAlmostEqual(m['f1'],.4);self.assertEqual(m['abstentions'],1)
    def test_undefined_not_fabricated(self):
        m=binary_metrics([dict(expected='nonurgent',predicted='nonurgent')])
        self.assertIsNone(m['precision']);self.assertIsNone(m['recall']);self.assertIsNone(m['f1'])
    def test_error_is_missed_urgent(self):
        m=binary_metrics([dict(expected='urgent',predicted='error')])
        self.assertEqual(m['fn'],1);self.assertEqual(m['errors'],1)
    def test_slow_and_missing_review_fail(self):
        ts=[dict(id='a',suite='messages',expected='urgent',predicted='urgent',elapsed=6,checks={'urgent_handoff':True}),dict(id='b',suite='messages',expected='urgent',predicted='urgent',elapsed=1,checks={'urgent_handoff':False})]
        s=summarize([{'turns':ts}],1,5)
        self.assertFalse(s['passed']);self.assertEqual(s['score'],0)
    def test_main_binary_includes_routine_and_followup(self):
        ts=[dict(id=str(i),suite='main_system',expected=e,predicted=p,elapsed=.01,checks={}) for i,(e,p) in enumerate([('routine','routine'),('followup','urgent'),('urgent','followup')])]
        s=summarize([{'turns':ts}],1,5)
        self.assertEqual(s['binary']['tn'],1);self.assertEqual(s['binary']['fp'],1);self.assertEqual(s['binary']['fn'],1)
    def test_workflow_abstention_not_false_alarm_but_classifier_not_exact(self):
        t=dict(id='a',suite='messages',expected='nonurgent',predicted='uncertain',elapsed=.01,checks={})
        self.assertEqual(summarize([{'turns':[t]}],1,5)['score'],1)
        t['suite']='classifier'
        self.assertEqual(summarize([{'turns':[t]}],1,5)['score'],0)

if __name__=='__main__':unittest.main()
