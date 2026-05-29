"""PoC (phase: direction/counterparty/purpose diagnosis). Throwaway, not part of the package.

Theory under test:
  * direction + bulk  -> decidable from HEADERS (cheap, reliable)
  * counterparty + purpose -> need the BODY TEXT read by the LLM (domain is only a weak hint;
    e.g. Vision Box/Amadeus is a CLIENT despite the domain).

Run: .venv/bin/python design/poc-diagnose.py
"""
from __future__ import annotations

import email
import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from email.utils import parseaddr
from pathlib import Path

sys.path.insert(0, "src")
from google import genai  # noqa: E402
from google.genai import types  # noqa: E402

from email2data.envelope import parse_eml  # noqa: E402

PROJECT, LOCATION, MODEL = "materials-492723", "global", "gemini-2.5-flash"
OURS = "lindoservico"
BODY_CAP = 4000

COUNTERPARTY = ["CLIENT", "SUPPLIER", "INTERNAL", "BULK", "OTHER"]
PURPOSE = [
    "PO_FROM_CLIENT", "ESTIMATE_REQUEST_FROM_CLIENT", "OUR_ORDER_TO_SUPPLIER",
    "SUPPLIER_REPLY_OR_CONFIRMATION", "INVOICE_OR_ACCOUNTING", "FOLLOW_UP",
    "PUBLICITY", "INTERNAL_OPS", "OTHER",
]
SCHEMA = {
    "type": "object",
    "properties": {
        "counterparty": {"type": "string", "enum": COUNTERPARTY},
        "purpose": {"type": "string", "enum": PURPOSE},
        "direction": {"type": "string", "enum": ["them->us", "us->them", "internal"]},
        "urgency": {"type": "integer"},
        "reason": {"type": "string"},
    },
    "required": ["counterparty", "purpose", "direction", "urgency", "reason"],
}

SYSTEM = """És analista de triagem de email da Lindo Serviço (fabricação: corte laser, CNC, sinalética, brindes).
Recebes UM email. DECIDE PELO CORPO DO TEXTO, não pelo domínio do remetente.

Definições:
- CLIENT  = alguém que nos compra / nos envia uma encomenda (PO) / pede orçamento de um trabalho nosso.
- SUPPLIER = alguém a quem NÓS compramos materiais ou serviços (nós é que encomendamos).
- INTERNAL = colega interno (@lindoservico.pt).
- BULK = newsletter / publicidade / promoção em massa.
O domínio é só uma pista fraca: tanto clientes como fornecedores têm qualquer domínio. Ex.: Vision Box / Amadeus
é CLIENTE nosso, apesar do domínio. Lê o corpo: quem pede o quê a quem?

purpose: PO_FROM_CLIENT, ESTIMATE_REQUEST_FROM_CLIENT, OUR_ORDER_TO_SUPPLIER, SUPPLIER_REPLY_OR_CONFIRMATION,
INVOICE_OR_ACCOUNTING, FOLLOW_UP, PUBLICITY, INTERNAL_OPS, OTHER.
urgency 0-100 = pressão temporal (prazos, "urgente", cliente à espera). reason = uma frase em PT citando a evidência do corpo."""

client = genai.Client(vertexai=True, project=PROJECT, location=LOCATION)


def header_signals(msg):
    _, frm = parseaddr(msg.get("From", ""))
    dom = (frm.split("@")[-1] or "").lower()
    bulk = bool(msg.get("List-Unsubscribe") or msg.get("List-Id")
                or msg.get("Precedence", "").lower() in ("bulk", "list"))
    internal = OURS in dom
    return dom, bulk, internal


def diagnose(path: Path):
    raw = path.read_bytes()
    msg = email.message_from_bytes(raw)
    dom, bulk, internal = header_signals(msg)
    env = parse_eml(raw)
    hint = f"[cabeçalho] direção={'interno' if internal else 'inbound (externo)'}; bulk={'sim' if bulk else 'não'}; remetente_dominio={dom}"
    user = f"{hint}\nAssunto: {env['subject']}\n---CORPO---\n{env['body_text'][:BODY_CAP]}"
    try:
        r = client.models.generate_content(
            model=MODEL, contents=user,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM, temperature=0, max_output_tokens=400,
                response_mime_type="application/json", response_schema=SCHEMA,
                thinking_config=types.ThinkingConfig(thinking_budget=0)),
        )
        d = json.loads(r.text)
    except Exception as e:  # noqa: BLE001
        d = {"counterparty": "OTHER", "purpose": "OTHER", "direction": "?",
             "urgency": 0, "reason": f"err:{type(e).__name__}"}
    d.update(dom=dom, bulk=bulk, internal=internal, subject=env["subject"][:42])
    return d


files = sorted(Path("corpus").glob("*.eml"))
with ThreadPoolExecutor(max_workers=8) as ex:
    rows = list(ex.map(diagnose, files))

print(f"\nAnalyzed {len(rows)} emails (body-aware)\n")
print("COUNTERPARTY:", dict(Counter(r["counterparty"] for r in rows)))
print("PURPOSE:     ", dict(Counter(r["purpose"] for r in rows)))

clients = [r for r in rows if r["counterparty"] == "CLIENT"]
print(f"\n--- CLIENTS found: {len(clients)} (the revenue signal) ---")
for r in sorted(clients, key=lambda x: -x["urgency"]):
    print(f"  u{r['urgency']:>3} {r['purpose']:<28} {r['dom']:<22} {r['subject']}")

print("\n--- validation: specific domains (body should override domain) ---")
for needle in ("amadeus", "vision", "cortico", "spandex", "festool", "toconline"):
    for r in rows:
        if needle in r["dom"]:
            print(f"  {needle:<9} -> {r['counterparty']:<8} {r['purpose']:<28} u{r['urgency']:<3} {r['subject']}")
            break

# header-bulk vs LLM publicity (does cheap header bulk-flag stay high-precision at scale?)
hb = sum(1 for r in rows if r["bulk"])
lp = sum(1 for r in rows if r["counterparty"] == "BULK")
both = sum(1 for r in rows if r["bulk"] and r["counterparty"] == "BULK")
print(f"\nheader-bulk={hb}  LLM-BULK={lp}  overlap={both}  "
      f"(header-bulk not called BULK by LLM: {hb - both})")
