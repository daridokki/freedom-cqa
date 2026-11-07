"""Gemini API diagnostics. Reads .env locally, never prints the API key.

Three independent checks distinguish authentication, authenticated POST transport,
and actual model generation. Do not pass customer conversations into this script.
"""
import os
import sys
import time
import httpx
from pathlib import Path
from dotenv import load_dotenv
from llm import _gemini_error_message, _parse_native_response

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / '.env', override=True)
HOST = 'https://generativelanguage.googleapis.com/v1beta'


def _send(client, method, url, key, payload=None):
    headers = {'x-goog-api-key': key, 'Content-Type': 'application/json'}
    try:
        start = time.monotonic()
        if method == 'GET':
            r = client.get(url, headers=headers)
        else:
            r = client.post(url, headers=headers, json=payload)
        return r, round(time.monotonic() - start, 1), None
    except httpx.HTTPError as exc:
        return None, None, type(exc).__name__ + ': ' + str(exc)[:150]


def main():
    key = os.getenv('CQA_LLM_API_KEY', '').strip()
    model = os.getenv('CQA_LLM_MODEL', 'gemini-3.8-flash').strip()
    if not key:
        print('CONFIG ERROR: CQA_LLM_API_KEY is missing in .env', flush=True)
        return 2
    print('Model:', model, flush=True)
    print('Provider for application:', os.getenv('CQA_LLM_PROVIDER', 'openai_compatible'), flush=True)
    timeout = max(10., float(os.getenv('CQA_LLM_TIMEOUT', '90')))
    config = httpx.Timeout(connect=12, write=12, pool=12, read=timeout)
    url = HOST + '/models/' + model + ':generateContent'
    with httpx.Client(timeout=config, trust_env=True, follow_redirects=False) as client:
        print('STEP 1/3 — authorized GET model metadata...', flush=True)
        response, elapsed, error = _send(client, 'GET', HOST + '/models/' + model, key)
        if error:
            print('NETWORK ERROR:', error, flush=True)
            return 1
        print('GET HTTP:', response.status_code, '| seconds:', elapsed, flush=True)
        if response.status_code != 200:
            print('Gemini error:', _gemini_error_message(response, key), flush=True)
            return 1

        print('STEP 2/3 — authorized POST with deliberately invalid payload...', flush=True)
        # This MUST return a quick HTTP error, normally 400. We test reachability,
        # not model inference, so there is no token usage or billable generation.
        response, elapsed, error = _send(client, 'POST', url, key, {})
        if error:
            print('NETWORK ERROR on authenticated POST:', error, flush=True)
            print('Inference has not been attempted. Check VPN/proxy/HTTPS inspection.', flush=True)
            return 1
        print('POST probe HTTP:', response.status_code, '| seconds:', elapsed, flush=True)
        if response.status_code not in (400, 422):
            if response.status_code >= 400:
                print('Gemini error:', _gemini_error_message(response, key), flush=True)
                return 1
            print('Warning: unexpected success for an empty payload.', flush=True)

        print('STEP 3/3 — minimal text generation via native Gemini API...', flush=True)
        payload = {'contents':[{'role':'user','parts':[{'text':'Reply with OK only.'}]}],
                   'generationConfig':{'maxOutputTokens':512}}
        fallback = [name.strip().removeprefix('models/') for name in
                    os.getenv('CQA_LLM_FALLBACK_MODELS', '').split(',') if name.strip()]
        choices = list(dict.fromkeys([model.removeprefix('models/')] + fallback))
        for i, candidate in enumerate(choices):
            if i:
                print('Trying backup model:', candidate, flush=True)
            active_url = HOST + '/models/' + candidate + ':generateContent'
            response, elapsed, error = _send(client, 'POST', active_url, key, payload)
            if error:
                print('NETWORK ERROR during generation:', error, flush=True)
                if i < len(choices)-1:
                    continue
                return 1
            print('Generation HTTP:', response.status_code, '| seconds:', elapsed,
                  '| model:', candidate, flush=True)
            if response.status_code != 200:
                print('Gemini error:', _gemini_error_message(response, key), flush=True)
                if response.status_code in (500, 502, 503, 504) and i < len(choices)-1:
                    continue
                return 1
            try:
                content, in_tokens, out_tokens = _parse_native_response(response.json())
                print('Model reply:', repr(content[:180]), flush=True)
                print('Tokens:', in_tokens, 'input /', out_tokens, 'output', flush=True)
            except (ValueError, KeyError, TypeError) as exc:
                print('Bad successful response:', type(exc).__name__, str(exc), flush=True)
                return 1
            print('SUCCESS: native Gemini generation works. Model:',candidate,flush=True)
            break
        else:
            return 1

    return 0

if __name__ == '__main__':
    sys.exit(main())
