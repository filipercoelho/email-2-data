"""SPIKE 2b — for the 58 model-returned QUOTES of `spike_quotes_raw.json`, which mechanism hides
each one that is in the body but not on screen?

`probe_region.py` answers this for extracted *values*; this answers it for the model's *evidence
sentences*, which is what a highlight would actually have to reach. The two populations disagree,
and the difference decides whether Phase 2 (restore the signature) is worth building:

  · a VALUE is a token — «274023911», «€ 950» — and lands wherever that token happens to sit.
  · a QUOTE is a whole sentence — «Com os melhores cumprimentos, Sofia Dias, Contribuinte …» — and
    the sentence carrying a person's name is very often the signature block itself.

Buckets are exactly probe_region.region_of's, so the two runs are directly comparable. Free to run
(no LLM): it re-reads the already-paid-for spike output and the on-disk corpus.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from email2data.envelope import clean_email_body, parse_eml  # noqa: E402
from email2data.identity import safe_filename  # noqa: E402
from probe_match import split_quote  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "corpus"
BUCKETS = ("visible", "signature", "truncated", "quoted", "absent")


def _ws(s: str) -> str:
    """Whitespace-tolerant form. The spike's finding (a): hard-wrap rewrapping is a KNOWN lossless
    difference between what the model was shown and what it returned, so normalising it is not a
    guess. Nothing else is normalised — no case folding, no accent stripping."""
    return re.sub(r"\s+", " ", s or "").strip()


def region_of_quote(quote: str, body: str) -> str:
    """Which mechanism hides this quote? Mirrors probe_region.region_of bucket-for-bucket."""
    clean_full = clean_email_body(body)
    clean_cut = clean_full[:3000]
    visible = split_quote(clean_cut)[:2000]

    def hit(hay: str) -> bool:
        return _ws(quote) in _ws(hay)

    if hit(visible):
        return "visible"
    if not hit(body):
        return "absent"
    if not hit(clean_full):
        return "signature"
    if not hit(clean_cut):
        return "truncated"
    return "quoted"


def main() -> None:
    rows = json.loads((ROOT / "design" / "spike_quotes_raw.json").read_text())
    per_key: dict[str, Counter] = defaultdict(Counter)
    total: Counter = Counter()
    missing = 0

    for r in rows:
        quote = r.get("quote")
        if not quote or quote == r.get("value"):
            continue                     # the echo failure — rejected by the Phase 4 validation stack
        eml = CORPUS / safe_filename(r.get("mid") or "")
        if not eml.exists():
            missing += 1
            continue
        try:
            env = parse_eml(eml.read_bytes())
        except (OSError, ValueError):
            missing += 1
            continue
        bucket = region_of_quote(quote, env.get("body_text") or "")
        per_key[r["key"]][bucket] += 1
        total[bucket] += 1

    width = max((len(k) for k in per_key), default=10) + 2
    print(f"{'key':<{width}}{'n':>4}" + "".join(f"{b:>12}" for b in BUCKETS))
    print("-" * (width + 4 + 12 * len(BUCKETS)))
    for key, c in sorted(per_key.items(), key=lambda x: -sum(x[1].values())):
        n = sum(c.values())
        cells = "".join(f"{c[b]:>7}{c[b] / n:>5.0%}" for b in BUCKETS)
        print(f"{key:<{width}}{n:>4}{cells}")
    n = sum(total.values())
    print("-" * (width + 4 + 12 * len(BUCKETS)))
    if n:
        cells = "".join(f"{total[b]:>7}{total[b] / n:>5.0%}" for b in BUCKETS)
        print(f"{'ALL':<{width}}{n:>4}{cells}")
    if missing:
        print(f"\n{missing} quote(s) skipped — the .eml is no longer in corpus/.")
    print("\nreadable: 'signature' is the share Phase 2 converts to 'visible'. Compare it against")
    print("probe_region.py's signature bucket for VALUES — if they disagree, the quote population")
    print("is the one a highlight has to reach, and it is the one that decides the phase.")


if __name__ == "__main__":
    main()
