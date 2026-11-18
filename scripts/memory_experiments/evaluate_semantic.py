"""Evidence ranking only; fixed top-k, no inferred business constraints or cutoff tuning."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.evaluate_memory_retrieval import evaluate, summarize  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--scores', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    fixture = json.loads(args.fixture.read_text())
    probe = json.loads(args.scores.read_text())
    assert probe['fixture_sha256'] == hashlib.sha256(args.fixture.read_bytes()).hexdigest()
    # Identical query text must have identical evidence scores, regardless of binding.
    by_query = {q['query']: dict(zip((m['content'] for m in fixture['memories']), scores, strict=True))
                for q, scores in zip(fixture['queries'], probe['scores'], strict=True)}
    rows = []
    for k in (1, 3, 5):
        def select(pool, query, **kwargs):
            return SimpleNamespace(items=sorted(pool, key=lambda m: (-by_query[query][m['content']], m['id']))[:k])
        trial = evaluate(fixture, candidate=select)
        for row in trial:
            if row['strategy'] == 'candidate_v2':
                row['strategy'] = f'semantic_top_{k}'
                rows.append(row)
            elif k == 1:
                rows.append(row)
    summaries = {str(b): {s: summarize([r for r in rows if r['budget'] == b and r['strategy'] == s])
                          for s in sorted({r['strategy'] for r in rows})} for b in fixture['budgets']}
    args.output.write_text(json.dumps({'model': probe['repo'], 'revision': probe['revision'],
                                      'fixture_sha256': probe['fixture_sha256'], 'by_budget': summaries,
                                      'results': rows}, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(summaries['4096'], indent=2))


if __name__ == '__main__':
    main()
