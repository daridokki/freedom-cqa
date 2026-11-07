"""Freedom Broker Conversation Quality Analytics — reproducible 24h hackathon app."""
import copy
from contextlib import contextmanager
import csv
import io
import math
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import sqlite3
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from checklist import DEFAULT_CHECKLIST
from scoring import rubric_score
from llm import LLMError, evaluate_with_llm

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent/'.env')
except ImportError:
    pass

ROOT = Path(__file__).resolve().parent
DB = Path(os.environ.get('CQA_DB_PATH',str(ROOT/'cqa.sqlite3')))
if not DB.is_absolute(): DB = ROOT / DB
app = FastAPI(title='Freedom Broker CQA — Conversation Quality Analytics',version='1.0.0')
app.mount('/static', StaticFiles(directory=ROOT/'static'), name='static')


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def db_conn():
    DB.parent.mkdir(parents=True,exist_ok=True)
    conn=sqlite3.connect(DB,timeout=10.0)
    try:
        conn.row_factory=sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('PRAGMA busy_timeout=10000')
        conn.execute('PRAGMA journal_mode=WAL')
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def row_json(r):
    return dict(r) if r is not None else None


def init_db():
    with db_conn() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY, channel TEXT NOT NULL, language TEXT NOT NULL,
            transcript_json TEXT NOT NULL, audio_url TEXT, created_at TEXT NOT NULL,
            dataset_group_id TEXT NOT NULL DEFAULT 'manual');
        CREATE TABLE IF NOT EXISTS checklists (
            id TEXT PRIMARY KEY, version INTEGER UNIQUE NOT NULL,
            data_json TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS evaluations (
            id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
            checklist_id TEXT NOT NULL, mode TEXT NOT NULL,
            original_json TEXT NOT NULL, created_at TEXT NOT NULL,
            FOREIGN KEY(conversation_id) REFERENCES conversations(id),
            FOREIGN KEY(checklist_id) REFERENCES checklists(id));
        CREATE TABLE IF NOT EXISTS appeals (
            id TEXT PRIMARY KEY, evaluation_id TEXT NOT NULL, criterion_id TEXT NOT NULL,
            comment TEXT NOT NULL, status TEXT NOT NULL, decision TEXT,
            decided_answer TEXT, reviewer_note TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            evidence_json TEXT,
            FOREIGN KEY(evaluation_id) REFERENCES evaluations(id));
        CREATE TABLE IF NOT EXISTS batch_jobs (
            id TEXT PRIMARY KEY, status TEXT NOT NULL, checklist_id TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            total INTEGER NOT NULL, completed INTEGER NOT NULL DEFAULT 0,
            succeeded INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0,
            ids_json TEXT NOT NULL, errors_json TEXT NOT NULL DEFAULT '[]',
            FOREIGN KEY(checklist_id) REFERENCES checklists(id));
        ''')
        # Migration for any previously opened MVP database.
        for table, col, ddl in [
            ('conversations','dataset_group_id',"TEXT NOT NULL DEFAULT 'manual'"),
            ('appeals','evidence_json','TEXT')]:
            if col not in {r[1] for r in db.execute(f'PRAGMA table_info({table})')}:
                db.execute(f'ALTER TABLE {table} ADD COLUMN {col} {ddl}')
        # Running threads do not survive server restart. Make that visible instead of
        # claiming the previous batch finished. The next launch may start a new job.
        db.execute("UPDATE batch_jobs SET status='interrupted', updated_at=? WHERE status IN ('running','pending','cancelling')", (now(),))
        if not db.execute('SELECT id FROM checklists LIMIT 1').fetchone():
            db.execute('INSERT INTO checklists VALUES (?,?,?,?)',(
                'checklist-v1',1,json.dumps(DEFAULT_CHECKLIST,ensure_ascii=False),now()))

init_db()


def get_checklist(db, cid: str|None = None):
    if cid:
        r=db.execute('SELECT * FROM checklists WHERE id=?',(cid,)).fetchone()
    else:
        r=db.execute('SELECT * FROM checklists ORDER BY version DESC LIMIT 1').fetchone()
    if not r: raise HTTPException(404,'Checklist not found')
    return {'id':r['id'],'version':r['version'],'created_at':r['created_at'],**json.loads(r['data_json'])}


def get_conversation(db, cid):
    r=db.execute('SELECT * FROM conversations WHERE id=?',(cid,)).fetchone()
    if not r: raise HTTPException(404,'Conversation not found')
    return {'conversation_id':r['id'],'channel':r['channel'],'language':r['language'],
            'transcript':json.loads(r['transcript_json']),'audio_url':r['audio_url'],
            'dataset_group_id':r['dataset_group_id']}


def validate_checklist(c: dict):
    if not isinstance(c.get('criteria'),list) or not c['criteria']:
        raise HTTPException(422,'Checklist must contain criteria')
    if len(c['criteria'])>30:
        raise HTTPException(422,'Too many criteria (>30)')
    if not isinstance(c.get('name'),str) or not c['name'].strip():
        raise HTTPException(422,'Checklist name required')
    seen=set(); total=0
    for x in c['criteria']:
        cid=str(x.get('id','')).strip()
        if not cid or cid in seen: raise HTTPException(422,'Missing/duplicate criterion id')
        seen.add(cid)
        if x.get('type') not in ['boolean','scale','flag']:
            raise HTTPException(422,f'{cid}: invalid type')
        try: w=float(x.get('weight',0))
        except Exception: raise HTTPException(422,f'{cid}: invalid weight')
        if not math.isfinite(w) or not 0<=w<=100: raise HTTPException(422,f'{cid}: weight outside 0-100')
        if x['type']=='flag' and w!=0: raise HTTPException(422,f'{cid}: flag must have weight 0')
        if x['type']=='flag' and x.get('allow_na'): raise HTTPException(422,f'{cid}: flag cannot allow N/A')
        if x['type']=='scale':
            vals=x.get('levels',[])
            if not isinstance(vals,list) or len(vals)<2 or any(type(v) not in [int,float] or not math.isfinite(v) or v<0 for v in vals) or vals!=sorted(set(vals)) or vals[0]!=0 or vals[-1]<=0:
                raise HTTPException(422,f'{cid}: scale must be sorted unique nonnegative steps starting with 0')
        if x.get('type') == 'flag' and x.get('levels'):
            raise HTTPException(422,f'{cid}: flag cannot have levels')
        if not x.get('name') or not x.get('rule'):
            raise HTTPException(422,f'{cid}: name/rule required')
        total+=w
    if total<=0:
        raise HTTPException(422,'At least one positive-weight criterion is required')


class ImportPayload(BaseModel):
    payload: dict


class UrlPayload(BaseModel):
    url: str


class ChecklistPayload(BaseModel):
    name: str
    criteria: list[dict]


class EvaluatePayload(BaseModel):
    conversation_id: str
    checklist_id: str|None = None


class AppealPayload(BaseModel):
    evaluation_id: str
    criterion_id: str
    comment: str = Field(min_length=2)


class DecisionPayload(BaseModel):
    decision: str  # confirm or change
    reviewer_note: str = Field(min_length=2)
    answer: str|int|float|None = None
    evidence_indices: list[int]|None = None

class ManualPayload(BaseModel):
    conversation_id: str
    checklist_id: str|None = None
    answers: list[dict]

class BatchPayload(BaseModel):
    checklist_id: str|None = None
    force: bool = False
    limit: int = Field(default=300, ge=1, le=3000)


@app.get('/')
def home():
    return FileResponse(ROOT/'static'/'index.html')


@app.get('/api/state')
def state():
    with db_conn() as db:
        dialogs=[dict(r) for r in db.execute('''
            SELECT c.id AS conversation_id,c.channel,c.language,c.created_at,c.dataset_group_id,
              (SELECT count(*) FROM evaluations e WHERE e.conversation_id=c.id) AS evaluations
            FROM conversations c ORDER BY c.id LIMIT 300''')]
        evals=[dict(r) for r in db.execute('''
            SELECT e.id,e.conversation_id,e.mode,e.created_at,cl.version AS checklist_version,
              (SELECT count(*) FROM appeals a WHERE a.evaluation_id=e.id AND a.status='pending') AS pending_appeals
            FROM evaluations e JOIN checklists cl ON e.checklist_id=cl.id
            ORDER BY e.created_at DESC LIMIT 500''')]
        checks=[{'id':r['id'],'version':r['version'],'created_at':r['created_at'],'name':json.loads(r['data_json'])['name']}
                for r in db.execute('SELECT * FROM checklists ORDER BY version DESC')]
        jobs=[dict(r) for r in db.execute("SELECT id,status,checklist_id,total,completed,succeeded,failed,created_at,updated_at FROM batch_jobs ORDER BY created_at DESC LIMIT 10")]
        return {'conversations':dialogs,'evaluations':evals,'checklists':checks,'jobs':jobs,
                'current_checklist':get_checklist(db),
                'llm_configured':bool(os.getenv('CQA_LLM_API_KEY') and os.getenv('CQA_LLM_MODEL')),
                'model':os.getenv('CQA_LLM_MODEL','') if os.getenv('CQA_LLM_API_KEY') else None}


@app.get('/api/checklists/{cid}')
def checklist(cid: str):
    with db_conn() as db:
        return get_checklist(db,cid)


@app.post('/api/checklists')
def save_checklist(p: ChecklistPayload):
    c=p.model_dump()
    validate_checklist(c)
    with db_conn() as db:
        v=db.execute('SELECT MAX(version) FROM checklists').fetchone()[0]+1
        cid=f'checklist-v{v}'
        db.execute('INSERT INTO checklists VALUES (?,?,?,?)',(cid,v,json.dumps(c,ensure_ascii=False),now()))
    return {'id':cid,'version':v,'message':'New checklist version saved; earlier evaluations unchanged'}


def ingest_data(payload: dict):
    # Organizer response: {isSuccess:true,data:{records:[...]}}
    data = payload.get('data',payload)
    if not isinstance(data,dict):
        raise HTTPException(422,'Source data must be a JSON object')
    records = data.get('records')
    if payload.get('isSuccess') is False:
        raise HTTPException(502,'The source API returned isSuccess=false')
    if not isinstance(records,list) or not records:
        raise HTTPException(422,'Expected data.records as a nonempty list')
    if len(records)>3000:
        raise HTTPException(422,'Too many records in one request')
    checked=[]
    group=str(data.get('dataset_group_id','manual'))[:80]
    seen_ids=set()
    for i,r in enumerate(records):
        if not isinstance(r,dict): raise HTTPException(422,f'Invalid record #{i}')
        cid=str(r.get('conversation_id','')).strip()
        transcript=r.get('transcript')
        lang=r.get('language','RU')
        if not cid or len(cid)>180 or cid in seen_ids or lang not in ('RU','KZ','EN') or not isinstance(transcript,list) or not transcript or len(transcript)>5000:

            raise HTTPException(422,f'Invalid conversation #{i}: id, language or transcript')
        seen_ids.add(cid)
        if r.get('channel','agent chat') not in ('agent chat','voice call'):
            raise HTTPException(422,f'{cid}: unsupported channel')
        for j,t in enumerate(transcript):
            if not isinstance(t,dict) or t.get('speaker') not in ('client','operator','bot','system') or not isinstance(t.get('text'),str) or len(t.get('text',''))>60000 or not isinstance(t.get('timestamp'),str):
                raise HTTPException(422,f'Invalid turn #{j} in conversation #{cid}')
        audio=r.get('audio_url')
        if audio is not None and (not isinstance(audio,str) or not audio.startswith(('https://','http://'))):
            raise HTTPException(422,f'{cid}: audio_url must be HTTP(S) or null')
        checked.append((cid,str(r.get('channel','agent chat')),lang,json.dumps(transcript,ensure_ascii=False),audio,now(),group))
    with db_conn() as db:
        # Never change a previously scored transcript in place: preserve evidence auditability.
        conflicting=[]
        for r in checked:
            existing=db.execute('SELECT transcript_json FROM conversations WHERE id=?',(r[0],)).fetchone()
            if existing and json.loads(existing['transcript_json']) != json.loads(r[3]):
                conflicting.append(r[0])
        if conflicting:
            raise HTTPException(409, 'Existing conversation IDs contain different transcripts: '+', '.join(conflicting[:5]))
        for r in checked:
            db.execute('INSERT OR IGNORE INTO conversations (id,channel,language,transcript_json,audio_url,created_at,dataset_group_id) VALUES (?,?,?,?,?,?,?)',r)
    return {'received':len(checked),'imported':len(checked),'dataset_group_id':group}


@app.post('/api/import/json')
def import_json(p: ImportPayload):
    return ingest_data(p.payload)


@app.post('/api/import/url')
def import_from_url(p: UrlPayload):
    target=urlparse(p.url)
    host=(target.hostname or '').lower()
    if target.scheme!='https' or target.username or target.password or target.port not in (None,443) or not host.endswith('.environment.api.powerplatform.com'):
        raise HTTPException(422,'Only organizers Power Platform HTTPS API links are allowed')
    try:
        with httpx.Client(timeout=45.0, follow_redirects=False) as client:
            resp=client.get(p.url)
            resp.raise_for_status()
            if len(resp.content)>8_000_000:
                raise HTTPException(413,'Data exceeds 8 MB')
            payload=resp.json()
    except httpx.HTTPError as e:
        raise HTTPException(502,f'Organizer API request failed: {type(e).__name__}') from e
    except ValueError as e:
        raise HTTPException(502,'Organizer API did not return JSON') from e
    if payload.get('isSuccess') is False:
        raise HTTPException(502,'Organizer API returned isSuccess=false')
    return ingest_data(payload)


@app.get('/api/conversations/{cid}')
def conversation(cid: str):
    with db_conn() as db:
        return get_conversation(db,cid)


@app.post('/api/evaluations')
def evaluate(p: EvaluatePayload):
    with db_conn() as db:
        c=get_conversation(db,p.conversation_id)
        rubric=get_checklist(db,p.checklist_id)
    try:
        score=evaluate_with_llm(c,rubric)
    except LLMError as e:
        raise HTTPException(503,str(e)) from e
    eid=store_evaluation(c['conversation_id'],rubric['id'],'llm',score)
    return {'evaluation_id':eid,'score':score['total_score'],'gross_error':score['gross_error']}


def get_evaluation_detail(db, eid):
    ev=db.execute('SELECT * FROM evaluations WHERE id=?',(eid,)).fetchone()
    if not ev: raise HTTPException(404,'Evaluation not found')
    checklist=get_checklist(db,ev['checklist_id'])
    conversation=get_conversation(db,ev['conversation_id'])
    original=json.loads(ev['original_json'])
    appeals=[dict(r) for r in db.execute('SELECT * FROM appeals WHERE evaluation_id=? ORDER BY created_at,id',(eid,))]
    entries={r['criterion_id']:{'criterion_id':r['criterion_id'],'answer':r['answer'],
                              'reason':r['reason'],'evidence_indices':r['evidence_indices']}
             for r in original['criteria']}
    for appeal in appeals:
        if appeal['status']=='resolved' and appeal['decision']=='change':
            key=appeal['criterion_id']
            entries[key]['answer']=json.loads(appeal['decided_answer'])
            entries[key]['reason']=f"Пересмотр: {appeal['reviewer_note']}"
            if appeal.get('evidence_json'):
                entries[key]['evidence_indices']=json.loads(appeal['evidence_json'])
    final=rubric_score(checklist,list(entries.values()))
    return {'id':ev['id'],'mode':ev['mode'],'created_at':ev['created_at'],
            'conversation':conversation,'checklist':checklist,'original':original,
            'final':final,'appeals':appeals}


@app.get('/api/evaluations/{eid}')
def evaluation(eid: str):
    with db_conn() as db:
        return get_evaluation_detail(db,eid)


@app.post('/api/appeals')
def appeal(p: AppealPayload):
    with db_conn() as db:
        ev=get_evaluation_detail(db,p.evaluation_id)
        if p.criterion_id not in {str(c['id']) for c in ev['checklist']['criteria']}:
            raise HTTPException(422,'Unknown criterion id')
        if db.execute("SELECT 1 FROM appeals WHERE evaluation_id=? AND criterion_id=? AND status='pending'",(p.evaluation_id,p.criterion_id)).fetchone():
            raise HTTPException(409,'There is already an open appeal for this criterion')
        aid=str(uuid.uuid4()); t=now()
        db.execute('INSERT INTO appeals (id,evaluation_id,criterion_id,comment,status,decision,decided_answer,reviewer_note,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)',(
            aid,p.evaluation_id,p.criterion_id,p.comment,'pending',None,None,None,t,t))
    return {'appeal_id':aid,'status':'pending'}


@app.post('/api/appeals/{aid}/decision')
def decide(aid: str, p: DecisionPayload):
    if p.decision not in ('confirm','change'):
        raise HTTPException(422,'decision must be confirm or change')
    with db_conn() as db:
        a=db.execute('SELECT * FROM appeals WHERE id=?',(aid,)).fetchone()
        if not a: raise HTTPException(404,'Appeal not found')
        if a['status']=='resolved': raise HTTPException(409,'Appeal already decided')
        ev=get_evaluation_detail(db,a['evaluation_id'])
        answers={r['criterion_id']:{'criterion_id':r['criterion_id'],'answer':r['answer'],
                                       'reason':r['reason'],'evidence_indices':r['evidence_indices']}
                  for r in ev['final']['criteria']}
        if p.decision=='change':
            if p.answer is None: raise HTTPException(422,'New answer required when changing')
            answers[a['criterion_id']]['answer']=p.answer
            if p.evidence_indices is not None:
                if not p.evidence_indices or any(type(i) is not int or i<0 or i>=len(ev['conversation']['transcript']) for i in p.evidence_indices):
                    raise HTTPException(422,'Reviewer evidence turn indexes invalid')
                answers[a['criterion_id']]['evidence_indices']=p.evidence_indices
            try:
                rubric_score(ev['checklist'],list(answers.values()))
            except ValueError as e:
                raise HTTPException(422,str(e)) from e
        db.execute('''UPDATE appeals SET status='resolved',decision=?,decided_answer=?,reviewer_note=?,updated_at=?,evidence_json=? WHERE id=?''',(
            p.decision,json.dumps(p.answer,ensure_ascii=False) if p.decision=='change' else None,
            p.reviewer_note,now(),json.dumps(p.evidence_indices) if p.decision=='change' and p.evidence_indices is not None else None,aid))
        updated=get_evaluation_detail(db,a['evaluation_id'])
    return {'appeal_id':aid,'status':'resolved','final_score':updated['final']['total_score']}


@app.post('/api/demo')
def seed_demo():
    transcript=[
        {'timestamp':'00:01','speaker':'client','text':'Здравствуйте! Где можно узнать комиссии за покупку акций?'},
        {'timestamp':'00:15','speaker':'operator','text':'Здравствуйте! Меня зовут Анна. Комиссии можно посмотреть в разделе «Тарифы» в приложении.'},
        {'timestamp':'00:25','speaker':'client','text':'А какой тариф лучше выбрать для небольших покупок?'},
        {'timestamp':'00:35','speaker':'operator','text':'Тарифы доступны в приложении.'},
        {'timestamp':'00:45','speaker':'client','text':'Понятно, спасибо.'},
        {'timestamp':'00:55','speaker':'operator','text':'Пожалуйста! Могу ли ещё чем-то помочь? Хорошего дня!'},
    ]
    # Curated, illustrative assessment, NOT produced by LLM or sourced from organizers.
    answers=[
        {'criterion_id':'01','answer':'yes','reason':'Первый ответ содержит приветствие и представление.','evidence_indices':[1]},
        {'criterion_id':'02','answer':'na','reason':'Для начального вопроса о месте просмотра тарифов уточнения не требовались.','evidence_indices':[0]},
        {'criterion_id':'03','answer':'na','reason':'Нет запроса к данным счёта или защищённым действиям.','evidence_indices':[0,2]},
        {'criterion_id':'04','answer':10,'reason':'На вопрос о выборе тарифа клиент получил лишь повторную ссылку на тарифы, без конкретного следующего шага.','evidence_indices':[2,3]},
        {'criterion_id':'05','answer':'yes','reason':'Вежливое обращение, жалоб не было.','evidence_indices':[1,5]},
        {'criterion_id':'06','answer':7,'reason':'Ответы понятны, но есть один заметный содержательный сбой.','evidence_indices':[2,3]},
        {'criterion_id':'07','answer':'yes','reason':'Оператор предложил помощь и попрощался.','evidence_indices':[5]},
        {'criterion_id':'08','answer':'no','reason':'Оскорблений, обхода компании и раскрытия данных не обнаружено.','evidence_indices':[1,3,5]},
    ]
    with db_conn() as db:
        cid='demo-ru-001'
        if not db.execute('SELECT id FROM conversations WHERE id=?',(cid,)).fetchone():
            db.execute('INSERT INTO conversations (id,channel,language,transcript_json,audio_url,created_at,dataset_group_id) VALUES (?,?,?,?,?,?,?)',(
                cid,'agent chat','RU',json.dumps(transcript,ensure_ascii=False),None,now(),'demo'))
        checklist=get_checklist(db,'checklist-v1')
        score=rubric_score(checklist,answers)
        eid=str(uuid.uuid4())
        db.execute('INSERT INTO evaluations VALUES (?,?,?,?,?,?)',(
            eid,cid,checklist['id'],'curated-demo',json.dumps(score,ensure_ascii=False),now()))
    return {'evaluation_id':eid,'note':'Manually prepared example, NOT LLM output'}

# ---------- Additional complete workflows ----------

def store_evaluation(conversation_id: str, checklist_id: str, mode: str, score: dict) -> str:
    evaluation_id = str(uuid.uuid4())
    with db_conn() as db:
        db.execute('''INSERT INTO evaluations
            (id,conversation_id,checklist_id,mode,original_json,created_at)
            VALUES (?,?,?,?,?,?)''',
            (evaluation_id,conversation_id,checklist_id,mode,
             json.dumps(score,ensure_ascii=False),now()))
    return evaluation_id


@app.get('/api/health')
def health():
    with db_conn() as db:
        db.execute('SELECT 1').fetchone()
    return {'status':'ok','database':'connected','model_configured':bool(os.getenv('CQA_LLM_API_KEY') and os.getenv('CQA_LLM_MODEL'))}


@app.post('/api/evaluations/manual')
def manual_evaluation(p: ManualPayload):
    """Explicit human-grade entry, not passed off as an AI-generated evaluation."""
    with db_conn() as db:
        convo=get_conversation(db,p.conversation_id)
        checklist=get_checklist(db,p.checklist_id)
    for row in p.answers:
        ids=row.get('evidence_indices')
        if not isinstance(ids,list) or not ids or any(type(i) is not int or i<0 or i>=len(convo['transcript']) for i in ids):
            raise HTTPException(422,'Evidence indexes out of transcript range')
        if not isinstance(row.get('reason'),str) or not row['reason'].strip():
            raise HTTPException(422,'Every manual answer must include a reason')
    try:
        score=rubric_score(checklist,p.answers)
    except (ValueError,TypeError) as exc:
        raise HTTPException(422,str(exc)) from exc
    evaluation_id=store_evaluation(convo['conversation_id'],checklist['id'],'manual',score)
    return {'evaluation_id':evaluation_id,'score':score['total_score'],'gross_error':score['gross_error'],'mode':'manual'}


@app.get('/api/analytics')
def analytics():
    """Aggregate FINAL grades including reviewer decisions; never change originals."""
    with db_conn() as db:
        rows=db.execute('SELECT id FROM evaluations ORDER BY created_at').fetchall()
        stats={'evaluations':len(rows),'scored':0,'average_score':None,'gross_errors':0,
               'pending_appeals':db.execute("SELECT count(*) FROM appeals WHERE status='pending'").fetchone()[0],
               'by_language':{},'by_checklist':{},'by_criterion':{}}
        grades=[]
        for row in rows:
            d=get_evaluation_detail(db,row['id'])
            score=d['final']['total_score']
            if score is None: continue
            stats['scored']+=1
            grades.append(score)
            if d['final']['gross_error']: stats['gross_errors']+=1
            lang=d['conversation']['language']
            bucket=stats['by_language'].setdefault(lang,{'count':0,'sum':0.0})
            bucket['count']+=1;bucket['sum']+=score
            vers='v'+str(d['checklist']['version'])
            bucket=stats['by_checklist'].setdefault(vers,{'count':0,'sum':0.0})
            bucket['count']+=1;bucket['sum']+=score
            for r in d['final']['criteria']:
                if r['fraction'] is None or r['effective_weight'] is None: continue
                bucket=stats['by_criterion'].setdefault(r['criterion_id'],{'count':0,'sum_fraction':0.0,'name':next((c['name'] for c in d['checklist']['criteria'] if c['id']==r['criterion_id']),r['criterion_id'])})
                bucket['count']+=1;bucket['sum_fraction']+=r['fraction']
        if grades: stats['average_score']=round(sum(grades)/len(grades),2)
        for category in ('by_language','by_checklist'):
            for b in stats[category].values():
                b['average']=round(b.pop('sum')/b['count'],2)
        for b in stats['by_criterion'].values():
            b['average_percent']=round(100*b.pop('sum_fraction')/b['count'],2)
        return stats


def _csv_safe(value):
    """Avoid spreadsheet formula injection when opening exported CSV."""
    cell=str(value if value is not None else '')
    return "'"+cell if cell.lstrip(' \t\r').startswith(('=','+','-','@')) else cell


@app.get('/api/export/results.csv')
def export_csv():
    output=io.StringIO()
    writer=csv.writer(output)
    writer.writerow(['evaluation_id','conversation_id','language','channel','group_id','checklist_version',
                     'mode','original_score','final_score','gross_error','pending_appeals','created_at'])
    with db_conn() as db:
        for row in db.execute('SELECT id FROM evaluations ORDER BY created_at'):
            d=get_evaluation_detail(db,row['id'])
            writer.writerow([_csv_safe(x) for x in [d['id'],d['conversation']['conversation_id'],
                d['conversation']['language'],d['conversation']['channel'],d['conversation']['dataset_group_id'],
                d['checklist']['version'],d['mode'],d['original']['total_score'],
                d['final']['total_score'],d['final']['gross_error'],
                sum(a['status']=='pending' for a in d['appeals']),d['created_at']]])
    return Response(content='\ufeff'+output.getvalue(),media_type='text/csv; charset=utf-8',
                    headers={'Content-Disposition':'attachment; filename="freedom-cqa-results.csv"'})


@app.get('/api/evaluations/{eid}/export.json')
def export_evaluation_json(eid: str):
    with db_conn() as db:
        return get_evaluation_detail(db,eid)


def get_job(db,job_id: str):
    row=db.execute('SELECT * FROM batch_jobs WHERE id=?',(job_id,)).fetchone()
    if row is None: raise HTTPException(404,'Batch job not found')
    out=dict(row)
    out['errors']=json.loads(out.pop('errors_json'))
    out.pop('ids_json')
    return out


def _batch_run(job_id: str):
    with db_conn() as db:
        job=db.execute('SELECT * FROM batch_jobs WHERE id=?',(job_id,)).fetchone()
        if job is None: return
        ids=json.loads(job['ids_json'])
        checklist_id=job['checklist_id']
        if job['status']=='cancelling':
            db.execute("UPDATE batch_jobs SET status='cancelled',updated_at=? WHERE id=?",(now(),job_id))
            return
        if job['status']!='pending':
            return
        db.execute("UPDATE batch_jobs SET status='running',updated_at=? WHERE id=?",(now(),job_id))

    def one(cid):
        try:
            with db_conn() as db:
                c=get_conversation(db,cid)
                rubric=get_checklist(db,checklist_id)
            scored=evaluate_with_llm(c,rubric)
            store_evaluation(cid,checklist_id,'llm',scored)
            return cid,None
        except Exception as e:
            # No raw conversation or API keys in persisted error logs.
            return cid,str(e)[:400]

    try:
        # Use conservative provider-specific concurrency and pacing for free-tier quotas.
        # Defaults preserve backward compatibility when .env is not configured.
        workers=max(1,min(4,int(os.getenv('CQA_BATCH_WORKERS','2'))))
        delay=max(0.0,min(120.0,float(os.getenv('CQA_BATCH_DELAY_SEC','0'))))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            active={}
            active_count_started=0
            queue=iter(ids)
            while True:
                with db_conn() as db:
                    status=db.execute('SELECT status FROM batch_jobs WHERE id=?',(job_id,)).fetchone()[0]
                while status=='running' and len(active)<workers:
                    try: cid=next(queue)
                    except StopIteration: break
                    if delay and active_count_started:
                        time.sleep(delay)
                    active[pool.submit(one,cid)]=cid
                    active_count_started += 1
                if not active: break
                future=next(as_completed(active))
                cid=active.pop(future)
                _,error=future.result()
                with db_conn() as db:
                    row=db.execute('SELECT errors_json FROM batch_jobs WHERE id=?',(job_id,)).fetchone()
                    errors=json.loads(row['errors_json'])
                    if error: errors.append({'conversation_id':cid,'error':error})
                    db.execute('''UPDATE batch_jobs SET completed=completed+1,
                        succeeded=succeeded+?, failed=failed+?, errors_json=?,updated_at=? WHERE id=?''',
                        (0 if error else 1,1 if error else 0,json.dumps(errors,ensure_ascii=False),now(),job_id))
        with db_conn() as db:
            status=db.execute('SELECT status FROM batch_jobs WHERE id=?',(job_id,)).fetchone()[0]
            db.execute('UPDATE batch_jobs SET status=?, updated_at=? WHERE id=?',
                       ('cancelled' if status=='cancelling' else 'completed',now(),job_id))
    except Exception as e:
        with db_conn() as db:
            db.execute('UPDATE batch_jobs SET status=?,errors_json=?,updated_at=? WHERE id=?',
                       ('failed',json.dumps([{'error':'Batch execution error: '+type(e).__name__}]),now(),job_id))


@app.get('/api/jobs')
def jobs():
    with db_conn() as db:
        return [get_job(db,r['id']) for r in db.execute('SELECT id FROM batch_jobs ORDER BY created_at DESC LIMIT 30')]


@app.get('/api/jobs/{job_id}')
def job_detail(job_id: str):
    with db_conn() as db:
        return get_job(db,job_id)


@app.post('/api/jobs/batch')
def create_batch(p: BatchPayload):
    if not (os.getenv('CQA_LLM_API_KEY') and os.getenv('CQA_LLM_MODEL')):
        raise HTTPException(503,'AI provider is not configured; batch would not produce real AI results')
    with db_conn() as db:
        rubric=get_checklist(db,p.checklist_id)
        if db.execute("SELECT 1 FROM batch_jobs WHERE checklist_id=? AND status IN ('pending','running','cancelling')",(rubric['id'],)).fetchone():
            raise HTTPException(409,'Another batch for this checklist is already running')
        if p.force:
            candidates=db.execute('SELECT id FROM conversations ORDER BY id LIMIT ?', (p.limit,)).fetchall()
        else:
            candidates=db.execute('''SELECT c.id FROM conversations c WHERE NOT EXISTS
                (SELECT 1 FROM evaluations e WHERE e.conversation_id=c.id AND e.checklist_id=?)
                ORDER BY c.id LIMIT ?''',(rubric['id'],p.limit)).fetchall()
        ids=[r['id'] for r in candidates]
        if not ids:
            return {'job_id':None,'total':0,'status':'nothing-to-do'}
        jid=str(uuid.uuid4()); t=now()
        db.execute('''INSERT INTO batch_jobs
            (id,status,checklist_id,created_at,updated_at,total,completed,succeeded,failed,ids_json,errors_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
            (jid,'pending',rubric['id'],t,t,len(ids),0,0,0,json.dumps(ids),'[]'))
    threading.Thread(target=_batch_run,args=(jid,),daemon=True,name='cqa-batch-'+jid[:8]).start()
    return {'job_id':jid,'total':len(ids),'status':'pending'}


@app.post('/api/jobs/{job_id}/cancel')
def cancel_batch(job_id: str):
    with db_conn() as db:
        job=get_job(db,job_id)
        if job['status'] not in ('pending','running'):
            raise HTTPException(409,'This job is not running')
        db.execute("UPDATE batch_jobs SET status='cancelling',updated_at=? WHERE id=?",(now(),job_id))
    return {'job_id':job_id,'status':'cancelling'}

@app.get('/api/review-queue')
def review_queue():
    with db_conn() as db:
        rows=db.execute('''SELECT a.id,a.evaluation_id,a.criterion_id,a.comment,a.created_at,
            e.conversation_id,cl.version AS checklist_version,c.language
            FROM appeals a
            JOIN evaluations e ON e.id=a.evaluation_id
            JOIN checklists cl ON cl.id=e.checklist_id
            JOIN conversations c ON c.id=e.conversation_id
            WHERE a.status='pending' ORDER BY a.created_at ASC''').fetchall()
        return [dict(r) for r in rows]
