#!/usr/bin/env python3
"""Local job tracker: star roles, move them through an application pipeline.

Serves on 127.0.0.1 only. A static digest file cannot write anywhere, so starring needs
something listening; this is the smallest thing that does the job, using only stdlib.
"""
from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import store

PAGE = """<!doctype html><meta charset="utf-8"><title>Job Tracker</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#fbfaf8;--fg:#1a1a19;--muted:#6b6a66;--line:#e5e2dc;--card:#fff;--accent:#7a4a2b;--star:#c8912f}
@media(prefers-color-scheme:dark){:root{--bg:#16151a;--fg:#eceaf0;--muted:#9a97a3;--line:#2b2933;--card:#1e1d24;--accent:#d9a273;--star:#e0b45c}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 ui-sans-serif,-apple-system,"Segoe UI",sans-serif}
.wrap{max-width:900px;margin:0 auto;padding:32px 20px 80px}
h1{font-size:22px;margin:0 0 18px}
nav{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:26px}
nav button{font:inherit;font-size:13px;padding:6px 13px;border-radius:20px;cursor:pointer;
  border:1px solid var(--line);background:var(--card);color:var(--muted)}
nav button.on{background:var(--accent);border-color:var(--accent);color:#fff}
nav button .n{opacity:.7;margin-left:5px}
.card{background:var(--card);border:1px solid var(--line);border-radius:9px;padding:14px 16px;margin-bottom:10px}
.card h3{margin:0;font-size:15px;font-weight:640}
.card h3 a{color:var(--fg);text-decoration:none}
.card h3 a:hover{color:var(--accent)}
.co{color:var(--muted);font-size:13px;margin:2px 0 7px}
.sum{font-size:13.5px;margin:0 0 8px}
.meta{font-size:11.5px;color:var(--muted);display:flex;gap:10px;flex-wrap:wrap;margin-bottom:10px}
.tag{border:1px solid var(--line);border-radius:20px;padding:1px 8px}
.acts{display:flex;gap:6px;flex-wrap:wrap;align-items:center}
.acts button{font:inherit;font-size:12.5px;padding:4px 11px;border-radius:6px;cursor:pointer;
  border:1px solid var(--line);background:transparent;color:var(--fg)}
.acts button:hover{border-color:var(--accent);color:var(--accent)}
.acts button.star{color:var(--star);border-color:var(--star)}
.note{margin-left:auto;font:inherit;font-size:12px;padding:4px 8px;border-radius:6px;
  border:1px solid var(--line);background:transparent;color:var(--fg);width:190px}
.empty{color:var(--muted);padding:26px 0;font-size:14px}
.filters{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-bottom:16px}
.filters .chip{font:inherit;font-size:12.5px;padding:4px 11px;border-radius:20px;cursor:pointer;
  border:1px solid var(--line);background:var(--card);color:var(--muted)}
.filters .chip.on{background:var(--accent);border-color:var(--accent);color:#fff}
.filters .chip .n{opacity:.65;margin-left:5px}
#q{font:inherit;font-size:13px;padding:6px 11px;border-radius:20px;border:1px solid var(--line);
  background:var(--card);color:var(--fg);min-width:200px}
.count{font-size:12.5px;color:var(--muted);margin:0 0 12px}
.more{display:block;margin:18px auto 0;font:inherit;font-size:13px;padding:8px 18px;
  border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--fg);cursor:pointer}
.more:hover{border-color:var(--accent);color:var(--accent)}
.bar{display:flex;gap:18px;flex-wrap:wrap;font-size:12.5px;color:var(--muted);margin-bottom:20px}
.bar b{color:var(--fg);font-weight:640}
.kan{display:flex;gap:12px;overflow-x:auto;padding-bottom:12px;align-items:flex-start}
.col{flex:0 0 268px;background:transparent}
.col h4{margin:0 0 9px;font-size:11.5px;text-transform:uppercase;letter-spacing:.08em;
  color:var(--muted);display:flex;justify-content:space-between}
.col .drop{min-height:70px;border-radius:9px;padding:2px;transition:background .12s}
.col .drop.over{background:color-mix(in srgb,var(--accent) 14%,transparent);
  outline:1px dashed var(--accent)}
.kcard{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 11px;
  margin-bottom:8px;cursor:grab;font-size:13px}
.kcard:hover{border-color:var(--accent)}
.kcard.drag{opacity:.4}
.kcard .t{font-weight:620;line-height:1.35;margin-bottom:2px}
.kcard .c{color:var(--muted);font-size:12px}
.kcard .row{display:flex;gap:6px;align-items:center;margin-top:7px;flex-wrap:wrap}
.pri{letter-spacing:1px;color:var(--star);font-size:11px}
.mini{font-size:10.5px;border:1px solid var(--line);border-radius:20px;padding:0 7px;color:var(--muted)}
.na{font-size:11px;color:var(--accent);margin-top:6px}
.scrim{position:fixed;inset:0;background:rgba(0,0,0,.45);display:none}
.scrim.on{display:block}
.drawer{position:fixed;top:0;right:0;bottom:0;width:min(460px,100%);background:var(--bg);
  border-left:1px solid var(--line);padding:22px;overflow-y:auto;display:none;z-index:5}
.drawer.on{display:block}
.drawer h2{margin:0 0 3px;font-size:17px}
.drawer .co{margin-bottom:14px}
.fld{margin-bottom:12px}
.fld label{display:block;font-size:11px;text-transform:uppercase;letter-spacing:.07em;
  color:var(--muted);margin-bottom:4px}
.fld input,.fld select,.fld textarea{width:100%;font:inherit;font-size:13px;padding:7px 9px;
  border-radius:7px;border:1px solid var(--line);background:var(--card);color:var(--fg)}
.fld textarea{min-height:110px;resize:vertical;line-height:1.5}
.hist{font-size:12px;color:var(--muted);border-top:1px solid var(--line);padding-top:12px;margin-top:16px}
.hist div{margin-bottom:3px}
.close{position:absolute;top:16px;right:18px;font-size:20px;cursor:pointer;color:var(--muted);
  background:none;border:none}
.saved{font-size:11.5px;color:var(--accent);height:14px}


</style>
<div class="wrap"><h1>Job Tracker</h1><nav id="nav"></nav><div id="stats" class="bar"></div><div id="filters"></div><div id="list"></div></div><div class="scrim" id="scrim" onclick="closeDrawer()"></div><aside class="drawer" id="drawer"></aside>
<script>
const LABEL={new:"Inbox",starred:"Starred",applied:"Applied",interviewing:"Interviewing",
             offer:"Offer",rejected:"Rejected",passed:"Passed"};
const COLS=["new","starred","applied","interviewing","offer"];
const STAGES=["","phone screen","technical","take-home","onsite","final","offer call"];
let data={},facets={},stats={},view=localStorage.getItem("view")||"board";
let filt={vertical:"",subtype:"",q:"",small:false},rows=[],total=0,offset=0,open=null;

const api=(u,o)=>fetch(u,o).then(r=>r.json());
const post=(u,b)=>api(u,{method:"POST",headers:{"Content-Type":"application/json"},
                        body:JSON.stringify(b)});
function esc(s){ return (s??"").toString().replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }

async function refresh(){ [data,stats]=await Promise.all([api("/api/board"),api("/api/stats")]); render(); }
async function loadFacets(){ facets=await api("/api/facets"); }
async function loadPool(more){
  if(!more){ offset=0; rows=[]; }
  const p=new URLSearchParams({limit:60,offset});
  if(filt.vertical)p.set("vertical",filt.vertical);
  if(filt.subtype)p.set("subtype",filt.subtype);
  if(filt.q)p.set("q",filt.q);
  if(filt.small)p.set("max_openings",60);
  const r=await api("/api/pool?"+p);
  total=r.total; rows=rows.concat(r.rows); offset=rows.length; render();
}
async function move(url,status){
  await post("/api/status",{url,status});
  if(view==="browse"){ rows=rows.filter(r=>r.url!==url); total--; await loadFacets(); }
  await refresh();
  if(open===url) await openDrawer(url);
}
async function starPool(url){
  await post("/api/star",{url});
  rows=rows.filter(r=>r.url!==url); total--;
  await loadFacets(); await refresh();
}
async function save(url,field,value){
  await post("/api/update",{url,fields:{[field]:value}});
  const el=document.getElementById("saved"); if(el){ el.textContent="saved";
    setTimeout(()=>{ if(el) el.textContent=""; },1200); }
  await refresh();
}

function render(){
  document.getElementById("nav").innerHTML=
    [["board","Board"],["browse","Browse all"]].map(([v,l])=>
      `<button class="${v===view?"on":""}" onclick="pick('${v}')">${l}${
        v==="browse"&&facets.total!=null?`<span class="n">${facets.total}</span>`:""}</button>`).join("");
  document.getElementById("stats").innerHTML=statsBar();
  document.getElementById("filters").innerHTML = view==="browse" ? filterBar() : "";
  document.getElementById("list").innerHTML = view==="browse" ? browseList() : kanban();
  if(view==="browse"){ const q=document.getElementById("q");
    if(q&&document.activeElement!==q) q.value=filt.q; }
}
function statsBar(){
  const c=stats.counts||{};
  const bits=[`<span><b>${stats.tracked_total??0}</b> tracked</span>`,
              `<span><b>${stats.applied_total??0}</b> applied</span>`];
  if(stats.applied_total) bits.push(
    `<span><b>${stats.interview_rate}%</b> reached interview</span>`,
    `<span><b>${stats.response_rate}%</b> heard back</span>`);
  const openN=(c.starred||0)+(c.applied||0)+(c.interviewing||0)+(c.offer||0);
  bits.push(`<span><b>${openN}</b> open</span>`);
  return bits.join("");
}
function kanban(){
  return `<div class="kan">`+COLS.map(s=>{
    const list=data[s]||[];
    return `<div class="col"><h4><span>${LABEL[s]}</span><span>${list.length}</span></h4>
      <div class="drop" data-status="${s}" ondragover="dragOver(event)"
           ondragleave="dragLeave(event)" ondrop="drop(event,'${s}')">
        ${list.map(kcard).join("")||`<div class="empty" style="padding:8px;font-size:12px">Drop here</div>`}
      </div></div>`;
  }).join("")+`</div>`+closedRow();
}
function closedRow(){
  const closed=[...(data.rejected||[]),...(data.passed||[])];
  if(!closed.length) return "";
  return `<div style="margin-top:22px"><h4 style="font-size:11.5px;text-transform:uppercase;
    letter-spacing:.08em;color:var(--muted)">Closed (${closed.length})</h4>
    <div class="kan">${closed.map(j=>`<div class="col" style="flex:0 0 268px">${kcard(j)}</div>`).join("")}</div></div>`;
}
function kcard(j){
  const pri=j.priority?`<span class="pri">${"\u25cf".repeat(j.priority)}</span>`:"";
  const tags=[];
  if(j.subtype&&j.subtype!=="unspecified") tags.push(`<span class="mini">${esc(j.subtype)}</span>`);
  if(j.stage) tags.push(`<span class="mini">${esc(j.stage)}</span>`);
  if(j.deadline) tags.push(`<span class="mini">due ${esc(j.deadline)}</span>`);
  return `<div class="kcard" draggable="true" ondragstart="dragStart(event,'${esc(j.url)}')"
       ondragend="dragEnd(event)" onclick="openDrawer('${esc(j.url)}')">
    <div class="t">${esc(j.title)}</div><div class="c">${esc(j.company)}</div>
    <div class="row">${pri}${tags.join("")}</div>
    ${j.next_action?`<div class="na">\u2192 ${esc(j.next_action)}${
      j.next_action_date?" ("+esc(j.next_action_date)+")":""}</div>`:""}</div>`;
}
let dragged=null;
function dragStart(e,url){ dragged=url; e.currentTarget.classList.add("drag");
  e.dataTransfer.effectAllowed="move"; e.stopPropagation(); }
function dragEnd(e){ e.currentTarget.classList.remove("drag"); }
function dragOver(e){ e.preventDefault(); e.currentTarget.classList.add("over"); }
function dragLeave(e){ e.currentTarget.classList.remove("over"); }
function drop(e,status){ e.preventDefault(); e.currentTarget.classList.remove("over");
  if(dragged){ const u=dragged; dragged=null; move(u,status); } }

async function openDrawer(url){
  open=url;
  const j=await api("/api/job?url="+encodeURIComponent(url));
  const f=(label,field,type)=>`<div class="fld"><label>${label}</label>
    <input type="${type||"text"}" value="${esc(j[field])}"
      onchange="save('${esc(url)}','${field}',this.value)"></div>`;
  document.getElementById("drawer").innerHTML=`
    <button class="close" onclick="closeDrawer()">\u00d7</button>
    <h2><a href="${esc(j.url)}" target="_blank" rel="noopener"
      style="color:inherit;text-decoration:none">${esc(j.title)}</a></h2>
    <div class="co">${esc(j.company)}${j.location?" \u00b7 "+esc(j.location.split(",").slice(0,2).join(",")):""}</div>
    <div class="acts" style="margin-bottom:16px">${
      Object.keys(LABEL).map(s=>`<button class="${s===j.status?"star":""}"
        onclick="move('${esc(url)}','${s}')">${LABEL[s]}</button>`).join("")}</div>
    <div id="saved" class="saved"></div>
    <div class="fld"><label>Priority</label>
      <select onchange="save('${esc(url)}','priority',this.value)">
        ${[0,1,2,3].map(n=>`<option value="${n}" ${n===j.priority?"selected":""}>${
          n?"\u25cf".repeat(n):"none"}</option>`).join("")}</select></div>
    <div class="fld"><label>Interview stage</label>
      <select onchange="save('${esc(url)}','stage',this.value)">
        ${STAGES.map(s=>`<option value="${s}" ${s===j.stage?"selected":""}>${s||"not started"}</option>`).join("")}</select></div>
    ${f("Next action","next_action")}
    ${f("Next action date","next_action_date","date")}
    ${f("Application deadline","deadline","date")}
    ${f("Contact / referral","contact")}
    ${f("Compensation","compensation")}
    <div class="fld"><label>Notes</label>
      <textarea onchange="save('${esc(url)}','notes',this.value)"
        placeholder="Why this role, who you spoke to, what to prepare\u2026">${esc(j.notes)}</textarea></div>
    ${j.summary?`<div class="fld"><label>Description</label>
      <div style="font-size:13px;line-height:1.5">${esc(j.summary)}</div></div>`:""}
    <div class="hist"><b>History</b>
      ${j.applied_at?`<div>applied ${esc(j.applied_at.slice(0,10))}</div>`:""}
      ${j.events.map(e=>`<div>${esc(e.from_status||"\u2014")} \u2192 ${esc(e.to_status)} \u00b7 ${
        esc(e.at.slice(0,10))}</div>`).join("")}</div>`;
  document.getElementById("drawer").classList.add("on");
  document.getElementById("scrim").classList.add("on");
}
function closeDrawer(){ open=null;
  document.getElementById("drawer").classList.remove("on");
  document.getElementById("scrim").classList.remove("on"); }
document.addEventListener("keydown",e=>{ if(e.key==="Escape") closeDrawer(); });

function browseList(){
  return (rows.length?`<p class="count">${total} matching, showing ${rows.length}</p>`:"")
    + (rows.length?rows.map(poolCard).join(""):`<div class="empty">Nothing matches those filters.</div>`)
    + (rows.length<total?`<button class="more" onclick="loadPool(true)">Load 60 more</button>`:"");
}
function filterBar(){
  const v=facets.vertical||{}, s=facets.subtype||{};
  const chip=(label,on,fn,n)=>`<button class="chip ${on?"on":""}" onclick="${fn}">${label}${n!=null?`<span class="n">${n}</span>`:""}</button>`;
  let h=`<div class="filters">`;
  h+=chip("All",!filt.vertical&&!filt.subtype&&!filt.small,"clearFilters()");
  for(const k of ["health-bio","robotics","hard-tech","legal"])
    if(v[k]) h+=chip(k,filt.vertical===k,`setF('vertical','${k}')`,v[k]);
  for(const k of ["Winter 2027","Spring 2027"])
    if(s[k]) h+=chip(k,filt.subtype===k,`setF('subtype','${k}')`,s[k]);
  h+=chip("Startups only",filt.small,"toggleSmall()");
  h+=`<input id="q" placeholder="search company or title" value="${esc(filt.q)}" oninput="onSearch(this.value)">`;
  return h+`</div>`;
}
let timer=null;
function onSearch(v){ filt.q=v; clearTimeout(timer); timer=setTimeout(()=>loadPool(false),250); }
function setF(k,v){ filt[k]=filt[k]===v?"":v; loadPool(false); }
function toggleSmall(){ filt.small=!filt.small; loadPool(false); }
function clearFilters(){ filt={vertical:"",subtype:"",q:"",small:false}; loadPool(false); }
function pick(v){ view=v; localStorage.setItem("view",v);
  if(v==="browse"){ loadFacets().then(()=>loadPool(false)); } else refresh(); }
function meta(j){
  const m=[];
  if(j.subtype&&j.subtype!=="unspecified") m.push(`<span class="tag">${esc(j.subtype)}</span>`);
  if(j.published_at) m.push("posted "+esc(j.published_at));
  if(j.location) m.push(esc(j.location.split(",").slice(0,3).join(",")));
  if(j.employer_openings!=null) m.push(esc(j.employer_openings)+" open at employer");
  if(j.on_watchlist) m.push("watchlist");
  return m;
}
function poolCard(j){
  return `<div class="card">
    <h3><a href="${esc(j.url)}" target="_blank" rel="noopener">${esc(j.title)}</a></h3>
    <div class="co">${esc(j.company)}</div>
    ${j.summary?`<p class="sum">${esc(j.summary.slice(0,190))}${j.summary.length>190?"\u2026":""}</p>`:""}
    <div class="meta">${meta(j).join(" &middot; ")}</div>
    <div class="acts">
      <button class="star" onclick="starPool('${esc(j.url)}')">\u2605 Star</button>
      <button onclick="move('${esc(j.url)}','passed')">Pass</button>
    </div></div>`;
}
loadFacets().then(()=>{ refresh(); if(view==="browse") loadPool(false); });
load();
</script>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        payload = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path == "/":
            return self._send(200, PAGE, "text/html; charset=utf-8")
        if self.path == "/api/board":
            with store.connect() as con:
                return self._send(200, json.dumps(store.board(con)))
        if self.path == "/api/stats":
            with store.connect() as con:
                return self._send(200, json.dumps(store.stats(con)))
        if self.path.startswith("/api/job"):
            url = (parse_qs(urlparse(self.path).query).get("url") or [""])[0]
            with store.connect() as con:
                try:
                    return self._send(200, json.dumps(store.job_detail(con, url)))
                except KeyError:
                    return self._send(404, json.dumps({"error": "unknown url"}))
        if self.path == "/api/facets":
            with store.connect() as con:
                return self._send(200, json.dumps(store.pool_facets(con)))
        if self.path.startswith("/api/pool"):
            qs = parse_qs(urlparse(self.path).query)
            one = lambda k: (qs.get(k) or [None])[0]
            with store.connect() as con:
                return self._send(200, json.dumps(store.query_pool(
                    con, vertical=one("vertical"), category=one("category"),
                    subtype=one("subtype"), max_age=one("max_age"),
                    max_openings=one("max_openings"), q=one("q"),
                    limit=min(int(one("limit") or 60), 200),
                    offset=int(one("offset") or 0))))
        self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._send(400, json.dumps({"error": "bad json"}))
        try:
            with store.connect() as con:
                if self.path == "/api/update":
                    result = store.update_fields(con, body["url"], body.get("fields") or {})
                elif self.path == "/api/star":
                    result = store.star_from_pool(con, body["url"])
                elif self.path == "/api/status":
                    result = store.set_status(con, body["url"], body["status"])
                elif self.path == "/api/note":
                    store.set_note(con, body["url"], body.get("note", ""))
                    result = {"ok": True}
                else:
                    return self._send(404, json.dumps({"error": "not found"}))
                store.export_json(con)
            self._send(200, json.dumps(result))
        except KeyError:
            self._send(404, json.dumps({"error": "unknown url"}))
        except ValueError as exc:
            self._send(400, json.dumps({"error": str(exc)}))

    def log_message(self, *args):
        pass


def main() -> int:
    ap = argparse.ArgumentParser(prog="tracker")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--open", action="store_true")
    args = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"tracker on {url}  (ctrl-c to stop)")
    if args.open:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
