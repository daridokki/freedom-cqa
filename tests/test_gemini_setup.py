"""Offline native Gemini API integration tests (no secret keys, no external requests)."""
import json
import os
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch, MagicMock
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from checklist import DEFAULT_CHECKLIST
from llm import evaluate_with_llm, LLMError, _gemini_error_message, _parse_native_response
import check_gemini

NATIVE='https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent'


def sample():
    turns=[{'timestamp':'00:00','speaker':'client','text':'Где тариф?'},
           {'timestamp':'00:15','speaker':'operator','text':'Здравствуйте, я Лена. В меню Кабинет.'}]
    dialog={'language':'RU','channel':'agent chat','transcript':turns}
    answers=[]
    for c in DEFAULT_CHECKLIST['criteria']:
        ans='no' if c['type']=='flag' else max(c['levels']) if c['type']=='scale' else 'na' if c['id']=='03' else 'yes'
        answers.append({'criterion_id':c['id'], 'answer':ans,
                        'reason':'Оператор даёт ответ с понятной ссылкой на меню.',
                        'evidence_indices':[0,1]})
    return dialog, answers


def response(data, status_code=200):
    r=MagicMock();r.status_code=status_code;r.json.return_value=data;r.headers={}
    return r


def client_mock(*responses):
    c=MagicMock();c.post.side_effect=list(responses)
    cm=MagicMock();cm.__enter__.return_value=c
    return c,cm

class GeminiConfigTests(TestCase):
    def test_example_has_native_defaults_no_secrets(self):
        contents=(Path(__file__).resolve().parents[1]/'.env.example').read_text(encoding='utf-8')
        self.assertIn('CQA_LLM_PROVIDER=gemini_native',contents)
        self.assertIn('CQA_LLM_MODEL=gemini-3.8-flash',contents)
        self.assertRegex(contents,r'(?m)^CQA_LLM_API_KEY=\s*$')

    def test_native_evaluation_and_scoring(self):
        dialog, answers=sample()
        raw={'candidates':[{'content':{'parts':[{'text':json.dumps({'results':answers},ensure_ascii=False)}]}}],
             'usageMetadata':{'promptTokenCount':220,'candidatesTokenCount':180}}
        client,cm=client_mock(response(raw))
        env={'CQA_LLM_PROVIDER':'gemini_native','CQA_LLM_API_KEY':'fake-key',
             'CQA_LLM_MODEL':'gemini-3.8-flash','CQA_LLM_JSON_MODE':'1',
             'CQA_LLM_HTTP_RETRIES':'0'}
        with patch.dict(os.environ,env):
            with patch('llm.httpx.Client',return_value=cm):
                grade=evaluate_with_llm(dialog,DEFAULT_CHECKLIST)
        self.assertEqual(grade['total_score'],100)
        self.assertEqual(grade['model_info']['provider'],'gemini_native')
        self.assertEqual(grade['model_info']['prompt_tokens'],220)
        self.assertEqual(grade['model_info']['completion_tokens'],180)
        args, kwargs=client.post.call_args
        self.assertEqual(args[0],NATIVE)
        self.assertEqual(kwargs['headers']['x-goog-api-key'],'fake-key')
        self.assertIn('systemInstruction',kwargs['json'])
        self.assertEqual(kwargs['json']['generationConfig']['responseMimeType'],'application/json')
        self.assertNotIn('response_format',kwargs['json'])

    def test_native_json_mode_can_be_disabled(self):
        dialog, answers=sample()
        native={'candidates':[{'content':{'parts':[{'text':json.dumps({'results':answers},ensure_ascii=False)}]}}]}
        client,cm=client_mock(response(native))
        with patch.dict(os.environ,{'CQA_LLM_PROVIDER':'gemini_native','CQA_LLM_API_KEY':'secret',
                                     'CQA_LLM_MODEL':'gemini-3.8-flash','CQA_LLM_JSON_MODE':'0',
                                     'CQA_LLM_HTTP_RETRIES':'0'}):
            with patch('llm.httpx.Client',return_value=cm):
                evaluate_with_llm(dialog,DEFAULT_CHECKLIST)
        self.assertNotIn('generationConfig',client.post.call_args.kwargs['json'])

    def test_native_google_error_message_redacts_key(self):
        r=response([{'error':{'status':'INVALID_ARGUMENT','message':'Key secret-value invalid'}}],400)
        self.assertNotIn('secret-value',_gemini_error_message(r,'secret-value'))
        self.assertIn('INVALID_ARGUMENT',_gemini_error_message(r,'secret-value'))

    def test_native_response_no_candidates(self):
        with self.assertRaisesRegex(ValueError,'did not produce candidates'):
            _parse_native_response({'promptFeedback':{'blockReason':'SAFETY'}})

    def test_native_404_has_informative_exception(self):
        dialog, _ = sample()
        r=response({'error':{'status':'NOT_FOUND','message':'Model unavailable'}},404)
        c,cm=client_mock(r)
        with patch.dict(os.environ,{'CQA_LLM_PROVIDER':'gemini_native','CQA_LLM_API_KEY':'test',
                                     'CQA_LLM_MODEL':'gemini-3.8-flash','CQA_LLM_HTTP_RETRIES':'0'}):
            with patch('llm.httpx.Client',return_value=cm):
                with self.assertRaisesRegex(LLMError,'Model unavailable'):
                    evaluate_with_llm(dialog,DEFAULT_CHECKLIST)

    def test_diagnostic_get_post_and_generation_mock(self):
        fake_client=MagicMock()
        fake_client.get.return_value=response({'name':'models/gemini-3.8-flash'})
        fake_client.post.side_effect=[
            response({'error':{'code':400,'message':'Invalid contents'}},400),
            response({'candidates':[{'content':{'parts':[{'text':'OK'}]}}]}),
        ]
        cm=MagicMock();cm.__enter__.return_value=fake_client
        with patch.dict(os.environ,{'CQA_LLM_API_KEY':'test','CQA_LLM_MODEL':'gemini-3.8-flash',
                                     'CQA_LLM_PROVIDER':'gemini_native'}):
            with patch('check_gemini.httpx.Client',return_value=cm):
                self.assertEqual(check_gemini.main(),0)
        self.assertEqual(fake_client.post.call_count,2)
        self.assertEqual(fake_client.post.call_args.args[0],NATIVE)
