import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import unittest
from checklist import DEFAULT_CHECKLIST
from scoring import rubric_score

class ScoringTests(unittest.TestCase):
    def test_all_perfect_is_100(self):
        answers=[{'criterion_id':c['id'],'answer':('no' if c['type']=='flag' else max(c['levels']) if c['type']=='scale' else 'yes'),'evidence_indices':[0]}
                 for c in DEFAULT_CHECKLIST['criteria']]
        s=rubric_score(DEFAULT_CHECKLIST,answers)
        self.assertEqual(s['total_score'],100)
        self.assertFalse(s['gross_error'])

    def test_na_renormalizes(self):
        answers=[{'criterion_id':c['id'],'answer':('no' if c['type']=='flag' else max(c['levels']) if c['type']=='scale' else 'yes'),'evidence_indices':[0]}
                 for c in DEFAULT_CHECKLIST['criteria']]
        answers[1]['answer']='na'; answers[2]['answer']='na'
        s=rubric_score(DEFAULT_CHECKLIST,answers)
        self.assertEqual(s['total_score'],100)
        self.assertEqual(s['applicable_weight'],65)
        self.assertIsNone(s['criteria'][1]['effective_points'])

    def test_allowed_steps_not_arbitrary(self):
        answers=[{'criterion_id':c['id'],'answer':('no' if c['type']=='flag' else max(c['levels']) if c['type']=='scale' else 'yes'),'evidence_indices':[0]}
                 for c in DEFAULT_CHECKLIST['criteria']]
        answers[5]['answer']=7
        s=rubric_score(DEFAULT_CHECKLIST,answers)
        self.assertAlmostEqual(s['total_score'],95.5)
        answers[5]['answer']=8
        with self.assertRaises(ValueError): rubric_score(DEFAULT_CHECKLIST,answers)

    def test_gross_error_does_not_zero_points(self):
        answers=[{'criterion_id':c['id'],'answer':('yes' if c['type']=='flag' else max(c['levels']) if c['type']=='scale' else 'yes'),'evidence_indices':[0]}
                 for c in DEFAULT_CHECKLIST['criteria']]
        s=rubric_score(DEFAULT_CHECKLIST,answers)
        self.assertTrue(s['gross_error']); self.assertEqual(s['total_score'],100)

    def test_invalid_na_for_no_na_criterion(self):
        answers=[{'criterion_id':c['id'],'answer':('no' if c['type']=='flag' else max(c['levels']) if c['type']=='scale' else 'yes'),'evidence_indices':[0]}
                 for c in DEFAULT_CHECKLIST['criteria']]
        answers[0]['answer']='na'
        with self.assertRaises(ValueError): rubric_score(DEFAULT_CHECKLIST,answers)

    def test_all_weighted_na_no_score(self):
        rubric={'criteria':[{'id':'1','type':'boolean','weight':100,'allow_na':True,'section':'A'},
                            {'id':'2','type':'flag','weight':0,'allow_na':False,'section':'B'}]}
        s=rubric_score(rubric,[{'criterion_id':'1','answer':'na','evidence_indices':[]},
                             {'criterion_id':'2','answer':'no','evidence_indices':[]}])
        self.assertIsNone(s['total_score'])

if __name__=='__main__': unittest.main()
