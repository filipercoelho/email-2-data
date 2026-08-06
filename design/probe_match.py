"""SPIKE — can the app find an extracted entity value inside the text the user actually SEES?

Premise under test (the user's proposal B): "use the AI-extracted value to then search the thread
for a match". If the match rate is high, highlighting needs NO change to the AI layer and no
re-billing. If it is low, the fallback silently highlights nothing for most fields.

Measured on the real corpus, per entity key, at three levels:
  raw     — body_text as the LLM saw it (subject + full body)
  clean   — envelope.clean_email_body(body)[:3000]   (what the server ships as body_clean)
  visible — clean, split at the quote marker, truncated to 2000 chars (what msgHTML renders)

Match tiers, cheapest first:
  exact   — literal substring
  folded  — accent-stripped + casefolded + whitespace-collapsed
  smart   — per-key normalisation (ISO date -> PT renderings; IBAN de-spaced; money digits-only)
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from email2data.envelope import clean_email_body, parse_eml  # noqa: E402
from email2data.identity import safe_filename  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "corpus"
KEYS = ["money", "deadline", "product_or_service", "action_requested", "client_name", "nif", "iban"]

QUOTE_PATS = [
    re.compile(r"^>.*", re.M),
    re.compile(r"^\s*-{2,}\s*(original message|mensagem original)\s*-{2,}", re.I | re.M),
    re.compile(r"^_{5,}\s*$", re.M),
    re.compile(r"^No dia .+", re.M),
    re.compile(r"^Em .+escreveu:", re.I | re.M),
    re.compile(r"^On .+wrote:$", re.I | re.M),
    re.compile(r"^\s*De:\s.+\n(?:.*\n){0,3}?\s*(Enviad[ao]|Para):", re.I | re.M),
    re.compile(r"^\s*From:\s.+\n(?:.*\n){0,3}?\s*(Sent|To):", re.I | re.M),
]


def split_quote(raw: str) -> str:
    body = (raw or "").replace("\r\n", "\n")
    idx = -1
    for p in QUOTE_PATS:
        m = p.search(body)
        if m and (idx < 0 or m.start() < idx):
            idx = m.start()
    return (body[:idx] if idx >= 0 else body).strip()


def fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).casefold()
    return re.sub(r"\s+", " ", s).strip()


MONTHS = ["janeiro", "fevereiro", "marco", "abril", "maio", "junho", "julho",
          "agosto", "setembro", "outubro", "novembro", "dezembro"]


def smart_variants(key: str, val: str) -> list[str]:
    """Per-key renderings a human body might plausibly contain for this stored value."""
    v = val.strip()
    out = [v]
    if key == "deadline":
        m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", v)
        if m:
            y, mo, d = m.group(1), m.group(2), m.group(3)
            di, mi = int(d), int(mo)
            out += [f"{d}/{mo}/{y}", f"{di}/{mi}/{y}", f"{d}-{mo}-{y}", f"{di}/{mi}",
                    f"{d}/{mo}", f"{di}/{mi}/{y[2:]}", f"{di} de {MONTHS[mi - 1]}",
                    f"{di} {MONTHS[mi - 1]}"]
    elif key == "iban":
        digits = re.sub(r"\s+", "", v)
        out += [digits, " ".join(digits[i:i + 4] for i in range(0, len(digits), 4))]
    elif key == "money":
        digits = re.sub(r"[^\d]", "", v)
        if digits:
            out.append(digits)
            # bare integer part, e.g. "1.200,00 EUR" -> "1200" / "1.200"
            intpart = re.sub(r"[^\d]", "", v.split(",")[0])
            if intpart:
                out.append(intpart)
                if len(intpart) > 3:
                    out.append(intpart[:-3] + "." + intpart[-3:])
    return [x for x in out if x]


def matched(val: str, hay: str, hay_folded: str, key: str) -> str:
    """Return the cheapest tier that matches: 'exact' | 'folded' | 'smart' | ''."""
    if val and val in hay:
        return "exact"
    if fold(val) and fold(val) in hay_folded:
        return "folded"
    for cand in smart_variants(key, val)[1:]:
        if fold(cand) and fold(cand) in hay_folded:
            return "smart"
    return ""


def main() -> None:
    results = [json.loads(x) for x in (ROOT / "out" / "results.jsonl").read_text().splitlines() if x]
    stats: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    misses: dict[str, list[tuple[str, str]]] = defaultdict(list)
    n_msgs = n_found = 0

    for r in results:
        ents = r.get("entities") or {}
        if not any(ents.get(k) for k in KEYS):
            continue
        n_msgs += 1
        mid = r.get("message_id") or ""
        eml = CORPUS / safe_filename(mid)
        if not eml.exists():
            continue
        n_found += 1
        try:
            env = parse_eml(eml.read_bytes())
        except Exception:
            continue
        raw = (env.get("subject") or "") + "\n" + (env.get("body_text") or "")
        clean = clean_email_body(env.get("body_text") or "")[:3000]
        visible = split_quote(clean)[:2000]
        layers = {"raw": raw, "clean": clean, "visible": visible}
        folded = {k: fold(v) for k, v in layers.items()}

        for key in KEYS:
            val = ents.get(key)
            if not val or not str(val).strip():
                continue
            val = str(val)
            stats[key]["n"] += 1
            for layer in layers:
                tier = matched(val, layers[layer], folded[layer], key)
                if tier:
                    stats[key][f"{layer}_any"] += 1
                    stats[key][f"{layer}_{tier}"] += 1
            if not matched(val, layers["visible"], folded["visible"], key):
                if len(misses[key]) < 6:
                    misses[key].append((val[:70], visible[:110].replace("\n", " ⏎ ")))

    print(f"messages with >=1 entity: {n_msgs}   .eml located: {n_found}\n")
    hdr = f"{'key':<20}{'n':>5}{'raw':>8}{'clean':>8}{'VISIBLE':>9}   (visible tiers: exact/folded/smart)"
    print(hdr)
    print("-" * len(hdr))
    for key in KEYS:
        s = stats[key]
        n = s["n"]
        if not n:
            continue
        def pct(x):
            return f"{100 * s[x] / n:5.0f}%"
        print(f"{key:<20}{n:>5}{pct('raw_any'):>8}{pct('clean_any'):>8}{pct('visible_any'):>9}"
              f"   {s['visible_exact']:>4}/{s['visible_folded']:>4}/{s['visible_smart']:>4}")
    print("\n--- samples of values NOT findable in the rendered (visible) text ---")
    for key in KEYS:
        for val, ctx in misses[key][:3]:
            print(f"  [{key}] {val!r}\n      visible starts: {ctx!r}")


if __name__ == "__main__":
    main()
