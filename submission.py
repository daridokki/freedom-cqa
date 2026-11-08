"""Freedom Broker CQA results.json schema 1.0 (appendix, 3 pages).

This module is deliberately provider-neutral: it transforms saved human/LLM
rubric decisions into the organizer's fixed wire format, without inventing
missing scores or calling a model.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

CRITERIA = (
    ('01', 'contact', 'bool', False, ()),
    ('02', 'discovery', 'bool', True, ()),
    ('03', 'identification', 'bool', True, ()),
    ('04', 'consultation', 'scale', True, (0, 5, 10, 15, 20)),
    ('05', 'empathy', 'bool', False, ()),
    ('06', 'dialogue', 'scale', False, (0, 4, 7, 10)),
    ('07', 'closure', 'bool', True, ()),
    ('08', 'critical', 'bool', False, ()),
)
OFFICIAL_IDS = [r[1] for r in CRITERIA]
INTERNAL_IDS = [r[0] for r in CRITERIA]
MODE_VALUES = {'local', 'external_api', 'hybrid'}

class SubmissionError(ValueError):
    pass


def _as_two_decimals(value: Any) -> float:
    if type(value) not in (int, float) or isinstance(value, bool):
        raise SubmissionError('final_score must be a number')
    d = Decimal(str(value))
    if not d.is_finite() or d < 0 or d > 100:
        raise SubmissionError('final_score must be between 0 and 100')
    if d != d.quantize(Decimal('.01'), rounding=ROUND_HALF_UP):
        raise SubmissionError('final_score must be rounded to two decimals')
    return float(d)


def checklist_shape(checklist: dict) -> None:
    """Organizer 1.0 requires exactly eight IDs and unchanged grading types.

    Rules/weights may be edited in the app for the organizer's control change,
    but re-mapping a ninth/renamed criterion silently is unacceptable.
    """
    cfg = checklist.get('criteria')
    if not isinstance(cfg, list) or {str(c.get('id')) for c in cfg} != set(INTERNAL_IDS) or len(cfg) != 8:
        raise SubmissionError('Для формата 1.0 нужны ровно критерии 01–08 (без новых ID)')
    by_id = {str(c['id']):c for c in cfg}
    for inner, outer, kind, na, levels in CRITERIA:
        c=by_id[inner]
        expected_type = 'flag' if outer=='critical' else ('scale' if kind=='scale' else 'boolean')
        if c.get('type') != expected_type or bool(c.get('allow_na')) != na:
            raise SubmissionError(f'Критерий {inner}/{outer}: несовместимый тип или N/A')
        if kind=='scale' and tuple(c.get('levels', [])) != levels:
            raise SubmissionError(f'Критерий {inner}/{outer}: шкала должна оставаться {list(levels)}')


def _mapped_value(answer: Any, criterion: tuple) -> Any:
    inner, outer, kind, na, levels=criterion
    if answer == 'na' and na:
        return None
    if kind == 'bool':
        if answer not in ('yes', 'no'):
            raise SubmissionError(f'{outer}: допустимы только yes/no' + ('/na' if na else ''))
        return answer == 'yes'
    if type(answer) not in (int, float) or answer not in levels:
        raise SubmissionError(f'{outer}: недопустимая ступень шкалы {answer!r}')
    return int(answer)


def convert_saved_evaluation(detail: dict) -> dict:
    """Use FINAL grades after decided appeals. 0-based -> 1-based evidence."""
    checklist_shape(detail['checklist'])
    transcript = detail['conversation']['transcript']
    if not transcript:
        raise SubmissionError('Empty transcript')
    grades = detail['final']['criteria']
    by_id = {str(x['criterion_id']):x for x in grades}
    if len(by_id) != 8 or set(by_id) != set(INTERNAL_IDS):
        raise SubmissionError('Missing or duplicate criteria in saved evaluation')
    criteria=[]
    for spec in CRITERIA:
        internal, name, kind, allow_na, levels=spec
        result=by_id[internal]
        reason=result.get('reason')
        if not isinstance(reason,str) or not reason.strip():
            raise SubmissionError(f'{name}: empty rationale')
        evidence=result.get('evidence_indices')
        if not isinstance(evidence,list) or not evidence or any(type(i) is not int or not (0<=i<len(transcript)) for i in evidence):
            raise SubmissionError(f'{name}: invalid evidence_indices')
        criteria.append({'criterion_id':name, 'value':_mapped_value(result.get('answer'),spec),
                         'rationale':reason.strip(), 'evidence_turns':[i+1 for i in evidence]})
    score=detail['final']
    if score.get('total_score') is None:
        raise SubmissionError('No applicable weighted criteria; no numerical result')
    numerical=_as_two_decimals(score['total_score'])
    flag=bool(score['gross_error'])
    if flag != criteria[-1]['value']:
        raise SubmissionError('critical_flag != critical.value')
    return {'conversation_id':detail['conversation']['conversation_id'], 'status':'ok',
            'criteria':criteria, 'score':{'final_score':numerical, 'critical_flag':flag}}


def error_result(conversation_id: str, reason: str) -> dict:
    return {'conversation_id':conversation_id, 'status':'error', 'criteria':[], 'score':None,
            'error':reason.strip()[:600] or 'Оценка не выполнена'}


def validate_document(document: dict, expected_ids: list[str], transcripts: dict[str,list]|None=None,
                      checklist: dict|None=None) -> dict:
    """Strict preflight, including type checks (bool is not int!) and 1-based turns.

    If checklist is supplied, also re-compute 100-point scoring deterministically.
    """
    from scoring import rubric_score

    if type(document) is not dict or set(document) != {'schema_version','team_name','model','results'}:
        raise SubmissionError('Top-level JSON fields differ from schema 1.0')
    if document['schema_version'] != '1.0':
        raise SubmissionError('schema_version must be "1.0"')
    if not isinstance(document['team_name'],str) or not document['team_name'].strip():
        raise SubmissionError('team_name must be nonempty')
    m=document['model']
    if not isinstance(m,dict) or set(m) != {'mode','name'} or m['mode'] not in MODE_VALUES or not isinstance(m['name'],str) or not m['name'].strip():
        raise SubmissionError('Invalid model object')
    rows=document['results']
    if not isinstance(rows,list) or len(rows)!=len(expected_ids):
        raise SubmissionError(f'Wrong result count: expected {len(expected_ids)}, got {len(rows) if isinstance(rows,list) else "not a list"}')
    if len(expected_ids)!=len(set(expected_ids)):
        raise SubmissionError('Duplicated IDs in original dataset')
    ids=[r.get('conversation_id') for r in rows if isinstance(r,dict)]
    if len(ids)!=len(rows) or len(ids)!=len(set(ids)) or set(ids)!=set(expected_ids):
        raise SubmissionError('Missing, duplicate, or unexpected conversation_id')
    if checklist: checklist_shape(checklist)
    ok=err=0
    for r in rows:
        cid=r['conversation_id']
        if r.get('status')=='error':
            if set(r) != {'conversation_id','status','criteria','score','error'} or r['criteria']!=[] or r['score'] is not None or not isinstance(r['error'],str) or not r['error'].strip():
                raise SubmissionError(f'{cid}: invalid error entry')
            err+=1
            continue
        if r.get('status')!='ok' or set(r) != {'conversation_id','status','criteria','score'}:
            raise SubmissionError(f'{cid}: invalid ok entry fields')
        c=r['criteria']
        if not isinstance(c,list) or len(c)!=8 or [x.get('criterion_id') for x in c] != OFFICIAL_IDS:
            raise SubmissionError(f'{cid}: expected 8 criteria in organizer order')
        for spec, x in zip(CRITERIA,c):
            _,name,kind,na,levels=spec
            if set(x) != {'criterion_id','value','rationale','evidence_turns'}:
                raise SubmissionError(f'{cid}/{name}: criterion fields mismatch')
            v=x['value']
            if (kind=='bool' and not (type(v) is bool or (v is None and na)) or
                kind=='scale' and not ((type(v) is int and v in levels) or (v is None and na))):
                raise SubmissionError(f'{cid}/{name}: wrong value type/value')
            if not isinstance(x['rationale'],str) or not x['rationale'].strip():
                raise SubmissionError(f'{cid}/{name}: missing rationale')
            ev=x['evidence_turns']
            if not isinstance(ev,list) or not ev or any(type(i) is not int or i<1 for i in ev):
                raise SubmissionError(f'{cid}/{name}: evidence must be 1-based positive integers')
            if transcripts is not None and (cid not in transcripts or any(i>len(transcripts[cid]) for i in ev)):
                raise SubmissionError(f'{cid}/{name}: evidence turn exceeds original transcript')
        score=r.get('score')
        if not isinstance(score,dict) or set(score)!={'final_score','critical_flag'} or type(score['critical_flag']) is not bool:
            raise SubmissionError(f'{cid}: score object invalid')
        if score['critical_flag'] is not c[-1]['value']:
            raise SubmissionError(f'{cid}: critical_flag disagrees with criterion')
        _as_two_decimals(score['final_score'])
        if checklist:
            rev=[]
            for spec, grade in zip(CRITERIA,c):
                inner,name,kind,na,levels=spec
                v=grade['value']
                answer='na' if v is None else (('yes' if v else 'no') if kind=='bool' else v)
                rev.append({'criterion_id':inner,'answer':answer,'reason':grade['rationale'],
                            'evidence_indices':[i-1 for i in grade['evidence_turns']]})
            recomputed=rubric_score(checklist,rev)
            if recomputed['total_score'] != score['final_score']:
                raise SubmissionError(f'{cid}: final_score differs from deterministic recomputation')
        ok+=1
    return {'total':len(rows),'ok':ok,'error':err,'valid':True}
