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

from .config import ConfigError, load_dotenv, load_settings, paths
from .schema import COUNTERPARTY, HIGH_VALUE_COUNTERPARTIES


def _load_settings(args: argparse.Namespace) -> dict[str, Any]:
    settings = load_settings(args.settings)
    settings["__settings_path__"] = str(Path(args.settings).resolve())
    return settings


def cmd_fetch(args: argparse.Namespace) -> int:
    from . import fetch

    settings = _load_settings(args)
    counts = fetch.fetch_all(settings, full=getattr(args, "full", False))
    for acc, n in counts.items():
        print(f"  {acc}: {n} messages cached")
    mode = "full rebuild" if getattr(args, "full", False) else "incremental (since last retrieve)"
    print(f"Done [{mode}]. {sum(counts.values())} emails in corpus.")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    """Pull only new mail, then classify only the new emails — fetch + triage in one shot."""
    from . import sync

    settings = _load_settings(args)
    c = sync.run_sync(settings, full=getattr(args, "full", False))
    print(f"  fetched:  {c['fetched']} in corpus")
    print(f"  triaged:  {c['triaged_new']} new ({c['offline']} offline, {c['llm']} LLM), "
          f"{c['triaged_skipped']} skipped"
          + (f", {c['failed']} FAILED" if c["failed"] else ""))
    return 0


_PRI_ORDER = {"HIGH": 0, "NEEDS_REVIEW": 1, "MEDIUM": 2, "LOW": 3, "IGNORE": 4}


def cmd_triage(args: argparse.Namespace) -> int:
    from . import cascade

    settings = _load_settings(args)
    store = cascade.build_store(settings)
    try:
        counts = cascade.triage_corpus(settings, store, full=getattr(args, "full", False))
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
    mode = "full rebuild" if getattr(args, "full", False) else "incremental"
    print(f"\n{counts['corpus']} in corpus [{mode}]: {counts.get('new', counts['corpus'])} processed "
          f"({counts['offline']} offline Tier-0/0 tokens, {counts['llm']} via LLM Tier-1), "
          f"{counts.get('skipped', 0)} already done"
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

    settings = _load_settings(args)
    p = paths(settings, settings["__settings_path__"])
    if not (p["out_dir"] / "results.jsonl").exists():
        print("No out/results.jsonl — run `email2data triage` first.", file=sys.stderr)
        return 1
    counts = crm.build_crm(settings)  # shared with `sync` so the relations DB is never stale
    print(f"\nRecorded {counts['recorded']} interactions "
          f"({counts['skipped']} skipped: parse fail or no verdict).")
    print(f"Contacts: {counts['contacts']} ({counts['external']} external) | "
          f"Interactions: {counts['interactions']}")
    store = crm.CrmStore(p["out_dir"] / "crm.db").connect()  # reopen the fresh DB for the rollup table
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


def _write_labelsheet(path: Path, specs: list[dict]) -> None:
    from . import jobspec as js
    cols = ["message_id", "subject"] + js.MUST + js.SHOULD
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for s in specs:
            w.writerow([s["message_id"], s["subject"]] + [""] * (len(js.MUST) + len(js.SHOULD)))


def _read_spec_labels(path: Path) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    with path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            mid = (row.get("message_id") or "").strip()
            if mid:
                out[mid] = {k: (v or "") for k, v in row.items() if k not in ("message_id", "subject")}
    return out


def cmd_jobspec(args: argparse.Namespace) -> int:
    """Phase A/B: build JobSpecs + Gate-1 readiness for job-relevant emails (LEAD / PO / estimate).

    Full rebuild (every job email); the per-email pipeline now lives in ``specbuild`` so the webapp
    can run the same extraction incrementally after each sync."""
    from . import specbuild

    settings = _load_settings(args)
    p = paths(settings, settings["__settings_path__"])
    results_path = p["out_dir"] / "results.jsonl"
    if not results_path.exists():
        print("No out/results.jsonl — run `email2data triage` first.", file=sys.stderr)
        return 1
    counts = specbuild.rebuild_jobspecs(
        settings, draft=args.draft, reply=args.reply, incremental=False,
        log=lambda m: print(f"  {m}", file=sys.stderr))

    specs = [json.loads(x) for x in (p["out_dir"] / "jobspecs.jsonl").read_text().splitlines() if x]
    _write_labelsheet(p["out_dir"] / "spec_labelsheet.csv", specs)

    tags = (f" · drafted {counts['drafted']}" if args.draft else "") + (" · replies" if args.reply else "")
    print(f"\n{counts['total']} job-relevant emails (LEAD/PO/estimate){tags}")
    print(f"  {'EST':<3} {'COV':>4} {'ATT':>3}  {'MISSING must-haves':<38} SUBJECT")
    print("  " + "-" * 92)
    for s in sorted(specs, key=lambda x: -x["readiness"]["coverage"]):
        rd = s["readiness"]
        print(f"  {'YES' if rd['estimable'] else '–':<3} {int(rd['coverage']*100):>3}% "
              f"{'att' if s['has_attachment'] else '–':>3}  {','.join(rd['missing'])[:37]:<38} {(s['subject'] or '')[:30]}")
    nattach = sum(1 for s in specs if s["readiness"]["attachment_to_review"])
    print(f"\n  {nattach}/{len(specs)} need the attachment reviewed to complete the spec.")
    print("  -> out/jobspecs.jsonl · gold-set scaffold -> out/spec_labelsheet.csv")

    if args.score:
        from . import jobspec as js
        base = Path(settings["__settings_path__"]).parents[1]
        lp = base / "labels" / "spec_labels.csv"
        if lp.exists():
            print("\n  draft-vs-label agreement:", json.dumps(js.score_drafts(specs, _read_spec_labels(lp))))
        else:
            print("\n  (no labels/spec_labels.csv — fill out/spec_labelsheet.csv, move to labels/, re-run --score)")
    return 0


def cmd_relations(args: argparse.Namespace) -> int:
    """Show emails related to <message_id>: thread siblings, same-contact history, entity cross-refs."""
    from . import crm

    settings = _load_settings(args)
    p = paths(settings, settings["__settings_path__"])
    db = p["out_dir"] / "crm.db"
    if not db.exists():
        print("No out/crm.db — run `email2data crm` first.", file=sys.stderr)
        return 1

    store = crm.CrmStore(db).connect()
    try:
        result = store.related(args.message_id)
    finally:
        store.close()

    if not any(result.values()):
        print(f"No relations found for {args.message_id!r}.")
        return 0

    labels = {
        "thread":     "Thread siblings",
        "by_contact": "Same contact (across all threads)",
        "by_entity":  "Entity cross-references",
    }
    for section, rows in result.items():
        if not rows:
            continue
        print(f"\n{labels[section]} ({len(rows)}):")
        header = f"  {'DATE':<12} {'PRIORITY':<11} {'FROM':<34} {'PURPOSE':<28}"
        if section == "by_entity":
            header += " MATCHED_ENTITY"
        print(header)
        print("  " + "-" * (92 + (15 if section == "by_entity" else 0)))
        for r in rows:
            entity_col = f" {r.get('_matched_entity', '')}" if section == "by_entity" else ""
            print(
                f"  {(r.get('date') or '')[:10]:<12}"
                f" {r.get('priority', ''):<11}"
                f" {r.get('from_email', '')[:33]:<34}"
                f" {r.get('purpose', '')[:27]:<28}"
                f"{entity_col}"
            )
    return 0


def _open_project_ctx(settings: dict[str, Any]):
    """Open the stores a project command needs: Workspace (+ProjectStore), jobspecs, optional CRM."""
    from . import crm, project as projmod, webapp
    from .workspace import Workspace

    p = paths(settings, settings["__settings_path__"])
    ws = Workspace(p["out_dir"] / "workspace.db").connect()
    store = projmod.ProjectStore(ws._conn)
    jobspecs = webapp._load_jobspecs(p["out_dir"])
    crm_db = p["out_dir"] / "crm.db"
    crm_store = crm.CrmStore(crm_db).connect() if crm_db.exists() else None
    return p, ws, store, jobspecs, crm_store


def cmd_project(args: argparse.Namespace) -> int:
    """Manage cross-thread projects: new / attach / list / show / export."""
    from . import export as exportmod, jobspec as js, project as projmod

    settings = _load_settings(args)
    p, ws, store, jobspecs, crm_store = _open_project_ctx(settings)
    try:
        if args.action == "new":
            client_name = args.client
            if args.from_message and not client_name:
                j = jobspecs.get(args.from_message)
                client_name = (j or {}).get("counterparty") or None
            pid = store.create(args.title, client_email=args.client, client_name=client_name)
            attached = None
            if args.from_message:
                attached = projmod.resolve_thread_root(crm_store, args.from_message)
                store.attach_thread(pid, attached)
                projmod.seed_items_from(store, ws, jobspecs, pid, args.from_message)
            elif args.from_thread:
                attached = args.from_thread
                store.attach_thread(pid, attached)
            print(f"Created {pid}  ({args.title!r})" + (f"  + thread {attached}" if attached else ""))
            return 0

        if args.action == "attach":
            if store.get(args.project_id) is None:
                print(f"No such project {args.project_id}", file=sys.stderr)
                return 1
            root = projmod.resolve_thread_root(crm_store, args.ref)
            store.attach_thread(args.project_id, root)
            seeded = projmod.seed_items_from(store, ws, jobspecs, args.project_id, args.ref)
            print(f"Attached thread {root} to {args.project_id}" + ("  (items seeded)" if seeded else ""))
            return 0

        if args.action == "detach":
            if store.get(args.project_id) is None:
                print(f"No such project {args.project_id}", file=sys.stderr)
                return 1
            root = projmod.resolve_thread_root(crm_store, args.ref)
            store.detach_thread(args.project_id, root)
            print(f"Detached thread {root} from {args.project_id}")
            return 0

        if args.action == "delete":
            if not store.delete(args.project_id):
                print(f"No such project {args.project_id}", file=sys.stderr)
                return 1
            print(f"Deleted {args.project_id}")
            return 0

        if args.action == "list":
            projects = store.list(include_archived=args.all)
            if not projects:
                print("No projects yet. Create one:  email2data project new --title ... --from-message <mid>")
                return 0
            print(f"\n{'ID':<8} {'STAGE':<10} {'THREADS':>7} {'EXTERNAL':<14} TITLE")
            print("-" * 80)
            for pr in projects:
                nthreads = len(store.threads_for(pr["project_id"]))
                print(f"{pr['project_id']:<8} {pr['stage']:<10} {nthreads:>7} "
                      f"{(pr['external_id'] or '–'):<14} {(pr['title'] or '')[:36]}")
            return 0

        if args.action == "show":
            if store.get(args.project_id) is None:
                print(f"No such project {args.project_id}", file=sys.stderr)
                return 1
            proj = store.get(args.project_id)
            spec, rd, prov, conflicts = projmod.build_canonical(
                store, ws, jobspecs, args.project_id, crm_store)
            print(f"\n{proj['project_id']}  {proj['title']!r}")
            print(f"  stage={proj['stage']}  client={proj.get('client_name') or '–'}  "
                  f"external={proj.get('external_id') or '–'}")
            roots = store.threads_for(args.project_id)
            print(f"  threads ({len(roots)}): {', '.join(roots) or '–'}")
            dangling = projmod.dangling_threads(store, args.project_id, crm_store)
            if dangling:
                print(f"  ⚠ dangling (no CRM match — rebuild CRM or detach): {', '.join(dangling)}")
            print(f"  estimable={rd['estimable']}  coverage={int(rd['coverage']*100)}%  "
                  f"missing={','.join(rd['missing']) or '–'}")
            print("  job fields:")
            for k in js.JOB_KEYS:
                fld = spec.job_fields.get(k)
                if fld and fld.value:
                    src = prov.get(k, "")
                    print(f"    {k:<22} {fld.value}" + (f"   [{src}]" if src else ""))
            for i, item in enumerate(spec.items):
                vals = {k: item[k].value for k in js.ITEM_KEYS if item.get(k) and item[k].value}
                if vals:
                    print(f"  item #{i}: " + "; ".join(f"{k}={v}" for k, v in vals.items()))
            if conflicts:
                print("  ⚠ conflicts (equal-authority sources disagree):")
                for k, cands in conflicts.items():
                    print(f"    {k}: " + " | ".join(
                        f"{c['value']} ({c['source']})" for c in cands))
            return 0

        if args.action == "export":
            if store.get(args.project_id) is None:
                print(f"No such project {args.project_id}", file=sys.stderr)
                return 1
            if args.adapter == "materials-costing":
                try:
                    adapter = exportmod.MaterialsCostingAdapter.from_settings(settings)
                except ValueError as exc:
                    print(f"Cannot build materials-costing adapter: {exc}", file=sys.stderr)
                    return 2
            else:
                adapter = exportmod.JsonFileAdapter(p["out_dir"])
            result = exportmod.export_project(
                store, ws, jobspecs, adapter, args.project_id, crm_store=crm_store, force=args.force)
            if result.ok:
                print(f"Exported {args.project_id} -> {result.external_id}  ({result.detail})")
                return 0
            print(f"Export failed: {result.detail}", file=sys.stderr)
            return 1

        print(f"Unknown project action: {args.action}", file=sys.stderr)
        return 2
    finally:
        ws.close()
        if crm_store is not None:
            crm_store.close()


def _free_port(preferred: int) -> int:
    """Return *preferred* if it is free, otherwise let the OS pick a free port."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _resolve_serve_port(preferred: int, host: str) -> tuple[int | None, str | None]:
    """Decide the serve port. Returns ``(port, note)`` — or ``(None, error)`` when we must NOT rebind.

    On localhost a busy port silently falls back to an OS-picked one (dev convenience). In CONTAINER
    mode (``--host 0.0.0.0``/``::``) the published port is FIXED by compose (``8042:8042``); silently
    serving on a different port would leave the published port with no listener (connection refused),
    so a busy port is fatal — fail loudly instead of rebinding to a port nothing maps to."""
    port = _free_port(preferred)
    if port == preferred:
        return port, None
    if host in ("0.0.0.0", "::"):
        return None, (f"Port {preferred} is unavailable and --host {host} (container mode) needs the "
                      f"published port — refusing to rebind. Free {preferred} or change the mapping.")
    return port, f"Port {preferred} is in use — using {port} instead."


def cmd_intake_bot(args: argparse.Namespace) -> int:
    """Run the conversational-intake Telegram worker (ADR-019/-021): an outbound long-poll worker that
    writes captures via the store seam (never the HTTP API, so 8042 stays closed) and never binds a
    port. Config in settings.json ``intake`` block; the bot token in .env. Ctrl-C to stop."""
    from . import captures as capmod, intake, project as projmod, telegram as tg
    from .config import resolve_secret
    from .workspace import Workspace, WorkspaceVersionError

    settings = _load_settings(args)
    cfg = settings.get("intake", {})
    if not cfg.get("enabled", False):
        print("intake bot is disabled — set intake.enabled=true in config/settings.json",
              file=sys.stderr)
        return 1
    try:
        token = resolve_secret(cfg.get("bot_token_env", "TELEGRAM_BOT_TOKEN"))
        client = tg.TelegramClient(token)
    except (ConfigError, ValueError) as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2

    p = paths(settings, settings["__settings_path__"])
    try:
        # Single-migrator gate (ADR-021): the worker refuses to migrate the precious DB — run
        # `email2data serve` once to upgrade it. Only the webapp/CLI migrates workspace.db.
        ws = Workspace(p["out_dir"] / "workspace.db").connect(migrate=False)
    except WorkspaceVersionError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2
    try:
        captures = capmod.CaptureStore(ws._conn)
        projects = projmod.ProjectStore(ws._conn)
        for entry in cfg.get("allowlist", []):
            uid = entry.get("telegram_user_id") if isinstance(entry, dict) else entry
            captures.allow(
                int(uid),
                display_name=(entry.get("display_name", "") if isinstance(entry, dict) else ""),
                roster_owner=(entry.get("roster_owner", "") if isinstance(entry, dict) else ""),
                added_by="settings")
        bot = intake.IntakeBot(
            client=client, captures=captures, projects=projects, captures_dir=p["captures_dir"],
            admin_chat_id=cfg.get("admin_chat_id"),
            delete_after_scrub=cfg.get("delete_after_scrub", True))
        bot_name = cfg.get("bot_name", "default")
        print(f"intake-bot polling (bot={bot_name}; outbound long-poll only; Ctrl-C to stop)")
        intake.poll_forever(
            client=client, bot=bot, bot_name=bot_name,
            offset_path=p["out_dir"] / "intake_offset.json")
    except KeyboardInterrupt:
        print("\nintake-bot stopped.")
    finally:
        ws.close()
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    """Run the local workspace (the 'confirm one lead' slice). Read-only IMAP; never sends."""
    try:
        import uvicorn
    except ImportError:
        print("Install web deps first:  pip install -e '.[web]'", file=sys.stderr)
        return 1
    from . import webapp

    if args.port == 8000:
        print("Port 8000 is not allowed for this project. Pick another (default 8042).", file=sys.stderr)
        return 2
    host = args.host
    port, note = _resolve_serve_port(args.port, host)
    if port is None:
        print(note, file=sys.stderr)
        return 1
    if note:
        print(note, file=sys.stderr)
    settings = _load_settings(args)
    app = webapp.from_settings(settings)
    shown = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    print(f"workspace → http://{shown}:{port}   (Ctrl-C to stop)")
    uvicorn.run(app, host=host, port=port)
    return 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv()  # secrets come from .env (or a real env var, which wins) — no more `export VAR=...`
    parser = argparse.ArgumentParser(prog="email2data", description="Read-only email triage for Lindo inboxes.")
    parser.add_argument("--settings", default="config/settings.json", help="path to settings.json")
    sub = parser.add_subparsers(dest="cmd", required=True)
    fp = sub.add_parser("fetch", help="M0: read-only IMAP -> corpus/*.eml (incremental since last retrieve)")
    fp.add_argument("--full", action="store_true", help="ignore the UID watermark and re-bootstrap by date")
    fp.set_defaults(fn=cmd_fetch)
    tp = sub.add_parser("triage", help="Tier-0 signals -> Tier-1 Flash -> out/results.jsonl (incremental)")
    tp.add_argument("--full", action="store_true", help="reclassify the whole corpus (overwrite results.jsonl)")
    tp.set_defaults(fn=cmd_triage)
    syp = sub.add_parser("sync", help="fetch new mail + triage new emails in one shot (incremental)")
    syp.add_argument("--full", action="store_true", help="re-bootstrap fetch and reclassify everything")
    syp.set_defaults(fn=cmd_sync)
    sub.add_parser("eval", help="score results.jsonl vs labels").set_defaults(fn=cmd_eval)
    sub.add_parser("crm", help="build CRM contacts/interactions from corpus + verdicts (no LLM)").set_defaults(fn=cmd_crm)
    jp = sub.add_parser("jobspec", help="build JobSpecs + Gate-1 readiness for LEAD/PO/estimate emails")
    jp.add_argument("--draft", action="store_true", help="run the tiered LLM spec draft (Phase B; costs tokens)")
    jp.add_argument("--reply", action="store_true", help="also draft a clarifying reply per job (Phase C; costs tokens)")
    jp.add_argument("--score", action="store_true", help="score drafts vs labels/spec_labels.csv if present")
    jp.set_defaults(fn=cmd_jobspec)
    rp = sub.add_parser("relations", help="show emails related to a message (thread / contact / entity)")
    rp.add_argument("message_id", help="message_id to look up (from results.jsonl)")
    rp.set_defaults(fn=cmd_relations)
    sv = sub.add_parser("serve", help="run the local workspace to confirm leads (localhost; never sends)")
    sv.add_argument("--port", type=int, default=8042)  # NEVER 8000 (user hard rule); 8042 is the agreed default
    sv.add_argument("--host", default="127.0.0.1",
                    help="bind address; use 0.0.0.0 inside a container (the Docker image does this)")
    sv.set_defaults(fn=cmd_serve)

    pp = sub.add_parser("project", help="cross-thread projects: group threads into one canonical spec")
    psub = pp.add_subparsers(dest="action", required=True)
    pnew = psub.add_parser("new", help="create a project (optionally seeded from a lead)")
    pnew.add_argument("--title", required=True)
    pnew.add_argument("--client", help="client email/name")
    pseed = pnew.add_mutually_exclusive_group()
    pseed.add_argument("--from-message", help="seed items + attach the thread of this message_id")
    pseed.add_argument("--from-thread", help="attach this thread_root")
    pat = psub.add_parser("attach", help="attach a thread (by message_id or thread_root) to a project")
    pat.add_argument("project_id")
    pat.add_argument("ref", help="message_id or thread_root")
    pdt = psub.add_parser("detach", help="remove a thread from a project")
    pdt.add_argument("project_id")
    pdt.add_argument("ref", help="message_id or thread_root")
    pdl = psub.add_parser("delete", help="hard-delete a project (mistakes/duplicates; ARCHIVED soft-retires)")
    pdl.add_argument("project_id")
    plist = psub.add_parser("list", help="list projects")
    plist.add_argument("--all", action="store_true", help="include ARCHIVED projects (hidden by default)")
    psh = psub.add_parser("show", help="show a project's merged canonical spec + readiness")
    psh.add_argument("project_id")
    pex = psub.add_parser("export", help="offload a project to an external system")
    pex.add_argument("project_id")
    pex.add_argument("--adapter", choices=["json", "materials-costing"], default="json")
    pex.add_argument("--force", action="store_true", help="export even if not estimable / re-export")
    pp.set_defaults(fn=cmd_project)
    ib = sub.add_parser(
        "intake-bot",
        help="run the conversational-intake Telegram worker (outbound long-poll; never binds a port)")
    ib.set_defaults(fn=cmd_intake_bot)
    args = parser.parse_args(argv)
    try:
        return args.fn(args)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        # A total fetch failure (every account down) surfaces as a tidy line, not a raw traceback.
        # Anything that is NOT a FetchError is re-raised so real bugs keep their stack trace.
        from .fetch import FetchError
        if isinstance(exc, FetchError):
            print(f"Fetch error: {exc}", file=sys.stderr)
            return 1
        raise


if __name__ == "__main__":
    raise SystemExit(main())
