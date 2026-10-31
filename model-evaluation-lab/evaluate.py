"""Offline behavior evaluation. Python 3.10+, standard library only."""
import argparse
import hashlib
import json
from pathlib import Path

CATEGORIES = {'billing', 'access', 'bug', 'other', 'unknown'}
ACTIONS = {'route', 'clarify', 'human_review'}
KEYWORDS = {'billing': ('发票', '扣费', '退款', '账单'),
            'access': ('登录', '密码', '账号', '权限'),
            'bug': ('崩溃', '报错', '白屏', '卡死')}
ROOT = Path(__file__).resolve().parent


def read_jsonl(path):
    rows = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    ids = [r['id'] for r in rows]
    if not rows or any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
        raise ValueError('Empty input, invalid or duplicate IDs')
    return rows


def valid_output(output):
    return (isinstance(output, dict) and set(output) == {'category', 'action'}
            and isinstance(output['category'], str) and output['category'] in CATEGORIES
            and isinstance(output['action'], str) and output['action'] in ACTIONS)


def predict(text, version):
    # No access to case IDs, gold labels or slices.
    hits = [label for label, words in KEYWORDS.items() if any(w in text for w in words)]
    if version == 'v2':
        # Single change: uncertainty gate before routing. Keyword taxonomy is unchanged.
        if len(hits) > 1 or (not hits and any(w in text for w in ('处理', '不能用', '怎么回事'))):
            return {'category': 'unknown', 'action': 'clarify'}
    return {'category': hits[0] if hits else 'other', 'action': 'route'}


def score(cases, predictions):
    expected_ids = {c['id'] for c in cases}
    actual_ids = {p['id'] for p in predictions}
    if len(actual_ids) != len(predictions) or actual_ids != expected_ids:
        raise ValueError('Prediction IDs must match cases exactly; no omissions, extras or duplicates')
    lookup = {p['id']: p.get('output') for p in predictions}
    details = []
    for c in cases:
        if not valid_output(c['expected']):
            raise ValueError('Invalid gold output')
        out = lookup[c['id']]
        valid = valid_output(out)
        cat = valid and out['category'] == c['expected']['category']
        action = valid and out['action'] == c['expected']['action']
        unsafe = c['expected']['action'] != 'route' and valid and out['action'] == 'route'
        errors = []
        if not valid: errors.append('schema_invalid')
        else:
            if not cat: errors.append('category_error')
            if not action: errors.append('action_error')
            if unsafe: errors.append('unsafe_auto_route')
            if out['action'] == 'clarify' and c['expected']['action'] == 'route': errors.append('unnecessary_clarification')
        details.append(dict(id=c['id'], slice=c['slice'], expected=c['expected'], output=out,
                            schema_valid=valid, category_correct=cat, action_correct=action,
                            success=cat and action, errors=errors))
    n = len(details)
    counts = {k: sum(d[k] for d in details) for k in ('schema_valid', 'category_correct', 'action_correct', 'success')}
    metrics = {'n': n, 'counts': counts, 'rates': {k: v/n for k, v in counts.items()},
               'errors': {k: sum(k in d['errors'] for d in details) for k in ('schema_invalid','category_error','action_error','unsafe_auto_route','unnecessary_clarification')},
               'slices': {s: {'n': sum(d['slice']==s for d in details), 'success': sum(d['slice']==s and d['success'] for d in details)} for s in sorted({d['slice'] for d in details})}}
    return metrics, details


def write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, default=ROOT/'eval_cases.jsonl')
    parser.add_argument('--predictions', type=Path, help='Externally collected JSONL: id + output')
    parser.add_argument('--out', type=Path, default=ROOT/'results')
    args = parser.parse_args()
    cases = read_jsonl(args.cases)
    args.out.mkdir(parents=True, exist_ok=True)
    report = {'evidence': 'Real scorer execution on authored synthetic cases; NOT an LLM benchmark',
              'cases_sha256': hashlib.sha256(args.cases.read_bytes()).hexdigest(),
              'scorer_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), 'runs': {}}
    runs = {'external': read_jsonl(args.predictions)} if args.predictions else {
        v: [{'id': c['id'], 'output': predict(c['text'], v)} for c in cases] for v in ('v1','v2')}
    all_details = {}
    for name, predictions in runs.items():
        metrics, details = score(cases, predictions)
        report['runs'][name] = metrics
        all_details[name] = details
        (args.out/f'predictions_{name}.jsonl').write_text(''.join(json.dumps(p,ensure_ascii=False)+'\n' for p in predictions))
        write_json(args.out/f'details_{name}.json', details)
    if not args.predictions:
        before, after = all_details['v1'], all_details['v2']
        report['paired_comparison'] = {
            'fixed': [a['id'] for b,a in zip(before,after) if not b['success'] and a['success']],
            'regressed': [a['id'] for b,a in zip(before,after) if b['success'] and not a['success']],
            'unchanged_failures': [a['id'] for b,a in zip(before,after) if not b['success'] and not a['success']]}
    write_json(args.out/'metrics.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
