"""Phase-1 labeling helper (throwaway tooling, not part of the package).

  build : body-aware classify all corpus/*.eml -> out/proposals.jsonl, then stratify-sample ~40
          (forcing in corticoenetos + a Vision Box mail) -> labels/worksheet.csv with my proposals
          pre-filled. You then CORRECT the `counterparty` / `priority` columns in place.
  score : read the corrected labels/worksheet.csv and print the baseline (counterparty accuracy,
          CLIENT recall, real-clients-binned, priority accuracy).

Run: .venv/bin/python design/labelsheet.py build   (then edit the sheet)   then  ... score
"""
from __future__ import annotations

import argparse
import csv
import email
import json
import re
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from email.utils import parseaddr
from pathlib import Path

sys.path.insert(0, "src")
from google import genai  # noqa: E402
from google.genai import types  # noqa: E402

from email2data.envelope import parse_eml  # noqa: E402

PROJECT, LOCATION, MODEL = "materials-492723", "global", "gemini-2.5-flash"
OURS, BODY_CAP = "lindoservico", 4000
COUNTERPARTY = ["CLIENT", "SUPPLIER", "INTERNAL", "BULK", "OTHER"]
PURPOSE = ["PO_FROM_CLIENT", "ESTIMATE_REQUEST_FROM_CLIENT", "OUR_ORDER_TO_SUPPLIER",
           "SUPPLIER_REPLY_OR_CONFIRMATION", "INVOICE_OR_ACCOUNTING", "FOLLOW_UP",
           "PUBLICITY", "INTERNAL_OPS", "OTHER"]
QUOTA = {"CLIENT": 16, "SUPPLIER": 8, "INTERNAL": 6, "BULK": 5, "OTHER": 5}
FORCE = ("cortico", "amadeus")  # always include these domains (the ambiguous cases)

SCHEMA = {
    "type": "object",
    "properties": {
        "counterparty": {"type": "string", "enum": COUNTERPARTY},
        "purpose": {"type": "string", "enum": PURPOSE},
        "urgency": {"type": "integer"},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["counterparty", "purpose", "urgency", "confidence", "reason"],
}
SYSTEM = """És analista de triagem de email da Lindo Serviço (corte laser, CNC, sinalética, brindes).
DECIDE PELO CORPO DO TEXTO, não pelo domínio.
- CLIENT = compra-nos / envia-nos encomenda (PO) / pede orçamento de trabalho nosso.
- SUPPLIER = compramos-lhe materiais/serviços (nós encomendamos).
- INTERNAL = colega @lindoservico.pt. BULK = newsletter/publicidade.
O domínio é pista fraca (ex.: Vision Box/Amadeus é CLIENTE). Se for um reencaminhamento/citação de um
email externo, classifica pelo ORIGINAL externo, não pelo reencaminhador.
purpose: PO_FROM_CLIENT, ESTIMATE_REQUEST_FROM_CLIENT, OUR_ORDER_TO_SUPPLIER,
SUPPLIER_REPLY_OR_CONFIRMATION, INVOICE_OR_ACCOUNTING, FOLLOW_UP, PUBLICITY, INTERNAL_OPS, OTHER.
urgency 0-100 = pressão temporal. confidence 0-1. reason = uma frase PT com a evidência do corpo."""

client = genai.Client(vertexai=True, project=PROJECT, location=LOCATION)


def _bulk(msg):
    return bool(msg.get("List-Unsubscribe") or msg.get("List-Id")
               or str(msg.get("Precedence", "")).lower() in ("bulk", "list"))


def _priority(cp, purpose, urgency, bulk):
    if bulk or cp == "BULK":
        return "IGNORE"
    if cp == "CLIENT" or purpose in ("PO_FROM_CLIENT", "ESTIMATE_REQUEST_FROM_CLIENT"):
        return "HIGH"
    return "HIGH" if urgency >= 70 else "MEDIUM"


def propose(path: Path):
    raw = path.read_bytes()
    msg = email.message_from_bytes(raw)
    env = parse_eml(raw)
    _, frm = parseaddr(msg.get("From", ""))
    dom = (frm.split("@")[-1] or "").lower()
    bulk = _bulk(msg)
    hint = f"[cabeçalho] direção={'interno' if OURS in dom else 'inbound'}; bulk={'sim' if bulk else 'não'}; dominio={dom}"
    try:
        r = client.models.generate_content(
            model=MODEL, contents=f"{hint}\nAssunto: {env['subject']}\n---CORPO---\n{env['body_text'][:BODY_CAP]}",
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM, temperature=0, max_output_tokens=400,
                response_mime_type="application/json", response_schema=SCHEMA,
                thinking_config=types.ThinkingConfig(thinking_budget=0)))
        d = json.loads(r.text)
    except Exception as e:  # noqa: BLE001
        d = {"counterparty": "OTHER", "purpose": "OTHER", "urgency": 0, "confidence": 0.0, "reason": f"err:{type(e).__name__}"}
    cp = d["counterparty"]
    return {
        "message_id": env["message_id"],
        "model_counterparty": cp, "model_priority": _priority(cp, d["purpose"], d["urgency"], bulk),
        "model_purpose": d["purpose"], "model_conf": round(float(d["confidence"]), 2),
        "_domain": dom, "_from": frm, "_subject": env["subject"][:60],
        "_snippet": re.sub(r"\s+", " ", env["body_text"])[:180],
    }


COLS = ["message_id", "counterparty", "priority", "model_counterparty", "model_priority",
        "model_purpose", "model_conf", "_domain", "_subject", "_snippet"]


def build():
    files = sorted(Path("corpus").glob("*.eml"))
    print(f"classifying {len(files)} emails (body-aware proposals)...")
    with ThreadPoolExecutor(max_workers=8) as ex:
        props = list(ex.map(propose, files))
    Path("out").mkdir(exist_ok=True)
    Path("out/proposals.jsonl").write_text("\n".join(json.dumps(p, ensure_ascii=False) for p in props))
    print("COUNTERPARTY (model):", dict(Counter(p["model_counterparty"] for p in props)))

    # stratified sample: uncertain-first within each bucket, plus forced ambiguous domains
    by = defaultdict(list)
    for p in props:
        by[p["model_counterparty"]].append(p)
    chosen, seen = [], set()
    for cp, quota in QUOTA.items():
        for p in sorted(by.get(cp, []), key=lambda x: x["model_conf"])[:quota]:
            chosen.append(p); seen.add(p["message_id"])
    for p in props:
        if any(n in p["_domain"] for n in FORCE) and p["message_id"] not in seen:
            chosen.append(p); seen.add(p["message_id"])

    with open("labels/worksheet.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS, extrasaction="ignore")
        w.writeheader()
        for p in chosen:
            row = dict(p, counterparty=p["model_counterparty"], priority=p["model_priority"])
            w.writerow(row)
    print(f"\nwrote labels/worksheet.csv with {len(chosen)} rows.")
    print("CORRECT the `counterparty` and `priority` columns (leave model_* alone), then run: score")
    print(f"  counterparty values: {COUNTERPARTY}")
    print("  priority values: ['HIGH','MEDIUM','IGNORE']")


def score():
    rows = list(csv.DictReader(open("labels/worksheet.csv")))
    n = len(rows)
    cp_ok = sum(r["counterparty"] == r["model_counterparty"] for r in rows)
    pr_ok = sum(r["priority"] == r["model_priority"] for r in rows)
    client_rows = [r for r in rows if r["counterparty"] == "CLIENT"]
    recalled = sum(r["model_counterparty"] == "CLIENT" for r in client_rows)
    binned = sum(r["model_priority"] == "IGNORE" for r in client_rows)
    print(f"\nBASELINE (current body-aware classifier vs your {n} labels)")
    print(f"  counterparty accuracy : {cp_ok}/{n} = {cp_ok/n:.0%}")
    print(f"  priority accuracy     : {pr_ok}/{n} = {pr_ok/n:.0%}")
    if client_rows:
        print(f"  CLIENT recall         : {recalled}/{len(client_rows)} = {recalled/len(client_rows):.0%}")
        print(f"  real-clients-binned   : {binned}  (must be 0)")
    conf = Counter((r["model_counterparty"], r["counterparty"]) for r in rows if r["counterparty"] != r["model_counterparty"])
    if conf:
        print("  confusions (model -> truth):")
        for (m, t), c in conf.most_common():
            print(f"      {m:9} -> {t:9} x{c}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["build", "score"])
    {"build": build, "score": score}[ap.parse_args().mode]()
