"""SPIKE 2 — when an extracted value IS in the email but NOT in the rendered region, WHERE is it?

If we highlight evidence spans, some spans will land in text the dossier currently hides. This
measures which hiding mechanism is responsible, so the UI knows what it must be able to reveal:

  signature  — removed by envelope.clean_email_body (closing salutation onwards)
  quoted     — after the quote marker, behind the «mensagem citada» toggle
  truncated  — past the 3000-char server cut or the 2000-char client cut
  absent     — not literally in the raw body at all (paraphrase / ISO normalisation / attachment)
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from email2data.envelope import clean_email_body, parse_eml  # noqa: E402
from email2data.identity import safe_filename  # noqa: E402
from probe_match import KEYS, fold, matched, split_quote  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "corpus"


def region_of(val: str, key: str, body: str) -> str:
    raw = body
    clean_full = clean_email_body(raw)
    clean_cut = clean_full[:3000]
    visible = split_quote(clean_cut)[:2000]

    def hit(hay: str) -> bool:
        return bool(matched(val, hay, fold(hay), key))

    if hit(visible):
        return "visible"
    if not hit(raw):
        return "absent"                 # never written in the body in any recognisable form
    if not hit(clean_full):
        return "signature"              # clean_email_body removed the region holding it
    if not hit(clean_cut):
        return "truncated"              # survived cleaning, lost to the 3000-char cut
    return "quoted"                     # survived cleaning + cut, sits after the quote marker


def main() -> None:
    results = [json.loads(x) for x in (ROOT / "out" / "results.jsonl").read_text().splitlines() if x]
    per_key: dict[str, Counter] = defaultdict(Counter)
    total = Counter()

    for r in results:
        ents = r.get("entities") or {}
        if not any(ents.get(k) for k in KEYS):
            continue
        eml = CORPUS / safe_filename(r.get("message_id") or "")
        if not eml.exists():
            continue
        try:
            env = parse_eml(eml.read_bytes())
        except Exception:
            continue
        body = (env.get("subject") or "") + "\n" + (env.get("body_text") or "")
        for key in KEYS:
            val = ents.get(key)
            if not val or not str(val).strip():
                continue
            reg = region_of(str(val), key, body)
            per_key[key][reg] += 1
            total[reg] += 1

    order = ["visible", "quoted", "signature", "truncated", "absent"]
    hdr = f"{'key':<20}{'n':>5}" + "".join(f"{o:>11}" for o in order)
    print(hdr)
    print("-" * len(hdr))
    for key in KEYS:
        c = per_key[key]
        n = sum(c.values())
        if not n:
            continue
        cells = "".join(f"{c[o]:>5} {100 * c[o] / n:3.0f}%" for o in order)
        print(f"{key:<20}{n:>5}{cells}")
    n = sum(total.values())
    print("-" * len(hdr))
    print(f"{'ALL':<20}{n:>5}" + "".join(f"{total[o]:>5} {100 * total[o] / n:3.0f}%" for o in order))
    print("\nreadable: 'absent' can NEVER be highlighted by searching — the value was normalised or")
    print("paraphrased, or came from an attachment. Only an evidence SPAN from the model can reach it.")


if __name__ == "__main__":
    main()
