#!/bin/sh
set -eu
cd "$(dirname "$0")"
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
[ -f .env ] || cp .env.example .env
printf '\nOpen http://127.0.0.1:8000 in your browser.\n'
exec uvicorn app:app --host 127.0.0.1 --port 8000
