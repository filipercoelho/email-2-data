"""Rich HTML report builder — ONE UI, served two ways.

``build_html(..., live=False)`` → the self-contained static report (``tools/make_report.py`` writes it
to ``out/report.html``).  ``build_html(..., live=True)`` → the SAME UI, but the job-spec panel becomes
editable and wired to the workspace API (``webapp.py`` serves it and persists confirmations). One UI,
not two. ``prepare(settings)`` does the shared data-prep (parse corpus for body/people, attach cost +
jobspec). Bodies are read from the local corpus at render time (results.jsonl stays body-free).
"""

from __future__ import annotations

import glob
import json
from pathlib import Path
from typing import Any

from . import crm, jobspec as _js
from .config import paths
from .envelope import parse_eml
from .signals import OUR_DOMAIN

BODY_CAP = 8000
_PRI = {"HIGH": 0, "NEEDS_REVIEW": 1, "MEDIUM": 2, "LOW": 3, "IGNORE": 4}


def _internal(em: str) -> bool:
    d = em.rsplit("@", 1)[-1].lower() if "@" in em else ""
    return d == OUR_DOMAIN or d.endswith("." + OUR_DOMAIN)


def prepare(settings: dict[str, Any], corpus_glob: str = "corpus/*.eml"):
    """Load + enrich emails (body/people/attachments/cost/jobspec) for the report. Returns (emails, contacts, cost)."""
    out = paths(settings, settings["__settings_path__"])["out_dir"]
    emails = [json.loads(x) for x in (out / "results.jsonl").read_text().splitlines() if x.strip()]
    contacts = ([json.loads(x) for x in (out / "contacts.jsonl").read_text().splitlines() if x.strip()]
                if (out / "contacts.jsonl").exists() else [])
    cost = json.loads((out / "cost.json").read_text()) if (out / "cost.json").exists() else {}
    per_cost = cost.get("per_email", {})
    jobspecs: dict[str, Any] = {}
    if (out / "jobspecs.jsonl").exists():
        for line in (out / "jobspecs.jsonl").read_text().splitlines():
            if line.strip():
                j = json.loads(line)
                jobspecs[j["message_id"]] = j
    emails.sort(key=lambda r: (_PRI.get(r.get("priority"), 9), -r.get("urgency", 0)))
    contacts.sort(key=lambda c: -c.get("msg_count", 0))
    mid2file = {}
    for f in glob.glob(corpus_glob):
        try:
            mid2file[parse_eml(Path(f).read_bytes())["message_id"]] = f
        except Exception:  # noqa: BLE001
            pass
    for r in emails:
        pc = per_cost.get(r["message_id"], {})
        r.update(_date=None, _body="", _body_trunc=False, _people=[], _attach=[], _reply=False, _thread="",
                 _tin=pc.get("in", 0), _tout=pc.get("out", 0), _cost=pc.get("cost", 0.0),
                 _jobspec=jobspecs.get(r["message_id"]))
        f = mid2file.get(r["message_id"])
        if not f:
            continue
        env = parse_eml(Path(f).read_bytes())
        body = env.get("body_text") or ""
        r["_date"] = env.get("date")
        r["_body"] = body[:BODY_CAP]
        r["_body_trunc"] = len(body) > BODY_CAP
        r["_thread"] = crm._thread_root(env)
        r["_reply"] = bool(env.get("in_reply_to") or env.get("references"))
        r["_people"] = [{"email": e, "name": n, "role": role, "internal": _internal(e)}
                        for e, n, role in crm.participants(env)]
        r["_attach"] = [{"name": a.get("filename") or "(unnamed)", "type": a.get("content_type"),
                         "size": a.get("size_bytes")} for a in (env.get("attachments") or [])]
    return emails, contacts, cost


def _embed(obj) -> str:
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


def build_html(emails, contacts, cost, reclassifications=None, *, live: bool = False) -> str:
    return (TEMPLATE
            .replace("__EMAILS__", _embed(emails))
            .replace("__CONTACTS__", _embed(contacts))
            .replace("__COST__", _embed({k: v for k, v in cost.items() if k != "per_email"}))
            .replace("__OURDOMAIN__", _embed(OUR_DOMAIN))
            .replace("__LIVE__", "true" if live else "false")
            .replace("__MUST__", _embed(_js.MUST))
            .replace("__SHOULD__", _embed(_js.SHOULD))
            .replace("__ITEMKEYS__", _embed(_js.ITEM_KEYS))
            .replace("__JOBKEYS__", _embed(_js.JOB_KEYS))
            .replace("__ITEMMUST__", _embed(_js.ITEM_MUST))
            .replace("__JOBMUST__", _embed(_js.JOB_MUST))
            .replace("__RECLASSIFICATIONS__", _embed(reclassifications or {})))


TEMPLATE = r"""<!doctype html>
<html lang="pt">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>email-2-data · workspace</title>
<style>
  :root{--bg:#eef0f3;--card:#fff;--bd:#e3e6ea;--bd2:#eef0f3;--tx:#15181c;--mut:#6b7280;--mut2:#9aa1ab;
    --ac:#3358d4;--int:#0d9488;--ext:#64748b;--shadow:0 1px 2px rgba(20,24,28,.05),0 1px 3px rgba(20,24,28,.04);}
  *{box-sizing:border-box} html,body{margin:0}
  body{font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;color:var(--tx);background:var(--bg)}
  header{background:var(--card);border-bottom:1px solid var(--bd);padding:16px 28px;position:sticky;top:0;z-index:20;box-shadow:var(--shadow)}
  .htop{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap}
  h1{margin:0;font-size:17px;font-weight:680;letter-spacing:-.01em}
  .sub{color:var(--mut);font-size:12.5px}
  .nlink{color:var(--mut);text-decoration:none;font-size:13px;font-weight:600;padding:5px 10px;border-radius:8px}
  .nlink:hover{background:var(--bg);color:var(--tx)}
  .stats{display:flex;flex-wrap:wrap;gap:8px;margin-top:13px}
  .stat{background:var(--bg);border:1px solid var(--bd);border-radius:10px;padding:7px 13px;min-width:84px}
  .stat .n{font-size:18px;font-weight:680;letter-spacing:-.02em} .stat.cost .n{color:var(--int)}
  .stat .l{font-size:10.5px;color:var(--mut);text-transform:uppercase;letter-spacing:.05em;margin-top:1px}
  .distbar{display:flex;height:7px;border-radius:6px;overflow:hidden;margin-top:13px;border:1px solid var(--bd)}
  .distbar>span{display:block}
  .wrap{max-width:1320px;margin:0 auto;padding:18px 28px 40px}
  .tabs{display:flex;gap:7px;margin-bottom:14px}
  .tab{padding:8px 16px;border:1px solid var(--bd);background:var(--card);border-radius:9px;cursor:pointer;font-weight:600;color:var(--mut);box-shadow:var(--shadow)}
  .tab.on{background:var(--ac);color:#fff;border-color:var(--ac)}
  .toolbar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:12px}
  input[type=search]{flex:1;min-width:240px;padding:10px 13px;border:1px solid var(--bd);border-radius:9px;font-size:14px;background:var(--card);box-shadow:var(--shadow)}
  input[type=search]:focus{outline:2px solid var(--ac);outline-offset:-1px;border-color:var(--ac)}
  .chip{padding:6px 12px;border:1px solid var(--bd);background:var(--card);border-radius:20px;cursor:pointer;font-size:12px;font-weight:550;color:var(--mut);user-select:none}
  .chip.on{background:var(--tx);color:#fff;border-color:var(--tx)}
  .chip .cn{opacity:.6;font-variant-numeric:tabular-nums;margin-left:2px}
  .chip.on .cn{opacity:.85}
  .chip.z{opacity:.42} .chip.z.on{opacity:1}
  .count{color:var(--mut);font-size:12px;margin-left:auto}
  /* ── Faceted filter panel (always-visible chip rows) ─────────────────── */
  .filters{border:1px solid var(--bd);border-radius:12px;background:var(--card);box-shadow:var(--shadow);margin-bottom:12px;overflow:hidden}
  .filters .fhd{display:flex;align-items:center;flex-wrap:wrap;gap:8px 10px;padding:9px 13px;cursor:pointer;user-select:none;font-size:11px;text-transform:uppercase;letter-spacing:.05em;font-weight:700;color:var(--mut)}
  .filters .fhd:hover{color:var(--ac)} .filters .fhd .ar{transition:transform .15s} .filters.col .fhd .ar{transform:rotate(-90deg)}
  .filters .fhd .nact{font-weight:600;text-transform:none;letter-spacing:0;color:var(--ac);background:#eef2ff;border:1px solid #cdd7ff;border-radius:20px;padding:1px 9px;font-size:11px}
  .filters .fhd .clr{margin-left:auto;font-weight:600;text-transform:none;letter-spacing:0;color:var(--ac);font-size:12px}
  .filters .fhd .clr:hover{text-decoration:underline}
  .filters .fsum{display:inline-flex;flex-wrap:wrap;gap:6px;min-width:0}
  .fpill{display:inline-flex;align-items:center;gap:4px;background:#eef2ff;border:1px solid #cdd7ff;color:var(--ac);border-radius:20px;padding:2px 9px;font-size:11.5px;font-weight:600;text-transform:none;letter-spacing:0;cursor:pointer}
  .fpill:hover{background:#e0e7ff}
  .filters .fbody{padding:4px 13px 11px;border-top:1px solid var(--bd2)} .filters.col .fbody{display:none}
  .frow{display:flex;align-items:flex-start;gap:9px;padding:7px 0;border-top:1px solid var(--bd2)} .frow:first-child{border-top:none}
  .frow .rl{flex:0 0 78px;font-size:10px;text-transform:uppercase;letter-spacing:.06em;font-weight:700;color:var(--mut2);padding-top:6px}
  .frow .rc{display:flex;flex-wrap:wrap;gap:6px;min-width:0;flex:1}
  .frow .chip{padding:4px 10px;font-size:11.5px}
  .frow .subt{align-self:center;font-size:10px;color:var(--mut2);font-weight:700;margin:0 1px 0 4px;text-transform:uppercase;letter-spacing:.04em}
  .banner{display:flex;gap:8px;align-items:center;flex-wrap:wrap;background:#eef2ff;border:1px solid #cdd7ff;border-radius:9px;padding:8px 12px;margin-bottom:12px;font-size:12.5px}
  .banner b{color:var(--ac)} .banner .x{margin-left:auto;cursor:pointer;color:var(--ac);font-weight:600}
  .layout{display:grid;grid-template-columns:390px 1fr;gap:16px;align-items:start}
  .list{background:var(--card);border:1px solid var(--bd);border-radius:14px;overflow:hidden;max-height:calc(100vh - 250px);overflow-y:auto;box-shadow:var(--shadow)}
  .item{display:block;padding:12px 14px;border-bottom:1px solid var(--bd2);border-left:3px solid transparent;cursor:pointer}
  .item:hover{background:#f8f9fb} .item.sel{background:#eef2ff;border-left-color:var(--ac)}
  .item .top{display:flex;align-items:center;gap:6px;margin-bottom:5px}
  .item .subj{font-weight:620;font-size:13.5px;line-height:1.35;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
  .item .frm{color:var(--mut);font-size:12px;margin-top:3px;display:flex;align-items:center;gap:5px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
  .item .date{color:var(--mut2);font-size:11px;margin-left:auto;white-space:nowrap}
  .detail{background:var(--card);border:1px solid var(--bd);border-radius:14px;padding:22px 26px;box-shadow:var(--shadow);min-height:300px}
  .bc{margin:-4px 0 12px;font-size:12.5px} .bc span{color:var(--ac);cursor:pointer;font-weight:600}
  .detail h2{margin:0 0 12px;font-size:19px;line-height:1.3;letter-spacing:-.01em}
  .meta{display:flex;flex-wrap:wrap;gap:7px;align-items:center;margin-bottom:6px}
  .dmut{color:var(--mut);font-size:12.5px}
  .sec{margin-top:20px} .sec h3{margin:0 0 10px;font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--mut);font-weight:680;display:flex;align-items:center;gap:8px}
  .legend{font-weight:500;text-transform:none;letter-spacing:0;color:var(--mut2);font-size:11px;margin-left:auto;display:flex;gap:11px}
  .legend i{font-style:normal;display:inline-flex;align-items:center;gap:4px}
  .dot{width:8px;height:8px;border-radius:50%;display:inline-block}
  .people{display:flex;flex-wrap:wrap;gap:7px}
  .person{display:flex;flex-direction:column;border:1px solid var(--bd);border-left:3px solid var(--ext);border-radius:9px;padding:6px 11px;background:#fcfcfd;min-width:0;cursor:pointer}
  .person:hover{background:#f1f5ff;border-color:#cdd7ff} .person.int{border-left-color:var(--int);background:#f0fdfa}
  .person .role{font-size:9.5px;text-transform:uppercase;letter-spacing:.05em;color:var(--mut2);font-weight:680}
  .person .nm{font-weight:600;font-size:12.5px} .person .em{font-size:11.5px;color:var(--mut)}
  .ents{display:flex;flex-wrap:wrap;gap:8px}
  .ent{background:var(--bg);border:1px solid var(--bd);border-radius:9px;padding:6px 11px;font-size:12.5px;max-width:100%}
  .ent b{color:var(--mut);font-weight:680;text-transform:uppercase;font-size:9.5px;letter-spacing:.04em;display:block;margin-bottom:1px}
  .atts{display:flex;flex-wrap:wrap;gap:7px}
  .att{background:#fff;border:1px solid var(--bd);border-radius:8px;padding:5px 10px;font-size:12px;display:inline-flex;align-items:center;gap:5px} .att .ty{color:var(--mut2);font-size:10.5px}
  .att.clk{cursor:pointer;text-decoration:none;color:var(--tx)} .att.clk:hover{border-color:var(--ac);background:#f1f5ff}
  .reason{background:#fffdf3;border:1px solid #f0e6c0;border-radius:10px;padding:12px 14px;font-size:13px;color:#4a4326;line-height:1.55}
  .body{white-space:pre-wrap;overflow-wrap:anywhere;font:12.5px/1.65 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;background:#fbfcfd;border:1px solid var(--bd);border-radius:10px;padding:16px 18px;max-height:440px;overflow:auto;color:#23272c}
  .qline{color:#a3aab4}
  .badge{display:inline-block;padding:2px 10px;border-radius:20px;font-size:11px;font-weight:680;white-space:nowrap}
  .badge.clk{cursor:pointer} .badge.clk:hover{filter:brightness(.94)}
  .pill{display:inline-flex;align-items:center;gap:5px;padding:3px 10px;border:1px solid var(--bd);border-radius:20px;font-size:11.5px;color:var(--mut);background:#fff}
  .pill.clk{cursor:pointer} .pill.clk:hover{border-color:var(--ac);color:var(--ac)}
  .pill.cost{font-family:ui-monospace,Menlo,monospace;color:var(--int);border-color:#bfe6e0;background:#f0fdfa}
  .urg{font-variant-numeric:tabular-nums;font-weight:680}
  .link{color:var(--ac);cursor:pointer} .link:hover{text-decoration:underline}
  .kv{display:flex;flex-wrap:wrap;gap:18px;margin:4px 0 2px} .kv div{font-size:13px} .kv b{display:block;font-size:10px;text-transform:uppercase;letter-spacing:.04em;color:var(--mut);font-weight:680;margin-bottom:1px}
  .sublist{border:1px solid var(--bd);border-radius:10px;overflow:hidden}
  .mini{display:flex;align-items:center;gap:9px;padding:8px 12px;border-bottom:1px solid var(--bd2);cursor:pointer;font-size:13px}
  .mini:last-child{border-bottom:none} .mini:hover{background:#f8f9fb}
  .mini .ms{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:550}
  .mini .md{color:var(--mut2);font-size:11px;white-space:nowrap} .mini .mc{font-family:ui-monospace,monospace;font-size:11px;color:var(--int);white-space:nowrap}
  .copips{display:flex;flex-wrap:wrap;gap:6px}
  .copip{border:1px solid var(--bd);border-left:3px solid var(--ext);border-radius:8px;padding:4px 9px;font-size:12px;cursor:pointer;background:#fcfcfd}
  .copip:hover{background:#f1f5ff} .copip.int{border-left-color:var(--int);background:#f0fdfa} .copip b{color:var(--mut);font-weight:680}
  .empty{color:var(--mut2);padding:50px;text-align:center} .muted{color:var(--mut)} .hidden{display:none}
  table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--bd);border-radius:14px;overflow:hidden;box-shadow:var(--shadow)}
  th,td{padding:11px 14px;text-align:left;border-bottom:1px solid var(--bd2)} th{font-size:10.5px;text-transform:uppercase;letter-spacing:.05em;color:var(--mut);background:var(--bg)}
  tr.crow{cursor:pointer} tr.crow:hover td{background:#f8f9fb} tr.int td{background:#f0fdfa}
  .js{margin-top:20px;border:1px solid var(--bd);border-radius:12px;overflow:hidden}
  .js .jh{display:flex;align-items:center;gap:10px;padding:11px 14px;background:#f5f7ff;border-bottom:1px solid var(--bd);font-weight:650;font-size:13px}
  .js .jb{padding:14px}
  .covbar{height:8px;background:#e6e8ec;border-radius:6px;overflow:hidden;width:150px;display:inline-block;vertical-align:middle}
  .covbar>span{display:block;height:100%;background:var(--int)}
  .miss{display:flex;flex-wrap:wrap;gap:6px;margin:4px 0 10px}
  .miss .m{background:#fff3f3;border:1px solid #f3c9c9;color:#b4424a;border-radius:7px;padding:3px 9px;font-size:11.5px}
  .qs{margin:2px 0 4px;padding-left:18px;color:#3a4150;font-size:13px}
  .lbl{font-size:11px;text-transform:uppercase;letter-spacing:.05em;font-weight:680;color:var(--mut);margin-bottom:6px}
  .metabar{display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin-bottom:6px}
  .metabar .mp{font-size:11px;color:var(--mut);background:#f6f7f9;border:1px solid var(--bd);border-radius:20px;padding:2px 9px;display:inline-flex;align-items:center;gap:4px}
  .metabar .mp.clk{cursor:pointer} .metabar .mp.clk:hover{color:var(--ac);border-color:var(--ac)}
  .cols{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-top:8px;align-items:start}
  .colL,.colR{min-width:0} .colL>.sec:first-child,.colR>.sec:first-child{margin-top:14px}
  .jgrid{display:grid;grid-template-columns:1.25fr 1fr;gap:18px;align-items:start}
  .leg{display:flex;gap:14px;align-items:center;font-size:11px;color:var(--mut);margin-bottom:12px}
  .leg span{display:inline-flex;align-items:center;gap:5px}
  .fgrp{margin-bottom:14px} .fglab{font-size:10px;text-transform:uppercase;letter-spacing:.07em;color:var(--mut2);font-weight:700;margin-bottom:2px}
  .itemcard{border:1px solid var(--bd2);border-radius:9px;padding:7px 10px 9px;margin-bottom:10px;background:#fcfcfd}
  .itemcard>.fglab{display:flex;align-items:center;gap:8px;color:var(--ac)}
  .rmitem{margin-left:auto;cursor:pointer;color:var(--mut2);font-weight:700;font-size:12px} .rmitem:hover{color:#e5484d}
  .additem{display:inline-block;font-size:12px;color:var(--ac);cursor:pointer;font-weight:600;border:1px dashed var(--bd);border-radius:8px;padding:5px 11px} .additem:hover{background:#eef2ff;border-color:var(--ac)}
  .fr{display:grid;grid-template-columns:118px 1fr 11px;align-items:center;gap:10px;padding:6px 9px;border-radius:7px}
  .fr+.fr{border-top:1px solid var(--bd2)}
  .fr label{font-size:12.5px;color:#3a4150;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .fin{border:none;border-bottom:1px solid transparent;background:transparent;padding:3px 2px;font:13px inherit;color:var(--tx);width:100%}
  .fin::placeholder{color:#c4c8ce} .fin:hover{border-bottom-color:var(--bd)} .fin:focus{outline:none;border-bottom:1.5px solid var(--ac)} .fin.saved{background:#f0fdfa}
  .fs{width:9px;height:9px;border-radius:50%;background:#d5d8dd;display:inline-block;justify-self:center}
  .fs.done,.fr.done .fs{background:var(--int)} .fs.auto,.fr.auto .fs{background:#f5a623} .fs.miss,.fr.miss .fs{background:#e5484d}
  .fr.miss{background:#fff7f7} .fr.miss label{color:#b4424a} .fr.auto{background:#fffdf6}
  .optd{margin-top:6px;border-top:1px solid var(--bd2);padding-top:8px} .optd summary{font-size:12px;color:var(--ac);cursor:pointer;font-weight:600}
  .rhead{font-size:13px;font-weight:680;margin-bottom:6px} .rhead .muted{font-weight:400}
  .reply textarea{width:100%;min-height:160px;border:1px solid var(--bd);border-radius:9px;padding:12px;font:13px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;resize:vertical;background:#fcfcfd;color:var(--tx)}
  .reply .bar{display:flex;align-items:center;gap:10px;margin-top:8px;flex-wrap:wrap}
  .btn{background:var(--ac);color:#fff;border:none;border-radius:8px;padding:8px 15px;font-weight:600;cursor:pointer;font-size:12.5px}
  .btn.sm{padding:7px 11px} .btn:hover{filter:brightness(.95)}
  .copied{color:var(--int);font-size:12px;font-weight:600}
  /* ── Thread conversation ribbon ─────────────────────────────────────── */
  .conv{border:1px solid var(--bd);border-radius:11px;overflow:hidden;margin-bottom:16px}
  .conv-hd{font-size:10.5px;text-transform:uppercase;letter-spacing:.05em;color:var(--mut);font-weight:680;padding:7px 12px;background:var(--bg);border-bottom:1px solid var(--bd);display:flex;align-items:center;gap:7px}
  .conv-row{display:flex;align-items:center;gap:9px;padding:7px 12px;border-bottom:1px solid var(--bd2);font-size:12.5px;line-height:1.4}
  .conv-row:last-child{border-bottom:none}.conv-nav{cursor:pointer}.conv-nav:hover{background:#f8f9fb}.conv-cur{background:#eef2ff}
  .conv-d{color:var(--mut2);font-size:11px;white-space:nowrap;min-width:56px}
  .conv-f{max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px;color:var(--mut)}
  .conv-s{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12.5px;color:var(--mut)}
  .conv-cur .conv-s{color:var(--tx);font-weight:600}.conv-cur .conv-f{color:var(--tx);font-weight:600}
  /* ── Entity relations panel ─────────────────────────────────────────── */
  .rel{border:1px solid var(--bd);border-radius:11px;overflow:hidden;margin-top:20px}
  .rel-hd{font-size:10.5px;text-transform:uppercase;letter-spacing:.05em;color:var(--mut);font-weight:680;padding:7px 12px;background:var(--bg);border-bottom:1px solid var(--bd);display:flex;align-items:center;gap:7px;cursor:pointer;user-select:none}
  .rel-hd:hover{background:#eef2ff;color:var(--ac)}.rel-hd.open{border-bottom:1px solid var(--bd)}
  .rel-row{display:flex;align-items:center;gap:9px;padding:7px 12px;border-bottom:1px solid var(--bd2);cursor:pointer;font-size:12.5px}
  .rel-row:last-child{border-bottom:none}.rel-row:hover{background:#f8f9fb}
  .rel-tag{font-size:9.5px;text-transform:uppercase;letter-spacing:.04em;font-weight:680;border-radius:5px;padding:1px 6px;white-space:nowrap;flex-shrink:0;background:#eef2ff;color:var(--ac);border:1px solid #cdd7ff}
  .rel-tag.fin{background:#f0fdfa;color:#0d9488;border-color:#bfe6e0}
  .rel-tag.nom{background:#f0fdf4;color:#13a36a;border-color:#bbf0d5}
  /* ── Conv ribbon direction arrows ───────────────────────────────────── */
  .conv-arr{font-size:12px;font-weight:700;flex-shrink:0;width:16px;text-align:center}
  .conv-arr.out{color:var(--int)}.conv-arr.in{color:var(--ac)}
  /* ── Lead thread expanded messages ──────────────────────────────────── */
  .lthread{border:1px solid var(--bd);border-radius:11px;overflow:hidden;margin:16px 0 0}
  .lmsg{border-bottom:1px solid var(--bd2)}.lmsg:last-child{border-bottom:none}
  .lmsg-hd{display:flex;align-items:center;gap:9px;padding:8px 12px;cursor:pointer;font-size:12.5px;user-select:none}
  .lmsg-hd:hover{background:#f8f9fb}.lmsg-out .lmsg-hd{background:#f0fdfa}
  .lmsg-body{padding:10px 14px 14px;border-top:1px solid var(--bd2)}
  @media(max-width:1200px){.cols,.jgrid{grid-template-columns:1fr}}
  .aprev{display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin-bottom:12px;padding-bottom:10px;border-bottom:1px solid var(--bd2)}
  .achip{display:inline-flex;align-items:center;gap:5px;border:1px solid #cdd7ff;background:#eef2ff;color:var(--ac);border-radius:8px;padding:4px 10px;font-size:12px;font-weight:600;cursor:pointer} .achip:hover{background:#e0e7ff}
  .pv{position:fixed;top:0;right:0;bottom:0;width:46vw;max-width:780px;background:var(--card);border-left:1px solid var(--bd);box-shadow:-6px 0 22px rgba(20,24,28,.13);z-index:40;display:flex;flex-direction:column}
  .pv.hidden{display:none}
  .pvh{display:flex;align-items:center;gap:10px;padding:11px 16px;border-bottom:1px solid var(--bd);font-weight:600;font-size:13px}
  .pvh .nm{overflow:hidden;text-overflow:ellipsis;white-space:nowrap} .pvh .x{cursor:pointer;font-size:18px;color:var(--mut);line-height:1} .pvh .x:hover{color:var(--tx)}
  .pvbody{flex:1;overflow:auto;background:#525659;display:flex} .pvbody iframe{flex:1;width:100%;border:none} .pvbody img{margin:auto;max-width:100%;max-height:100%;object-fit:contain} .pvmsg{margin:auto;text-align:center;color:#fff;padding:40px} .pvmsg .btn{margin-top:12px;display:inline-block;text-decoration:none}
  body.previewing .list{display:none}
  body.previewing .layout{grid-template-columns:1fr}
  body.previewing .wrap{max-width:none;margin:0;padding-right:calc(min(46vw,780px) + 20px)}
  @media(max-width:900px){.wrap{padding:14px}.layout{grid-template-columns:1fr}.list{max-height:340px}.detail{padding:18px}.pv{width:100vw;max-width:none}body.previewing .wrap{padding-right:14px}}
  /* ── Reclassify panel ───────────────────────────────────────────────── */
  .rc{margin-top:14px;border:1px solid var(--bd);border-radius:10px;overflow:hidden}
  .rc summary{font-size:11.5px;color:var(--ac);cursor:pointer;font-weight:650;padding:8px 13px;list-style:none;display:flex;align-items:center;gap:7px;background:var(--bg)}
  .rc summary::-webkit-details-marker{display:none}
  .rc summary:hover{background:#eef2ff}
  .rc .rcbody{padding:12px 14px;display:flex;flex-wrap:wrap;gap:14px;align-items:flex-end}
  .rcfld{display:flex;flex-direction:column;gap:4px}
  .rcfld .rclbl{font-size:10px;text-transform:uppercase;letter-spacing:.05em;color:var(--mut);font-weight:680}
  .rcfld select{border:1px solid var(--bd);border-radius:7px;padding:5px 9px;font:12.5px inherit;color:var(--tx);background:var(--card);cursor:pointer;min-width:130px}
  .rcfld select.ov{border-color:var(--ac);color:var(--ac);background:#eef2ff;font-weight:650}
  .rcok{font-size:11.5px;color:var(--int);font-weight:600;align-self:flex-end;padding-bottom:6px}
  /* ── Focus visibility + screen-reader-only text (a11y) ───────────────── */
  :focus-visible{outline:2px solid var(--ac);outline-offset:2px;border-radius:6px}
  .tab:focus-visible,.chip:focus-visible,.item:focus-visible,.mp:focus-visible,.badge:focus-visible{outline-offset:-2px}
  .vh{position:absolute!important;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap;border:0}
  .khint{margin-left:auto;border:1px solid var(--bd);background:var(--card);color:var(--mut);border-radius:8px;padding:5px 11px;font-size:12px;font-weight:600;cursor:pointer;box-shadow:var(--shadow)}
  .khint:hover{color:var(--ac);border-color:var(--ac)}
  /* ── Toasts (replace alert/confirm) ──────────────────────────────────── */
  #toasts{position:fixed;left:50%;bottom:22px;transform:translateX(-50%);z-index:60;display:flex;flex-direction:column;gap:8px;align-items:center;pointer-events:none}
  .toast{pointer-events:auto;background:#15181c;color:#fff;border-radius:10px;padding:10px 14px;font-size:13px;box-shadow:0 6px 22px rgba(20,24,28,.25);display:flex;align-items:center;gap:14px;max-width:min(92vw,460px);animation:tin .18s ease}
  .toast.err{background:#b4232c}.toast.ok{background:#0d7a6f}
  .toast .ta{color:#9ec5ff;font-weight:700;cursor:pointer;white-space:nowrap;background:none;border:none;font-size:13px;padding:0}
  .toast.err .ta{color:#ffd9d9}.toast.ok .ta{color:#bff0e8}
  @keyframes tin{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
  /* ── Keyboard help overlay ───────────────────────────────────────────── */
  .kbd{position:fixed;inset:0;background:rgba(20,24,28,.45);z-index:70;display:flex;align-items:center;justify-content:center;padding:20px}
  .kbd.hidden{display:none}
  .kbd-card{background:var(--card);border-radius:14px;box-shadow:0 12px 40px rgba(20,24,28,.3);max-width:480px;width:100%;max-height:82vh;overflow:auto;padding:20px 24px}
  .kbd-card h3{margin:0 0 14px;font-size:15px}
  .kbd-card table{box-shadow:none;border:none;border-radius:0}.kbd-card td{border:none;padding:5px 8px;font-size:13px;background:none}.kbd-card td:first-child{width:96px;white-space:nowrap}
  .kbd-card kbd{background:var(--bg);border:1px solid var(--bd);border-bottom-width:2px;border-radius:6px;padding:1px 7px;font:12px ui-monospace,Menlo,monospace;color:var(--tx)}
  .kbd-card .kx{float:right;cursor:pointer;color:var(--mut);font-size:20px;line-height:1;background:none;border:none}
  @media(prefers-reduced-motion:reduce){.toast{animation:none}*{scroll-behavior:auto!important}}
  /* ── Responsive: tablet / phone ──────────────────────────────────────── */
  @media(max-width:600px){
    html,body{overflow-x:hidden}
    header{padding:12px 14px}.htop{gap:8px}h1{font-size:16px}
    .stats{gap:6px}.stat{flex:1 1 calc(33.333% - 8px);min-width:0;padding:6px 9px}.stat .n{font-size:15px}.stat .l{font-size:9.5px}
    .wrap{padding:12px 10px 32px}
    .toolbar{gap:6px}input[type=search]{min-width:140px;flex:1 1 100%;order:-1}
    .tabs{overflow-x:auto;-webkit-overflow-scrolling:touch}.tab{flex:0 0 auto}
    .khint{display:none}
    .detail{padding:16px 14px;overflow-x:hidden}.detail h2{font-size:17px}
    .conv-s,.conv-f,.lmsg .conv-s,.lmsg .conv-f{min-width:0}
    .js .jb{padding:10px}.fr{grid-template-columns:92px 1fr 11px;gap:7px}
    .pv{width:100vw}
  }
</style>
</head>
<body>
<header>
  <div class="htop"><h1 id="title">email-2-data · workspace</h1><span class="sub" id="sub"></span>
    <a class="nlink" href="/">Fila</a>
    <a class="nlink" href="/contrapartes">Contrapartes</a>
    <a class="nlink" href="/projetos">Projetos</a>
    <a class="nlink" href="/para-ti">Para ti</a>
    <button class="khint" id="syncBtn" onclick="syncNow()" title="buscar emails novos + triagem (incremental)" style="display:none">↻ Sincronizar</button>
    <span class="sub" id="syncStatus" aria-live="polite"></span>
    <button class="khint" onclick="toggleHelp(true)" title="atalhos de teclado (?)" aria-label="Mostrar atalhos de teclado">⌨ atalhos</button></div>
  <div class="stats" id="stats" aria-label="Resumo da triagem"></div>
  <div class="distbar" id="dist" role="img" title="priority mix"></div>
</header>
<div id="toasts" aria-live="polite" aria-atomic="true"></div>
<div id="kbd" class="kbd hidden" role="dialog" aria-modal="true" aria-label="Atalhos de teclado" onclick="if(event.target===this)toggleHelp(false)">
  <div class="kbd-card">
    <button class="kx" onclick="toggleHelp(false)" aria-label="Fechar">✕</button>
    <h3>Atalhos de teclado</h3>
    <table><tbody>
      <tr><td><kbd>j</kbd> / <kbd>↓</kbd></td><td>seguinte na lista</td></tr>
      <tr><td><kbd>k</kbd> / <kbd>↑</kbd></td><td>anterior na lista</td></tr>
      <tr><td><kbd>Enter</kbd> / <kbd>o</kbd></td><td>abrir o selecionado</td></tr>
      <tr><td><kbd>/</kbd></td><td>focar a pesquisa</td></tr>
      <tr><td><kbd>r</kbd></td><td>reclassificar (corrigir o LLM)</td></tr>
      <tr><td><kbd>c</kbd></td><td>copiar resposta sugerida</td></tr>
      <tr><td><kbd>1</kbd>–<kbd>4</kbd></td><td>mudar de separador</td></tr>
      <tr><td><kbd>Esc</kbd></td><td>fechar / sair da pesquisa</td></tr>
      <tr><td><kbd>?</kbd></td><td>mostrar esta ajuda</td></tr>
    </tbody></table>
  </div>
</div>
<div id="pv" class="pv hidden">
  <div class="pvh">📎 <span class="nm" id="pvttl"></span>
    <a id="pvlnk" class="link" target="_blank" rel="noopener" style="margin-left:auto" title="abrir em nova aba">↗ nova aba</a>
    <span class="x" role="button" tabindex="0" onclick="closePreview()" title="fechar (Esc)" aria-label="Fechar pré-visualização">✕</span></div>
  <div class="pvbody" id="pvbody"></div>
</div>
<div class="wrap">
  <div class="tabs" role="tablist" aria-label="Vistas">
    <div class="tab on" role="tab" tabindex="0" aria-selected="true" data-tab="emails" onclick="switchTab('emails')">Emails</div>
    <div class="tab" role="tab" tabindex="0" aria-selected="false" data-tab="contacts" onclick="switchTab('contacts')">Contactos</div>
    <div class="tab" role="tab" tabindex="0" aria-selected="false" data-tab="leads" onclick="switchTab('leads')">Leads</div>
    <div class="tab" role="tab" tabindex="0" aria-selected="false" data-tab="projects" onclick="switchTab('projects')">Projetos</div>
  </div>
  <div id="emails">
    <div class="toolbar">
      <input type="search" id="q" placeholder="Procurar assunto, remetente, corpo…" aria-label="Procurar emails" oninput="_deb('q',function(){renderFilters('emails');renderList();})"/>
      <span class="count" id="ecount"></span>
    </div>
    <div id="filters" class="filters" aria-label="Filtros"></div>
    <div id="banner"></div>
    <div class="layout">
      <div class="list" id="list"></div>
      <div class="detail" id="detail"><div class="empty">Selecione um email — ou clique numa pessoa, organização ou thread.</div></div>
    </div>
  </div>
  <div id="contacts" class="hidden">
    <div class="toolbar">
      <input type="search" id="cq" placeholder="Procurar nome, email, domínio…" aria-label="Procurar contactos" oninput="_deb('cq',function(){renderFilters('contacts');renderContacts();})"/>
      <span class="muted" style="font-size:11px;text-transform:uppercase;letter-spacing:.05em;font-weight:700">ordenar</span>
      <span class="chip on" id="csort-msgs" onclick="setCSort('msgs')">+ mensagens</span>
      <span class="chip" id="csort-silent" onclick="setCSort('silent')">+ silenciosos</span>
      <span class="chip" id="csort-name" onclick="setCSort('name')">A→Z</span>
      <span class="count" id="ccount"></span>
    </div>
    <div id="cfilters" class="filters" aria-label="Filtros"></div>
    <div class="layout">
      <div class="list" id="clist"></div>
      <div class="detail" id="cdetail"><div class="empty">Selecione um contacto para ver o historial e ligações.</div></div>
    </div>
  </div>
  <div id="leads" class="hidden">
    <div class="toolbar">
      <input type="search" id="lq" placeholder="Procurar cliente, assunto…" aria-label="Procurar leads" oninput="_deb('lq',function(){renderFilters('leads');renderLeads();})"/>
      <span class="count" id="lcount"></span>
    </div>
    <div id="lfilters" class="filters" aria-label="Filtros"></div>
    <div class="layout">
      <div class="list" id="llist"></div>
      <div class="detail" id="ldetail"><div class="empty">Selecione uma conversa.</div></div>
    </div>
  </div>
  <div id="projects" class="hidden">
    <div class="toolbar">
      <input type="search" id="pq" placeholder="Procurar projeto, cliente…" aria-label="Procurar projetos" oninput="_deb('pq',function(){renderFilters('projects');renderProjects();})"/>
      <input id="pnew-title" placeholder="Título do novo projeto" style="flex:0 0 220px"/>
      <span class="chip on" onclick="createProject()">+ Novo projeto</span>
      <span class="count" id="pcount"></span>
    </div>
    <div id="pfilters" class="filters" aria-label="Filtros"></div>
    <div class="layout">
      <div class="list" id="plist"></div>
      <div class="detail" id="pdetail"><div class="empty">Selecione um projeto.</div></div>
    </div>
  </div>
</div>
<script>
const EMAILS=__EMAILS__, CONTACTS=__CONTACTS__, COST=__COST__, OURDOMAIN=__OURDOMAIN__;
const LIVE=__LIVE__, MUST=__MUST__, SHOULD=__SHOULD__;
const ITEM_KEYS=__ITEMKEYS__, JOB_KEYS=__JOBKEYS__, ITEM_MUST=__ITEMMUST__, JOB_MUST=__JOBMUST__;
const RECLASSIFICATIONS=__RECLASSIFICATIONS__;
const PRIcol={HIGH:'#e5484d',NEEDS_REVIEW:'#8e4ec6',MEDIUM:'#f5a623',LOW:'#3358d4',IGNORE:'#9aa1ab'};
const CPcol={CLIENT:'#13a36a',LEAD:'#0d9488',SUPPLIER:'#3358d4',INTERNAL:'#7c7f86',BULK:'#b9bbc6',OTHER:'#8b8d98'};
const PRIS=['HIGH','NEEDS_REVIEW','MEDIUM','LOW','IGNORE'], ROLE={from:'From',reply_to:'Reply-To',to:'To',cc:'Cc'};
const CPpt={CLIENT:'Cliente',LEAD:'Lead',SUPPLIER:'Fornecedor',INTERNAL:'Interno',BULK:'Newsletter',OTHER:'Outro'};
const PURpt={PO_FROM_CLIENT:'Encomenda de cliente',ESTIMATE_REQUEST_FROM_CLIENT:'Pedido de orçamento',OUTBOUND_INVOICE:'Fatura nossa',OUR_ORDER_TO_SUPPLIER:'Encomenda a fornecedor',SUPPLIER_REPLY_OR_CONFIRMATION:'Resposta de fornecedor',INVOICE_OR_ACCOUNTING:'Fatura / contabilidade',FOLLOW_UP:'Seguimento',PUBLICITY:'Publicidade',INTERNAL_OPS:'Operações internas',OTHER:'Outro'};
const PRIpt={HIGH:'Alta',MEDIUM:'Média',LOW:'Baixa',IGNORE:'Ignorar',NEEDS_REVIEW:'Rever'};
const DIRpt={inbound:'entrada',outbound:'saída',internal:'interno'};
// Stage + lead-status labels/colors — defined here so the project/lead facet registries can use them.
const STAGEcol={LEAD:'#0d9488',GATHERING:'#3358d4',ESTIMABLE:'#13a36a',QUOTED:'#7c5cff',WON:'#13a36a',LOST:'#e5484d',ARCHIVED:'#9aa1ab'};
const STAGEpt={LEAD:'Lead',GATHERING:'A reunir',ESTIMABLE:'Orçamentável',QUOTED:'Orçamentado',WON:'Ganho',LOST:'Perdido',ARCHIVED:'Arquivado'};
const LSTATUSpt={unanswered:'Sem resposta',waiting:'A aguardar deles',active:'Ativa'};
const LSTATUScol={unanswered:'#e5484d',waiting:'#f5a623',active:'#13a36a'};
// ─── Generic faceted-filter registry, keyed by tab ───────────────────────────
// Each tab declares facets over ITS object type. enum = multi-select chips (OR within, AND across);
// flag = single toggle. Bucket fields are enums over computed ranges. The engine
// (matchFacets/facetCounts/applyFacets/renderFilters) is fully generic and shared by all four tabs;
// dataset()/search() make counts + free-text search per-tab. To filter on a NEW data point, add one
// entry to the relevant tab. NOTE: facet test()/dataset() may reference helpers defined later in this
// script (realAtts, _allLeadThreads, projData, …) — fine, they're only invoked at render time.
const _NOW=new Date();
function _daysAgo(s){if(!s)return null;const t=Date.parse(String(s).slice(0,10));return isNaN(t)?null:Math.floor((_NOW-t)/86400000);}
function _volBucket(n,v){n=n||0;return v==='1'?n===1:v==='s'?(n>=2&&n<=5):v==='m'?(n>=6&&n<=20):n>20;}
const TABFILTERS={
  emails:{searchId:'q', dataset:()=>EMAILS,
    search:e=>`${e.subject} ${e.from_addr} ${e.reason} ${e.purpose} ${e.counterparty} ${e._body}`,
    groups:['Prioridade','Tipo','Purpose','Direção','Sinais','Estado','Entidades'],
    facets:[
      {key:'priority',group:'Prioridade',type:'enum',values:PRIS,labelFor:v=>PRIpt[v]||v,colorFor:v=>PRIcol[v],test:(e,v)=>e.priority===v},
      {key:'counterparty',group:'Tipo',type:'enum',values:Object.keys(CPpt),labelFor:v=>CPpt[v]||v,colorFor:v=>CPcol[v],test:(e,v)=>e.counterparty===v},
      {key:'purpose',group:'Purpose',type:'enum',values:Object.keys(PURpt),labelFor:v=>PURpt[v]||v,test:(e,v)=>e.purpose===v},
      {key:'direction',group:'Direção',type:'enum',label:'Direção',values:['inbound','outbound','internal'],labelFor:v=>DIRpt[v]||v,test:(e,v)=>e.direction===v},
      {key:'decided',group:'Direção',type:'enum',label:'Decisão',values:['offline','llm'],labelFor:v=>v==='offline'?'offline':'via LLM',test:(e,v)=>{const t0=(e.decided_by||'').startsWith('tier0');return v==='offline'?t0:!t0;}},
      {key:'urgency',group:'Sinais',type:'enum',label:'Urgência',values:['hi','mid','lo'],labelFor:v=>({hi:'alta ≥70',mid:'média 40–69',lo:'baixa <40'}[v]),test:(e,v)=>{const u=e.urgency||0;return v==='hi'?u>=70:v==='mid'?(u>=40&&u<70):u<40;}},
      {key:'confidence',group:'Sinais',type:'enum',label:'Confiança',values:['hi','lo'],labelFor:v=>v==='hi'?'alta ≥0.7':'baixa <0.7',test:(e,v)=>{const c=e.confidence==null?1:e.confidence;return v==='hi'?c>=0.7:c<0.7;}},
      {key:'received',group:'Sinais',type:'enum',label:'Recebido',values:['today','w','m','old'],labelFor:v=>({today:'hoje',w:'últ. 7 dias',m:'últ. 30 dias',old:'+ antigo'}[v]),test:(e,v)=>{const d=_daysAgo(e._date);if(d==null)return false;return v==='today'?d===0:v==='w'?(d>=1&&d<=7):v==='m'?(d>=8&&d<=30):d>30;}},
      {key:'has_attachment',group:'Estado',type:'flag',label:'Com anexo',test:e=>realAtts(e).length>0},
      {key:'is_reply',group:'Estado',type:'flag',label:'É resposta',test:e=>!!e._reply},
      {key:'unanswered',group:'Estado',type:'flag',label:'Sem resposta',test:e=>!threadHasOutbound[e._thread]&&_isLeadThread(e._thread)&&e.direction!=='outbound'},
      {key:'has_jobspec',group:'Estado',type:'flag',label:'Tem job-spec',test:e=>!!e._jobspec},
      {key:'estimable',group:'Estado',type:'flag',label:'Orçamentável',test:e=>!!(e._jobspec&&e._jobspec.readiness&&e._jobspec.readiness.estimable)},
      {key:'reclassified',group:'Estado',type:'flag',label:'Corrigido',test:e=>!!(e._auto_counterparty||e._auto_purpose||e._auto_priority)},
      {key:'has_deadline',group:'Entidades',type:'flag',label:'Prazo',test:e=>!!(e.entities&&e.entities.deadline)},
      {key:'has_money',group:'Entidades',type:'flag',label:'Valor',test:e=>!!(e.entities&&e.entities.money)},
      {key:'has_nif',group:'Entidades',type:'flag',label:'NIF',test:e=>!!(e.entities&&e.entities.nif)},
      {key:'has_iban',group:'Entidades',type:'flag',label:'IBAN',test:e=>!!(e.entities&&e.entities.iban)},
    ]},
  contacts:{searchId:'cq', dataset:()=>CONTACTS,
    search:c=>`${c.display_name||''} ${c.email} ${c.domain||''}`,
    groups:['Tipo','Purpose','Origem','Atividade','Volume'],
    facets:[
      {key:'last_counterparty',group:'Tipo',type:'enum',label:'Tipo',values:Object.keys(CPpt),labelFor:v=>CPpt[v]||v,colorFor:v=>CPcol[v],test:(c,v)=>c.last_counterparty===v},
      {key:'last_purpose',group:'Purpose',type:'enum',label:'Purpose',values:Object.keys(PURpt),labelFor:v=>PURpt[v]||v,test:(c,v)=>c.last_purpose===v},
      {key:'origin',group:'Origem',type:'enum',label:'Origem',values:['int','ext'],labelFor:v=>v==='int'?'interno':'externo',test:(c,v)=>v==='int'?!!c.is_internal:!c.is_internal},
      {key:'activity',group:'Atividade',type:'enum',label:'Visto',values:['w','m','q','silent'],labelFor:v=>({w:'últ. 7 dias',m:'últ. 30 dias',q:'últ. 90 dias',silent:'silencioso >90d'}[v]),test:(c,v)=>{const d=_daysAgo(c.last_from_date);if(d==null)return v==='silent';return v==='w'?d<=7:v==='m'?(d>7&&d<=30):v==='q'?(d>30&&d<=90):d>90;}},
      {key:'volume',group:'Volume',type:'enum',label:'Mensagens',values:['1','s','m','l'],labelFor:v=>({'1':'1',s:'2–5',m:'6–20',l:'20+'}[v]),test:(c,v)=>_volBucket(c.msg_count,v)},
    ]},
  leads:{searchId:'lq', dataset:()=>_allLeadThreads(),
    search:t=>`${(t.emails[0]&&t.emails[0].subject)||''} ${_threadClientEmail(t.thread)} ${nameOf(_threadClientEmail(t.thread))}`,
    groups:['Estado','Espera','Marcadores'],
    facets:[
      {key:'status',group:'Estado',type:'enum',label:'Estado',values:['unanswered','waiting','active'],labelFor:v=>LSTATUSpt[v]||v,colorFor:v=>LSTATUScol[v],test:(t,v)=>t.status===v},
      {key:'wait',group:'Espera',type:'enum',label:'Espera',values:['a','b','c','d'],labelFor:v=>({a:'≤3 dias',b:'4–7 dias',c:'8–30 dias',d:'>30 dias'}[v]),test:(t,v)=>{const d=t.ds;if(d==null)return false;return v==='a'?d<=3:v==='b'?(d>=4&&d<=7):v==='c'?(d>=8&&d<=30):d>30;}},
      {key:'estimable',group:'Marcadores',type:'flag',label:'Orçamentável',test:t=>{const b=_bestJobspecEmail(t.emails);return !!(b&&b._jobspec&&b._jobspec.readiness&&b._jobspec.readiness.estimable);}},
      {key:'has_attachment',group:'Marcadores',type:'flag',label:'Com anexo',test:t=>t.emails.some(e=>realAtts(e).length>0)},
    ]},
  projects:{searchId:'pq', dataset:()=>projData,
    search:p=>`${p.title||''} ${p.client_name||''} ${p.client_email||''}`,
    groups:['Estágio','Cobertura','Marcadores'],
    facets:[
      {key:'stage',group:'Estágio',type:'enum',label:'Estágio',values:Object.keys(STAGEcol),labelFor:v=>STAGEpt[v]||v,colorFor:v=>STAGEcol[v],test:(p,v)=>p.stage===v},
      {key:'coverage',group:'Cobertura',type:'enum',label:'Cobertura',values:['full','hi','lo'],labelFor:v=>({full:'completa',hi:'alta ≥70%',lo:'baixa <70%'}[v]),test:(p,v)=>{const c=p.coverage||0;return v==='full'?c>=0.999:v==='hi'?(c>=0.7&&c<0.999):c<0.7;}},
      {key:'estimable',group:'Marcadores',type:'flag',label:'Orçamentável',test:p=>!!p.estimable},
      {key:'exported',group:'Marcadores',type:'flag',label:'Exportado',test:p=>!!p.external_id},
    ]},
};
// Per-tab facet lookup + per-tab filter state. FSTATE[tab][key] = Set (enum) | true (flag).
Object.values(TABFILTERS).forEach(t=>{t.by={};t.facets.forEach(f=>t.by[f.key]=f);});
const FSTATE={emails:{},contacts:{},leads:{},projects:{}};
const FCOLLAPSED={emails:true,contacts:true,leads:true,projects:true};
// Per-item fields shown as rows; job-level split into must (shown) + optional (in <details>).
const ITEM_OPT=ITEM_KEYS.filter(k=>!ITEM_MUST.includes(k));               // e.g. colour_finish
const JOB_OPT=JOB_KEYS.filter(k=>!JOB_MUST.includes(k)&&k!=='client_identity'); // quality/delivery/budget

const byMid={}, threadMids={}, threadHasOutbound={}, personMids={}, orgs={}, contactBy={};
function domainOf(e){return (e.split('@')[1]||'').toLowerCase();}
EMAILS.forEach(e=>{
  byMid[e.message_id]=e;
  (threadMids[e._thread]=threadMids[e._thread]||new Set()).add(e.message_id);
  if(e.direction==='outbound') threadHasOutbound[e._thread]=true;
  (e._people||[]).forEach(p=>{
    (personMids[p.email]=personMids[p.email]||new Set()).add(e.message_id);
    const d=domainOf(p.email);
    const o=orgs[d]=orgs[d]||{people:new Set(),mids:new Set(),internal:p.internal};
    o.people.add(p.email); o.mids.add(e.message_id);
  });
});
CONTACTS.forEach(c=>contactBy[c.email]=c);
// Apply stored human reclassifications to email objects (mutates in-place so list badges + filters pick them up)
Object.entries(RECLASSIFICATIONS).forEach(([mid,ov])=>{
  const e=byMid[mid]; if(!e)return;
  ['counterparty','purpose','priority'].forEach(f=>{if(ov[f]){e['_auto_'+f]=e[f];e[f]=ov[f];}});
});
function nameOf(e){return (contactBy[e]&&contactBy[e].display_name)||e.split('@')[0];}
function isInt(e){return contactBy[e]?!!contactBy[e].is_internal:(domainOf(e)==OURDOMAIN||domainOf(e).endsWith('.'+OURDOMAIN));}
function esc(s){return (s==null?'':String(s)).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function badge(t,c,clk){return `<span class="badge ${clk?'clk':''}" style="background:${c}22;color:${c}" ${clk?`onclick="${clk}"`:''}>${esc(t)}</span>`;}
function fdate(s){return s?esc(String(s).slice(0,16).replace('T',' ')):'';}
function fTok(n){return (n||0).toLocaleString();}
function fCost(v){return v?('$'+v.toFixed(5)):'$0';}
function costOf(mids){let i=0,o=0,c=0;mids.forEach(m=>{const e=byMid[m];i+=e._tin;o+=e._tout;c+=e._cost;});return{i,o,c};}
function costPill(e){return e._cost?`<span class="pill cost" title="triage tokens">${fTok(e._tin)} in · ${fTok(e._tout)} out · ${fCost(e._cost)}</span>`:'<span class="pill cost">offline · $0</span>';}

let navStack=[];
function navTo(kind,id){navStack.push({kind,id});renderList();}
function navReset(kind,id){navStack=[{kind,id}];renderList();}
function back(){if(navStack.length>1){navStack.pop();renderList();}}

// ─── Debounced search (one timer per input id) ───────────────────────────────
const _debT={};
function _deb(key,fn){clearTimeout(_debT[key]);_debT[key]=setTimeout(fn,140);}

// ─── Toasts (replace alert/confirm) ──────────────────────────────────────────
function toast(msg,opts){opts=opts||{};
  const c=document.getElementById('toasts'); if(!c)return ()=>{};
  const t=document.createElement('div');
  t.className='toast'+(opts.type==='error'?' err':opts.type==='ok'?' ok':'');
  t.setAttribute('role','status');
  const span=document.createElement('span'); span.textContent=msg; t.appendChild(span);
  let killed=false; const kill=()=>{if(killed)return;killed=true;t.remove();};
  if(opts.undo){const a=document.createElement('button');a.type='button';a.className='ta';a.textContent=opts.undoLabel||'Anular';
    a.onclick=()=>{try{opts.undo();}finally{kill();}};t.appendChild(a);}
  c.appendChild(t);
  setTimeout(kill,opts.ms||(opts.undo?6000:3200));
  return kill;
}

// ─── Keyboard help overlay ───────────────────────────────────────────────────
function toggleHelp(force){const k=document.getElementById('kbd');if(!k)return;
  const show=force===undefined?k.classList.contains('hidden'):!!force;
  k.classList.toggle('hidden',!show);}

// ─── Active-tab tracking + keyboard navigation ───────────────────────────────
let curTab='emails';
const _vis={emails:[],contacts:[],leads:[],projects:[]};   // visible row ids per tab (set by each render)
const _LISTID={emails:'list',contacts:'clist',leads:'llist',projects:'plist'};
const _DETAILID={emails:'detail',contacts:'cdetail',leads:'ldetail',projects:'pdetail'};
const _SEARCHID={emails:'q',contacts:'cq',leads:'lq',projects:'pq'};
function curSel(){
  if(curTab==='emails'){const t=navStack[navStack.length-1];return t&&t.kind==='email'?t.id:null;}
  if(curTab==='contacts')return selContact;
  if(curTab==='leads')return selLeadThread;
  if(curTab==='projects')return selProject;
  return null;
}
function selectId(id){
  if(id==null)return;
  if(curTab==='emails')navReset('email',id);
  else if(curTab==='contacts'){selContact=id;renderContacts();}
  else if(curTab==='leads'){selLeadThread=id;renderLeads();}
  else if(curTab==='projects'){selProject=id;projDetail=null;renderProjects();}
  _scrollSelIntoView();
}
function _scrollSelIntoView(){requestAnimationFrame(()=>{
  const el=document.querySelector('#'+_LISTID[curTab]+' .item.sel');
  if(el)el.scrollIntoView({block:'nearest'});});}
function navMove(d){const ids=_vis[curTab]||[];if(!ids.length)return;
  let i=ids.indexOf(curSel());i=i<0?(d>0?0:0):Math.min(ids.length-1,Math.max(0,i+d));
  selectId(ids[i]);}
function openReclassify(){const id=curSel();if(!id)return;
  const det=document.getElementById('rc_'+_sid(id));
  if(det){det.open=true;const sel=det.querySelector('select');if(sel){sel.focus();det.scrollIntoView({block:'nearest'});}}}
function copyCurrentReply(){const ta=document.querySelector('#'+_DETAILID[curTab]+' textarea[id^="jreply_"]');
  if(ta){ta.select();try{document.execCommand('copy');toast('Resposta copiada',{type:'ok'});}catch(_){}}}
function onKey(ev){
  const k=document.getElementById('kbd'), helpOpen=k&&!k.classList.contains('hidden');
  const tag=(ev.target.tagName||'').toUpperCase();
  const typing=tag==='INPUT'||tag==='TEXTAREA'||tag==='SELECT'||ev.target.isContentEditable;
  if(ev.key==='Escape'){
    if(helpOpen){toggleHelp(false);return;}
    if(!document.getElementById('pv').classList.contains('hidden')){closePreview();return;}
    if(typing){ev.target.blur();return;}
    return;
  }
  if(ev.metaKey||ev.ctrlKey||ev.altKey)return;
  if(helpOpen){if(ev.key==='?')toggleHelp(false);return;}
  // Enter/Space activates a focused custom control (list item, chip, tab, role=button)
  if((ev.key==='Enter'||ev.key===' ')&&document.activeElement&&document.activeElement!==document.body
     &&document.activeElement.matches&&document.activeElement.matches('.item,.chip,.tab,[role="button"]')){
    ev.preventDefault();document.activeElement.click();return;}
  if(typing)return;   // never hijack while typing in a field
  switch(ev.key){
    case 'j':case 'ArrowDown':ev.preventDefault();navMove(1);break;
    case 'k':case 'ArrowUp':ev.preventDefault();navMove(-1);break;
    case 'o':case 'Enter':selectId(curSel());break;
    case '/':ev.preventDefault();{const el=document.getElementById(_SEARCHID[curTab]);if(el)el.focus();}break;
    case 'r':if(curTab==='emails')openReclassify();break;
    case 'c':copyCurrentReply();break;
    case '?':toggleHelp(true);break;
    case '1':switchTab('emails');break;
    case '2':switchTab('contacts');break;
    case '3':switchTab('leads');break;
    case '4':switchTab('projects');break;
  }
}

// ─── URL deep-linking (hash router) ──────────────────────────────────────────
let _urlReady=false;
// Registry-driven, per active tab: enum facets → comma-joined values under their key; flags → key=1;
// the tab's search box under its searchId; plus the tab's selection + (emails) thread.
function writeURL(){
  if(!_urlReady)return;
  const p=new URLSearchParams(); p.set('tab',curTab);
  const cfg=TABFILTERS[curTab], F=FSTATE[curTab];
  if(cfg){
    cfg.facets.forEach(f=>{const v=F[f.key]; if(!v)return; p.set(f.key, f.type==='flag'?'1':[...v].join(','));});
    const sv=(document.getElementById(cfg.searchId)||{}).value||''; if(sv)p.set(cfg.searchId,sv);
  }
  if(curTab==='emails'){const t=navStack[navStack.length-1]; if(t&&t.kind==='email')p.set('sel',t.id); if(threadFilter)p.set('thread',threadFilter);}
  else if(curTab==='contacts'){if(selContact)p.set('sel',selContact);}
  else if(curTab==='leads'){if(selLeadThread)p.set('sel',selLeadThread);}
  else if(curTab==='projects'){if(selProject)p.set('sel',selProject);}
  const h='#'+p.toString();
  if(location.hash!==h){try{history.replaceState(null,'',h);}catch(_){}}
}
function applyURLState(){
  const h=(location.hash||'').slice(1); if(!h)return null;
  const p=new URLSearchParams(h); const tab=p.get('tab')||'emails';
  const cfg=TABFILTERS[tab];
  if(cfg){
    const F={};
    cfg.facets.forEach(f=>{const raw=p.get(f.key); if(raw==null)return;
      if(f.type==='flag'){F[f.key]=true;}
      else{const vs=raw.split(',').filter(v=>f.values.includes(v)); if(vs.length)F[f.key]=new Set(vs);}});
    FSTATE[tab]=F;
    const qel=document.getElementById(cfg.searchId); if(qel)qel.value=p.get(cfg.searchId)||'';
  }
  if(tab==='emails'){threadFilter=p.get('thread')||''; const sel=p.get('sel'); if(sel&&byMid[sel])navStack=[{kind:'email',id:sel}];}
  else if(tab==='contacts'){selContact=p.get('sel')||null;}
  else if(tab==='leads'){selLeadThread=p.get('sel')||null;}
  else if(tab==='projects'){selProject=p.get('sel')||null;}
  return tab;
}

// threadFilter (emails only) is a special, non-registry filter set by clicking "↳ N na thread".
let threadFilter='';
const _LEAD_CPS=new Set(['LEAD','CLIENT']), _LEAD_PURS=new Set(['ESTIMATE_REQUEST_FROM_CLIENT','PO_FROM_CLIENT']);
function _isLeadThread(t){return [...(threadMids[t]||[])].some(m=>{const e=byMid[m];return e&&(_LEAD_CPS.has(e.counterparty)||_LEAD_PURS.has(e.purpose));});}
const _RERENDER={emails:()=>renderList(),contacts:()=>renderContacts(),leads:()=>renderLeads(),projects:()=>renderProjects()};
function anyFilter(tab){return Object.keys(FSTATE[tab]).length>0||(tab==='emails'&&!!threadFilter);}
function _afterFilterChange(tab){renderFilters(tab);_RERENDER[tab]();}   // re-render also writes the URL
function toggleFacet(tab,key,v){const F=FSTATE[tab],s=F[key]||(F[key]=new Set());s.has(v)?s.delete(v):s.add(v);if(!s.size)delete F[key];_afterFilterChange(tab);}
function toggleFlag(tab,key){const F=FSTATE[tab];F[key]?delete F[key]:F[key]=true;_afterFilterChange(tab);}
function setCp(cp){toggleFacet('emails','counterparty',cp);}   // email badge click-to-filter (detail view)
function setThread(t){threadFilter=(threadFilter==t?'':t);_afterFilterChange('emails');}
function toggleFiltersPanel(tab){FCOLLAPSED[tab]=!FCOLLAPSED[tab];renderFilters(tab);}
function clearFilters(tab){FSTATE[tab]={};if(tab==='emails')threadFilter='';_afterFilterChange(tab);}
// Does object o pass every active facet of `tab`? (flag → test true; enum → ≥1 selected value matches)
function matchFacets(tab,o,exceptKey){
  const F=FSTATE[tab],by=TABFILTERS[tab].by;
  for(const k in F){
    if(k===exceptKey)continue;
    const f=by[k]; if(!f)continue;
    if(f.type==='flag'){if(!f.test(o))return false;}
    else{let ok=false;for(const v of F[k]){if(f.test(o,v)){ok=true;break;}}if(!ok)return false;}
  }
  return true;
}
function searchPass(tab,o){
  const cfg=TABFILTERS[tab],el=document.getElementById(cfg.searchId),q=((el&&el.value)||'').toLowerCase();
  return !q||cfg.search(o).toLowerCase().includes(q);
}
// Filter an already-loaded array through a tab's active facets + its search box.
function applyFacets(tab,rows){return rows.filter(o=>searchPass(tab,o)&&matchFacets(tab,o));}
function filtered(){return EMAILS.filter(e=>(!threadFilter||e._thread===threadFilter)&&searchPass('emails',e)&&matchFacets('emails',e));}
// Narrowing counts: how many results each chip would yield given the REST of the active selection.
function facetCounts(tab,f){
  const base=TABFILTERS[tab].dataset().filter(o=>searchPass(tab,o)&&matchFacets(tab,o,f.key)&&(tab!=='emails'||!threadFilter||o._thread===threadFilter));
  if(f.type==='flag')return base.reduce((n,o)=>n+(f.test(o)?1:0),0);
  const c={}; f.values.forEach(v=>c[v]=0);
  base.forEach(o=>f.values.forEach(v=>{if(f.test(o,v))c[v]++;}));
  return c;
}

async function syncNow(){
  if(!LIVE)return;
  const btn=document.getElementById('syncBtn'), st=document.getElementById('syncStatus');
  btn.disabled=true; st.textContent='a sincronizar…';
  try{
    const r=await fetch('/api/sync',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    if(r.status===409){toast('Sincronização já em curso.',{type:'error'});st.textContent='';btn.disabled=false;return;}
    if(!r.ok){const e=await r.json().catch(()=>({}));toast('Sincronização falhou: '+(e.error||r.status),{type:'error'});st.textContent='';btn.disabled=false;return;}
    const c=await r.json();
    toast(`Sincronizado: ${c.fetched} no corpus, ${c.triaged_new} novos triados, ${c.triaged_skipped} já feitos.`);
    setTimeout(()=>location.reload(),700);
  }catch(e){toast('Falha na sincronização.',{type:'error'});st.textContent='';btn.disabled=false;}
}
// Reflect a background (startup) sync; once it finishes with new mail, refresh to show it.
async function pollSyncStatus(){
  if(!LIVE)return;
  try{
    const r=await fetch('/api/sync/status'); if(!r.ok)return;
    const s=await r.json(), st=document.getElementById('syncStatus');
    if(s.running){st.textContent='a sincronizar…'; setTimeout(pollSyncStatus,1500);}
    else if(st.textContent==='a sincronizar…'){location.reload();}
  }catch(e){}
}

function renderHeader(){
  const n=EMAILS.length,by={};EMAILS.forEach(e=>by[e.priority]=(by[e.priority]||0)+1);
  document.getElementById('title').textContent = LIVE ? 'email-2-data · workspace (live)' : 'email-2-data · triage report';
  if(LIVE){const sb=document.getElementById('syncBtn'); if(sb){sb.style.display='';} pollSyncStatus();}
  document.getElementById('sub').textContent=`${n} emails · ${COST.offline_pct??'–'}% offline / ${COST.llm_pct??'–'}% via LLM · ${COST.model||''}`;
  const cards=[['Emails',n,'','total triado'],['Offline',COST.offline_calls??'–','','decididos sem LLM (grátis)'],['Via LLM',COST.llm_calls??'–','','classificados pelo LLM'],
    ['Custo total','$'+(COST.cost_usd??0).toFixed(4),'cost','custo desta triagem'],['Por 1000','$'+(COST.cost_per_1k_usd??0).toFixed(3),'cost','custo por mil emails'],
    ['Tokens',fTok((COST.input_tokens||0)+(COST.output_tokens||0)),'','tokens usados']];
  document.getElementById('stats').innerHTML=cards.map(([l,v,c,t])=>`<div class="stat ${c}" title="${t||''}"><div class="n">${v}</div><div class="l">${l}</div></div>`).join('');
  const dist=document.getElementById('dist');
  dist.innerHTML=PRIS.filter(p=>by[p]).map(p=>`<span style="flex:${by[p]};background:${PRIcol[p]}" title="${p}: ${by[p]}"></span>`).join('');
  dist.setAttribute('aria-label','Distribuição de prioridade — '+(PRIS.filter(p=>by[p]).map(p=>`${p}: ${by[p]}`).join(', ')||'sem dados'));
}
// ─── Shared faceted filter panel — collapsed by default, one per tab ─────────
const _FILTEL={emails:'filters',contacts:'cfilters',leads:'lfilters',projects:'pfilters'};
function _chipHtml(tab,f,v,n){
  const sel=FSTATE[tab][f.key]&&FSTATE[tab][f.key].has(v), col=f.colorFor&&f.colorFor(v);
  const style=col&&sel?` style="background:${col};border-color:${col};color:#fff"`:col?` style="border-color:${col}88;color:${col}"`:'';
  return `<span class="chip${sel?' on':''}${n?'':' z'}"${style} role="checkbox" aria-checked="${!!sel}" tabindex="0" onclick="toggleFacet('${tab}','${f.key}','${esc(v)}')">${esc(f.labelFor(v))}<span class="cn">${n}</span></span>`;
}
function _flagHtml(tab,f,n){
  const sel=!!FSTATE[tab][f.key];
  return `<span class="chip${sel?' on':''}${n?'':' z'}" role="checkbox" aria-checked="${sel}" tabindex="0" onclick="toggleFlag('${tab}','${f.key}')">${esc(f.label)}<span class="cn">${n}</span></span>`;
}
// Removable pills shown in the collapsed header — one per active selection.
function _activePills(tab){
  const F=FSTATE[tab],by=TABFILTERS[tab].by,out=[];
  for(const k in F){const f=by[k];if(!f)continue;
    if(f.type==='flag')out.push(`<span class="fpill" onclick="event.stopPropagation();toggleFlag('${tab}','${k}')">${esc(f.label)} ✕</span>`);
    else for(const v of F[k])out.push(`<span class="fpill" onclick="event.stopPropagation();toggleFacet('${tab}','${k}','${esc(v)}')">${esc(f.labelFor(v))} ✕</span>`);
  }
  if(tab==='emails'&&threadFilter){const e=byMid[[...(threadMids[threadFilter]||[])][0]];
    out.push(`<span class="fpill" onclick="event.stopPropagation();setThread('${esc(threadFilter)}')">thread: ${esc(((e&&e.subject)||'').slice(0,24))} ✕</span>`);}
  return out.join('');
}
function renderFilters(tab){
  tab=tab||curTab;
  const cfg=TABFILTERS[tab], el=document.getElementById(_FILTEL[tab]); if(!cfg||!el)return;
  const collapsed=FCOLLAPSED[tab], nact=Object.keys(FSTATE[tab]).length+(tab==='emails'&&threadFilter?1:0);
  let rows='';
  cfg.groups.forEach(g=>{
    const fs=cfg.facets.filter(f=>f.group===g); if(!fs.length)return;
    const multiEnum=fs.filter(f=>f.type==='enum').length>1;
    let cells='';
    fs.forEach(f=>{
      const cnt=facetCounts(tab,f);
      if(f.type==='flag'){cells+=_flagHtml(tab,f,cnt);return;}
      if(multiEnum&&f.label)cells+=`<span class="subt">${esc(f.label)}</span>`;
      cells+=f.values.map(v=>_chipHtml(tab,f,v,cnt[v])).join('');
    });
    rows+=`<div class="frow"><span class="rl">${esc(g)}</span><span class="rc">${cells}</span></div>`;
  });
  const clr=anyFilter(tab)?`<span class="clr" role="button" tabindex="0" onclick="event.stopPropagation();clearFilters('${tab}')">limpar tudo ✕</span>`:'';
  const summary=collapsed?`<span class="fsum">${nact?_activePills(tab):'<span class="muted" style="font-weight:400;text-transform:none;letter-spacing:0">sem filtros — clique para filtrar</span>'}</span>`:'';
  el.className='filters'+(collapsed?' col':'');
  el.innerHTML=`<div class="fhd" role="button" tabindex="0" aria-expanded="${!collapsed}" onclick="toggleFiltersPanel('${tab}')">
      <span class="ar">▾</span> Filtros ${nact?`<span class="nact">${nact}</span>`:''}${summary}${clr}</div>
    <div class="fbody">${rows}</div>`;
}
function renderBanner(){
  if(!threadFilter){document.getElementById('banner').innerHTML='';return;}
  const e=byMid[[...(threadMids[threadFilter]||[])][0]];
  document.getElementById('banner').innerHTML=`<div class="banner">Thread <b>${esc((e&&e.subject)||'')}</b> (${threadMids[threadFilter].size} msgs)<span class="x" onclick="setThread('${esc(threadFilter)}')">limpar ✕</span></div>`;
}
function renderList(){
  renderBanner();
  const rows=filtered(); const top=navStack[navStack.length-1];
  const selMid=top&&top.kind=='email'?top.id:null;
  _vis.emails=rows.map(e=>e.message_id);
  document.getElementById('ecount').textContent=`${rows.length} visíveis`;
  if(!navStack.length&&rows.length)navStack=[{kind:'email',id:rows[0].message_id}];
  document.getElementById('list').innerHTML=rows.map(e=>`
    <div class="item ${e.message_id==selMid?'sel':''}" role="button" tabindex="0" aria-current="${e.message_id==selMid}" aria-label="${esc((e.priority||'')+' '+(e.counterparty||'')+': '+(e.subject||'sem assunto'))}" style="border-left-color:${e.message_id==selMid?'':PRIcol[e.priority]+'88'}" onclick="navReset('email','${esc(e.message_id)}')">
      <div class="top">${badge(e.priority,PRIcol[e.priority])}${badge(e.counterparty,CPcol[e.counterparty])}<span class="date">${fdate(e._date)}</span></div>
      <div class="subj">${esc(e.subject||'(sem assunto)')}</div>
      <div class="frm"><span class="dot" style="background:${e.direction=='internal'?'var(--int)':'var(--ext)'}"></span>${esc(e.from_addr||'')}</div>
    </div>`).join('')||'<div class="empty">Nenhum email.</div>';
  renderDetail();
  writeURL();
}

function peopleRow(ps){
  if(!ps||!ps.length)return '<span class="muted">nenhuma</span>';
  const ord={from:0,reply_to:1,to:2,cc:3};
  return [...ps].sort((a,b)=>ord[a.role]-ord[b.role]).map(p=>`
    <div class="person ${p.internal?'int':''}" onclick="navTo('person','${esc(p.email)}')">
      <span class="role">${ROLE[p.role]||p.role}${p.internal?' · interno':''}</span>
      <span class="nm">${esc(p.name||p.email.split('@')[0])}</span><span class="em">${esc(p.email)}</span></div>`).join('');
}
function bodyHtml(t){if(!t)return '<span class="muted">sem corpo</span>';
  return t.split('\n').map(l=>l.trimStart().startsWith('>')?`<span class="qline">${esc(l)}</span>`:esc(l)).join('\n');}
function entChips(en){
  const want=[['client_name','cliente'],['client_email','email'],['deadline','prazo'],['money','valor'],['nif','nif'],['iban','iban'],['product_or_service','produto / serviço'],['action_requested','ação']];
  const g=want.filter(([k])=>en&&en[k]);
  return g.length?g.map(([k,l])=>`<div class="ent"><b>${l}</b>${esc(en[k])}</div>`).join(''):'<span class="muted">sem entidades</span>';
}
function miniRows(mids){
  return [...mids].map(m=>byMid[m]).sort((a,b)=>(b._date||'').localeCompare(a._date||'')).map(e=>`
    <div class="mini" onclick="navTo('email','${esc(e.message_id)}')">
      <span class="dot" style="background:${PRIcol[e.priority]}"></span>
      <span class="ms">${esc(e.subject||'(sem assunto)')}</span>
      <span class="md">${esc((e._date||'').slice(0,10))}</span><span class="mc">${e._cost?fCost(e._cost):'$0'}</span></div>`).join('');
}

const FLABEL={item:'o que produzir',design_ready:'ficheiro',dimensions:'dimensões',material:'material',thickness:'espessura',material_supplied_by:'fornece material',process:'processo',quantity:'quantidade',deadline:'prazo',colour_finish:'cor/acabamento',quality_acceptance:'aceitação',delivery:'entrega',budget:'budget'};
function copyText(id){const t=document.getElementById(id);t.select();try{document.execCommand('copy');const c=document.getElementById(id+'c');if(c)c.textContent='copiado ✓';}catch(_){}}
function readinessText(rd){const cov=Math.round((rd.coverage||0)*100), m=(rd.missing||[]).length;
  const txt=rd.estimable?'<b style="color:var(--int)">Pronto para orçamentar ✓</b>':(m?`Faltam <b>${m}</b> campos para orçamentar`:'Especificação incompleta');
  return `${txt} <div class="covbar"><span style="width:${cov}%"></span></div> <span class="muted" style="font-size:12px">${cov}%</span>`;}
function fState(f,missing){
  if(f&&f.value&&f.source=='user')return['done','confirmado por si'];
  if(f&&f.value)return['auto','extraído automaticamente — reveja e confirme'];
  if(missing)return['miss','obrigatório em falta'];
  return['','opcional'];}
function _addr(k,i){return i==null?k:k+'#'+i;}
function fieldRow(mid,k,i,f,missing){const s=fState(f,missing), a=_addr(k,i), id=_fid(mid,a);
  return `<div class="fr ${s[0]}" id="fr_${id}"><label for="jf_${id}" title="${esc(FLABEL[k]||k)}">${esc(FLABEL[k]||k)}</label>
    <input id="jf_${id}" class="fin" value="${esc((f&&f.value)||'')}" placeholder="—" onchange="confirmField('${esc(mid)}','${esc(a)}')"/>
    <span class="fs" id="fs_${id}" title="${s[1]}"></span></div>`;}
// Live edits patch e._jobspec then re-render the spec column + readiness head (NOT the reply textarea,
// so an in-progress reply edit survives a field confirmation / add / remove).
function applySpec(mid,d){const e=byMid[mid];
  if(e&&e._jobspec){e._jobspec.job_fields=d.job_fields; e._jobspec.items=d.items; e._jobspec.readiness=d.readiness;}
  const sid=_sid(mid), jh=document.getElementById('jh_'+sid), sp=document.getElementById('jsspec_'+sid);
  if(jh)jh.innerHTML=readinessHead(byMid[mid]); if(sp)sp.innerHTML=specHtml(byMid[mid]);}
async function confirmField(mid,addr){
  const inp=document.getElementById('jf_'+_fid(mid,addr)); if(!inp)return;
  const r=await fetch('/api/confirm',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message_id:mid,field:addr,value:inp.value})});
  if(r.ok)applySpec(mid,await r.json());
}
async function addItem(mid){
  const r=await fetch('/api/item/add',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message_id:mid})});
  if(r.ok)applySpec(mid,await r.json());
}
async function removeItem(mid,i){
  const r=await fetch('/api/item/remove',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message_id:mid,index:i})});
  if(r.ok)applySpec(mid,await r.json());
}
async function regenReply(mid){
  const sid=_sid(mid), ta=document.getElementById('jreply_'+sid), msg=document.getElementById('jmsg_'+sid);
  if(!ta)return;
  if(msg)msg.textContent='a gerar…'; ta.value='';
  const commit=v=>{const e=byMid[mid];if(e&&e._jobspec)e._jobspec.draft_reply=v;};
  try{
    const r=await fetch('/api/reply/stream',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message_id:mid})});
    if(r.ok&&r.body&&r.body.getReader){            // token-stream: append chunks as they arrive
      const reader=r.body.getReader(), dec=new TextDecoder(); let acc='';
      for(;;){const {value,done}=await reader.read(); if(done)break;
        acc+=dec.decode(value,{stream:true}); ta.value=acc; ta.scrollTop=ta.scrollHeight;}
      acc+=dec.decode(); ta.value=acc; commit(acc);
    }else{                                          // fallback: non-streaming endpoint
      const r2=await fetch('/api/reply',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message_id:mid})});
      const d=await r2.json(); if(d.reply){ta.value=d.reply; commit(d.reply);}
    }
  }catch(_){toast('Falha ao gerar resposta',{type:'error'});}
  if(msg)msg.textContent='';
}
function readinessHead(e){const j=e._jobspec||{}, rd=j.readiness||{};
  const mk=LIVE?`<button class="btn sm" style="margin-left:auto" title="criar um projeto a partir desta lead (anexa a thread + importa a especificação)" onclick="createProjectFromMessage('${esc(e.message_id)}')">+ Criar projeto</button>`:'';
  const att=j.has_attachment?`<span class="pill" title="o pedido traz um anexo — a especificação pode estar lá" style="margin-left:${LIVE?'8px':'auto'}">📎 rever anexo</span>`:'';
  return `<span>${readinessText(rd)}</span>${mk}${att}`;}
function itemBlock(mid,it,i,miss,nItems){
  it=it||{};
  const rows=ITEM_MUST.map(k=>fieldRow(mid,k,i,it[k],miss.has(_addr(k,i)))).join('');
  const opt=ITEM_OPT.map(k=>fieldRow(mid,k,i,it[k],false)).join('');
  const rm=(LIVE&&nItems>1)?`<span class="rmitem" title="remover este artigo" onclick="removeItem('${esc(mid)}',${i})">✕ remover</span>`:'';
  const optBlock=opt?`<details class="optd"><summary>+ opcionais</summary>${opt}</details>`:'';
  return `<div class="fgrp itemcard"><div class="fglab">Artigo ${i+1}${rm}</div>${rows}${optBlock}</div>`;
}
// Spec column only — kept separate from jobspecPanel so it can be re-rendered in place on every edit.
function specHtml(e){
  const j=e._jobspec||{}, rd=j.readiness||{}, job=j.job_fields||{}, items=j.items||[], miss=new Set(rd.missing||[]);
  if(LIVE){
    const ats=realAtts(e), nz=noiseAtts(e).length;
    const aStrip=(e._attach&&e._attach.length)?`<div class="aprev"><span class="muted" style="font-size:11px">anexo:</span> ${ats.map(a=>`<span class="achip" onclick="openPreview('${esc(e.message_id)}',${a._i})" title="ler enquanto preenche a especificação">${attIcon(a.type)} ${esc(a.name)}</span>`).join('')||'<span class="muted" style="font-size:11px">só imagens de assinatura</span>'}${nz?` <span class="muted" style="font-size:11px">(+${nz} assinatura)</span>`:''}</div>`:'';
    const itemsHtml=(items.length?items:[{}]).map((it,i)=>itemBlock(e.message_id,it,i,miss,items.length)).join('');
    const addBtn=`<div style="margin:-2px 0 14px"><span class="additem" onclick="addItem('${esc(e.message_id)}')">+ adicionar artigo</span></div>`;
    const jobRows=JOB_MUST.map(k=>fieldRow(e.message_id,k,null,job[k],miss.has(k))).join('');
    const jobOpt=JOB_OPT.map(k=>fieldRow(e.message_id,k,null,job[k],false)).join('');
    return `${aStrip}<div class="leg"><span><i class="fs done"></i>confirmado</span><span><i class="fs auto"></i>automático</span><span><i class="fs miss"></i>em falta</span></div>
      ${itemsHtml}${addBtn}
      <div class="fgrp"><div class="fglab">Pedido (comum a todos os artigos)</div>${jobRows}
      <details class="optd"><summary>+ campos opcionais (${JOB_OPT.length})</summary>${jobOpt}</details></div>`;
  }
  const itemBlocks=items.map((it,i)=>{
    const known=ITEM_KEYS.filter(k=>it[k]&&it[k].value).map(k=>`<div class="ent"><b>${esc(FLABEL[k]||k)}</b>${esc(it[k].value)}</div>`).join('');
    return known?`<div class="lbl">Artigo ${i+1}</div><div class="ents" style="margin-bottom:8px">${known}</div>`:'';
  }).join('');
  const jobKnown=JOB_KEYS.filter(k=>k!=='client_identity'&&job[k]&&job[k].value).map(k=>`<div class="ent"><b>${esc(FLABEL[k]||k)} · ${esc(job[k].source)}</b>${esc(job[k].value)}</div>`).join('');
  const ms=[...miss].map(a=>{const p=a.split('#'); const idx=p.length>1?` (artigo ${parseInt(p[1])+1})`:''; return `<span class="m">${esc(FLABEL[p[0]]||p[0])}${idx}</span>`;}).join('');
  return (itemBlocks||'')+(jobKnown?`<div class="lbl">Pedido</div><div class="ents" style="margin-bottom:10px">${jobKnown}</div>`:'')+(ms?`<div class="lbl">Em falta</div><div class="miss">${ms}</div>`:'')||'<span class="muted">sem especificação no corpo</span>';
}
function jobspecPanel(e){
  const j=e._jobspec; if(!j) return '';
  const sid=_sid(e.message_id), rd=j.readiness||{}, reply=j.draft_reply||'';
  const showReply=LIVE||reply;
  const replyBlock=showReply
    ?`<div class="reply"><div class="rhead">Resposta sugerida <span class="muted">· rascunho, reveja antes de enviar</span></div>
        <textarea id="jreply_${sid}" ${LIVE?'':'readonly'}>${esc(reply)}</textarea>
        <div class="bar">${LIVE?`<button class="btn" onclick="regenReply('${esc(e.message_id)}')">Regenerar</button>`:''}<button class="btn" onclick="copyText('jreply_${sid}')">Copiar</button>
        <span class="muted" style="font-size:11.5px">nunca envia</span><span class="copied" id="jmsg_${sid}"></span><span class="copied" id="jreply_${sid}c"></span></div></div>`
    :((rd.questions&&rd.questions.length)?`<div class="lbl">Perguntas a colocar</div><ul class="qs">${rd.questions.map(q=>`<li>${esc(q)}</li>`).join('')}</ul>`:'');
  return `<div class="js" id="jspanel_${sid}"><div class="jh" id="jh_${sid}">${readinessHead(e)}</div>
    <div class="jb"><div class="jgrid"><div id="jsspec_${sid}">${specHtml(e)}</div><div>${replyBlock}</div></div></div></div>`;
}
function attIcon(t){t=(t||'').toLowerCase();return t.includes('pdf')?'📄':t.startsWith('image/')?'🖼️':(t.includes('sheet')||t.includes('excel')||t.includes('csv'))?'📊':(t.includes('zip')||t.includes('compress')||t.includes('rar'))?'🗜️':(t.includes('word')||t.includes('document'))?'📝':'📎';}
function attIsNoise(a){const t=(a.type||'').toLowerCase(), n=(a.name||'').toLowerCase(), sz=a.size||0;
  if(!t.startsWith('image/'))return false;
  return (sz>0&&sz<25000)||/^image\d{3,}\./.test(n)||/logo|assinatura|signature|icon/.test(n);}
function realAtts(e){return (e._attach||[]).map((a,i)=>({...a,_i:i})).filter(a=>!attIsNoise(a));}
function noiseAtts(e){return (e._attach||[]).map((a,i)=>({...a,_i:i})).filter(a=>attIsNoise(a));}
function attChip(mid,a){const inner=`${attIcon(a.type)} ${esc(a.name)} <span class="ty">${esc((a.type||'').split('/').pop())}${a.size?' · '+Math.round(a.size/1024)+'kb':''}</span>`;
  return LIVE?`<span class="att clk" onclick="openPreview('${esc(mid)}',${a._i})" title="pré-visualizar">${inner}</span>`:`<span class="att">${inner}</span>`;}
function openPreview(mid,i){
  const e=byMid[mid], a=(e&&e._attach&&e._attach[i])||{}, url='/api/attachment/'+mid+'/'+i, t=(a.type||'').toLowerCase();
  document.getElementById('pvttl').textContent=a.name||'anexo'; document.getElementById('pvlnk').href=url;
  const bd=document.getElementById('pvbody');
  if(t.includes('pdf')) bd.innerHTML=`<iframe src="${url}"></iframe>`;
  else if(t.startsWith('image/')) bd.innerHTML=`<img src="${url}"/>`;
  else bd.innerHTML=`<div class="pvmsg"><p>Sem pré-visualização para este tipo de ficheiro.</p><a class="btn" href="${url}" target="_blank" rel="noopener">Transferir ${esc(a.name||'ficheiro')}</a></div>`;
  document.getElementById('pv').classList.remove('hidden'); document.body.classList.add('previewing');
}
function closePreview(){document.getElementById('pv').classList.add('hidden');document.getElementById('pvbody').innerHTML='';document.body.classList.remove('previewing');}

// ─── Reclassification (human verdict correction) ─────────────────────────────
async function doReclassify(mid,field,value){
  const e=byMid[mid]; if(!e)return;
  const autoKey='_auto_'+field;
  // Preserve original auto value on first override
  if(value&&!e[autoKey])e[autoKey]=e[field];
  const valueAuto=e[autoKey]||e[field];
  // Optimistic update
  if(value)e[field]=value; else{e[field]=e[autoKey]||e[field];delete e[autoKey];}
  const ok=document.getElementById('rcok_'+_sid(mid)); if(ok)ok.textContent='a guardar…';
  try{
    const r=await fetch('/api/reclassify',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({message_id:mid,field,value_auto:valueAuto,value_human:value||null})});
    if(r.ok){
      if(ok){ok.textContent=value?'guardado ✓':'reposto ✓';setTimeout(()=>{if(ok)ok.textContent='';},1500);}
      renderList(); renderDetail();
    }else{
      // Revert on server error
      if(value&&e[autoKey]){e[field]=e[autoKey];delete e[autoKey];}
      if(ok)ok.textContent='erro';
    }
  }catch(_){if(ok)ok.textContent='erro';}
}
function rcPanel(e){
  if(!LIVE)return'';
  const mid=e.message_id, sid=_sid(mid);
  const isOv=f=>!!e['_auto_'+f];
  const hint=['counterparty','purpose','priority'].filter(isOv).length;
  const mkSel=(f,opts,labels)=>{
    const ov=isOv(f), cur=ov?e[f]:'', autoVal=e['_auto_'+f]||e[f];
    const optsHtml=opts.map((v,i)=>`<option value="${esc(v)}"${cur===v?' selected':''}>${esc(labels?labels[i]:v)}</option>`).join('');
    const FNAMES={counterparty:'Tipo',purpose:'Purpose',priority:'Prioridade'};
    return`<div class="rcfld">
      <span class="rclbl">${FNAMES[f]}${ov?` · <span style="color:var(--int)">auto: ${esc(autoVal)}</span>`:''}</span>
      <select class="${ov?'ov':''}" onchange="doReclassify('${esc(mid)}','${f}',this.value)">
        <option value="">— auto (${esc(autoVal)})</option>${optsHtml}
      </select></div>`;
  };
  const CPKEYS=['CLIENT','LEAD','SUPPLIER','INTERNAL','BULK','OTHER'];
  const PURKEYS=Object.keys(PURpt);
  const PRIKEYS=['HIGH','NEEDS_REVIEW','MEDIUM','LOW','IGNORE'];
  return`<details class="rc" id="rc_${sid}" open>
    <summary>Reclassificar${hint?` · <span style="color:var(--int);font-weight:400">${hint} campo${hint!==1?'s':''} corrigido${hint!==1?'s':''}</span>`:' · corrigir o LLM'}</summary>
    <div class="rcbody">
      ${mkSel('counterparty',CPKEYS)}
      ${mkSel('purpose',PURKEYS,PURKEYS.map(k=>PURpt[k]||k))}
      ${mkSel('priority',PRIKEYS,PRIKEYS.map(k=>PRIpt[k]||k))}
      <span class="rcok" id="rcok_${sid}"></span>
    </div></details>`;
}

function emailCard(e){
  const ni=(e._people||[]).filter(p=>p.internal).length, ne=(e._people||[]).length-ni;
  const dom=domainOf(e.from_addr||''); const tcount=threadMids[e._thread]?threadMids[e._thread].size:1; const cpc=CPcol[e.counterparty]||'#888';
  const priBadge=e._auto_priority
    ?`<span class="badge" style="background:${PRIcol[e.priority]}22;color:${PRIcol[e.priority]}" title="✏ corrigido · auto: ${e._auto_priority}">✏ ${esc(PRIpt[e.priority]||e.priority)}</span>`
    :badge(PRIpt[e.priority]||e.priority,PRIcol[e.priority]);
  const cpBadge=`<span class="badge clk" style="background:${cpc}22;color:${cpc}" title="${e._auto_counterparty?`✏ corrigido · auto: ${e._auto_counterparty}`:esc(e.counterparty)+' — clique para filtrar'}" onclick="setCp('${esc(e.counterparty)}')">${e._auto_counterparty?'✏ ':''}${esc(CPpt[e.counterparty]||e.counterparty)}</span>`;
  const purPill=`<span class="mp" title="${e._auto_purpose?`✏ corrigido · auto: ${e._auto_purpose}`:PURpt[e.purpose]||e.purpose}">${e._auto_purpose?'✏ ':''}${esc(PURpt[e.purpose]||e.purpose)}</span>`;
  const head=`
    <h2>${esc(e.subject||'(sem assunto)')}</h2>
    <div class="metabar">
      ${priBadge}${cpBadge}${purPill}
      <span class="mp" title="quem enviou"><span class="dot" style="background:${e.direction=='internal'?'var(--int)':'var(--ext)'}"></span>${e.direction=='internal'?'interno':'externo'}</span>
      <span class="mp" title="urgência 0–100 (pressão de tempo)">urgência ${e.urgency}</span>
      ${tcount>1?`<span class="mp clk" onclick="setThread('${esc(e._thread)}')" title="ver a conversa completa">↳ ${tcount} na thread</span>`:''}
      <span class="mp" title="${e.decided_by&&e.decided_by.startsWith('tier0')?'decidido offline, sem LLM':'classificado pelo LLM'}">${e.decided_by&&e.decided_by.startsWith('tier0')?'offline':'via LLM'}</span>
      ${costPill(e)}</div>
    <div class="dmut">${fdate(e._date)} · de <span class="link" onclick="navTo('person','${esc(e.from_addr)}')">${esc(e.from_addr||'')}</span>${dom?` · <span class="link" onclick="navTo('org','${esc(dom)}')">${esc(dom)}</span>`:''}</div>`;
  const bodySec=`<div class="sec"><h3>Email original${e._body_trunc?' · (truncado)':''}</h3><div class="body">${bodyHtml(e._body)}</div></div>`;
  const peopleSec=`<div class="sec"><h3>Pessoas · ${ni} int, ${ne} ext
      <span class="legend"><i><span class="dot" style="background:var(--int)"></span>int</i><i><span class="dot" style="background:var(--ext)"></span>ext</i></span></h3>
      <div class="people">${peopleRow(e._people)}</div></div>`;
  const _ra=realAtts(e), _na=noiseAtts(e);
  const attSec=(e._attach&&e._attach.length)?`<div class="sec"><h3>Anexos · ${_ra.length}${LIVE?' <span class="muted" style="font-weight:400;text-transform:none">· clique para ver</span>':''}</h3>
    <div class="atts">${_ra.map(a=>attChip(e.message_id,a)).join('')||'<span class="muted">só imagens de assinatura</span>'}</div>
    ${_na.length?`<details class="optd" style="margin-top:6px"><summary>+ ${_na.length} imagem(ns) de assinatura</summary><div class="atts" style="margin-top:8px">${_na.map(a=>attChip(e.message_id,a)).join('')}</div></details>`:''}
  </div>`:'';
  const verdictSec=`<div class="sec"><h3>Porquê (veredicto)</h3><div class="reason">${esc(e.reason||'—')}</div><div class="ents" style="margin-top:8px">${entChips(e.entities)}</div></div>`;
  const conv=convPanel(e), rel=relPanel(e), rc=rcPanel(e);
  if(e._jobspec){
    return head + conv + rc + jobspecPanel(e) + `<div class="cols"><div class="colL">${bodySec}</div><div class="colR">${peopleSec}${attSec}${verdictSec}${rel}</div></div>`;
  }
  return head + conv + rc + `<div class="cols"><div class="colL">${bodySec}</div><div class="colR">${verdictSec}${peopleSec}${attSec}${rel}</div></div>`;
}

function tally(jsonStr){try{const o=JSON.parse(jsonStr||'{}');return Object.entries(o).sort((a,b)=>b[1]-a[1]);}catch(_){return [];}}
function personCard(email){
  const c=contactBy[email]||{}, mids=[...(personMids[email]||[])], cs=costOf(mids), dom=domainOf(email);
  const co={}; mids.forEach(m=>byMid[m]._people.forEach(p=>{if(p.email!=email)co[p.email]=(co[p.email]||0)+1;}));
  const coArr=Object.entries(co).sort((a,b)=>b[1]-a[1]).slice(0,24);
  const cpt=tally(c.counterparty_counts), put=tally(c.purpose_counts);
  return `
    <h2>${esc(c.display_name||nameOf(email))} ${isInt(email)?'<span class="badge" style="background:#0d948822;color:#0d9488">interno</span>':''}</h2>
    <div class="dmut">${esc(email)} · <span class="link" onclick="navTo('org','${esc(dom)}')">${esc(dom)}</span></div>
    <div class="meta" style="margin-top:10px">${badge(c.last_counterparty||'—',CPcol[c.last_counterparty]||'#888')}
      <span class="pill cost">${fTok(cs.i+cs.o)} tok · ${fCost(cs.c)} total</span></div>
    <div class="sec"><div class="kv">
      <div><b>mensagens</b>${mids.length}</div><div><b>from/to/cc</b>${c.from_count||0}/${c.to_count||0}/${c.cc_count||0}</div>
      <div><b>1ª vez</b>${esc((c.first_seen||'').slice(0,10))}</div><div><b>visto</b>${esc((c.last_seen||'').slice(0,10))}</div>
      <div><b>último purpose</b>${esc(c.last_purpose||'—')}</div></div></div>
    ${cpt.length?`<div class="sec"><h3>Tipo de relação</h3><div class="ents">${cpt.map(([k,v])=>`<div class="ent"><b>${esc(k)}</b>${v}</div>`).join('')}</div></div>`:''}
    ${put.length?`<div class="sec"><h3>Tipos de email</h3><div class="ents">${put.map(([k,v])=>`<div class="ent"><b>${esc(k)}</b>${v}</div>`).join('')}</div></div>`:''}
    ${coArr.length?`<div class="sec"><h3>Pessoas ligadas · ${coArr.length}<span class="legend"><i class="muted">co-aparecem — clique para abrir</i></span></h3><div class="copips">${coArr.map(([e,n])=>`<div class="copip ${isInt(e)?'int':''}" onclick="navTo('person','${esc(e)}')">${esc(nameOf(e))} <b>×${n}</b></div>`).join('')}</div></div>`:''}
    <div class="sec"><h3>Emails · ${mids.length}</h3><div class="sublist">${miniRows(mids)}</div></div>`;
}

function orgCard(dom){
  const o=orgs[dom]||{people:new Set(),mids:new Set()}; const ppl=[...o.people], mids=[...o.mids], cs=costOf(mids);
  const cpc={}; ppl.forEach(e=>{const c=contactBy[e];if(c&&c.last_counterparty)cpc[c.last_counterparty]=(cpc[c.last_counterparty]||0)+1;});
  const cpArr=Object.entries(cpc).sort((a,b)=>b[1]-a[1]);
  const dates=mids.map(m=>byMid[m]._date).filter(Boolean).sort();
  return `
    <h2>${esc(dom)} ${o.internal?'<span class="badge" style="background:#0d948822;color:#0d9488">interno</span>':''}</h2>
    <div class="meta" style="margin-top:6px">${cpArr.map(([k,v])=>badge(k+' ×'+v,CPcol[k]||'#888')).join(' ')||''}
      <span class="pill cost">${fTok(cs.i+cs.o)} tok · ${fCost(cs.c)} total</span></div>
    <div class="sec"><div class="kv">
      <div><b>pessoas</b>${ppl.length}</div><div><b>emails</b>${mids.length}</div>
      <div><b>1º contacto</b>${esc((dates[0]||'').slice(0,10))}</div><div><b>último</b>${esc((dates[dates.length-1]||'').slice(0,10))}</div></div></div>
    <div class="sec"><h3>Pessoas · ${ppl.length}</h3><div class="copips">${ppl.map(e=>{const c=contactBy[e]||{};return `<div class="copip ${isInt(e)?'int':''}" onclick="navTo('person','${esc(e)}')">${esc(nameOf(e))} <b>×${c.msg_count||0}</b></div>`;}).join('')}</div></div>
    <div class="sec"><h3>Emails · ${mids.length}</h3><div class="sublist">${miniRows(mids)}</div></div>`;
}

function renderDetail(){
  const top=navStack[navStack.length-1], d=document.getElementById('detail');
  if(!top){d.innerHTML='<div class="empty">Selecione um email — ou clique numa pessoa, organização ou thread.</div>';return;}
  const bc=navStack.length>1?`<div class="bc"><span onclick="back()">← voltar</span></div>`:'';
  if(top.kind=='email'){const e=byMid[top.id];d.innerHTML=bc+(e?emailCard(e):'<div class="empty">não encontrado</div>');}
  else if(top.kind=='person')d.innerHTML=bc+personCard(top.id);
  else if(top.kind=='org')d.innerHTML=bc+orgCard(top.id);
}

let selContact=null,cSort='msgs';
function setCSort(s){cSort=s;['msgs','silent','name'].forEach(x=>{const el=document.getElementById('csort-'+x);if(el)el.classList.toggle('on',x===s);});renderContacts();}
function daysSince(d){if(!d)return Infinity;const t=new Date(d).getTime();return isNaN(t)?Infinity:Math.floor((Date.now()-t)/86400000);}
function daysLabel(d){const n=daysSince(d);if(n===Infinity)return'—';if(n===0)return'hoje';if(n<7)return`${n}d`;if(n<30)return`${Math.round(n/7)}sem`;if(n<365)return`${Math.round(n/30)}m`;return`${Math.round(n/365)}a`;}
function renderContacts(){
  renderFilters('contacts');
  let rows=applyFacets('contacts',CONTACTS);
  if(cSort==='silent')rows=rows.slice().sort((a,b)=>{const da=daysSince(a.last_from_date),db=daysSince(b.last_from_date);if(da===Infinity&&db===Infinity)return 0;if(da===Infinity)return 1;if(db===Infinity)return-1;return db-da;});
  else if(cSort==='name')rows=rows.slice().sort((a,b)=>(a.display_name||a.email).localeCompare(b.display_name||b.email));
  if(selContact&&!rows.find(c=>c.email===selContact))selContact=rows.length?rows[0].email:null;
  if(!selContact&&rows.length)selContact=rows[0].email;
  _vis.contacts=rows.map(c=>c.email);
  document.getElementById('ccount').textContent=`${rows.length} visíveis`;
  document.getElementById('clist').innerHTML=rows.map(c=>{
    const cpc=CPcol[c.last_counterparty]||'#888',ds=daysLabel(c.last_from_date);
    return`<div class="item ${c.email===selContact?'sel':''}" role="button" tabindex="0" aria-current="${c.email===selContact}" onclick="selContact='${esc(c.email)}';renderContacts()">
      <div class="top">${badge(c.last_counterparty||'—',cpc)}<span class="date">${esc(ds)}</span></div>
      <div class="subj">${esc(c.display_name||c.email.split('@')[0])}</div>
      <div class="frm"><span class="dot" style="background:${c.is_internal?'var(--int)':'var(--ext)'}"></span>${esc(c.email)} · ${c.msg_count}msg</div>
    </div>`;}).join('')||'<div class="empty">Nenhum contacto.</div>';
  renderContactDetail();
  if(curTab==='contacts')writeURL();
}
function renderContactDetail(){
  const d=document.getElementById('cdetail');
  if(!selContact){d.innerHTML='<div class="empty">Selecione um contacto.</div>';return;}
  d.innerHTML=contactCard(selContact);
}
function contactCard(email){
  const c=contactBy[email]||{},mids=[...(personMids[email]||[])],cs=costOf(mids),dom=domainOf(email);
  const co={};mids.forEach(m=>{((byMid[m]&&byMid[m]._people)||[]).forEach(p=>{if(p.email!==email)co[p.email]=(co[p.email]||0)+1;});});
  const coArr=Object.entries(co).sort((a,b)=>b[1]-a[1]).slice(0,24);
  const orgSiblings=CONTACTS.filter(x=>x.email!==email&&domainOf(x.email)===dom).sort((a,b)=>b.msg_count-a.msg_count).slice(0,12);
  const cpt=tally(c.counterparty_counts),put=tally(c.purpose_counts);
  const cpc=CPcol[c.last_counterparty]||'#888';
  const ds=daysSince(c.last_from_date),dl=daysLabel(c.last_from_date);
  const silentWarn=(ds>30&&!c.is_internal)?`<span class="pill" style="background:#fff7ed;border-color:#f5a623;color:#92400e">⚠ silêncio ${dl}</span>`:'';
  return`
    <h2>${esc(c.display_name||email.split('@')[0])} ${c.is_internal?'<span class="badge" style="background:#0d948822;color:#0d9488">interno</span>':''}</h2>
    <div class="dmut">${esc(email)} · <span class="link" onclick="switchTab('emails');navTo('org','${esc(dom)}')">${esc(dom)}</span></div>
    <div class="meta" style="margin-top:10px">${badge(c.last_counterparty||'—',cpc)}${silentWarn}
      <span class="pill cost">${fTok(cs.i+cs.o)} tok · ${fCost(cs.c)}</span>
      <button class="btn sm" style="margin-left:auto" onclick="switchTab('emails');navReset('person','${esc(email)}')">Ver emails ↗</button>
    </div>
    <div class="sec"><div class="kv">
      <div><b>mensagens</b>${mids.length}</div>
      <div><b>from / to / cc</b>${c.from_count||0} / ${c.to_count||0} / ${c.cc_count||0}</div>
      <div><b>1.º contacto</b>${esc((c.first_seen||'').slice(0,10))}</div>
      <div><b>último contacto</b>${esc((c.last_seen||'').slice(0,10))}</div>
      <div><b>último inbound</b>${esc((c.last_from_date||'').slice(0,10))} <span class="muted">${esc(dl)}</span></div>
    </div></div>
    ${cpt.length?`<div class="sec"><h3>Tipo de relação</h3><div class="ents">${cpt.map(([k,v])=>`<div class="ent"><b>${esc(k)}</b> ${v}×</div>`).join('')}</div></div>`:''}
    ${put.length?`<div class="sec"><h3>Assuntos</h3><div class="ents">${put.map(([k,v])=>`<div class="ent"><b>${esc(PURpt[k]||k)}</b> ${v}×</div>`).join('')}</div></div>`:''}
    ${orgSiblings.length?`<div class="sec"><h3>Mesmo domínio · ${esc(dom)} <span class="legend"><i class="muted">${orgSiblings.length} pessoa${orgSiblings.length!==1?'s':''} — clique para abrir</i></span></h3><div class="copips">${orgSiblings.map(x=>`<div class="copip ${x.is_internal?'int':''}" onclick="selContact='${esc(x.email)}';renderContacts()">${esc(x.display_name||x.email.split('@')[0])} <b>×${x.msg_count}</b></div>`).join('')}</div></div>`:''}
    ${coArr.length?`<div class="sec"><h3>Co-ocorrências · ${coArr.length} <span class="legend"><i class="muted">outras pessoas nos mesmos emails — clique para abrir</i></span></h3><div class="copips">${coArr.map(([e,n])=>`<div class="copip ${isInt(e)?'int':''}" onclick="selContact='${esc(e)}';renderContacts()">${esc(nameOf(e))} <b>×${n}</b></div>`).join('')}</div></div>`:''}
    <div class="sec"><h3>Emails · ${mids.length}</h3><div class="sublist">${miniRowsC(mids)}</div></div>`;
}
function miniRowsC(mids){
  return[...mids].map(m=>byMid[m]).filter(Boolean).sort((a,b)=>(b._date||'').localeCompare(a._date||'')).map(e=>`
    <div class="mini" onclick="switchTab('emails');navReset('email','${esc(e.message_id)}')">
      <span class="dot" style="background:${PRIcol[e.priority]}"></span>
      <span class="ms">${esc(e.subject||'(sem assunto)')}</span>
      <span class="md">${esc((e._date||'').slice(0,10))}</span><span class="mc">${e._cost?fCost(e._cost):'$0'}</span></div>`).join('');
}
// ─── Thread conversation ribbon (client-side, works in static + live) ────────
function convPanel(e){
  const tmids=[...(threadMids[e._thread]||[])];
  if(tmids.length<=1)return'';
  const msgs=tmids.map(m=>byMid[m]).filter(Boolean).sort((a,b)=>(a._date||'').localeCompare(b._date||''));
  if(msgs.length<=1)return'';
  return`<div class="conv">
    <div class="conv-hd"><svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" style="flex-shrink:0"><path d="M2 4h12M2 8h8M2 12h5"/></svg>Conversa · ${msgs.length} mensagens</div>${
    msgs.map(m=>{const cur=m.message_id===e.message_id, out=m.direction==='outbound';
    return`<div class="conv-row ${cur?'conv-cur':'conv-nav'}"${cur?'':` onclick="navReset('email','${esc(m.message_id)}')"`}>
      <span class="conv-arr ${out?'out':'in'}">${out?'→':'←'}</span>
      <span class="conv-d">${(m._date||'').slice(0,10)}</span>
      <span class="conv-f">${esc(nameOf(m.from_addr||''))}</span>
      <span class="conv-s">${esc(m.subject||'(sem assunto)')}</span>
      ${cur?`<span style="font-size:9px;color:var(--ac);font-weight:700;white-space:nowrap;flex-shrink:0;letter-spacing:.05em">◀ ESTE</span>`:`<span class="badge" style="background:${PRIcol[m.priority]||'#aaa'}22;color:${PRIcol[m.priority]||'#aaa'};font-size:9.5px">${m.priority}</span>`}
    </div>`;}).join('')}
  </div>`;
}
// ─── Entity relations panel (live only, lazy-loaded via /api/relations) ───────
function _sid(s){return s.replace(/[^a-zA-Z0-9]/g,'_');}
// Per-field element id, namespaced by message_id so two job-spec panels (Emails #detail + Leads
// #ldetail) can coexist in the DOM without colliding on `jf_material_0` etc. Without this, confirmField
// reads getElementById's first match — the wrong panel — and posts an empty value (silent clear).
function _fid(mid,a){return _sid(mid)+'__'+_sid(a);}
const _relCache={};
function relPanel(e){
  if(!LIVE)return'';
  const sid=_sid(e.message_id);
  return`<div class="rel">
    <div class="rel-hd" id="rel-h-${sid}" onclick="toggleRel('${esc(e.message_id)}','${sid}')">
      <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" style="flex-shrink:0"><circle cx="4" cy="8" r="2.5"/><circle cx="12" cy="3" r="2.5"/><circle cx="12" cy="13" r="2.5"/><line x1="6.2" y1="7" x2="9.8" y2="4.3"/><line x1="6.2" y1="9" x2="9.8" y2="11.7"/></svg>
      Emails relacionados por entidade
      <span id="rel-hint-${sid}" style="margin-left:auto;font-size:11px;font-weight:400;color:var(--mut2);text-transform:none;letter-spacing:0">carregar ▾</span>
    </div>
    <div id="rel-body-${sid}" class="hidden"></div>
  </div>`;
}
async function toggleRel(mid,sid){
  const body=document.getElementById('rel-body-'+sid),hint=document.getElementById('rel-hint-'+sid),hdr=document.getElementById('rel-h-'+sid);
  if(!body)return;
  const open=!body.classList.contains('hidden');
  if(open){body.classList.add('hidden');if(hint)hint.textContent='carregar ▾';if(hdr)hdr.classList.remove('open');return;}
  body.classList.remove('hidden');if(hdr)hdr.classList.add('open');
  if(_relCache[mid]){renderRelBody(sid,_relCache[mid]);return;}
  if(hint)hint.textContent='…';
  try{
    const r=await fetch('/api/relations/'+encodeURIComponent(mid));
    const d=r.ok?await r.json():{by_entity:[]};
    _relCache[mid]=d;renderRelBody(sid,d);
    const n=(d.by_entity||[]).length;
    if(hint)hint.textContent=n?`${n} relacionado${n!==1?'s':''} ▴`:' — ▴';
  }catch(_){if(hint)hint.textContent='erro ▾';}
}
const _KEY_PT={client_name:'nome',client_email:'e-mail',nif:'NIF',iban:'IBAN',product_or_service:'produto',deadline:'prazo'};
function renderRelBody(sid,data){
  const body=document.getElementById('rel-body-'+sid);if(!body)return;
  const items=data.by_entity||[];
  if(!items.length){body.innerHTML='<div style="padding:10px 12px;color:var(--mut2);font-size:12px">Sem emails relacionados encontrados neste corpus.</div>';return;}
  body.innerHTML=items.map(r=>{
    const key=r._matched_entity||'';
    const cls=(['nif','iban'].includes(key))?'rel-tag fin':(['client_name','client_email'].includes(key))?'rel-tag nom':'rel-tag';
    return`<div class="rel-row" onclick="navTo('email','${esc(r.message_id)}')">
      <span class="dot" style="background:${PRIcol[r.priority]||'#888'}"></span>
      <span class="${cls}">por ${esc(_KEY_PT[key]||key.replace(/_/g,' '))}</span>
      <span style="color:var(--mut2);font-size:11px;white-space:nowrap">${(r.date||'').slice(0,10)}</span>
      <span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(r.subject||'(sem assunto)')}</span>
      <span style="color:var(--mut);font-size:11px;flex-shrink:0;max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(nameOf(r.from_email||''))}</span>
    </div>`;}).join('');
}
function switchTab(t){curTab=t;
  document.querySelectorAll('.tab').forEach(x=>{const on=x.dataset.tab==t;x.classList.toggle('on',on);x.setAttribute('aria-selected',on?'true':'false');});
  ['emails','contacts','leads','projects'].forEach(id=>document.getElementById(id).classList.toggle('hidden',id!==t));
  if(t==='leads')renderLeads();
  if(t==='projects')loadProjects();
  writeURL();}

// ─── Projects tab (live only; cross-thread canonical spec + export) ────────────
// STAGEcol/STAGEpt are defined up top with the filter registry. projData caches the full list
// (incl. archived) so the Estágio facet can filter every stage client-side.
let projData=[], selProject=null, projDetail=null;

async function loadProjects(){
  if(!LIVE){document.getElementById('plist').innerHTML='';
    document.getElementById('pdetail').innerHTML='<div class="empty">Projetos disponíveis no modo live (<code>email2data serve</code>).</div>';
    document.getElementById('pcount').textContent='';return;}
  try{const r=await fetch('/api/projects?archived=true');projData=r.ok?await r.json():[];}
  catch(e){projData=[];}
  if(!selProject&&projData.length)selProject=projData[0].project_id;
  await renderProjects();
}

async function createProject(){
  const inp=document.getElementById('pnew-title'), title=(inp.value||'').trim();
  if(!title){inp.focus();return;}
  const r=await fetch('/api/projects',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title})});
  if(r.ok){const p=await r.json();inp.value='';selProject=p.project_id;await loadProjects();}
}

// Create a project straight from a lead email: attaches that message's thread and seeds the spec.
async function createProjectFromMessage(mid){
  if(!LIVE){toast('Disponível no modo live (email2data serve).',{type:'error'});return;}
  const e=byMid[mid]||{}, title=(e.subject||'').trim()||('Projeto '+mid.slice(0,14));
  const r=await fetch('/api/projects',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({title, from_message:mid, client_email:e.from_addr||null})});
  if(!r.ok){toast('Falha ao criar projeto: '+(await r.text()),{type:'error'});return;}
  const p=await r.json();
  selProject=p.project_id; projDetail=null;
  toast('Projeto criado a partir da lead',{type:'ok'});
  switchTab('projects');   // selects + opens the new project (loadProjects respects selProject)
}

async function attachThread(pid){
  const inp=document.getElementById('pattach'), ref=(inp.value||'').trim();
  if(!ref)return;
  const r=await fetch('/api/projects/'+pid+'/attach',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ref})});
  if(r.ok){inp.value='';await loadProjects();toast('Thread anexada',{type:'ok'});}else{toast('Falha ao anexar: '+(await r.text()),{type:'error'});}
}

async function projConfirm(pid,addr,inp){
  const r=await fetch('/api/projects/'+pid+'/field',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({field:addr,value:inp.value})});
  if(r.ok){projDetail=await r.json();await renderProjects();}
}

async function setStage(pid,stage){
  const r=await fetch('/api/projects/'+pid+'/stage',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({stage})});
  if(r.ok)await loadProjects();
}

async function exportProject(pid,adapter,force){
  const btn=event&&event.target;if(btn)btn.textContent='A exportar…';
  const r=await fetch('/api/projects/'+pid+'/export',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({adapter,force:!!force})});
  const res=await r.json();
  if(res.ok){toast('Exportado: '+res.external_id+' · '+res.detail,{type:'ok',ms:5000});await loadProjects();}
  else{toast('Não exportado: '+res.detail,{type:'error',ms:5000});if(btn)btn.textContent='Exportar';}
}

function projCard(p,sel){
  const sc=STAGEcol[p.stage]||'#9aa1ab', cov=Math.round((p.coverage||0)*100);
  return`<div class="item ${sel?'sel':''}" role="button" tabindex="0" aria-current="${!!sel}" onclick="selProject='${esc(p.project_id)}';projDetail=null;renderProjects()">
    <div class="top">
      <span style="font-size:10px;font-weight:700;padding:1px 7px;border-radius:20px;background:${sc}18;color:${sc}">${p.stage}</span>
      <span class="date">${p.n_threads||0} thread(s)</span>
      ${p.external_id?`<span class="muted" style="font-size:10px;margin-left:auto">↗ ${esc(p.external_id)}</span>`:''}
    </div>
    <div class="subj">${esc(p.title||p.project_id)}</div>
    <div class="frm"><span class="dot" style="background:var(--ext)"></span>${esc(p.client_name||p.client_email||'—')}</div>
    <div style="margin-top:4px"><span class="covbar" style="display:inline-block;width:52px;vertical-align:middle"><span style="width:${cov}%"></span></span> <span class="muted" style="font-size:11px">${cov}%</span>${p.estimable?' <span style="color:var(--int);font-size:11px;font-weight:700">✓</span>':''}</div>
  </div>`;
}

function projFieldRow(pid,addr,label,fld,prov){
  const v=fld&&fld.value?fld.value:'', src=prov&&prov[addr]?` <span class="muted" style="font-size:10px">[${esc(prov[addr])}]</span>`:'';
  return`<div style="display:flex;align-items:center;gap:8px;margin:3px 0">
    <span style="flex:0 0 150px;font-size:12px;color:var(--mut)">${esc(label)}${src}</span>
    <input style="flex:1" value="${esc(v)}" onchange="projConfirm('${esc(pid)}',${JSON.stringify(addr)},this)"/>
  </div>`;
}

async function renderProjectDetail(pid){
  if(!projDetail||projDetail.project_id!==pid){
    const r=await fetch('/api/projects/'+pid);if(!r.ok){return'<div class="empty">Projeto não encontrado.</div>';}
    projDetail=await r.json();
  }
  const d=projDetail, p=d.project, rd=d.readiness||{}, jf=d.job_fields||{}, items=d.items||[], prov=d.provenance||{};
  const JOBlabels={item:'o que produzir',design_ready:'ficheiro/desenho',dimensions:'dimensões',material:'material',thickness:'espessura',material_supplied_by:'quem fornece',process:'processo',quantity:'quantidade',deadline:'prazo',colour_finish:'cor/acabamento',quality_acceptance:'aceitação',delivery:'entrega',budget:'orçamento',client_identity:'cliente'};
  const jobRows=JOB_KEYS.map(k=>projFieldRow(pid,k,JOBlabels[k]||k,jf[k],prov)).join('');
  const itemRows=items.map((it,i)=>`<div style="border:1px solid var(--bd);border-radius:9px;padding:8px;margin:6px 0">
    <div style="font-weight:700;font-size:12px;margin-bottom:4px">Peça #${i}</div>
    ${ITEM_KEYS.map(k=>projFieldRow(pid,k+'#'+i,JOBlabels[k]||k,it[k],prov)).join('')}
  </div>`).join('');
  const conf=(d.conflicts&&Object.keys(d.conflicts).length)?`<div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:9px;padding:8px;margin:8px 0;font-size:12px">⚠ Valores divergentes entre threads:<br>${Object.entries(d.conflicts).map(([k,c])=>`<b>${esc(k)}</b>: `+c.map(x=>esc(x.value)+' ('+esc((x.source||x.ref||'').slice(0,18))+')').join(' | ')).join('<br>')}</div>`:'';
  const dang=(d.dangling_threads&&d.dangling_threads.length)?`<div style="background:#fef2f2;border:1px solid #fecaca;border-radius:9px;padding:8px;margin:8px 0;font-size:12px">⚠ Thread(s) anexada(s) sem correspondência no CRM atual (sem mensagens carregadas — reconstrua o CRM ou remova): ${d.dangling_threads.map(t=>esc(t)).join(', ')}</div>`:'';
  const cov=Math.round((rd.coverage||0)*100);
  const sc=STAGEcol[p.stage]||'#9aa1ab';
  return`<h2>${esc(p.title||p.project_id)} <span style="font-size:11px;font-weight:700;padding:2px 8px;border-radius:20px;background:${sc}18;color:${sc}">${p.stage}</span></h2>
    <div class="dmut" style="margin-bottom:10px">${esc(p.client_name||p.client_email||'—')} · ${(d.threads||[]).length} thread(s) · ${(d.message_ids||[]).length} mensagem(ns)
      ${p.external_id?` · <b>↗ ${esc(p.external_id)}</b>`:''}</div>
    <div style="margin-bottom:10px"><span class="covbar" style="display:inline-block;width:90px;vertical-align:middle"><span style="width:${cov}%"></span></span> ${cov}% · estimável: <b style="color:${rd.estimable?'var(--int)':'var(--mut)'}">${rd.estimable?'sim':'não'}</b>${rd.missing&&rd.missing.length?` · em falta: ${rd.missing.map(esc).join(', ')}`:''}</div>
    <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:6px">
      <input id="pattach" placeholder="anexar thread (message_id ou thread_root)" style="flex:1;min-width:200px"/>
      <button class="btn sm" onclick="attachThread('${esc(pid)}')">Anexar thread</button>
    </div>
    <div style="display:flex;gap:5px;flex-wrap:wrap;margin-bottom:12px">${(d.threads||[]).map(t=>`<span class="pill" title="${esc(t)}">${esc(t.slice(0,28))}${t.length>28?'…':''} <span style="cursor:pointer;color:var(--mut)" title="remover thread" onclick="detachThread('${esc(pid)}',${JSON.stringify(t)})">✕</span></span>`).join('')||'<span class="muted" style="font-size:11px">sem threads anexadas</span>'}</div>
    ${dang}
    ${conf}
    <h3 style="margin:12px 0 4px;font-size:13px">Spec do trabalho (canónico)</h3>
    ${jobRows}
    ${itemRows}
    <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:14px;border-top:1px solid var(--bd);padding-top:12px">
      <button class="btn sm" onclick="exportProject('${esc(pid)}','json',false)">Exportar (JSON dry-run)</button>
      <button class="btn sm" onclick="exportProject('${esc(pid)}','materials-costing',false)">Criar no materials-costing</button>
      <span style="flex:1"></span>
      ${['QUOTED','WON','LOST','ARCHIVED'].map(s=>`<button class="btn sm" onclick="setStage('${esc(pid)}','${s}')">${s}</button>`).join('')}
      <button class="btn sm" style="color:#b91c1c;border-color:#fecaca" title="eliminar definitivamente (duplicados/enganos)" onclick="deleteProject('${esc(pid)}')">Eliminar</button>
    </div>`;
}

async function detachThread(pid,ref){
  const r=await fetch('/api/projects/'+pid+'/detach',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ref})});
  if(r.ok){projDetail=await r.json();await renderProjects();toast('Thread removida',{type:'ok'});}else{toast('Falha ao remover thread: '+(await r.text()),{type:'error'});}
}

// Optimistic delete with a 6s undo window — the hard DELETE only fires if the toast isn't undone.
function deleteProject(pid){
  const snapshot=projData.slice();
  projData=projData.filter(p=>p.project_id!==pid);
  if(selProject===pid){selProject=projData[0]?projData[0].project_id:null;projDetail=null;}
  renderProjects();
  let committed=false;
  const t=setTimeout(async()=>{committed=true;
    const r=await fetch('/api/projects/'+pid,{method:'DELETE'});
    if(!r.ok){toast('Falha ao eliminar: '+(await r.text()),{type:'error'});projData=snapshot;await loadProjects();}
  },6000);
  toast('Projeto eliminado',{undo:()=>{if(committed)return;clearTimeout(t);projData=snapshot;selProject=pid;projDetail=null;renderProjects();}});
}

async function renderProjects(){
  if(!LIVE)return;
  renderFilters('projects');
  let rows=applyFacets('projects',projData);
  _vis.projects=rows.map(p=>p.project_id);
  document.getElementById('pcount').textContent=`${rows.length} projeto(s)`;
  document.getElementById('plist').innerHTML=rows.map(p=>projCard(p,p.project_id===selProject)).join('')||'<div class="empty">Sem projetos. Crie um a partir de um título ou de uma lead.</div>';
  const sel=rows.find(p=>p.project_id===selProject)||rows[0];
  document.getElementById('pdetail').innerHTML=sel?await renderProjectDetail(sel.project_id):'<div class="empty">Selecione um projeto.</div>';
  if(curTab==='projects')writeURL();
}

// ─── Leads tab ───────────────────────────────────────────────────────────────
let selLeadThread=null;

function _leadStatus(thread){
  const mids=[...(threadMids[thread]||[])];
  const emails=mids.map(m=>byMid[m]).filter(Boolean).sort((a,b)=>(a._date||'').localeCompare(b._date||''));
  if(!emails.length)return null;
  const hasLead=emails.some(e=>_LEAD_CPS.has(e.counterparty)||_LEAD_PURS.has(e.purpose));
  if(!hasLead)return null;
  const last=emails[emails.length-1], ds=daysSince(last._date||'');
  if(!threadHasOutbound[thread])return{status:'unanswered',emails,last,ds};
  if(last.direction==='outbound')return{status:'waiting',emails,last,ds};
  return{status:'active',emails,last,ds};
}

function _allLeadThreads(){
  const seen=new Set(), out=[];
  EMAILS.forEach(e=>{
    if(!e._thread||seen.has(e._thread))return; seen.add(e._thread);
    const st=_leadStatus(e._thread); if(st)out.push({thread:e._thread,...st});
  });
  const ord={unanswered:0,waiting:1,active:2};
  out.sort((a,b)=>{const d=ord[a.status]-ord[b.status];if(d)return d;
    return a.status==='active'?(b.last._date||'').localeCompare(a.last._date||''):(a.last._date||'').localeCompare(b.last._date||'');});
  return out;
}

function _threadClientEmail(thread){
  for(const mid of (threadMids[thread]||[])){const e=byMid[mid];if(e&&e.direction==='inbound'&&e.from_addr)return e.from_addr;}
  for(const mid of (threadMids[thread]||[])){const e=byMid[mid];if(e&&e.direction!=='outbound'&&e.from_addr)return e.from_addr;}
  return'';
}

function _bestJobspecEmail(emails){
  return emails.filter(e=>e._jobspec).sort((a,b)=>((b._jobspec.readiness||{}).coverage||0)-((a._jobspec.readiness||{}).coverage||0))[0]||null;
}

function _dsLabel(ds){return ds===Infinity?'—':ds===0?'hoje':`${ds}d`;}

function leadThreadCard(t,sel){
  const SC={unanswered:'#e5484d',waiting:'#f5a623',active:'#3358d4'};
  const ARR={unanswered:'←',waiting:'→',active:'↔'};
  const sc=SC[t.status], arr=ARR[t.status], ds=_dsLabel(t.ds);
  const SLBL={unanswered:'sem resposta',waiting:'a aguardar deles',active:'activo'};
  const client=_threadClientEmail(t.thread);
  const subj=t.emails[0]?.subject||'(sem assunto)';
  const best=_bestJobspecEmail(t.emails), rd=best?._jobspec?.readiness;
  const rdBit=rd?`<span class="covbar" style="display:inline-block;width:52px;vertical-align:middle"><span style="width:${Math.round((rd.coverage||0)*100)}%"></span></span> <span class="muted" style="font-size:11px">${Math.round((rd.coverage||0)*100)}%</span>${rd.estimable?' <span style="color:var(--int);font-size:11px;font-weight:700">✓</span>':''}`:''
  return`<div class="item ${sel?'sel':''}" role="button" tabindex="0" aria-current="${!!sel}" onclick="selLeadThread='${esc(t.thread)}';renderLeads()">
    <div class="top">
      <span style="font-size:12px;font-weight:700;color:${sc}">${arr} ${esc(ds)}</span>
      <span style="font-size:10px;font-weight:600;padding:1px 7px;border-radius:20px;background:${sc}18;color:${sc}">${SLBL[t.status]}</span>
      <span class="date">${fdate(t.last?._date)}</span>
    </div>
    <div class="subj">${esc(subj)}</div>
    <div class="frm"><span class="dot" style="background:var(--ext)"></span>${esc(nameOf(client)||client)}</div>
    ${rdBit?`<div style="margin-top:4px">${rdBit}</div>`:''}
  </div>`;
}

function toggleMsgBody(id){const el=document.getElementById(id);if(el)el.classList.toggle('hidden');}

function leadThreadDetail(t){
  const client=_threadClientEmail(t.thread);
  const subj=t.emails[0]?.subject||'(sem assunto)';
  const best=_bestJobspecEmail(t.emails);
  const msgs=t.emails.map((e,i)=>{
    const out=e.direction==='outbound', ac=out?'var(--int)':'var(--ac)', arrow=out?'→':'←';
    const isLast=i===t.emails.length-1;
    const ats=realAtts(e);
    return`<div class="lmsg ${out?'lmsg-out':''}">
      <div class="lmsg-hd" onclick="toggleMsgBody('lmb_${i}')">
        <span style="font-size:13px;font-weight:700;color:${ac};flex-shrink:0;width:16px">${arrow}</span>
        <span class="conv-d">${(e._date||'').slice(0,10)}</span>
        <span class="conv-f">${esc(nameOf(e.from_addr||''))}</span>
        <span class="conv-s">${esc(e.subject||'')}</span>
        ${badge(e.priority,PRIcol[e.priority])}
        <span style="margin-left:auto;font-size:11px;color:var(--mut2);flex-shrink:0">${isLast?'▴':'▾'}</span>
      </div>
      <div class="lmsg-body ${isLast?'':'hidden'}" id="lmb_${i}">
        <div class="body">${bodyHtml(e._body||'')}</div>
        ${ats.length?`<div class="atts" style="margin-top:8px">${ats.map(a=>attChip(e.message_id,a)).join('')}</div>`:''}
      </div>
    </div>`;
  }).join('');
  return`<h2>${esc(subj)}</h2>
    <div class="dmut" style="margin-bottom:14px">${esc(nameOf(client)||client)} · ${esc(client)} · ${t.emails.length} mensagens
      <button class="btn sm" style="margin-left:12px" onclick="switchTab('emails');navReset('email','${esc(t.last?.message_id||'')}')">Ver no inbox ↗</button>
    </div>
    <div class="lthread">${msgs}</div>
    ${best?jobspecPanel(best):''}`;
}

function renderLeads(){
  renderFilters('leads');
  let threads=applyFacets('leads',_allLeadThreads());
  if(selLeadThread&&!threads.find(t=>t.thread===selLeadThread))selLeadThread=threads.length?threads[0].thread:null;
  if(!selLeadThread&&threads.length)selLeadThread=threads[0].thread;
  _vis.leads=threads.map(t=>t.thread);
  document.getElementById('lcount').textContent=`${threads.length} conversas`;
  document.getElementById('llist').innerHTML=threads.map(t=>leadThreadCard(t,t.thread===selLeadThread)).join('')||'<div class="empty">Nenhuma conversa encontrada.</div>';
  const sel=threads.find(t=>t.thread===selLeadThread);
  document.getElementById('ldetail').innerHTML=sel?leadThreadDetail(sel):'<div class="empty">Selecione uma conversa.</div>';
  if(curTab==='leads')writeURL();
}

function _init(){
  const tab=applyURLState();
  renderHeader(); renderFilters('emails'); renderList(); renderContacts();
  if(tab&&tab!=='emails')switchTab(tab);
  _urlReady=true; writeURL();
}
_init();
document.addEventListener('keydown',onKey);
window.addEventListener('hashchange',()=>{   // external/manual URL changes (replaceState does not fire this)
  const tab=applyURLState(); renderHeader(); renderFilters('emails'); renderList(); renderContacts();
  if(tab&&tab!=='emails')switchTab(tab);
});
</script>
</body>
</html>"""
