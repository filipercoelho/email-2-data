"""Command-line entry point. Thin orchestration; logic lives in the modules.

  email2data fetch     M0: read-only IMAP -> corpus/*.eml
  email2data triage    Phase 2/3: Tier-0 signals (offline bulk-IGNORE) -> Tier-1 Flash -> results.jsonl
  email2data eval      score results.jsonl (counterparty/priority) vs labels/worksheet.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

from .config import ConfigError, load_settings, paths
from .schema import COUNTERPARTY, HIGH_VALUE_COUNTERPARTIES


def _load_settings(args: argparse.Namespace) -> dict[str, Any]:
    settings = load_settings(args.settings)
    settings["__settings_path__"] = str(Path(args.settings).resolve())
    return settings


def cmd_fetch(args: argparse.Namespace) -> int:
    from . import fetch

    settings = _load_settings(args)
    counts = fetch.fetch_all(settings)
    for acc, n in counts.items():
        print(f"  {acc}: {n} messages cached")
    print(f"Done. {sum(counts.values())} emails in corpus.")
    return 0


_PRI_ORDER = {"HIGH": 0, "NEEDS_REVIEW": 1, "MEDIUM": 2, "LOW": 3, "IGNORE": 4}


def cmd_triage(args: argparse.Namespace) -> int:
    from . import cascade

    settings = _load_settings(args)
    store = cascade.build_store(settings)
    try:
        counts = cascade.triage_corpus(settings, store)
    finally:
        store.close()
    p = paths(settings, settings["__settings_path__"])
    rows = [json.loads(x) for x in (p["out_dir"] / "results.jsonl").read_text().splitlines() if x]
    rows.sort(key=lambda r: (_PRI_ORDER.get(r.get("priority"), 9), -r.get("urgency", 0)))
    print(f"\n{'URG':>3} {'PRIORITY':<11} {'COUNTERPARTY':<10} {'PURPOSE':<28} {'TIER':<6} SUBJECT")
    print("-" * 100)
    for r in rows:
        tier = "T0" if r.get("decided_by", "").startswith("tier0") else "T1"
        print(f"{r.get('urgency', 0):>3} {r.get('priority', ''):<11} {r.get('counterparty', ''):<10} "
              f"{r.get('purpose', ''):<28} {tier:<6} {(r.get('subject') or '')[:34]}")
    print(f"\n{counts['corpus']} emails: {counts['offline']} offline (Tier-0, 0 tokens), "
          f"{counts['llm']} via LLM (Tier-1)"
          + (f", {counts['failed']} FAILED" if counts["failed"] else ""))
    return 0


def _read_labels(path: Path) -> dict[str, dict[str, str]]:
    labels: dict[str, dict[str, str]] = {}
    with path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(r for r in fh if r.strip() and not r.lstrip().startswith("#")):
            mid = (row.get("message_id") or "").strip()
            cp = (row.get("counterparty") or "").strip()
            if not mid:
                continue
            if cp not in COUNTERPARTY:
                print(f"  warning: skipping label with invalid counterparty {cp!r} ({mid})", file=sys.stderr)
                continue
            labels[mid] = {"counterparty": cp, "priority": (row.get("priority") or "").strip()}
    return labels


def cmd_eval(args: argparse.Namespace) -> int:
    settings = _load_settings(args)
    p = paths(settings, settings["__settings_path__"])
    base = Path(settings["__settings_path__"]).parents[1]
    results_path = p["out_dir"] / "results.jsonl"
    labels_path = next((base / "labels" / n for n in ("worksheet.csv", "labels.csv")
                        if (base / "labels" / n).exists()), None)
    if not results_path.exists():
        print("No out/results.jsonl — run `email2data triage` first.", file=sys.stderr)
        return 1
    if labels_path is None:
        print("No labels/worksheet.csv or labels/labels.csv.", file=sys.stderr)
        return 1

    results = {r["message_id"]: r for r in (json.loads(x) for x in results_path.read_text().splitlines() if x)}
    labels = _read_labels(labels_path)
    matched = sorted(set(results) & set(labels))
    only_labels = sorted(set(labels) - set(results))
    print(f"\nLabels: {labels_path.name} | matched {len(matched)} | "
          f"labels w/o result: {len(only_labels)} | results w/o label: {len(set(results) - set(labels))}")
    if not matched:
        print("Nothing to score.", file=sys.stderr)
        return 1

    cp_ok = pr_ok = 0
    hv_total = hv_recalled = binned = 0
    confusions: dict[tuple[str, str], int] = {}
    for mid in matched:
        r, lab = results[mid], labels[mid]
        if r["counterparty"] == lab["counterparty"]:
            cp_ok += 1
        else:
            confusions[(r["counterparty"], lab["counterparty"])] = confusions.get((r["counterparty"], lab["counterparty"]), 0) + 1
        if lab.get("priority") and r["priority"] == lab["priority"]:
            pr_ok += 1
        if lab["counterparty"] in HIGH_VALUE_COUNTERPARTIES:
            hv_total += 1
            if r["counterparty"] in HIGH_VALUE_COUNTERPARTIES:
                hv_recalled += 1
            if r["priority"] == "IGNORE":
                binned += 1
    n = len(matched)
    print(f"\ncounterparty accuracy : {cp_ok}/{n} = {cp_ok/n:.0%}")
    print(f"priority accuracy     : {pr_ok}/{n} = {pr_ok/n:.0%}")
    if hv_total:
        print(f"CLIENT/LEAD recall    : {hv_recalled}/{hv_total} = {hv_recalled/hv_total:.0%}")
        print(f"real-clients-binned   : {binned}  (must be 0)")
    if confusions:
        print("confusions (model -> truth):")
        for (m, t), c in sorted(confusions.items(), key=lambda x: -x[1]):
            print(f"    {m:9} -> {t:9} x{c}")
    return 0


def cmd_crm(args: argparse.Namespace) -> int:
    """Build the CRM PoC from the corpus + existing verdicts (deterministic, no LLM)."""
    from . import crm
    from .envelope import parse_eml

    settings = _load_settings(args)
    p = paths(settings, settings["__settings_path__"])
    results_path = p["out_dir"] / "results.jsonl"
    if not results_path.exists():
        print("No out/results.jsonl — run `email2data triage` first.", file=sys.stderr)
        return 1
    verdicts = {r["message_id"]: r for r in (json.loads(x) for x in results_path.read_text().splitlines() if x)}

    db = p["out_dir"] / "crm.db"
    if db.exists():
        db.unlink()  # rebuild clean — contact rollups are cumulative
    store = crm.CrmStore(db).connect()
    recorded = skipped = 0
    for eml in sorted(p["corpus_dir"].glob("*.eml")):
        try:
            env = parse_eml(eml.read_bytes())
        except Exception:  # noqa: BLE001 — isolate per-email parse failures
            skipped += 1
            continue
        v = verdicts.get(env["message_id"])
        if not v:
            skipped += 1
            continue
        store.record(env, v)
        recorded += 1

    rollup = store.top_contacts(limit=10_000, external_only=False)
    (p["out_dir"] / "contacts.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rollup), encoding="utf-8")
    c = store.counts()
    print(f"\nRecorded {recorded} interactions ({skipped} skipped: parse fail or no verdict).")
    print(f"Contacts: {c['contacts']} ({c['external']} external) | Interactions: {c['interactions']}")
    print("\nTop external contacts (by volume):")
    print(f"  {'NAME':<20} {'EMAIL':<32} {'CPARTY':<9} {'MSG':>3} {'F/T/C':>7} {'LAST SEEN':<11} LAST PURPOSE")
    print("  " + "-" * 108)
    for r in store.top_contacts(limit=15, external_only=True):
        ftc = f"{r['from_count']}/{r['to_count']}/{r['cc_count']}"
        print(f"  {(r['display_name'] or '')[:19]:<20} {r['email'][:31]:<32} {(r['last_counterparty'] or ''):<9} "
              f"{r['msg_count']:>3} {ftc:>7} {(r['last_seen'] or '')[:10]:<11} {r['last_purpose'] or ''}")
    store.close()
    print(f"\nFull rollup -> {p['out_dir'] / 'contacts.jsonl'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="email2data", description="Read-only email triage for Lindo inboxes.")
    parser.add_argument("--settings", default="config/settings.json", help="path to settings.json")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fetch", help="M0: read-only IMAP -> corpus/*.eml").set_defaults(fn=cmd_fetch)
    sub.add_parser("triage", help="Tier-0 signals -> Tier-1 Flash -> out/results.jsonl").set_defaults(fn=cmd_triage)
    sub.add_parser("eval", help="score results.jsonl vs labels").set_defaults(fn=cmd_eval)
    sub.add_parser("crm", help="build CRM contacts/interactions from corpus + verdicts (no LLM)").set_defaults(fn=cmd_crm)
    args = parser.parse_args(argv)
    try:
        return args.fn(args)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
