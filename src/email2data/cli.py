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


def _in_container() -> bool:
    """Whether we are running inside the Docker image rather than on the host."""
    return Path("/.dockerenv").exists()


def _local_lan_ip() -> str:
    """This host's LAN address, or '' if it cannot be determined or is meaningless here.

    Used only to WARN that ``mail.base_url`` has drifted (``auth mail-test``). The machine running
    this app is a DHCP Wi-Fi client, so its address can change without anything in the config
    noticing -- and the symptom is a reset link that opens nothing, on the day someone needs it.
    No traffic is sent: connect() on a UDP socket just picks the route.

    **Returns '' inside a container, deliberately.** In Docker this resolves to the bridge address
    (e.g. 172.22.0.2), which is never what ``base_url`` should name -- so the comparison fired on
    every single run, reporting drift that did not exist. A warning that always fires is worse than
    no warning: it teaches the reader to skip the line, and the one time the address really has
    moved they skip that too. The check is only meaningful on the host, so on the container it
    declines to answer instead of answering wrongly.
    """
    import socket

    if _in_container():
        return ""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 9))       # TEST-NET-1, guaranteed unroutable — nothing leaves
        return sock.getsockname()[0]
    except OSError:
        return ""
    finally:
        sock.close()


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


def _resolve_only(settings: dict[str, Any], refs: list[str]) -> set[str]:
    """Expand ``--only`` refs into the set of message_ids to re-extract (ADR-025 §4).

    A ``p-XXXX`` ref expands to every message across that project's attached threads, so
    ``--only p-0002`` re-extracts the whole project rather than making the user paste message ids.
    Anything else is taken as a message_id verbatim. An unknown project_id is fatal: silently
    expanding it to nothing would print a reassuring "kept everything" and re-extract NOTHING."""
    from . import project as projmod

    mids: set[str] = set()
    project_refs = [r for r in refs if r.startswith("p-")]
    if not project_refs:
        return {r for r in refs if r}
    _p, ws, store, _jobspecs, crm_store = _open_project_ctx(settings)
    try:
        for ref in refs:
            if not ref.startswith("p-"):
                mids.add(ref)
                continue
            if store.get(ref) is None:
                raise SystemExit(f"No such project {ref}")
            mids.update(projmod.message_ids_for(store, ref, crm_store))
    finally:
        ws.close()
        if crm_store is not None:
            crm_store.close()
    return mids


def cmd_jobspec(args: argparse.Namespace) -> int:
    """Phase A/B: build JobSpecs + Gate-1 readiness for job-relevant emails (LEAD / PO / estimate).

    Full rebuild (every job email) by default; the per-email pipeline lives in ``specbuild`` so the
    webapp can run the same extraction incrementally after each sync.

    ``--only`` makes it a **scoped re-extract** (ADR-025 §4): only the named messages/projects are
    rebuilt and every other entry is kept byte-for-byte, so fixing one bad extraction costs one LLM
    call per message in that project instead of re-billing the whole corpus. ``--tier`` picks the
    model for this run only (``llm.with_tier``)."""
    from . import specbuild

    settings = _load_settings(args)
    p = paths(settings, settings["__settings_path__"])
    results_path = p["out_dir"] / "results.jsonl"
    if not results_path.exists():
        print("No out/results.jsonl — run `email2data triage` first.", file=sys.stderr)
        return 1
    only = _resolve_only(settings, args.only) if args.only else None
    if only is not None:
        print(f"  scoped re-extract: {len(only)} message(s)"
              + (f" · tier {args.tier}" if args.tier else ""), file=sys.stderr)
    counts = specbuild.rebuild_jobspecs(
        settings, draft=args.draft, reply=args.reply,
        # --only is meaningless with a full rebuild: `incremental` is what preserves everything
        # outside the scope. Without --only the command keeps its historical full-rebuild semantics.
        incremental=only is not None, only=only, tier=args.tier,
        log=lambda m: print(f"  {m}", file=sys.stderr))

    specs = [json.loads(x) for x in (p["out_dir"] / "jobspecs.jsonl").read_text().splitlines() if x]
    _write_labelsheet(p["out_dir"] / "spec_labelsheet.csv", specs)

    tags = (f" · drafted {counts['drafted']}" if args.draft else "") + (" · replies" if args.reply else "")
    if only is not None:
        tags += f" · rebuilt {counts['built']} · kept {counts['kept']}"
    if counts["failed"]:
        tags += f" · FAILED {counts['failed']} (see spec_error / audit.jsonl)"
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
    from . import (capture_resolve, captures as capmod, classifier, intake, project as projmod,
                   telegram as tg)
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
        # Increment 1 (audio): the shared Vertex/Gemini client for transcription (R3), built lazily —
        # any failure (missing ADC, bad config) degrades the bot to "stored, not transcribed", never a
        # crash. Plus the deterministic-resolve inputs (R2 seed): the capture playbook aliases + gazetteer.
        base = Path(settings["__settings_path__"]).parents[1]
        aliases = capture_resolve.load_aliases(base / "config" / "capture_playbook.md")
        gazetteer = capture_resolve.load_gazetteer(base / "config" / "gazetteer.csv")
        llm_cfg = settings.get("llm") or {}
        llm_client = None
        if llm_cfg:
            try:
                llm_client = classifier.make_client(settings)
            except Exception as exc:  # noqa: BLE001 — transcription is best-effort, never a hard dep
                print(f"(transcription disabled — LLM client unavailable: {exc})", file=sys.stderr)
        bot = intake.IntakeBot(
            client=client, captures=captures, projects=projects, captures_dir=p["captures_dir"],
            admin_chat_id=cfg.get("admin_chat_id"),
            delete_after_scrub=cfg.get("delete_after_scrub", True),
            llm_client=llm_client, llm_cfg=llm_cfg,
            resolve_aliases=aliases, resolve_gazetteer=gazetteer)
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


def cmd_scopes(args: argparse.Namespace) -> int:
    """Attribute cached mail to the inbox(es) it reached (ADR-038) -- the Phase A backfill + status.

    Read-only over the corpus; touches only ``out/sync.db``. Idempotent: a second run writes 0 rows.
    """
    from . import scopes, sync as sync_mod

    settings = _load_settings(args)
    store = sync_mod.open_store(settings)
    try:
        if args.action == "backfill":
            res = scopes.backfill(settings, sync=store)
            print(f"  messages scanned    : {res['messages']}")
            print(f"  header       (FACT) : {res['header']}")
            print(f"  participant  (INF)  : {res['participant']}")
            print(f"  unattributed (UNK)  : {res['unattributed']}"
                  f"  -> {scopes.SCOPE_UNATTRIBUTED} (admin-visible)")
            if res["unreadable"]:
                print(f"  unreadable files    : {res['unreadable']}")
            print(f"  scope rows written  : {res['rows']}")
        counts = store.scope_address_counts()
        if not counts:
            print("\n  no attribution recorded yet -- run `email2data scopes backfill`")
            return 0
        by_source = store.scope_source_counts()
        print("\n  attributed messages per inbox:")
        for addr, n in counts.items():
            print(f"    {n:5d}  {addr}")
        print("  evidence: " + ", ".join(f"{k}={v}" for k, v in sorted(by_source.items())))
    finally:
        store.close()
    return 0


def cmd_gazetteer(args: argparse.Namespace) -> int:
    """Manage the ADR-005 priors: see what the gazetteer holds, and recover the CSV that owns it.

    ``config/gazetteer.csv`` is gitignored (it names real clients), so it is the one store input with
    no second copy anywhere -- and ``seed_gazetteer`` REPLACES the table from it, which means losing
    the CSV leaves the priors alive inside ``knowledge.db`` but invisible and uneditable. ``export``
    is the documented way back: the live table is written out in seedable form.

    ``status`` prints counts per counterparty and never the keys themselves -- those are real
    client/supplier domains, and personal data does not belong in terminal scrollback or a log.
    """
    from . import cascade

    settings = _load_settings(args)
    gaz = cascade.gazetteer_csv(settings)
    store = cascade.open_store(settings)
    try:
        n = store.count()
        if args.action == "export":
            if gaz.exists() and not args.force:
                print(f"  {gaz} already exists -- refusing to overwrite it.", file=sys.stderr)
                print("  It is the source of truth; pass --force only to discard it.", file=sys.stderr)
                return 1
            if not n:
                print(f"  the gazetteer is empty -- nothing to export (this would only erase {gaz}).",
                      file=sys.stderr)
                return 1
            written = store.export_gazetteer(gaz)
            print(f"  wrote {written} row(s) -> {gaz}")
            print("  it is the source of truth again: edit it, then re-run `email2data triage`.")
            return 0
        print(f"  source CSV : {gaz}  [{'present' if gaz.exists() else 'MISSING'}]")
        print(f"  store      : {store.db_path}  [{n} row(s)]")
        for cp, c in store.counts_by_counterparty().items():
            print(f"    {c:5d}  {cp}")
        if not gaz.exists() and n:
            print("\n  WARNING: those priors are FROZEN at their last seed and cannot be edited."
                  "\n  Recover the source of truth with `email2data gazetteer export`.")
            return 1
    finally:
        store.close()
    return 0


def cmd_auth(args: argparse.Namespace) -> int:
    """Manage people + credentials (ADR-039). The admin surface for the auth layer.

    Passwords are read from a prompt, never from argv -- an argument would land in shell history and
    in `ps` output for every user on the box.
    """
    import getpass

    from . import auth as authmod, mailer as mailermod, scopes as scopesmod
    from .workspace import Workspace

    settings = _load_settings(args)
    p = paths(settings, settings["__settings_path__"])
    ws = Workspace(p["out_dir"] / "workspace.db").connect()
    auth = authmod.AuthStore(p["out_dir"] / "auth.db").connect()
    try:
        if args.action == "list":
            people = ws.people(include_inactive=True)
            if not people:
                print("  no people yet -- run `email2data auth setup --name <nome>`")
                return 0
            for person in people:
                kind = "admin" if person["is_admin"] else ("utilizador" if person["can_login"]
                                                           else "sem acesso")
                bits = [f"{person['name']:<18} {kind:<12}"]
                if not person["can_login"]:
                    responsible = ws.person_by_id(person["responsible_id"] or "")
                    bits.append(f"responsável: {(responsible or {}).get('name', '?')}")
                if person["can_login"]:
                    bits.append("com palavra-passe" if auth.has_credential(person["person_id"])
                                else "convite pendente")
                    # Said out loud, because "cannot recover" is invisible otherwise (ADR-042) and
                    # the way it is normally discovered is by being locked out.
                    bits.append(person["email"] if person["email"]
                                else "SEM EMAIL (não pode recuperar)")
                if person["scopes"]:
                    bits.append("caixas: " + ", ".join(person["scopes"]))
                if not person["active"]:
                    bits.append("INATIVO")
                print("  " + "  ".join(b for b in bits if b))

            # Cross-store consistency (ADR-039). Identity lives in workspace.db, secrets in auth.db,
            # joined by person_id with NO foreign key -- SQLite cannot enforce one across files. So
            # the two silently drift: restore one from a backup without the other, or delete a person
            # without calling purge_person, and you get credentials belonging to nobody. Reported
            # here rather than in a command someone has to remember to run.
            orphans = auth.known_person_ids() - {p["person_id"] for p in people}
            if orphans:
                print(f"\n  AVISO: {len(orphans)} identidade(s) em auth.db sem pessoa em "
                      f"workspace.db.")
                print("  Credenciais/sessões órfãs — os dois ficheiros divergiram (restauro parcial?).")
                for person_id in sorted(orphans):
                    print(f"    {person_id}")
            return 0

        if args.action == "setup":
            if auth.has_any_credentials():
                print("Já existe pelo menos uma conta. Usa `auth invite` para adicionar pessoas.",
                      file=sys.stderr)
                return 1
            password = getpass.getpass("Palavra-passe: ")
            if len(password) < 8:
                print("A palavra-passe precisa de pelo menos 8 caracteres.", file=sys.stderr)
                return 2
            if password != getpass.getpass("Confirmar: "):
                print("As palavras-passe não coincidem.", file=sys.stderr)
                return 2
            person = ws.person(args.name) or ws.create_person(
                args.name, can_login=True, is_admin=True)
            # BRICK GUARD. Setting a password on someone who cannot sign in flips
            # has_any_credentials() to True, which permanently 404s /setup -- and /setup is the only
            # unauthenticated way to mint the first admin. The result is an install with no one able
            # to log in and no way to create anyone: unrecoverable short of deleting auth.db.
            # Reachable with one typo, because the roster is full of assignable-only people (Rita &
            # co) whose names are exactly what someone would type here. The webapp's /setup route has
            # carried this check since ADR-039; the CLI path did not.
            if not (person["can_login"] and person["is_admin"]):
                print(f"{person['name']!r} já existe e não é um administrador com acesso — "
                      f"definir-lhe uma palavra-passe fecharia o /setup sem criar ninguém que "
                      f"consiga entrar.\n"
                      f"  Promove-o primeiro, ou escolhe outro nome.", file=sys.stderr)
                return 2
            auth.set_password(person["person_id"], password)
            print(f"Administrador {person['name']!r} criado.")
            return 0

        if args.action == "add":
            try:
                person = ws.create_person(
                    args.name, can_login=bool(args.login or args.admin), is_admin=bool(args.admin),
                    responsible=args.responsible or "", email=args.email or "")
            except ValueError as exc:
                print(str(exc), file=sys.stderr)
                return 2
            if args.scopes:
                ws.set_person_scopes(person["person_id"], args.scopes.split(","))
            kind = "admin" if person["is_admin"] else ("utilizador" if person["can_login"]
                                                       else "sem acesso à plataforma")
            print(f"{person['name']!r} criado ({kind}).")
            if person["can_login"]:
                print(f"  convida-o com: email2data auth invite --name {person['name']!r}")
            return 0

        if args.action == "invite":
            person = ws.person(args.name)
            if person is None or not person["can_login"]:
                print(f"{args.name!r} não existe ou não tem acesso à plataforma.", file=sys.stderr)
                return 2
            token = auth.create_invite(person["person_id"], created_by="cli")
            print(f"Convite para {person['name']!r} (válido {authmod.INVITE_TTL_HOURS}h, uso único):")
            print(f"  /aceitar-convite/{token}")
            print("  Abre este caminho no endereço onde a app está a servir.")
            return 0

        if args.action == "reset":
            # The recovery path for a forgotten password. `invite` was doing this job badly: it mints
            # an ONBOARDING token for someone already onboarded, and the person ends up choosing a
            # password through a page that says "bem-vindo". Here the temporary password is marked
            # temporary (must_change), and the webapp funnels them until they replace it — which is
            # what makes ADR-039's `must_change` column mean something for the first time.
            person = ws.person(args.name)
            if person is None:
                print(f"{args.name!r} não existe.", file=sys.stderr)
                return 2
            if not (person["can_login"] and person["active"]):
                print(f"{person['name']!r} não tem acesso à plataforma — uma palavra-passe para "
                      f"quem não pode entrar conta como credencial e não serve a ninguém.\n"
                      f"  Dá-lhe acesso primeiro (`auth add --login`).", file=sys.stderr)
                return 2
            password = getpass.getpass("Palavra-passe temporária: ")
            if len(password) < 8:
                print("A palavra-passe precisa de pelo menos 8 caracteres.", file=sys.stderr)
                return 2
            if password != getpass.getpass("Confirmar: "):
                print("As palavras-passe não coincidem.", file=sys.stderr)
                return 2
            # set_password revokes every live session in the same transaction — a reset that left the
            # suspected session alive would only add a second way in beside the one you were worried
            # about.
            auth.set_password(person["person_id"], password, must_change=True)
            print(f"Palavra-passe temporária definida para {person['name']!r}.")
            print("  Terá de a mudar no primeiro acesso; as sessões abertas foram terminadas.")
            return 0

        if args.action == "email":
            person = ws.person(args.name)
            if person is None:
                print(f"{args.name!r} não existe.", file=sys.stderr)
                return 2
            try:
                person = ws.set_person_email(person["person_id"], args.address or "")
            except ValueError as exc:
                print(str(exc), file=sys.stderr)
                return 2
            if person["email"]:
                print(f"{person['name']!r}: {person['email']}")
                if not person["can_login"]:
                    print("  AVISO: esta pessoa não tem acesso à plataforma, por isso não há "
                          "palavra-passe para recuperar — o endereço fica guardado e não é usado.")
            else:
                print(f"{person['name']!r}: sem email — não poderá usar «Esqueceste-te da "
                      f"palavra-passe?».")
            return 0

        if args.action == "mail-test":
            # Proves the credential and the transport, and says plainly that it does NOT prove
            # delivery -- the standing rule against reporting a proxy as the real thing.
            mailer = None
            try:
                mailer = mailermod.from_settings(settings)
            except Exception as exc:  # noqa: BLE001 — a config error is the answer, not a crash
                print(f"Configuração de email inválida: {exc}", file=sys.stderr)
                return 2
            if mailer is None:
                print("Envio de email desativado (config/settings.json -> mail.enabled).\n"
                      "  A recuperação por email fica indisponível; o /recuperar diz isso mesmo.")
                return 1
            try:
                mailer.verify_connection()
            except mailermod.MailError as exc:
                print(f"FALHOU: {exc}", file=sys.stderr)
                return 1
            base = str((settings.get("mail") or {}).get("base_url", "")).rstrip("/")
            print(f"Autenticação SMTP OK — {mailer}")
            print("  (prova a credencial e o transporte; NÃO prova entrega.)")
            if not base:
                print("  AVISO: mail.base_url está vazio — o /recuperar recusa-se a enviar um link "
                      "que não leva a lado nenhum.")
            else:
                print(f"  Os links vão apontar para {base}")
                host = base.split("//", 1)[-1].split(":")[0]
                if _in_container():
                    print("  (a partir do contentor não é possível confirmar o endereço da LAN — "
                          "corre este comando no host para verificar o base_url)")
                elif host not in ("127.0.0.1", "localhost"):
                    local = _local_lan_ip()
                    if local and local != host:
                        print(f"  AVISO: este host está agora em {local}, não em {host} — o "
                              f"endereço mudou (DHCP?) e os links enviados não abrem. "
                              f"Atualiza mail.base_url.")
            return 0

        if args.action == "scopes":
            person = ws.person(args.name)
            if person is None:
                print(f"{args.name!r} não existe.", file=sys.stderr)
                return 2
            ws.set_person_scopes(person["person_id"],
                                 args.grant.split(",") if args.grant else [])
            granted = ws.person_scopes(person["person_id"])
            print(f"{person['name']!r}: " + (", ".join(granted) if granted else "sem caixas"))
            print(f"  (a caixa por atribuir é {scopesmod.SCOPE_UNATTRIBUTED!r}; "
                  f"os admins veem tudo)")
            return 0

        if args.action == "revoke":
            person = ws.person(args.name)
            if person is None:
                print(f"{args.name!r} não existe.", file=sys.stderr)
                return 2
            n = auth.revoke_all_sessions(person["person_id"])
            print(f"{n} sessão(ões) terminada(s) para {person['name']!r}.")
            return 0
    finally:
        auth.close()
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
    cert, key = getattr(args, "tls_cert", ""), getattr(args, "tls_key", "")
    if bool(cert) != bool(key):
        print("--tls-cert and --tls-key must be given together.", file=sys.stderr)
        return 2
    scheme = "https" if cert else "http"
    if host in ("0.0.0.0", "::") and not cert:
        # A LAN bind without TLS puts the session cookie on the wire in clear text. Not fatal (the
        # container binds 0.0.0.0 internally and is published on loopback), but never silent.
        print("AVISO: a servir na LAN sem TLS — a cookie de sessão viaja em claro. "
              "Vê bin/make-cert.sh.", file=sys.stderr)
    print(f"workspace → {scheme}://{shown}:{port}   (Ctrl-C to stop)")
    uvicorn.run(app, host=host, port=port,
                **({"ssl_certfile": cert, "ssl_keyfile": key} if cert else {}))
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
    jp.add_argument("--tier", choices=["light", "standard", "heavy"],
                    help="model tier for THIS run only (light=flash-lite, standard=flash, heavy=pro);"
                         " only affects --draft/--reply")
    jp.add_argument("--only", action="append", metavar="REF", default=None,
                    help="scoped re-extract: rebuild ONLY this message_id or project_id (p-XXXX expands"
                         " to its threads' messages). Repeatable. Every other entry is kept as-is.")
    jp.set_defaults(fn=cmd_jobspec)
    rp = sub.add_parser("relations", help="show emails related to a message (thread / contact / entity)")
    rp.add_argument("message_id", help="message_id to look up (from results.jsonl)")
    rp.set_defaults(fn=cmd_relations)
    scp = sub.add_parser("scopes", help="ADR-038: attribute cached mail to the inbox(es) it reached")
    scpsub = scp.add_subparsers(dest="action", required=True)
    scpsub.add_parser("backfill", help="derive attribution for every cached message (idempotent)")
    scpsub.add_parser("status", help="show attributed messages per inbox")
    scp.set_defaults(fn=cmd_scopes)
    gz = sub.add_parser("gazetteer", help="ADR-005 priors: status + recover the editable CSV source of truth")
    gzsub = gz.add_subparsers(dest="action", required=True)
    gzsub.add_parser("status", help="is the CSV there, and what does the live table hold? (exit 1 if frozen)")
    gz_ex = gzsub.add_parser("export", help="write the live table back out as config/gazetteer.csv")
    gz_ex.add_argument("--force", action="store_true", help="overwrite an existing CSV (discards it)")
    gz.set_defaults(fn=cmd_gazetteer)
    ap = sub.add_parser("auth", help="ADR-039: people, passwords, invites and sessions")
    apsub = ap.add_subparsers(dest="action", required=True)
    apsub.add_parser("list", help="show every person, their access and their inbox grants")
    a_setup = apsub.add_parser("setup", help="create the FIRST administrator (once only)")
    a_setup.add_argument("--name", required=True)
    a_add = apsub.add_parser("add", help="add a person (assignable; optionally with platform access)")
    a_add.add_argument("--name", required=True)
    a_add.add_argument("--login", action="store_true", help="can sign in")
    a_add.add_argument("--admin", action="store_true", help="administrator (implies --login)")
    a_add.add_argument("--responsible", default="",
                       help="REQUIRED without --login: the user accountable for their queue")
    a_add.add_argument("--scopes", default="", help="comma-separated inbox addresses to grant")
    a_add.add_argument("--email", default="",
                       help="where a password-reset link is sent (ADR-042); never inferred")
    a_eml = apsub.add_parser("email", help="set or clear where a person's reset link is sent")
    a_eml.add_argument("--name", required=True)
    a_eml.add_argument("--address", default="", help="empty clears it (no address on file)")
    apsub.add_parser("mail-test", help="authenticate to the SMTP account without sending anything")
    a_inv = apsub.add_parser("invite", help="mint a single-use link for setting a password")
    a_inv.add_argument("--name", required=True)
    a_rst = apsub.add_parser("reset", help="set a TEMPORARY password the person must change")
    a_rst.add_argument("--name", required=True)
    a_sc = apsub.add_parser("scopes", help="replace a person's inbox grants")
    a_sc.add_argument("--name", required=True)
    a_sc.add_argument("--grant", default="", help="comma-separated addresses; empty revokes all")
    a_rv = apsub.add_parser("revoke", help="end every live session for a person")
    a_rv.add_argument("--name", required=True)
    ap.set_defaults(fn=cmd_auth)
    sv = sub.add_parser("serve", help="run the local workspace to confirm leads (localhost; never sends)")
    sv.add_argument("--port", type=int, default=8042)  # NEVER 8000 (user hard rule); 8042 is the agreed default
    sv.add_argument("--host", default="127.0.0.1",
                    help="bind address; use 0.0.0.0 inside a container (the Docker image does this)")
    # TLS is OPT-IN: default stays plain HTTP on loopback so tests and local dev are unchanged. Turn
    # it on together with a LAN bind -- see bin/make-cert.sh (ADR-039).
    sv.add_argument("--tls-cert", default="", help="PEM certificate; enables HTTPS (with --tls-key)")
    sv.add_argument("--tls-key", default="", help="PEM private key for --tls-cert")
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
