"""SPIKE 3 — THROWAWAY. Does the model return evidence quotes that are LITERALLY present?

The premise the whole evidence-highlighting design rests on, and the one thing the corpus
measurements could NOT answer: probes 1 and 2 measured whether the extracted VALUE is literal.
This measures whether a returned QUOTE is literal — a different quantity, and the one that decides
whether items 2/3/6 of the design exist at all.

Deliberately shaped as the LOCATE-ONLY pass the design proposes (given the already-extracted values,
find the sentence that supports each) — NOT a re-classification. So a good result here is direct
evidence for that exact mechanism, and it never touches the triage prompt.

Writes nothing outside this scratchpad. Reads corpus/ and out/results.jsonl only.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from email2data import classifier, llm  # noqa: E402
from email2data.config import load_settings  # noqa: E402
from email2data.envelope import clean_email_body, parse_eml  # noqa: E402
from email2data.identity import safe_filename  # noqa: E402
from probe_match import fold, split_quote  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "corpus"
N = 20

# The semantic fields — the ones search cannot reach, so the ones the locate pass must earn.
LOCATE_KEYS = ["money", "deadline", "product_or_service", "action_requested", "client_name"]

SYSTEM = """És um localizador de evidência. Recebes o corpo de um email e uma lista de valores que
já foram extraídos desse email por outro sistema.

Para CADA valor, devolves a frase EXACTA do corpo do email que justifica esse valor.

REGRAS ABSOLUTAS:
1. A citação tem de ser copiada CARÁCTER A CARÁCTER do corpo do email. Não corrijas ortografia,
   não normalizes espaços, não juntes linhas, não uses reticências, não traduzas.
2. Copia a linha tal como aparece, incluindo a quebra de linha se a frase estiver partida.
3. Se o valor NÃO estiver justificado por nenhuma frase do corpo (por exemplo veio de um anexo,
   ou foi inferido), devolve null. Devolver null é a resposta CORRECTA nesse caso.
4. NUNCA inventes uma citação. Uma citação errada é pior do que nenhuma."""

SCHEMA = {
    "type": "object",
    "properties": {k: {"type": "string", "nullable": True} for k in LOCATE_KEYS},
}


def pick(n: int) -> list[dict]:
    """Deterministic spread across the corpus, biased to messages carrying the hard fields."""
    rows = []
    for line in (ROOT / "out" / "results.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        ents = r.get("entities") or {}
        have = [k for k in LOCATE_KEYS if ents.get(k)]
        if len(have) < 2:
            continue
        if not (CORPUS / safe_filename(r.get("message_id") or "")).exists():
            continue
        rows.append(r)
    rows.sort(key=lambda r: r.get("message_id") or "")
    step = max(1, len(rows) // n)
    return rows[::step][:n]


def main() -> None:
    settings = load_settings(ROOT / "config" / "settings.json")
    settings["__settings_path__"] = str(ROOT / "config" / "settings.json")
    cfg = settings["llm"]
    client = classifier.make_client(settings)

    sample = pick(N)
    print(f"sampled {len(sample)} messages (>=2 semantic entities, .eml present)\n")

    tot = Counter()
    per_key = {k: Counter() for k in LOCATE_KEYS}
    rows_out = []

    for i, r in enumerate(sample, 1):
        mid = r["message_id"]
        env = parse_eml((CORPUS / safe_filename(mid)).read_bytes())
        body = env.get("body_text") or ""
        subject = env.get("subject") or ""
        visible = split_quote(clean_email_body(body)[:3000])[:2000]
        ents = r.get("entities") or {}
        asked = {k: ents[k] for k in LOCATE_KEYS if ents.get(k)}

        user = (f"ASSUNTO: {subject}\n\nVALORES JÁ EXTRAÍDOS (localiza cada um):\n"
                + "\n".join(f"  {k} = {v}" for k, v in asked.items())
                + f"\n\n--- CORPO DO EMAIL ---\n{body[:12000]}")
        try:
            got = llm.call(client, cfg, SYSTEM, user, schema=SCHEMA, temperature=0.0)
        except Exception as exc:  # noqa: BLE001
            print(f"{i:>3}. LLM FAILED {type(exc).__name__}: {exc}")
            tot["call_failed"] += 1
            continue

        line = []
        for k, val in asked.items():
            q = (got or {}).get(k)
            tot["asked"] += 1
            per_key[k]["asked"] += 1
            if not q or not str(q).strip():
                tot["null"] += 1
                per_key[k]["null"] += 1
                line.append(f"{k}=NULL")
                continue
            q = str(q)
            in_body = q in body
            in_body_f = fold(q) in fold(body)
            in_vis = q in visible
            in_vis_f = fold(q) in fold(visible)
            val_in_q = fold(str(val)) in fold(q)
            uniq = body.count(q) == 1 if in_body else None

            tot["quoted"] += 1
            per_key[k]["quoted"] += 1
            for name, cond in (("lit_body", in_body), ("fold_body", in_body_f),
                               ("lit_vis", in_vis), ("fold_vis", in_vis_f),
                               ("val_in_quote", val_in_q), ("unique", bool(uniq))):
                if cond:
                    tot[name] += 1
                    per_key[k][name] += 1
            line.append(f"{k}={'L' if in_body else ('f' if in_body_f else 'X')}"
                        f"{'V' if in_vis else '-'}{'=' if val_in_q else '~'}")
            rows_out.append({"mid": mid, "key": k, "value": val, "quote": q,
                             "lit_body": in_body, "fold_body": in_body_f, "lit_vis": in_vis,
                             "val_in_quote": val_in_q})
        print(f"{i:>3}. {' '.join(line)}")

    a, q = tot["asked"], tot["quoted"]
    print(f"\n{'=' * 66}\nvalues asked about : {a}")
    print(f"model returned null: {tot['null']}  ({100 * tot['null'] / a:.0f}%)  [honest abstention]")
    print(f"model gave a quote : {q}  ({100 * q / a:.0f}%)")
    if q:
        print(f"\nof the {q} quotes returned:")
        for name, label in (("lit_body", "LITERAL substring of body_text"),
                            ("fold_body", "matches body after folding (case/accent/space)"),
                            ("lit_vis", "LITERAL in the RENDERED visible region"),
                            ("fold_vis", "folded match in the visible region"),
                            ("val_in_quote", "the extracted VALUE appears inside its own quote"),
                            ("unique", "occurs exactly once in the body")):
            print(f"  {label:<48} {tot[name]:>3}  {100 * tot[name] / q:3.0f}%")
    print(f"\n{'key':<20}{'asked':>6}{'null':>6}{'lit_body':>10}{'lit_vis':>9}{'val_in_q':>10}")
    for k in LOCATE_KEYS:
        c = per_key[k]
        if c["asked"]:
            print(f"{k:<20}{c['asked']:>6}{c['null']:>6}{c['lit_body']:>10}{c['lit_vis']:>9}{c['val_in_quote']:>10}")

    out = Path(__file__).parent / "spike_quotes_raw.json"
    out.write_text(json.dumps(rows_out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nraw pairs -> {out}")


if __name__ == "__main__":
    main()
