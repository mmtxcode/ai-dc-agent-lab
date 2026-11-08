"""
Report MCP server — builds HTML + PDF environment reports.

The split of work is the point of this lab:
  * CODE collects the facts. report_collect pulls data straight from
    Intersight, ACI and Nexus Dashboard and saves an exact snapshot.
    Every table and number in the report comes from that snapshot.
  * The MODEL writes the story. It reads a short digest of the snapshot and
    supplies the executive summary, findings and recommendations to
    report_build. Those sections are labelled "AI-written" in the report.

Two tools, always used in order:
    report_collect(scope)  -> snapshot_id + digest     (reads the infrastructure)
    report_build(snapshot_id, title, executive_summary, findings, recommendations)
                           -> reports/<name>.html and .pdf (writes local files only)

PDF: Playwright drives the browser already on the jump host (Microsoft Edge),
falling back to Edge/Chrome's own headless print. Override with
REPORT_BROWSER=<path to msedge.exe or chrome.exe>.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from collections import Counter
from pathlib import Path

import anyio
import httpx
from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT / "mcp_servers"))

import aci_server as aci          # noqa: E402  reuse the same API code as the ACI tools
import intersight_server as isec  # noqa: E402  reuse the same API code as the Intersight tools

REPORTS = ROOT / "reports"
SNAPSHOTS = REPORTS / ".snapshots"
SCOPES = ("health", "inventory", "topology")

mcp = FastMCP("report", log_level="WARNING")


# =========================================================================
# Data collection (code, not the model)
# =========================================================================

def _now() -> str:
    return dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")


async def _section(name: str, fn, *args):
    """Run one collector. A failure is recorded, not raised: one domain being
    down shouldn't stop the whole report."""
    try:
        data = await anyio.to_thread.run_sync(fn, *args) if not _is_async(fn) else await fn(*args)
        return {"source": name, "status": "ok", "data": data, "at": _now()}
    except Exception as e:
        return {"source": name, "status": "error", "error": _err(e), "at": _now()}


def _err(e: BaseException) -> str:
    """Dig the real cause out of async exception groups ("unhandled errors in a TaskGroup")."""
    while isinstance(e, BaseExceptionGroup) and e.exceptions:
        e = e.exceptions[0]
    return f"{type(e).__name__}: {e}"[:300]


def _is_async(fn) -> bool:
    import inspect
    return inspect.iscoroutinefunction(fn)


async def _nd_calls(calls: list[tuple[str, dict]]) -> dict:
    """Call tools on Nexus Dashboard's built-in MCP server (we are its client here)."""
    url = os.getenv("ND_URL", "").rstrip("/") + "/api/v1/mcp"
    headers = {"X-Nd-Apikey": os.getenv("ND_API_KEY", ""), "X-Nd-Username": os.getenv("ND_USER", "")}
    verify = os.getenv("ND_VERIFY_TLS", "false").lower() == "true"

    def factory(headers=None, timeout=None, auth=None):
        return httpx.AsyncClient(headers=headers, timeout=timeout, auth=auth, verify=verify)

    out = {}
    async with streamablehttp_client(url, headers=headers, httpx_client_factory=factory) as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()
            available = {t.name for t in (await session.list_tools()).tools}
            for tool, args in calls:
                if tool not in available:
                    out[tool] = {"status": "error", "error": "tool not offered by this ND"}
                    continue
                res = await session.call_tool(tool, args)
                text = "\n".join(c.text for c in res.content if getattr(c, "type", "") == "text")
                try:
                    data = json.loads(text)
                except ValueError:
                    data = text
                out[tool] = {"status": "error" if res.isError else "ok", "data": data, "at": _now()}
    return out


def _aci_nodes():
    return aci.aci_query("/api/class/fabricNode.json")


def _aci_links():
    return aci.aci_query("/api/class/fabricLink.json")


async def _collect(scope: list[str]) -> dict:
    snap: dict = {"scope": scope, "collected_at": _now(), "sections": {}}
    s = snap["sections"]
    nd_calls: list[tuple[str, dict]] = []

    if "health" in scope:
        s["aci_health"] = await _section("ACI · aci_get_fabric_health", aci.aci_get_fabric_health)
        s["aci_faults"] = await _section("ACI · aci_list_faults", aci.aci_list_faults, "", 100)
        s["intersight_alarms"] = await _section("Intersight · cond/Alarms",
                                                isec.intersight_list_alarms, "", 100)
        nd_calls += [("get_nd_fabric_summary", {}), ("get_nd_anomalies_advisories_summary", {})]
    if "inventory" in scope:
        s["intersight_servers"] = await _section("Intersight · compute/PhysicalSummaries",
                                                 isec.intersight_list_servers, 200)
        s["aci_nodes"] = await _section("ACI · fabricNode", _aci_nodes)
    if "topology" in scope:
        s.setdefault("aci_nodes", await _section("ACI · fabricNode", _aci_nodes))
        s["aci_links"] = await _section("ACI · fabricLink", _aci_links)
        nd_calls += [("get_nd_unified_topology", {})]

    if nd_calls:
        try:
            for tool, result in (await _nd_calls(nd_calls)).items():
                result["source"] = f"Nexus Dashboard MCP · {tool}"
                s[f"nd_{tool}"] = result
        except Exception as e:
            for tool, _ in nd_calls:
                s[f"nd_{tool}"] = {"source": f"Nexus Dashboard MCP · {tool}", "status": "error",
                                   "error": _err(e), "at": _now()}
    return snap


def _ok(snap, key):
    sec = snap["sections"].get(key)
    return sec["data"] if sec and sec["status"] == "ok" else None


def _digest(snap: dict) -> dict:
    """Short version for the model: enough to write a summary, small enough to be fast."""
    d: dict = {"collected_at": snap["collected_at"], "sources": {}}
    for key, sec in snap["sections"].items():
        d["sources"][key] = sec["status"] if sec["status"] == "ok" else f"error: {sec['error'][:120]}"

    if (h := _ok(snap, "aci_health")) is not None:
        fabric = (h.get("fabric") or [{}])[0]
        d["aci_fabric_health"] = fabric.get("cur") or fabric.get("healthAvg") or fabric
        d["aci_lowest_tenant_health"] = sorted(
            [t for t in h.get("tenants", []) if t.get("health") not in (None, "")],
            key=lambda t: int(t["health"]))[:5]
    if (f := _ok(snap, "aci_faults")) is not None:
        d["aci_fault_counts"] = dict(Counter(x.get("severity", "?") for x in f))
        d["aci_top_faults"] = [{k: x.get(k) for k in ("severity", "code", "descr")} for x in f[:8]]
    if (a := _ok(snap, "intersight_alarms")) is not None:
        d["intersight_alarm_counts"] = dict(Counter(x.get("Severity", "?") for x in a))
        d["intersight_top_alarms"] = [{k: x.get(k) for k in ("Severity", "Code", "AffectedMoDisplayName", "Description")}
                                      for x in a[:8]]
    if (srv := _ok(snap, "intersight_servers")) is not None:
        d["server_count"] = len(srv)
        d["server_models"] = dict(Counter(x.get("Model", "?") for x in srv))
        d["server_power"] = dict(Counter(x.get("OperPowerState", "?") for x in srv))
    if (n := _ok(snap, "aci_nodes")) is not None:
        d["aci_nodes_by_role"] = dict(Counter(x.get("role", "?") for x in n))
    if (lk := _ok(snap, "aci_links")) is not None:
        d["aci_link_count"] = len(lk)
    for tool in ("get_nd_fabric_summary", "get_nd_anomalies_advisories_summary"):
        if (nd := _ok(snap, f"nd_{tool}")) is not None:
            d[f"nd_{tool}"] = json.dumps(nd, default=str)[:2500]
    return d


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def report_collect(scope: list[str] | None = None) -> dict:
    """Step 1 of building a report. Collects exact data from Intersight, ACI and
    Nexus Dashboard and saves it as a snapshot. scope: any of "health",
    "inventory", "topology" (default: all three).
    Returns snapshot_id and a short digest. Use ONLY the digest's facts when
    writing the narrative for report_build."""
    scope = [x.lower() for x in (scope or SCOPES) if x.lower() in SCOPES] or list(SCOPES)
    snap = await _collect(scope)
    snap_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    (SNAPSHOTS / f"{snap_id}.json").write_text(json.dumps(snap, default=str, indent=1), encoding="utf-8")
    return {"snapshot_id": snap_id, "scope": scope, "digest": _digest(snap)}


# =========================================================================
# Rendering (code, not the model)
# =========================================================================

E = html.escape
MAX_ROWS = 60


def _cell(v) -> str:
    if v is None or v == "":
        return '<span class="muted">—</span>'
    if isinstance(v, (dict, list)):
        txt = json.dumps(v, default=str)
        return f'<code>{E(txt[:200])}{"…" if len(txt) > 200 else ""}</code>'
    return E(str(v))


def _table(rows: list[dict], cols: list[str] | None = None, max_rows: int = MAX_ROWS) -> str:
    if not rows:
        return '<p class="muted">No records.</p>'
    if cols is None:
        cols = []
        for r in rows[:20]:
            for k, v in r.items():
                if k not in cols and not isinstance(v, (dict, list)):
                    cols.append(k)
        cols = cols[:8]
    keys = [c.split(":", 1)[0] for c in cols]            # "key:Label" sets a friendlier header
    head = "".join(f"<th>{E(c.split(':', 1)[-1])}</th>" for c in cols)
    body = "".join("<tr>" + "".join(f"<td>{_cell(r.get(k))}</td>" for k in keys) + "</tr>"
                   for r in rows[:max_rows])
    more = (f'<p class="muted">Showing {max_rows} of {len(rows)} rows. '
            f'Full data is in the snapshot file.</p>' if len(rows) > max_rows else "")
    return f'<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>{more}'


def _kv(d: dict) -> str:
    rows = "".join(f"<tr><th>{E(str(k))}</th><td>{_cell(v)}</td></tr>" for k, v in d.items())
    return f'<table class="kv"><tbody>{rows}</tbody></table>'


def _any(data, depth: int = 0) -> str:
    """Render data whose exact shape we don't control (Nexus Dashboard's MCP output)."""
    if isinstance(data, list):
        if data and all(isinstance(x, dict) for x in data):
            return _table(data)
        return _cell(data)
    if isinstance(data, dict):
        if all(not isinstance(v, (dict, list)) for v in data.values()):
            return _kv(data) if data else '<p class="muted">Empty.</p>'
        if depth >= 2:
            return _cell(data)
        scalars = {k: v for k, v in data.items() if not isinstance(v, (dict, list))}
        parts = [_kv(scalars)] if scalars else []
        for k, v in data.items():
            if isinstance(v, (dict, list)) and v:
                parts.append(f"<h4>{E(str(k))}</h4>{_any(v, depth + 1)}")
        return "".join(parts) or '<p class="muted">Empty.</p>'
    return f"<pre>{E(str(data))[:4000]}</pre>"


def _source_block(snap, key: str, title: str, body_fn) -> str:
    sec = snap["sections"].get(key)
    if not sec:
        return ""
    head = f'<h3>{E(title)}</h3><p class="src">Source: {E(sec["source"])} · {E(sec["at"])}</p>'
    if sec["status"] != "ok":
        return head + f'<p class="err">Not available: {E(sec.get("error", "error"))}</p>'
    return head + body_fn(sec["data"])


def _sev_badges(counts: dict) -> str:
    order = ["critical", "major", "minor", "warning", "info", "cleared"]
    items = sorted(counts.items(), key=lambda kv: order.index(kv[0].lower()) if kv[0].lower() in order else 99)
    return '<p class="badges">' + "".join(
        f'<span class="badge sev-{E(k.lower())}">{E(k)}: {v}</span>' for k, v in items) + "</p>"


# ---- topology -----------------------------------------------------------

TIER = {"spine": 0, "superspine": 0, "leaf": 1, "border": 1, "borderleaf": 1, "controller": 2}


def _svg_topology(nodes: list[dict], links: list[tuple[str, str]], label=lambda n: n["name"]) -> str:
    """Simple tiered diagram: spines on top, leaves in the middle, controllers below.
    Drawn as plain SVG so it works offline and prints cleanly to PDF."""
    if not nodes:
        return '<p class="muted">No nodes.</p>'
    nodes = nodes[:48]
    rows: dict[int, list[dict]] = {}
    for n in nodes:
        rows.setdefault(TIER.get(str(n.get("role", "")).lower().replace("-", "").replace(" ", ""), 1), []).append(n)
    bw, bh, gap, vgap = 118, 40, 22, 90
    width = max(len(r) for r in rows.values()) * (bw + gap) + gap
    pos, y = {}, 20
    parts = []
    for tier in sorted(rows):
        row = sorted(rows[tier], key=lambda n: str(n.get("name")))
        x0 = (width - (len(row) * (bw + gap) - gap)) / 2
        for i, n in enumerate(row):
            pos[str(n["id"])] = (x0 + i * (bw + gap), y)
        y += bh + vgap
    height = y - vgap + 20
    seen = Counter(tuple(sorted(l)) for l in links if l[0] in pos and l[1] in pos)
    for (a, b), count in seen.items():
        (xa, ya), (xb, yb) = pos[a], pos[b]
        x1, y1, x2, y2 = xa + bw / 2, ya + bh, xb + bw / 2, yb
        if ya > yb:
            x1, y1, x2, y2 = xb + bw / 2, yb + bh, xa + bw / 2, ya
        parts.append(f'<line x1="{x1:.0f}" y1="{y1:.0f}" x2="{x2:.0f}" y2="{y2:.0f}" class="link"/>')
        if count > 1:
            parts.append(f'<text x="{(x1 + x2) / 2:.0f}" y="{(y1 + y2) / 2:.0f}" class="lc">×{count}</text>')
    for n in nodes:
        x, yy = pos[str(n["id"])]
        role = str(n.get("role", "")).lower()
        parts.append(f'<rect x="{x:.0f}" y="{yy}" width="{bw}" height="{bh}" rx="6" class="node r-{E(role)}"/>'
                     f'<text x="{x + bw / 2:.0f}" y="{yy + 17}" class="nl">{E(str(label(n))[:16])}</text>'
                     f'<text x="{x + bw / 2:.0f}" y="{yy + 31}" class="ns">{E(role)} · {E(str(n["id"])[:10])}</text>')
    return (f'<svg viewBox="0 0 {width:.0f} {height:.0f}" class="topo" role="img" '
            f'style="max-width:{max(width, 420) * 1.15:.0f}px" '
            f'aria-label="Topology diagram">{"".join(parts)}</svg>')


def _find_list(d, pattern: str, depth: int = 0):
    if depth > 3:
        return None
    if isinstance(d, dict):
        for k, v in d.items():
            if re.search(pattern, k, re.I) and isinstance(v, list) and v and isinstance(v[0], dict):
                return v
        for v in d.values():
            if (found := _find_list(v, pattern, depth + 1)) is not None:
                return found
    return None


def _pick(d: dict, *keys):
    for k in keys:
        for real in d:
            if real.lower() == k.lower() and d[real] not in (None, ""):
                return d[real]
    return None


def _nd_topology(data) -> str:
    """ND's topology output format isn't documented, so try common shapes and fall
    back to a table when the nodes/links can't be identified."""
    raw_nodes = _find_list(data, r"node")
    raw_edges = _find_list(data, r"edge|link|connection")
    if raw_nodes:
        nodes = []
        for n in raw_nodes:
            nid = _pick(n, "id", "nodeId", "serialNumber", "serial", "name", "hostname")
            if nid is None:
                continue
            nodes.append({"id": str(nid), "name": str(_pick(n, "name", "hostname", "nodeName", "label") or nid),
                          "role": str(_pick(n, "role", "nodeRole", "type", "deviceRole") or "")})
        links = []
        for e in raw_edges or []:
            a = _pick(e, "source", "from", "src", "sourceId", "node1", "sourceNode")
            b = _pick(e, "target", "to", "dst", "targetId", "node2", "targetNode")
            if isinstance(a, dict):
                a = _pick(a, "id", "name")
            if isinstance(b, dict):
                b = _pick(b, "id", "name")
            if a is not None and b is not None:
                links.append((str(a), str(b)))
        if nodes:
            return _svg_topology(nodes, links) + f'<p class="muted">{len(nodes)} nodes, {len(links)} links.</p>'
    return _any(data)


# ---- page ---------------------------------------------------------------

CSS = """
@page { size: Letter; margin: 14mm 12mm; }
:root { --ink:#1b2430; --muted:#5d6b7a; --line:#d9e0e7; --accent:#0a5ea8; --soft:#f3f6f9; }
* { box-sizing: border-box; }
body { font: 13px/1.5 "Segoe UI", system-ui, -apple-system, Arial, sans-serif; color: var(--ink);
       background:#fff; margin:0; padding:24px; max-width:1100px; margin-inline:auto; }
h1 { font-size: 26px; margin: 0 0 4px; } h2 { font-size: 18px; margin: 28px 0 8px; padding-bottom:4px;
     border-bottom: 2px solid var(--accent); } h3 { font-size: 14px; margin: 18px 0 2px; }
h4 { font-size: 12px; margin: 12px 0 4px; color: var(--muted); text-transform: uppercase; letter-spacing:.04em; }
.cover { border-left: 6px solid var(--accent); padding: 4px 0 4px 16px; margin-bottom: 18px; }
.meta { color: var(--muted); margin: 0; }
.ai { background: #fff8e6; border: 1px solid #f0d58a; border-radius: 6px; padding: 10px 14px; }
.ai .tag { display:inline-block; font-size: 11px; font-weight: 600; color:#7a5800; background:#f7e3a6;
           border-radius: 4px; padding: 1px 6px; margin-bottom: 4px; }
.src { color: var(--muted); font-size: 11px; margin: 0 0 6px; }
.err { color: #a12622; background: #fdecea; border-radius: 4px; padding: 6px 10px; }
.muted { color: var(--muted); }
table { border-collapse: collapse; width: 100%; margin: 4px 0 8px; font-size: 12px; }
th, td { border: 1px solid var(--line); padding: 4px 6px; text-align: left; vertical-align: top; }
thead th { background: var(--soft); } table.kv th { width: 28%; background: var(--soft); font-weight: 600; }
tr { break-inside: avoid; } code { font-size: 11px; word-break: break-all; }
.badges { margin: 4px 0 8px; } .badge { display:inline-block; border-radius: 10px; padding: 1px 9px;
  margin-right: 6px; font-size: 12px; background: var(--soft); border:1px solid var(--line); }
.sev-critical { background:#fdecea; border-color:#f3b5b0; } .sev-major { background:#fff1e5; border-color:#f6c79d; }
.sev-minor, .sev-warning { background:#fff8e1; border-color:#efd88e; }
.topo { display: block; width: 100%; height: auto; margin: 6px auto; }
.topo .link { stroke: #8fa3b8; stroke-width: 1.5; } .topo .lc { font-size: 10px; fill: var(--muted); text-anchor: middle; }
.topo .node { fill: #eef4fb; stroke: var(--accent); stroke-width: 1.2; }
.topo .r-spine { fill: #e3eefa; } .topo .r-controller { fill: #f1eefa; stroke: #6a55b0; }
.topo .nl { font-size: 11px; font-weight: 600; text-anchor: middle; fill: var(--ink); }
.topo .ns { font-size: 9px; text-anchor: middle; fill: var(--muted); }
ul { margin: 4px 0; padding-left: 20px; }
footer { margin-top: 28px; color: var(--muted); font-size: 11px; border-top: 1px solid var(--line); padding-top: 6px; }
h2, h3 { break-after: avoid; }
"""


BULLET = re.compile(r"^[-*•]\s+")


def _paragraphs(text: str) -> str:
    out, bullets = [], []
    for line in (text or "").strip().splitlines():
        line = line.strip()
        if BULLET.match(line):
            bullets.append("<li>" + E(BULLET.sub("", line)) + "</li>")
            continue
        if bullets:
            out.append(f"<ul>{''.join(bullets)}</ul>")
            bullets = []
        if line:
            out.append(f"<p>{E(line)}</p>")
    if bullets:
        out.append(f"<ul>{''.join(bullets)}</ul>")
    return "".join(out)


def _render(snap: dict, title: str, summary: str, findings: list[str], recs: list[str]) -> str:
    scope = snap["scope"]
    b = [f'<div class="cover"><h1>{E(title)}</h1>'
         f'<p class="meta">Generated {E(_now())} · data collected {E(snap["collected_at"])} · '
         f'scope: {E(", ".join(scope))}</p></div>']

    b.append('<h2>Executive summary</h2><div class="ai"><span class="tag">AI-written · verify against the '
             'data below</span>' + _paragraphs(summary) + "</div>")
    if findings or recs:
        b.append('<h2>Findings and recommendations</h2><div class="ai"><span class="tag">AI-written</span>')
        if findings:
            b.append("<h3>Findings</h3><ul>" + "".join(f"<li>{E(f)}</li>" for f in findings) + "</ul>")
        if recs:
            b.append("<h3>Recommendations</h3><ol>" + "".join(f"<li>{E(r)}</li>" for r in recs) + "</ol>")
        b.append("</div>")

    if "health" in scope:
        b.append("<h2>Health</h2>")
        def aci_health(d):
            f = (d.get("fabric") or [{}])[0]
            return (_kv({"Fabric health score": f.get("cur"), "Highest fault severity": f.get("maxSev")}) +
                    "<h4>Tenants (least healthy first)</h4>" +
                 _table(sorted(d.get("tenants", []), key=lambda t: int(t["health"]) if str(t.get("health", "")).isdigit() else 999),
                           ["tenant:Tenant", "health:Health score"]))
        b.append(_source_block(snap, "aci_health", "ACI fabric and tenant health", aci_health))
        b.append(_source_block(snap, "aci_faults", "ACI faults (most severe first, up to 100)", lambda d:
                 _sev_badges(Counter(x.get("severity", "?") for x in d)) +
                 _table(d, ["severity:Severity", "code:Code", "cause:Cause", "descr:Description",
                            "dn:Object (DN)", "lastTransition:Last change"], 25)))
        b.append(_source_block(snap, "intersight_alarms", "Intersight active alarms (up to 100)", lambda d:
                 _sev_badges(Counter(x.get("Severity", "?") for x in d)) +
                 _table(d, ["Severity", "Code", "AffectedMoDisplayName:Affected object", "Description",
                            "LastTransitionTime:Last change"], 25)))
        b.append(_source_block(snap, "nd_get_nd_fabric_summary", "Nexus Dashboard fabric summary", _any))
        b.append(_source_block(snap, "nd_get_nd_anomalies_advisories_summary",
                               "Nexus Dashboard anomalies and advisories (last 2 hours)", _any))
    if "inventory" in scope:
        b.append("<h2>Inventory</h2>")
        b.append(_source_block(snap, "intersight_servers", "UCS servers (Intersight)", lambda d:
                 _table(d, ["Name", "Model", "Serial", "OperPowerState:Power", "Firmware",
                            "ManagementMode:Mgmt mode", "NumCpus:CPUs", "TotalMemory:Memory (MB)"])))
        b.append(_source_block(snap, "aci_nodes", "ACI fabric nodes", lambda d:
                 _table(sorted(d, key=lambda n: (str(n.get("role")), int(n["id"]) if str(n.get("id", "")).isdigit() else 0)),
                        ["id:Node ID", "name:Name", "role:Role", "model:Model", "serial:Serial",
                         "version:Version", "address:TEP address", "fabricSt:State"])))
    if "topology" in scope:
        b.append("<h2>Topology</h2>")
        nodes = _ok(snap, "aci_nodes") or []
        b.append(_source_block(snap, "aci_links", "ACI fabric (from fabricNode + fabricLink)", lambda d:
                 _svg_topology([{"id": str(n.get("id")), "name": n.get("name", n.get("id")), "role": n.get("role", "")}
                                for n in nodes if n.get("id")],
                               [(str(l.get("n1")), str(l.get("n2"))) for l in d]) +
                 f'<p class="muted">{len(nodes)} nodes, {len(d)} physical links '
                 '(lines are drawn once per node pair; ×N shows parallel links).</p>'))
        b.append(_source_block(snap, "nd_get_nd_unified_topology", "Nexus Dashboard unified topology", _nd_topology))

    prov = [{"section": k, "source": v["source"], "status": v["status"], "collected": v["at"]}
            for k, v in snap["sections"].items()]
    b.append("<h2>Data sources</h2><p>Every table above is filled from these API calls, not "
             "written by the model.</p>" + _table(prov, ["source:Source (API call)", "status:Status", "collected:Collected"]))
    b.append(f'<footer>Built by the AI DC Agent Lab report server · snapshot '
             f'{E(snap.get("id", ""))}</footer>')
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{E(title)}</title><style>{CSS}</style></head><body>{"".join(b)}</body></html>')


def _browser() -> str | None:
    if os.getenv("REPORT_BROWSER"):
        return os.getenv("REPORT_BROWSER")
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    for name in ("msedge", "google-chrome", "chromium", "chromium-browser", "chrome"):
        if (p := shutil.which(name)):
            return p
    return None


async def _pdf_playwright(html_path: Path, pdf_path: Path) -> None:
    """Preferred: Playwright drives the browser already installed on the machine
    (Edge on Windows: channel "msedge"), so nothing extra is downloaded."""
    from playwright.async_api import async_playwright
    exe = os.getenv("REPORT_BROWSER")
    channel = os.getenv("REPORT_BROWSER_CHANNEL", "msedge" if os.name == "nt" else "")
    async with async_playwright() as p:
        if exe:
            browser = await p.chromium.launch(executable_path=exe)
        elif channel:
            browser = await p.chromium.launch(channel=channel)
        else:
            browser = await p.chromium.launch()
        try:
            page = await browser.new_page()
            await page.goto(html_path.resolve().as_uri(), wait_until="load")
            await page.pdf(path=str(pdf_path), format="Letter", print_background=True,
                           margin={"top": "14mm", "bottom": "14mm", "left": "12mm", "right": "12mm"})
        finally:
            await browser.close()


def _pdf_cli(html_path: Path, pdf_path: Path) -> None:
    """Fallback: the browser's own headless print-to-pdf switch."""
    exe = _browser()
    if not exe:
        raise RuntimeError("no Edge or Chrome found (set REPORT_BROWSER)")
    profile = REPORTS / ".browser-profile"   # separate profile, so an open Edge window doesn't interfere
    subprocess.run([exe, "--headless", "--disable-gpu", "--no-first-run", "--no-pdf-header-footer",
                    f"--user-data-dir={profile}", f"--print-to-pdf={pdf_path}", html_path.resolve().as_uri()],
                   capture_output=True, timeout=60)


async def _print_pdf(html_path: Path, pdf_path: Path) -> str | None:
    """Returns None on success, otherwise why there's no PDF (the HTML is still complete)."""
    problems = []
    try:
        await _pdf_playwright(html_path, pdf_path)
    except Exception as e:
        problems.append(f"Playwright: {str(e).splitlines()[0][:160]}")
    if not (pdf_path.exists() and pdf_path.stat().st_size > 0):
        try:
            await anyio.to_thread.run_sync(_pdf_cli, html_path, pdf_path)
        except Exception as e:
            problems.append(f"browser CLI: {str(e)[:160]}")
    if pdf_path.exists() and pdf_path.stat().st_size > 0:
        return None
    return "No PDF made (" + "; ".join(problems or ["browser produced no file"]) + "). The HTML report is complete."


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
async def report_build(snapshot_id: str, title: str, executive_summary: str,
                 findings: list[str] | None = None, recommendations: list[str] | None = None) -> dict:
    """Step 2 of building a report. Renders the snapshot from report_collect as
    HTML and PDF in the reports folder. Writes local files only; changes nothing
    in the infrastructure.
    executive_summary: 3-6 sentences, plain text, written from the digest.
    findings / recommendations: short one-line items. Use only facts from the
    digest; tables with the exact data are added automatically."""
    if not re.fullmatch(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{4}", snapshot_id or ""):
        raise ValueError("Unknown snapshot_id. Call report_collect first and use the id it returns.")
    path = SNAPSHOTS / f"{snapshot_id}.json"
    if not path.exists():
        raise ValueError(f"Snapshot {snapshot_id} not found. Call report_collect first.")
    snap = json.loads(path.read_text(encoding="utf-8"))
    snap["id"] = snapshot_id

    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40] or "report"
    stem = f"{snapshot_id[:15]}-{slug}"
    REPORTS.mkdir(exist_ok=True)
    html_path, pdf_path = REPORTS / f"{stem}.html", REPORTS / f"{stem}.pdf"
    html_path.write_text(_render(snap, title, executive_summary, findings or [], recommendations or []),
                         encoding="utf-8")
    pdf_problem = await _print_pdf(html_path, pdf_path)
    out = {"html": str(html_path)}
    if pdf_problem:
        out["pdf_note"] = pdf_problem
    else:
        out["pdf"] = str(pdf_path)
    if os.getenv("REPORT_AUTO_OPEN", "false").lower() == "true" and os.name == "nt":
        os.startfile(str(html_path))  # noqa: S606 — opens in the default browser on Windows
    return out


if __name__ == "__main__":
    mcp.run()
