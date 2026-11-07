"""Lists account-visible models supporting generation; never displays the API key."""
import os
from pathlib import Path
import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / '.env', override=True)
key = os.getenv('CQA_LLM_API_KEY', '').strip()
if not key:
    raise SystemExit('Missing CQA_LLM_API_KEY in .env')
try:
    url = 'https://generativelanguage.googleapis.com/v1beta/models'
    with httpx.Client(timeout=20) as client:
        names=[]
        while url:
            r=client.get(url, headers={'x-goog-api-key':key})
            r.raise_for_status()
            data=r.json()
            for m in data.get('models', []):
                if 'generateContent' in m.get('supportedGenerationMethods', []):
                    names.append(m['name'].removeprefix('models/'))
            token=data.get('nextPageToken')
            url=('https://generativelanguage.googleapis.com/v1beta/models?pageToken='+token) if token else None
    print('Models with generateContent:')
    for name in sorted(set(names)):
        print(' ', name)
    print('NOTE: listing does not guarantee free-tier capacity or generation access.')
except httpx.HTTPError as exc:
    print('Listing failed:',type(exc).__name__,str(exc)[:250])
