"""Generate a single self-contained out/report.html from the triage + CRM + cost outputs.

A connected explorer (Tier 1): clickable people / orgs / threads, Person & Org "360" panels, and the
associated token cost shown per email and aggregated per person/org. No server, no build, no external
deps — data is embedded; open the file in a browser. The email body + participants are read from the
local corpus at render time (results.jsonl stays body-free). Run from the repo root:
  .venv/bin/python tools/make_report.py
"""

from __future__ import annotations

import glob
import json
from pathlib import Path

from email2data import crm
from email2data.config import load_settings, paths
from email2data.envelope import parse_eml
from email2data.signals import OUR_DOMAIN

settings = load_settings("config/settings.json")
settings["__settings_path__"] = str(Path("config/settings.json").resolve())
out = paths(settings, settings["__settings_path__"])["out_dir"]

emails = [json.loads(l) for l in (out / "results.jsonl").read_text().splitlines() if l.strip()]
contacts = ([json.loads(l) for l in (out / "contacts.jsonl").read_text().splitlines() if l.strip()]
            if (out / "contacts.jsonl").exists() else [])
cost = json.loads((out / "cost.json").read_text()) if (out / "cost.json").exists() else {}
per_cost = cost.get("per_email", {})

PRI = {"HIGH": 0, "NEEDS_REVIEW": 1, "MEDIUM": 2, "LOW": 3, "IGNORE": 4}
emails.sort(key=lambda r: (PRI.get(r.get("priority"), 9), -r.get("urgency", 0)))
contacts.sort(key=lambda c: -c.get("msg_count", 0))

mid2file = {}
for f in glob.glob("corpus/*.eml"):
    try:
        mid2file[parse_eml(Path(f).read_bytes())["message_id"]] = f
    except Exception:
        pass

BODY_CAP = 8000


def _internal(em: str) -> bool:
    d = em.rsplit("@", 1)[-1].lower() if "@" in em else ""
    return d == OUR_DOMAIN or d.endswith("." + OUR_DOMAIN)


for r in emails:
    pc = per_cost.get(r["message_id"], {})
    r.update(_date=None, _body="", _body_trunc=False, _people=[], _attach=[], _reply=False, _thread="",
             _tin=pc.get("in", 0), _tout=pc.get("out", 0), _cost=pc.get("cost", 0.0))
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


def _embed(obj) -> str:
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>email-2-data · triage report</title>
<style>
  :root{--bg:#eef0f3;--card:#fff;--bd:#e3e6ea;--bd2:#eef0f3;--tx:#15181c;--mut:#6b7280;--mut2:#9aa1ab;
    --ac:#3358d4;--int:#0d9488;--ext:#64748b;--shadow:0 1px 2px rgba(20,24,28,.05),0 1px 3px rgba(20,24,28,.04);}
  *{box-sizing:border-box} html,body{margin:0}
  body{font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;color:var(--tx);background:var(--bg)}
  header{background:var(--card);border-bottom:1px solid var(--bd);padding:16px 28px;position:sticky;top:0;z-index:20;box-shadow:var(--shadow)}
  .htop{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap}
  h1{margin:0;font-size:17px;font-weight:680;letter-spacing:-.01em}
  .sub{color:var(--mut);font-size:12.5px}
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
  .count{color:var(--mut);font-size:12px;margin-left:auto}
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
  .att{background:#fff;border:1px solid var(--bd);border-radius:8px;padding:5px 10px;font-size:12px} .att .ty{color:var(--mut2);font-size:10.5px}
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
  @media(max-width:900px){.layout{grid-template-columns:1fr}.list{max-height:340px}}
</style>
</head>
<body>
<header>
  <div class="htop"><h1>email-2-data · triage report</h1><span class="sub" id="sub"></span></div>
  <div class="stats" id="stats"></div>
  <div class="distbar" id="dist" title="priority mix"></div>
</header>
<div class="wrap">
  <div class="tabs">
    <div class="tab on" data-tab="emails" onclick="switchTab('emails')">Emails</div>
    <div class="tab" data-tab="contacts" onclick="switchTab('contacts')">Contacts</div>
  </div>
  <div id="emails">
    <div class="toolbar">
      <input type="search" id="q" placeholder="Search subject, sender, body, reason…" oninput="renderList()"/>
      <span id="prichips"></span>
      <span class="chip" id="dirchip" onclick="cycleDir()">Direction: all</span>
      <span class="count" id="ecount"></span>
    </div>
    <div id="banner"></div>
    <div class="layout">
      <div class="list" id="list"></div>
      <div class="detail" id="detail"><div class="empty">Select an email — or click a person, org or thread to explore connections.</div></div>
    </div>
  </div>
  <div id="contacts" class="hidden">
    <div class="toolbar">
      <input type="search" id="cq" placeholder="Search name, email…" oninput="renderContacts()"/>
      <span class="chip on" id="extchip" onclick="toggleExt()">external only</span>
      <span class="count" id="ccount"></span>
    </div>
    <table><thead><tr><th>Name</th><th>Email</th><th>Counterparty</th><th>Msgs</th><th>From/To/Cc</th><th>Last seen</th><th>Last purpose</th></tr></thead><tbody id="cbody"></tbody></table>
  </div>
</div>
<script>
const EMAILS=__EMAILS__, CONTACTS=__CONTACTS__, COST=__COST__, OURDOMAIN=__OURDOMAIN__;
const PRIcol={HIGH:'#e5484d',NEEDS_REVIEW:'#8e4ec6',MEDIUM:'#f5a623',LOW:'#3358d4',IGNORE:'#9aa1ab'};
const CPcol={CLIENT:'#13a36a',LEAD:'#0d9488',SUPPLIER:'#3358d4',INTERNAL:'#7c7f86',BULK:'#b9bbc6',OTHER:'#8b8d98'};
const PRIS=['HIGH','NEEDS_REVIEW','MEDIUM','LOW','IGNORE'], ROLE={from:'From',reply_to:'Reply-To',to:'To',cc:'Cc'};

// indexes
const byMid={}, threadMids={}, personMids={}, orgs={}, contactBy={};
function domainOf(e){return (e.split('@')[1]||'').toLowerCase();}
EMAILS.forEach(e=>{
  byMid[e.message_id]=e;
  (threadMids[e._thread]=threadMids[e._thread]||new Set()).add(e.message_id);
  (e._people||[]).forEach(p=>{
    (personMids[p.email]=personMids[p.email]||new Set()).add(e.message_id);
    const d=domainOf(p.email);
    const o=orgs[d]=orgs[d]||{people:new Set(),mids:new Set(),internal:p.internal};
    o.people.add(p.email); o.mids.add(e.message_id);
  });
});
CONTACTS.forEach(c=>contactBy[c.email]=c);
function nameOf(e){return (contactBy[e]&&contactBy[e].display_name)||e.split('@')[0];}
function isInt(e){return contactBy[e]?!!contactBy[e].is_internal:(domainOf(e)==OURDOMAIN||domainOf(e).endsWith('.'+OURDOMAIN));}
function esc(s){return (s==null?'':String(s)).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function badge(t,c,clk){return `<span class="badge ${clk?'clk':''}" style="background:${c}22;color:${c}" ${clk?`onclick="${clk}"`:''}>${esc(t)}</span>`;}
function fdate(s){return s?esc(String(s).slice(0,16).replace('T',' ')):'';}
function fTok(n){return (n||0).toLocaleString();}
function fCost(v){return v?('$'+v.toFixed(5)):'$0';}
function costOf(mids){let i=0,o=0,c=0;mids.forEach(m=>{const e=byMid[m];i+=e._tin;o+=e._tout;c+=e._cost;});return{i,o,c};}
function costPill(e){return e._cost?`<span class="pill cost" title="triage tokens for this email">${fTok(e._tin)} in · ${fTok(e._tout)} out · ${fCost(e._cost)}</span>`:'<span class="pill cost">offline · $0</span>';}

// ---- nav stack (drill-down with back) ----
let navStack=[];
function navTo(kind,id){navStack.push({kind,id});renderList();}
function navReset(kind,id){navStack=[{kind,id}];renderList();}
function back(){if(navStack.length>1){navStack.pop();renderList();}}

// ---- left list filters ----
let priFilter='ALL',dirFilter='all',cpFilter='ALL',threadFilter='';
function setPri(p){priFilter=p;renderHeader();renderList();}
function setCp(cp){cpFilter=(cpFilter==cp?'ALL':cp);renderList();}
function setThread(t){threadFilter=(threadFilter==t?'':t);renderList();}
function cycleDir(){dirFilter={all:'internal',internal:'external',external:'all'}[dirFilter];
  const c=document.getElementById('dirchip');c.textContent='Direction: '+dirFilter;c.classList.toggle('on',dirFilter!='all');renderList();}
function clearFilters(){cpFilter='ALL';threadFilter='';priFilter='ALL';dirFilter='all';
  const c=document.getElementById('dirchip');c.textContent='Direction: all';c.classList.remove('on');renderHeader();renderList();}
function filtered(){
  const q=(document.getElementById('q').value||'').toLowerCase();
  return EMAILS.filter(e=>(priFilter=='ALL'||e.priority==priFilter)&&(dirFilter=='all'||e.direction==dirFilter)
    &&(cpFilter=='ALL'||e.counterparty==cpFilter)&&(!threadFilter||e._thread==threadFilter)
    &&(!q||(`${e.subject} ${e.from_addr} ${e.reason} ${e.purpose} ${e.counterparty} ${e._body}`).toLowerCase().includes(q)));
}

function renderHeader(){
  const n=EMAILS.length,by={};EMAILS.forEach(e=>by[e.priority]=(by[e.priority]||0)+1);
  document.getElementById('sub').textContent=`${n} emails · ${COST.offline_pct??'–'}% offline / ${COST.llm_pct??'–'}% via LLM · ${COST.model||''}`;
  const cards=[['Emails',n,''],['Offline · free',COST.offline_calls??'–',''],['LLM calls',COST.llm_calls??'–',''],
    ['Run cost','$'+(COST.cost_usd??0).toFixed(4),'cost'],['Per 1k','$'+(COST.cost_per_1k_usd??0).toFixed(3),'cost'],
    ['Tokens',fTok((COST.input_tokens||0)+(COST.output_tokens||0)),'']];
  document.getElementById('stats').innerHTML=cards.map(([l,v,c])=>`<div class="stat ${c}"><div class="n">${v}</div><div class="l">${l}</div></div>`).join('');
  document.getElementById('dist').innerHTML=PRIS.filter(p=>by[p]).map(p=>`<span style="flex:${by[p]};background:${PRIcol[p]}" title="${p}: ${by[p]}"></span>`).join('');
  document.getElementById('prichips').innerHTML=['ALL',...PRIS].map(p=>`<span class="chip ${p==priFilter?'on':''}" onclick="setPri('${p}')">${p}${p!='ALL'?' '+(by[p]||0):''}</span>`).join('');
}
function renderBanner(){
  const b=[];
  if(cpFilter!='ALL')b.push(`counterparty <b>${esc(cpFilter)}</b>`);
  if(threadFilter){const e=byMid[[...(threadMids[threadFilter]||[])][0]];b.push(`thread <b>${esc((e&&e.subject)||'')}</b> (${threadMids[threadFilter].size} msgs)`);}
  document.getElementById('banner').innerHTML=b.length?`<div class="banner">Filtered by ${b.join(' · ')}<span class="x" onclick="clearFilters()">clear ✕</span></div>`:'';
}
function renderList(){
  renderBanner();
  const rows=filtered(); const top=navStack[navStack.length-1];
  const selMid=top&&top.kind=='email'?top.id:null;
  document.getElementById('ecount').textContent=`${rows.length} shown`;
  if(!navStack.length&&rows.length)navStack=[{kind:'email',id:rows[0].message_id}];
  document.getElementById('list').innerHTML=rows.map(e=>`
    <div class="item ${e.message_id==selMid?'sel':''}" style="border-left-color:${e.message_id==selMid?'':PRIcol[e.priority]+'88'}" onclick="navReset('email','${esc(e.message_id)}')">
      <div class="top">${badge(e.priority,PRIcol[e.priority])}${badge(e.counterparty,CPcol[e.counterparty])}<span class="date">${fdate(e._date)}</span></div>
      <div class="subj">${esc(e.subject||'(no subject)')}</div>
      <div class="frm"><span class="dot" style="background:${e.direction=='internal'?'var(--int)':'var(--ext)'}"></span>${esc(e.from_addr||'')}</div>
    </div>`).join('')||'<div class="empty">No emails match.</div>';
  renderDetail();
}

function peopleRow(ps){
  if(!ps||!ps.length)return '<span class="muted">none</span>';
  const ord={from:0,reply_to:1,to:2,cc:3};
  return [...ps].sort((a,b)=>ord[a.role]-ord[b.role]).map(p=>`
    <div class="person ${p.internal?'int':''}" onclick="navTo('person','${esc(p.email)}')">
      <span class="role">${ROLE[p.role]||p.role}${p.internal?' · internal':''}</span>
      <span class="nm">${esc(p.name||p.email.split('@')[0])}</span><span class="em">${esc(p.email)}</span></div>`).join('');
}
function bodyHtml(t){if(!t)return '<span class="muted">no body text</span>';
  return t.split('\n').map(l=>l.trimStart().startsWith('>')?`<span class="qline">${esc(l)}</span>`:esc(l)).join('\n');}
function entChips(en){
  const want=[['client_name','client'],['client_email','client email'],['deadline','deadline'],['money','money'],['nif','nif'],['iban','iban'],['product_or_service','product / service'],['action_requested','action requested']];
  const g=want.filter(([k])=>en&&en[k]);
  return g.length?g.map(([k,l])=>`<div class="ent"><b>${l}</b>${esc(en[k])}</div>`).join(''):'<span class="muted">no entities extracted</span>';
}
function miniRows(mids){
  return [...mids].map(m=>byMid[m]).sort((a,b)=>(b._date||'').localeCompare(a._date||'')).map(e=>`
    <div class="mini" onclick="navTo('email','${esc(e.message_id)}')">
      <span class="dot" style="background:${PRIcol[e.priority]}"></span>
      <span class="ms">${esc(e.subject||'(no subject)')}</span>
      <span class="md">${esc((e._date||'').slice(0,10))}</span><span class="mc">${e._cost?fCost(e._cost):'$0'}</span></div>`).join('');
}

function emailCard(e){
  const ni=(e._people||[]).filter(p=>p.internal).length, ne=(e._people||[]).length-ni;
  const dom=domainOf(e.from_addr||''); const tcount=threadMids[e._thread]?threadMids[e._thread].size:1;
  return `
    <h2>${esc(e.subject||'(no subject)')}</h2>
    <div class="meta">
      ${badge(e.priority,PRIcol[e.priority])}${badge(e.counterparty,CPcol[e.counterparty],`setCp('${esc(e.counterparty)}')`)}
      <span class="pill">${esc(e.purpose)}</span>
      <span class="pill"><span class="dot" style="background:${e.direction=='internal'?'var(--int)':'var(--ext)'}"></span>${esc(e.direction)}</span>
      <span class="pill">urgency&nbsp;<b class="urg">${e.urgency}</b></span><span class="pill">conf ${e.confidence}</span>
      <span class="pill">${e.decided_by&&e.decided_by.startsWith('tier0')?'T0 · offline':'T1 · LLM'}</span>
      ${tcount>1?`<span class="pill clk" onclick="setThread('${esc(e._thread)}')">↳ thread (${tcount})</span>`:''}
      ${e._reply?'<span class="pill">↩ reply</span>':''}
      ${costPill(e)}</div>
    <div class="dmut">${fdate(e._date)} · from <span class="link" onclick="navTo('person','${esc(e.from_addr)}')">${esc(e.from_addr||'')}</span>${dom?` · <span class="link" onclick="navTo('org','${esc(dom)}')">${esc(dom)}</span>`:''}</div>
    <div class="sec"><h3>People involved · ${ni} internal, ${ne} external
      <span class="legend"><i><span class="dot" style="background:var(--int)"></span>internal</i><i><span class="dot" style="background:var(--ext)"></span>external</i><i class="muted">click to open profile</i></span></h3>
      <div class="people">${peopleRow(e._people)}</div></div>
    ${e._attach&&e._attach.length?`<div class="sec"><h3>Attachments · ${e._attach.length}</h3><div class="atts">${e._attach.map(a=>`<div class="att">${esc(a.name)} <span class="ty">${esc((a.type||'').split('/').pop())}${a.size?' · '+Math.round(a.size/1024)+'kb':''}</span></div>`).join('')}</div></div>`:''}
    <div class="sec"><h3>Why this verdict</h3><div class="reason">${esc(e.reason||'—')}</div></div>
    <div class="sec"><h3>Extracted data</h3><div class="ents">${entChips(e.entities)}</div></div>
    <div class="sec"><h3>Original email${e._body_trunc?' · (truncated)':''}</h3><div class="body">${bodyHtml(e._body)}</div></div>`;
}

function tally(jsonStr){try{const o=JSON.parse(jsonStr||'{}');return Object.entries(o).sort((a,b)=>b[1]-a[1]);}catch(_){return [];}}
function personCard(email){
  const c=contactBy[email]||{}, mids=[...(personMids[email]||[])], cs=costOf(mids), dom=domainOf(email);
  const co={}; mids.forEach(m=>byMid[m]._people.forEach(p=>{if(p.email!=email)co[p.email]=(co[p.email]||0)+1;}));
  const coArr=Object.entries(co).sort((a,b)=>b[1]-a[1]).slice(0,24);
  const cpt=tally(c.counterparty_counts), put=tally(c.purpose_counts);
  return `
    <h2>${esc(c.display_name||nameOf(email))} ${isInt(email)?'<span class="badge" style="background:#0d948822;color:#0d9488">internal</span>':''}</h2>
    <div class="dmut">${esc(email)} · <span class="link" onclick="navTo('org','${esc(dom)}')">${esc(dom)}</span></div>
    <div class="meta" style="margin-top:10px">${badge(c.last_counterparty||'—',CPcol[c.last_counterparty]||'#888')}
      <span class="pill cost">${fTok(cs.i+cs.o)} tok · ${fCost(cs.c)} total</span></div>
    <div class="sec"><div class="kv">
      <div><b>messages</b>${mids.length}</div><div><b>from/to/cc</b>${c.from_count||0}/${c.to_count||0}/${c.cc_count||0}</div>
      <div><b>first seen</b>${esc((c.first_seen||'').slice(0,10))}</div><div><b>last seen</b>${esc((c.last_seen||'').slice(0,10))}</div>
      <div><b>last purpose</b>${esc(c.last_purpose||'—')}</div></div></div>
    ${cpt.length?`<div class="sec"><h3>Relationship type</h3><div class="ents">${cpt.map(([k,v])=>`<div class="ent"><b>${esc(k)}</b>${v}</div>`).join('')}</div></div>`:''}
    ${put.length?`<div class="sec"><h3>Email types</h3><div class="ents">${put.map(([k,v])=>`<div class="ent"><b>${esc(k)}</b>${v}</div>`).join('')}</div></div>`:''}
    ${coArr.length?`<div class="sec"><h3>Connected people · ${coArr.length}<span class="legend"><i class="muted">co-appear in emails — click to open</i></span></h3><div class="copips">${coArr.map(([e,n])=>`<div class="copip ${isInt(e)?'int':''}" onclick="navTo('person','${esc(e)}')">${esc(nameOf(e))} <b>×${n}</b></div>`).join('')}</div></div>`:''}
    <div class="sec"><h3>Emails · ${mids.length}</h3><div class="sublist">${miniRows(mids)}</div></div>`;
}

function orgCard(dom){
  const o=orgs[dom]||{people:new Set(),mids:new Set()}; const ppl=[...o.people], mids=[...o.mids], cs=costOf(mids);
  const cpc={}; ppl.forEach(e=>{const c=contactBy[e];if(c&&c.last_counterparty)cpc[c.last_counterparty]=(cpc[c.last_counterparty]||0)+1;});
  const cpArr=Object.entries(cpc).sort((a,b)=>b[1]-a[1]);
  const dates=mids.map(m=>byMid[m]._date).filter(Boolean).sort();
  return `
    <h2>${esc(dom)} ${o.internal?'<span class="badge" style="background:#0d948822;color:#0d9488">internal</span>':''}</h2>
    <div class="meta" style="margin-top:6px">${cpArr.map(([k,v])=>badge(k+' ×'+v,CPcol[k]||'#888')).join(' ')||''}
      <span class="pill cost">${fTok(cs.i+cs.o)} tok · ${fCost(cs.c)} total</span></div>
    <div class="sec"><div class="kv">
      <div><b>people</b>${ppl.length}</div><div><b>emails</b>${mids.length}</div>
      <div><b>first contact</b>${esc((dates[0]||'').slice(0,10))}</div><div><b>last contact</b>${esc((dates[dates.length-1]||'').slice(0,10))}</div></div></div>
    <div class="sec"><h3>People · ${ppl.length}</h3><div class="copips">${ppl.map(e=>{const c=contactBy[e]||{};return `<div class="copip ${isInt(e)?'int':''}" onclick="navTo('person','${esc(e)}')">${esc(nameOf(e))} <b>×${c.msg_count||0}</b></div>`;}).join('')}</div></div>
    <div class="sec"><h3>Emails · ${mids.length}</h3><div class="sublist">${miniRows(mids)}</div></div>`;
}

function renderDetail(){
  const top=navStack[navStack.length-1], d=document.getElementById('detail');
  if(!top){d.innerHTML='<div class="empty">Select an email — or click a person, org or thread.</div>';return;}
  const bc=navStack.length>1?`<div class="bc"><span onclick="back()">← back</span></div>`:'';
  if(top.kind=='email'){const e=byMid[top.id];d.innerHTML=bc+(e?emailCard(e):'<div class="empty">not found</div>');}
  else if(top.kind=='person')d.innerHTML=bc+personCard(top.id);
  else if(top.kind=='org')d.innerHTML=bc+orgCard(top.id);
}

// contacts tab
let extOnly=true;
function toggleExt(){extOnly=!extOnly;document.getElementById('extchip').classList.toggle('on',extOnly);renderContacts();}
function openContact(email){switchTab('emails');navReset('person',email);}
function renderContacts(){
  const q=(document.getElementById('cq').value||'').toLowerCase();
  const rows=CONTACTS.filter(c=>(!extOnly||!c.is_internal)&&(!q||(`${c.display_name} ${c.email}`).toLowerCase().includes(q)));
  document.getElementById('ccount').textContent=`${rows.length} shown`;
  document.getElementById('cbody').innerHTML=rows.map(c=>`
    <tr class="crow ${c.is_internal?'int':''}" onclick="openContact('${esc(c.email)}')">
      <td>${esc(c.display_name||'—')}</td><td class="muted"><span class="dot" style="background:${c.is_internal?'var(--int)':'var(--ext)'}"></span> ${esc(c.email)}</td>
      <td>${badge(c.last_counterparty||'—',CPcol[c.last_counterparty]||'#888')}</td><td class="urg">${c.msg_count}</td>
      <td class="muted">${c.from_count}/${c.to_count}/${c.cc_count}</td><td class="muted">${esc((c.last_seen||'').slice(0,10))}</td><td>${esc(c.last_purpose||'')}</td></tr>`).join('')||'<tr><td colspan="7" class="empty">No contacts match.</td></tr>';
}
function switchTab(t){document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('on',x.dataset.tab==t));
  document.getElementById('emails').classList.toggle('hidden',t!='emails');document.getElementById('contacts').classList.toggle('hidden',t!='contacts');}

renderHeader(); renderList(); renderContacts();
</script>
</body>
</html>"""

html = (TEMPLATE
        .replace("__EMAILS__", _embed(emails))
        .replace("__CONTACTS__", _embed(contacts))
        .replace("__COST__", _embed({k: v for k, v in cost.items() if k != "per_email"}))
        .replace("__OURDOMAIN__", _embed(OUR_DOMAIN)))
(out / "report.html").write_text(html, encoding="utf-8")
print(f"wrote {out / 'report.html'}  ({len(emails)} emails, {len(contacts)} contacts)")
