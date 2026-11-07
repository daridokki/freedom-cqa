"""End-to-end API workflows; all test data synthetic. No paid API requests."""
import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
import app as module
from checklist import DEFAULT_CHECKLIST
from llm import evaluate_with_llm, LLMError


class EndToEndTests(unittest.TestCase):
    def setUp(self):
        # Isolate offline tests from the real Gemini key that users may put in .env.
        self.env_patcher=patch.dict(os.environ, {'CQA_LLM_API_KEY':'', 'CQA_LLM_MODEL':''})
        self.env_patcher.start()
        self.temp=tempfile.TemporaryDirectory()
        self.old_db=module.DB
        module.DB=Path(self.temp.name)/'test.sqlite3'
        module.init_db()
        self.client=TestClient(module.app)
        self.turns=[
            {'timestamp':'00:00','speaker':'client','text':'Здравствуйте, какие есть тарифы?'},
            {'timestamp':'00:05','speaker':'operator','text':'Здравствуйте! Я Айгуль. Тарифы доступны на сайте.'},
            {'timestamp':'00:10','speaker':'client','text':'Спасибо!'},
            {'timestamp':'00:12','speaker':'operator','text':'Хорошего дня, до свидания!'},
        ]
        self.payload={'error':'','isSuccess':True,'data':{'dataset_group_id':'DEV','total_records':1,
            'records':[{'conversation_id':'safe-ru-001','channel':'agent chat','language':'RU',
                        'transcript':self.turns,'audio_url':None}]}}
        r=self.client.post('/api/import/json',json={'payload':self.payload})
        self.assertEqual(r.status_code,200,r.text)

    def tearDown(self):
        module.DB=self.old_db
        self.temp.cleanup()
        self.env_patcher.stop()

    def test_health_state_and_import_metadata(self):
        r=self.client.get('/api/health');self.assertEqual(r.json()['status'],'ok')
        r=self.client.get('/api/state')
        self.assertEqual(r.json()['conversations'][0]['dataset_group_id'],'DEV')
        self.assertEqual(len(r.json()['current_checklist']['criteria']),8)
        duplicate=self.client.post('/api/import/json',json={'payload':self.payload})
        self.assertEqual(duplicate.status_code,200)
        changed=copy.deepcopy(self.payload)
        changed['data']['records'][0]['transcript'][0]['text']='CHANGED'
        self.assertEqual(self.client.post('/api/import/json',json={'payload':changed}).status_code,409)

    def test_manual_evaluate_appeal_review_export(self):
        rubric=self.client.get('/api/state').json()['current_checklist']
        answers=[]
        for c in rubric['criteria']:
            if c['type']=='flag': ans='no'
            elif c['type']=='scale':ans=max(c['levels'])
            elif c['id']=='01':ans='no'
            elif c.get('allow_na'):ans='na'
            else:ans='yes'
            answers.append({'criterion_id':c['id'],'answer':ans,
                            'reason':'Основание: изучено сообщение клиента и ответ оператора.',
                            'evidence_indices':[0,1]})
        e=self.client.post('/api/evaluations/manual',json={'conversation_id':'safe-ru-001','answers':answers})
        self.assertEqual(e.status_code,200,e.text)
        eid=e.json()['evaluation_id']
        before=self.client.get(f'/api/evaluations/{eid}').json()
        self.assertAlmostEqual(before['original']['total_score'],83.33,places=2)
        self.assertEqual(before['mode'],'manual')
        a=self.client.post('/api/appeals',json={'evaluation_id':eid,'criterion_id':'01','comment':'Приветствие было.'})
        self.assertEqual(a.status_code,200,a.text)
        self.assertEqual(self.client.get('/api/review-queue').json()[0]['criterion_id'],'01')
        self.assertEqual(self.client.post('/api/appeals',json={'evaluation_id':eid,'criterion_id':'01','comment':'Duplicate'}).status_code,409)
        aid=a.json()['appeal_id']
        bad=self.client.post(f'/api/appeals/{aid}/decision',json={'decision':'change','answer':'na','reviewer_note':'Wrong'})
        self.assertEqual(bad.status_code,422)
        ok=self.client.post(f'/api/appeals/{aid}/decision',json={'decision':'change','answer':'yes',
            'reviewer_note':'Оператор действительно представился и поздоровался.', 'evidence_indices':[1]})
        self.assertEqual(ok.status_code,200,ok.text)
        after=self.client.get(f'/api/evaluations/{eid}').json()
        self.assertEqual(after['original']['total_score'],before['original']['total_score'])
        self.assertEqual(after['final']['total_score'],100)
        self.assertEqual(after['final']['criteria'][0]['evidence_indices'],[1])
        self.assertEqual(self.client.post(f'/api/appeals/{aid}/decision',json={'decision':'confirm','reviewer_note':'No'}).status_code,409)
        self.assertEqual(self.client.get('/api/analytics').json()['average_score'],100)
        report=self.client.get('/api/export/results.csv')
        self.assertEqual(report.status_code,200)
        self.assertIn('safe-ru-001',report.text)
        self.assertIn('original',self.client.get(f'/api/evaluations/{eid}/export.json').json())

    def test_new_checklist_version_and_manual_new_criterion(self):
        original=self.client.get('/api/state').json()['current_checklist']
        config={'name':'Test updated requirements','criteria':copy.deepcopy(original['criteria'])}
        config['criteria'].append({'id':'09','name':'New criterion','section':'Additional','type':'boolean',
            'weight':5,'allow_na':True,'levels':[],'rule':'If client requests summary, operator provides it. N/A if no summary requested.'})
        saved=self.client.post('/api/checklists',json=config)
        self.assertEqual(saved.status_code,200,saved.text)
        self.assertEqual(saved.json()['version'],2)
        self.assertEqual(len(self.client.get('/api/state').json()['current_checklist']['criteria']),9)
        config['criteria'][0]['weight']=-1
        self.assertEqual(self.client.post('/api/checklists',json=config).status_code,422)

    def test_batch_queue_and_cancel_validation(self):
        import time
        from scoring import rubric_score
        rubric=self.client.get('/api/state').json()['current_checklist']
        answers=[]
        for c in rubric['criteria']:
            ans='no' if c['type']=='flag' else max(c['levels']) if c['type']=='scale' else 'yes'
            answers.append({'criterion_id':c['id'],'answer':ans,'reason':'Operator fulfilled this criterion',
                            'evidence_indices':[0,1]})
        score=rubric_score(rubric,answers)
        with patch.dict('os.environ',{'CQA_LLM_API_KEY':'mock-key','CQA_LLM_MODEL':'mock-model'}):
            with patch.object(module,'evaluate_with_llm',return_value=score):
                created=self.client.post('/api/jobs/batch',json={})
                self.assertEqual(created.status_code,200,created.text)
                jid=created.json()['job_id']
                self.assertIsNotNone(jid)
                for _ in range(200):
                    job=self.client.get('/api/jobs/'+jid).json()
                    if job['status'] in ('completed','failed','cancelled'): break
                    time.sleep(0.01)
                self.assertEqual(job['status'],'completed',job)
                self.assertEqual(job['succeeded'],1)
                self.assertEqual(job['failed'],0)
                self.assertEqual(self.client.post('/api/jobs/batch',json={}).json()['total'],0)
                self.assertEqual(self.client.post('/api/jobs/'+jid+'/cancel').status_code,409)

    def test_empty_batch_and_bad_import(self):
        self.assertEqual(self.client.post('/api/jobs/batch',json={}).status_code,503)
        bad=copy.deepcopy(self.payload)
        bad['data']['records'][0]['transcript'][1]['speaker']='unknown-role'
        self.assertEqual(self.client.post('/api/import/json',json={'payload':bad}).status_code,422)
        self.assertEqual(self.client.post('/api/import/url',json={'url':'https://127.0.0.1/admin'}).status_code,422)
        failed=copy.deepcopy(self.payload);failed['isSuccess']=False
        self.assertEqual(self.client.post('/api/import/json',json={'payload':failed}).status_code,502)


class MockLLMTests(unittest.TestCase):
    def test_model_schema_and_scoring(self):
        turns=[{'timestamp':'00:01','speaker':'client','text':'Расскажите про комиссии'},
               {'timestamp':'00:10','speaker':'operator','text':'Здравствуйте, я Анна. Смотрите тарифы в приложении.'}]
        dialog={'language':'RU','channel':'agent chat','transcript':turns}
        result=[]
        for c in DEFAULT_CHECKLIST['criteria']:
            if c['type']=='flag':ans='no'
            elif c['type']=='scale':ans=max(c['levels'])
            elif c['id'] in ('02','03','07'):ans='na'
            else:ans='yes'
            result.append({'criterion_id':c['id'],'answer':ans,
                           'reason':'Основание — реплика оператора и запрос клиента.',
                           'evidence_indices':[0,1]})
        resp={'model':'mock-model','choices':[{'message':{'content':json.dumps({'results':result},ensure_ascii=False)}}],
              'usage':{'prompt_tokens':1200,'completion_tokens':400}}
        response=MagicMock();response.status_code=200;response.json.return_value=resp
        response.raise_for_status.return_value=None
        client=MagicMock();client.post.return_value=response
        cm=MagicMock();cm.__enter__.return_value=client
        with patch.dict('os.environ',{'CQA_LLM_API_KEY':'test-key','CQA_LLM_MODEL':'mock-model', 'CQA_LLM_PROVIDER':'openai_compatible',
                                    'CQA_INPUT_USD_PER_1M':'1','CQA_OUTPUT_USD_PER_1M':'2'}):
            with patch('llm.httpx.Client',return_value=cm):
                scored=evaluate_with_llm(dialog,DEFAULT_CHECKLIST)
        self.assertEqual(scored['total_score'],100)
        self.assertEqual(scored['model_info']['approx_cost_usd'],0.002)
        self.assertEqual(scored['model_info']['attempts'],1)
        self.assertEqual(client.post.call_count,1)

    def test_rejects_uncited_model_answers(self):
        from llm import _validate_evidence
        turns=[{'timestamp':'00:01','speaker':'client','text':'Hi'}]
        result=[{'criterion_id':c['id'],'answer':'no','reason':'Explanation with enough words',
                 'evidence_indices':[]} for c in DEFAULT_CHECKLIST['criteria']]
        with self.assertRaises(ValueError): _validate_evidence(result,turns,DEFAULT_CHECKLIST)

if __name__=='__main__':unittest.main()
