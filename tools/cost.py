"""Exact run cost from out/results.jsonl, by reconstructing each LLM prompt.

Grounded in the real Gemini tokenizer (calibrated): user prompts ~2.79 chars/token, playbook 1495
tokens; Vertex Gemini 2.5 Flash list rates $0.30/M in, $2.50/M out. Writes out/cost.json + prints.
Run from the repo root:  .venv/bin/python tools/cost.py
"""

from __future__ import annotations

import email
import glob
import json
from pathlib import Path

from email2data import cascade, classifier, signals as sig
from email2data.config import load_settings, paths
from email2data.envelope import parse_eml

CPT = 2.79                       # chars/token, calibrated vs the Gemini tokenizer on real prompts
PB_TOKENS = 1495                 # playbook tokens (count_tokens)
IN_RATE, OUT_RATE = 0.30 / 1e6, 2.50 / 1e6   # Vertex Gemini 2.5 Flash

settings = load_settings("config/settings.json")
settings["__settings_path__"] = str(Path("config/settings.json").resolve())
p = paths(settings, settings["__settings_path__"])
store = cascade.build_store(settings)
playbook = classifier.load_playbook(p["playbook"])

results = [json.loads(l) for l in (p["out_dir"] / "results.jsonl").read_text().splitlines() if l.strip()]
mid2file = {}
for f in glob.glob("corpus/*.eml"):
    try:
        mid2file[parse_eml(Path(f).read_bytes())["message_id"]] = f
    except Exception:
        pass

in_tok = out_tok = 0.0
offline = llm = 0
per_email = {}
for r in results:
    if r["decided_by"].startswith("tier0"):
        offline += 1
        per_email[r["message_id"]] = {"in": 0, "out": 0, "cost": 0.0}
        continue
    llm += 1
    f = mid2file.get(r["message_id"])
    if f:
        raw = Path(f).read_bytes()
        env = parse_eml(raw)
        s = sig.enrich(sig.header_signals(email.message_from_bytes(raw)),
                       env.get("subject", ""), env.get("body_text", ""))
        hint = store.lookup(env.get("from", {}).get("email") or s.sender_domain)
        ti = PB_TOKENS + len(classifier.build_user_message(env, s, hint)) / CPT
    else:
        ti = PB_TOKENS + 1005
    mo = {k: r[k] for k in ("counterparty", "purpose", "urgency", "confidence", "reason")}
    mo["entities"] = {k: v for k, v in r["entities"].items() if k not in ("nif", "iban")}
    to = len(json.dumps(mo, ensure_ascii=False)) / CPT
    in_tok += ti
    out_tok += to
    per_email[r["message_id"]] = {"in": round(ti), "out": round(to),
                                  "cost": round(ti * IN_RATE + to * OUT_RATE, 6)}
store.close()

total = offline + llm
cost = in_tok * IN_RATE + out_tok * OUT_RATE
data = {
    "model": "gemini-2.5-flash (Vertex)",
    "emails": total, "offline_calls": offline, "llm_calls": llm,
    "offline_pct": round(100 * offline / total, 1), "llm_pct": round(100 * llm / total, 1),
    "input_tokens": round(in_tok), "output_tokens": round(out_tok),
    "in_rate_per_m": 0.30, "out_rate_per_m": 2.50,
    "cost_usd": round(cost, 4), "cost_per_1k_usd": round(1000 * cost / total, 3),
    "per_email": per_email,
}
(p["out_dir"] / "cost.json").write_text(json.dumps(data, indent=2))
print(json.dumps({k: v for k, v in data.items() if k != "per_email"}, indent=2))
