"""Conservative, transparent checks on model evidence and rubric compliance.

These flags are prompts for a human reviewer, NOT proofs of semantic correctness.
No inference here can silently overwrite a rubric score.
"""
import hashlib
import json
import re


IDENTITY_CONFIRM = re.compile(
    r'(личност[ьи]\s+клиента\s+подтвержден|идентификац\w*\s+(успешн\w*|подтвержден\w*)|'
    r'(identity|verification)\s+(has been\s+)?(verified|confirmed)|'
    r'тұлға\w*\s+(растал|тексерілді))', re.IGNORECASE)
CLOSING = re.compile(r'(до\s+свидания|хорошего\s+(дня|вечера)|'
                     r'сау\s+болыңыз|күніңіз\s+сәтті|goodbye|have a (nice|good)|'
                     r'thank you for (contacting|reaching))', re.IGNORECASE)
CONTENT_ONLY = re.compile(r'(не\s+ответил|не\s+решил|не\s+дал\s+ответ|'
                          r'ошибочн\w*\s+(ответ|совет|рекомендац)|'
                          r'no\s+(substantive\s+)?answer|wrong\s+recommendation)',re.IGNORECASE)


def score_signature(score):
    """Detect stale second-pass checks after a reviewer changes a grade/evidence."""
    entries = sorted((str(r['criterion_id']), r['answer'], r.get('evidence_indices', []))
                     for r in score['criteria'])
    return hashlib.sha256(json.dumps(entries, ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def evidence_warnings(dialog, checklist, score):
    """Flag potentially suspicious citations without making unsafe hard assertions.

    For example a cited turn can exist yet be unrelated: use the independent
    semantic audit and manual review to decide that, not string matching.
    """
    transcript=dialog['transcript']
    criteria={str(c['id']):c for c in checklist['criteria']}
    warnings=[]

    def flag(code, cid, message):
        warnings.append({'code':code,'criterion_id':cid,'message':message})

    op_indices=[i for i,t in enumerate(transcript) if t['speaker']=='operator']
    first_op=op_indices[0] if op_indices else None
    last_op=op_indices[-1] if op_indices else None
    for row in score.get('criteria', []):
        cid=str(row['criterion_id']); c=criteria.get(cid, {})
        label=str(c.get('name','')).casefold()
        ans=row.get('answer'); indices=row.get('evidence_indices',[])
        if not indices:
            flag('NO_EVIDENCE',cid,'Для критерия отсутствует привязка к реплике.')
        elif any(i >= len(transcript) or i<0 for i in indices):
            flag('INVALID_EVIDENCE',cid,'Ссылка на реплику вне диапазона транскрипта.')
        if ans=='na' and indices and not any(transcript[i]['speaker']=='client' for i in indices if i<len(transcript)):
            flag('NA_CONTEXT',cid,'N/A требует проверки условия неприменимости по запросу клиента; приведите реплику клиента.')
        if ('контакт' in label or 'greeting' in label) and ans=='yes' and first_op is not None and first_op not in indices:
            flag('FIRST_RESPONSE',cid,'Положительная оценка контакта должна подтверждаться первым ответом оператора.')
        if ('идентификац' in label or 'identif' in label) and ans=='yes':
            confirmation=[i for i,t in enumerate(transcript) if IDENTITY_CONFIRM.search(t['text'])]
            if not confirmation:
                flag('IDENTITY_PROOF',cid,'В транскрипте не найдено явного подтверждения идентификации; проверьте вручную.')
            elif not any(i in indices for i in confirmation):
                flag('IDENTITY_CITATION',cid,'Подтверждение идентификации существует, но не указано среди доказательств.')
        if ('завершени' in label or 'closing' in label) and ans=='no' and last_op is not None:
            if CLOSING.search(transcript[last_op]['text']):
                flag('CLOSING_REVIEW',cid,'Последняя реплика содержит прощание: проверьте, выполнены ли остальные условия завершения.')
        if ('управлени' in label or 'communication' in label) and row.get('fraction') is not None and row['fraction']<1:
            if CONTENT_ONLY.search(row.get('reason','')):
                flag('DOUBLE_PENALTY',cid,'Похоже, снижение за ясность объяснено только содержательной ошибкой; перепроверьте независимость критериев.')
    return warnings
