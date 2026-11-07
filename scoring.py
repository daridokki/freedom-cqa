"""Deterministic scoring engine for the Freedom Broker CQA rubric."""
from decimal import Decimal, ROUND_HALF_UP
from typing import Any


def _d(x: Any) -> Decimal:
    return Decimal(str(x))


def _round2(x: Decimal) -> float:
    return float(x.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))


def rubric_score(checklist: dict, answers: list[dict]) -> dict:
    """Calculate normalized score / section scores, separately flag gross errors.

    A criterion with NA contributes neither earned nor available weight.
    Remaining applicable weights are re-normalized to 100, even after editing.
    This raises ValueError for invalid or missing answers instead of silently
    accepting an invalid model response.
    """
    cfg = checklist['criteria']
    by_id = {str(x['criterion_id']): x for x in answers}
    if len(by_id) != len(answers):
        raise ValueError('Duplicate criterion_id in evaluation')
    expected = {str(x['id']) for x in cfg}
    if set(by_id) != expected:
        raise ValueError(f'Missing/unknown criteria: expected={sorted(expected)}, received={sorted(by_id)}')

    valid_types = {'boolean', 'scale', 'flag'}
    weighted = []
    gross_errors = []
    result = []
    for c in cfg:
        cid = str(c['id'])
        entry = by_id[cid]
        kind = c['type']
        if kind not in valid_types:
            raise ValueError(f'{cid}: unsupported type {kind}')
        weight = _d(c.get('weight', 0))
        if weight < 0:
            raise ValueError(f'{cid}: weight must be nonnegative')
        answer = entry.get('answer')
        if isinstance(answer, str):
            answer = answer.strip().lower()
        if answer == 'n/a':
            answer = 'na'
        if answer == 'na':
            if not c.get('allow_na', False) or kind == 'flag':
                raise ValueError(f'{cid}: N/A not permitted')
            fraction = None
        elif kind in {'boolean', 'flag'}:
            if answer not in {'yes', 'no'}:
                raise ValueError(f'{cid}: expected yes/no')
            fraction = _d(1 if answer == 'yes' else 0)
        elif kind == 'scale':
            levels = c.get('levels', [])
            if not levels or max(levels) <= 0:
                raise ValueError(f'{cid}: invalid scale')
            try:
                val = _d(answer)
            except Exception as exc:
                raise ValueError(f'{cid}: expected one of {levels}') from exc
            if val not in [_d(v) for v in levels]:
                raise ValueError(f'{cid}: {answer} is not one of {levels}')
            fraction = val / _d(max(levels))
            answer = int(val) if val == int(val) else float(val)
        if kind == 'flag':
            if answer == 'yes':
                gross_errors.append(cid)
            weight = _d(0)
        elif fraction is not None and weight > 0:
            weighted.append(cid)
        ev = entry.get('evidence_indices', [])
        if not isinstance(ev, list) or any(type(n) is not int or n < 0 for n in ev):
            raise ValueError(f'{cid}: evidence_indices must be nonnegative integer list')
        result.append({
            'criterion_id': cid, 'answer': answer,
            'reason': str(entry.get('reason', '')).strip(),
            'evidence_indices': ev,
            'raw_weight': float(weight), 'fraction': None if fraction is None else float(fraction),
            'raw_points': None if fraction is None else _round2(weight * fraction),
            'effective_weight': None, 'effective_points': None,
            'section': c.get('section', 'Other'),
        })
    available = sum((_d(r['raw_weight']) for r in result if r['criterion_id'] in weighted), _d(0))
    section_scores = {}
    if available > 0:
        multiplier = _d(100) / available
        score = _d(0)
        for r in result:
            if r['criterion_id'] not in weighted:
                continue
            w = _d(r['raw_weight']) * multiplier
            p = w * _d(r['fraction'])
            r['effective_weight'] = _round2(w)
            r['effective_points'] = _round2(p)
            score += p
            section = r['section']
            if section not in section_scores:
                section_scores[section] = {'earned': _d(0), 'maximum': _d(0)}
            section_scores[section]['earned'] += p
            section_scores[section]['maximum'] += w
        total = _round2(score)
        section_scores = {k: {'earned': _round2(v['earned']), 'maximum': _round2(v['maximum'])}
                          for k,v in section_scores.items()}
    else:
        total = None
    return {
        'total_score': total, 'gross_error': bool(gross_errors), 'gross_error_criteria': gross_errors,
        'applicable_weight': float(available), 'sections': section_scores,
        'criteria': result,
    }
