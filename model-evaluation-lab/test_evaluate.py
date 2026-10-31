import copy
import tempfile
import unittest
from pathlib import Path
from evaluate import score, read_jsonl, predict, valid_output

class ScorerTests(unittest.TestCase):
    def setUp(self):
        self.cases = [dict(id='a', slice='x', expected=dict(category='billing', action='route')),
                      dict(id='b', slice='y', expected=dict(category='access', action='human_review'))]
        self.pred = [dict(id=c['id'], output=c['expected'].copy()) for c in self.cases]

    def test_gold_and_order(self):
        metrics, _ = score(self.cases, self.pred[::-1])
        self.assertEqual(metrics['counts']['success'], 2)

    def test_hand_calculated_safety(self):
        self.pred[1]['output']['action'] = 'route'
        m, _ = score(self.cases, self.pred)
        self.assertEqual(m['rates']['success'], .5)
        self.assertEqual(m['rates']['category_correct'], 1)
        self.assertEqual(m['errors']['unsafe_auto_route'], 1)

    def test_invalid_is_failure_not_omitted(self):
        for bad in [None, {}, 'billing', {'category': [], 'action': 'route'},
                    {'category': 'made_up', 'action': 'route'},
                    {'category': 'billing', 'action': 'route', 'extra': 1}]:
            with self.subTest(bad=bad):
                predictions = copy.deepcopy(self.pred)
                predictions[0]['output'] = bad
                m, _ = score(self.cases, predictions)
                self.assertEqual(m['n'], 2)
                self.assertEqual(m['errors']['schema_invalid'], 1)
                self.assertEqual(m['counts']['success'], 1)

    def test_missing_extra_duplicate_rejected(self):
        for bad in [self.pred[:1], self.pred + [self.pred[0]],
                    self.pred + [dict(id='extra', output={})]]:
            with self.assertRaises(ValueError): score(self.cases, bad)

    def test_duplicate_input_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'bad.jsonl'
            p.write_text('{"id":"a"}\n{"id":"a"}\n')
            with self.assertRaises(ValueError): read_jsonl(p)

    def test_gate_and_known_regression_risk(self):
        self.assertEqual(predict('退款同时登录失败', 'v2')['action'], 'clarify')
        self.assertEqual(predict('账单没问题，登录失败', 'v2')['action'], 'clarify')
        self.assertEqual(predict('退款', 'v1'), predict('退款', 'v2'))

if __name__ == '__main__': unittest.main()
