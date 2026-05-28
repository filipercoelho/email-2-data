"""Command-line entry point. Thin orchestration only — logic lives in the modules."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

from .config import ConfigError, load_settings, paths
from .schema import TYPES

JOB_TYPES = {"CLIENT_JOB_REQUEST", "QUOTE_FOLLOWUP"}


def _load_settings(args: argparse.Namespace) -> dict[str, Any]:
    settings = load_settings(args.settings)
    settings["__settings_path__"] = str(Path(args.settings).resolve())
    return settings


def cmd_fetch(args: argparse.Namespace) -> int:
    from . import fetch

    settings = _load_settings(args)
    counts = fetch.fetch_all(settings)
    total = sum(counts.values())
    for acc, n in counts.items():
        print(f"  {acc}: {n} messages cached")
    print(f"Done. {total} emails in corpus.")
    return 0


def _print_results_table(rows: list[dict[str, Any]]) -> None:
    rows = sorted(rows, key=lambda r: r.get("urgency", 0), reverse=True)
    print(f"\n{'URG':>3}  {'PRIORITY':<12} {'TYPE':<20} SUBJECT")
    print("-" * 78)
    for r in rows:
        subj = (r.get("subject") or "")[:40]
        print(f"{r.get('urgency', 0):>3}  {r.get('priority', ''):<12} {r.get('type', ''):<20} {subj}")


def cmd_classify(args: argparse.Namespace) -> int:
    from . import classifier

    settings = _load_settings(args)
    counts = classifier.classify_corpus(settings)
    p = paths(settings, settings["__settings_path__"])
    rows = [json.loads(line) for line in (p["out_dir"] / "results.jsonl").read_text().splitlines() if line]
    _print_results_table(rows)
    print(
        f"\nClassified {counts['classified']}/{counts['corpus']} emails"
        + (f" — {counts['failed']} FAILED (see audit log)" if counts["failed"] else "")
    )
    return 0


def _read_labels(path: Path) -> dict[str, dict[str, str]]:
    labels: dict[str, dict[str, str]] = {}
    with path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            mid = (row.get("message_id") or "").strip()
            typ = (row.get("type") or "").strip()
            if not mid:
                continue
            if typ not in TYPES:
                print(f"  warning: skipping label with invalid type {typ!r} ({mid})", file=sys.stderr)
                continue
            labels[mid] = {"type": typ, "priority": (row.get("priority") or "").strip()}
    return labels


def cmd_eval(args: argparse.Namespace) -> int:
    settings = _load_settings(args)
    p = paths(settings, settings["__settings_path__"])
    results_path = p["out_dir"] / "results.jsonl"
    labels_path = Path(settings["__settings_path__"]).parents[1] / "labels" / "labels.csv"

    if not results_path.exists():
        print("No out/results.jsonl — run `email2data classify` first.", file=sys.stderr)
        return 1
    if not labels_path.exists():
        print("No labels/labels.csv — copy labels.example.csv and label ~40 emails.", file=sys.stderr)
        return 1

    results = {r["message_id"]: r for r in (json.loads(x) for x in results_path.read_text().splitlines() if x)}
    labels = _read_labels(labels_path)

    # Loud join (red-team B2): never silently inner-join.
    only_labels = sorted(set(labels) - set(results))
    only_results = sorted(set(results) - set(labels))
    matched = sorted(set(labels) & set(results))

    print(f"\nMatched {len(matched)} | labels w/o result: {len(only_labels)} | results w/o label: {len(only_results)}")
    if only_labels:
        print("  ! labels with NO matching result (check message_id copy):")
        for m in only_labels[:10]:
            print(f"      {m}")
    if not matched:
        print("Nothing to score.", file=sys.stderr)
        return 1

    type_correct = 0
    job_total = job_recalled = 0
    binned_clients = 0  # model said IGNORE but human says it's a real client -> the costly error
    needs_review = 0
    for mid in matched:
        r, lab = results[mid], labels[mid]
        if r["type"] == lab["type"]:
            type_correct += 1
        if lab["type"] in JOB_TYPES:
            job_total += 1
            if r["type"] in JOB_TYPES:
                job_recalled += 1
            if r["priority"] == "IGNORE":
                binned_clients += 1
        if r["priority"] == "NEEDS_REVIEW":
            needs_review += 1

    n = len(matched)
    print(f"\nType accuracy:            {type_correct}/{n} = {type_correct / n:.0%}")
    if job_total:
        print(f"CLIENT_JOB recall:        {job_recalled}/{job_total} = {job_recalled / job_total:.0%}")
    print(f"Real clients binned (=0!): {binned_clients}")
    print(f"NEEDS_REVIEW volume:      {needs_review}/{n} = {needs_review / n:.0%}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="email2data", description="Read-only email triage for Lindo inboxes.")
    parser.add_argument("--settings", default="config/settings.json", help="path to settings.json")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fetch", help="M0: read-only IMAP -> corpus/*.eml").set_defaults(fn=cmd_fetch)
    sub.add_parser("classify", help="M1: playbook + Claude -> out/results.jsonl").set_defaults(fn=cmd_classify)
    sub.add_parser("eval", help="score results.jsonl against labels/labels.csv").set_defaults(fn=cmd_eval)

    args = parser.parse_args(argv)
    try:
        return args.fn(args)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
