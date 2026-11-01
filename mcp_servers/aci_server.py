"""
ACI (APIC) MCP server — read AND write.

Read tools are marked readOnlyHint=True. Write tools are not, so the agent
asks the attendee to approve each one. aci_delete_tenant is also marked
destructiveHint=True. The system tenants (common, infra, mgmt) are blocked
in code — a guardrail that doesn't depend on the model behaving.

Env (.env):
    APIC_URL=https://10.10.10.10
    APIC_USER=admin
    APIC_PASSWORD=...
    APIC_VERIFY_TLS=false        # simulator uses a self-signed cert
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import requests
import urllib3
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

URL = os.getenv("APIC_URL", "").rstrip("/")
VERIFY = os.getenv("APIC_VERIFY_TLS", "false").lower() == "true"
if not VERIFY:
    urllib3.disable_warnings()

READ = ToolAnnotations(readOnlyHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False)
DESTROY = ToolAnnotations(readOnlyHint=False, destructiveHint=True)
PROTECTED_TENANTS = {"common", "infra", "mgmt"}
NAME_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
NOISE = {"childAction", "status", "lcOwn", "monPolDn", "uid", "userdom", "extMngdBy",
         "annotation", "nameAlias", "ownerKey", "ownerTag", "configIssues"}

mcp = FastMCP("aci", log_level="WARNING")
_s = requests.Session()
_s.verify = VERIFY


def _login():
    body = {"aaaUser": {"attributes": {"name": os.getenv("APIC_USER"),
                                       "pwd": os.getenv("APIC_PASSWORD")}}}
    r = _s.post(f"{URL}/api/aaaLogin.json", json=body, timeout=30)
    r.raise_for_status()


def _req(method: str, path: str, **kw) -> list[dict]:
    for attempt in (1, 2):
        r = _s.request(method, f"{URL}{path}", timeout=60, **kw)
        if r.status_code in (401, 403) and attempt == 1:
            _login()  # first call, or the token expired
            continue
        if r.status_code >= 400:
            raise RuntimeError(f"APIC {r.status_code}: {r.text[:500]}")
        return r.json().get("imdata", [])
    return []


def _flat(imdata: list[dict]) -> list[dict]:
    """Turn APIC's {class: {attributes: {...}}} into flat rows the model can read."""
    rows = []
    for item in imdata:
        for cls, body in item.items():
            row = {"class": cls}
            row.update({k: v for k, v in body.get("attributes", {}).items()
                        if v not in ("", None) and k not in NOISE})
            for child in body.get("children", []):
                for ccls, cbody in child.items():
                    row.setdefault("children", []).append(
                        {"class": ccls, **{k: v for k, v in cbody["attributes"].items()
                                           if v and k not in NOISE}})
            rows.append(row)
    return rows


def _check_name(*names: str):
    for n in names:
        if not NAME_RE.match(n or ""):
            raise ValueError(f"Invalid ACI object name: '{n}'")


# ------------------------------- READ ----------------------------------

@mcp.tool(annotations=READ)
def aci_get_fabric_health() -> dict:
    """Overall fabric health score (0-100) and health per tenant."""
    fabric = _flat(_req("GET", "/api/mo/topology/health.json"))
    tenants = _req("GET", "/api/class/fvTenant.json?rsp-include=health")
    per_tenant = []
    for t in tenants:
        attrs = t["fvTenant"]["attributes"]
        score = next((c["healthInst"]["attributes"]["cur"] for c in t["fvTenant"].get("children", [])
                      if "healthInst" in c), None)
        per_tenant.append({"tenant": attrs["name"], "health": score})
    return {"fabric": fabric, "tenants": per_tenant}


@mcp.tool(annotations=READ)
def aci_list_tenants() -> list[dict]:
    """List all tenants with their descriptions."""
    return _flat(_req("GET", "/api/class/fvTenant.json"))


@mcp.tool(annotations=READ)
def aci_get_tenant(tenant: str) -> list[dict]:
    """Everything inside one tenant: VRFs, bridge domains, subnets, app profiles, EPGs."""
    _check_name(tenant)
    return _flat(_req("GET", f"/api/mo/uni/tn-{tenant}.json?query-target=subtree"
                             "&target-subtree-class=fvCtx,fvBD,fvSubnet,fvAp,fvAEPg"))


@mcp.tool(annotations=READ)
def aci_list_faults(severity: str = "", top: int = 25) -> list[dict]:
    """Active faults, most severe first. severity: optional — critical, major, minor, warning."""
    q = f"/api/class/faultInst.json?order-by=faultInst.severity|desc&page-size={int(top)}"
    if severity:
        q += f'&query-target-filter=eq(faultInst.severity,"{severity.lower()}")'
    rows = _flat(_req("GET", q))
    keep = ("severity", "code", "cause", "descr", "dn", "lastTransition")
    return [{k: r[k] for k in keep if k in r} for r in rows]


@mcp.tool(annotations=READ)
def aci_find_endpoint(mac: str) -> list[dict]:
    """Find where a MAC address lives in the fabric: tenant/EPG and leaf/port path.
    Accepts any MAC format (aa:bb:cc:.., aabb.cc.., AA-BB-..)."""
    hexchars = re.sub(r"[^0-9A-Fa-f]", "", mac).upper()
    if len(hexchars) != 12:
        raise ValueError(f"Not a MAC address: {mac}")
    norm = ":".join(hexchars[i:i + 2] for i in range(0, 12, 2))
    learned = _req("GET", f'/api/class/fvCEp.json?query-target-filter=eq(fvCEp.mac,"{norm}")'
                          "&rsp-subtree=children&rsp-subtree-class=fvRsCEpToPathEp")
    static = _req("GET", f'/api/class/fvStCEp.json?query-target-filter=eq(fvStCEp.mac,"{norm}")'
                         "&rsp-subtree=children&rsp-subtree-class=fvRsStCEpToPathEp")
    found = _flat(learned) + _flat(static)
    return found or [{"result": f"{norm} not found in the fabric"}]


@mcp.tool(annotations=READ)
def aci_query(path: str) -> list[dict]:
    """Generic APIC REST read, e.g. path='/api/class/fabricNode.json'.
    Only GET is allowed."""
    if not path.startswith("/api/"):
        raise ValueError("path must start with /api/")
    return _flat(_req("GET", path))


# ------------------------------- WRITE ---------------------------------

@mcp.tool(annotations=WRITE)
def aci_create_app_tenant(tenant: str, vrf: str, bridge_domain: str, gateway: str,
                          app_profile: str, epgs: list[str]) -> dict:
    """Create a tenant with one VRF, one bridge domain + gateway subnet, and an
    application profile with the given EPGs (e.g. ["web","app","db"]).
    gateway is CIDR form, e.g. 10.1.1.1/24. Safe to re-run (APIC merges)."""
    _check_name(tenant, vrf, bridge_domain, app_profile, *epgs)
    if tenant.lower() in PROTECTED_TENANTS:
        raise ValueError(f"Tenant '{tenant}' is protected and cannot be modified.")
    body = {"fvTenant": {"attributes": {"name": tenant, "descr": "Created by AI agent lab"},
            "children": [
                {"fvCtx": {"attributes": {"name": vrf}}},
                {"fvBD": {"attributes": {"name": bridge_domain}, "children": [
                    {"fvRsCtx": {"attributes": {"tnFvCtxName": vrf}}},
                    {"fvSubnet": {"attributes": {"ip": gateway, "scope": "public"}}}]}},
                {"fvAp": {"attributes": {"name": app_profile}, "children": [
                    {"fvAEPg": {"attributes": {"name": e}, "children": [
                        {"fvRsBd": {"attributes": {"tnFvBDName": bridge_domain}}}]}}
                    for e in epgs]}}]}}
    _req("POST", "/api/mo/uni.json", json=body)
    return {"created": f"uni/tn-{tenant}", "vrf": vrf, "bridge_domain": bridge_domain,
            "gateway": gateway, "app_profile": app_profile, "epgs": epgs}


@mcp.tool(annotations=DESTROY)
def aci_delete_tenant(tenant: str) -> dict:
    """Delete a tenant and everything in it. Cannot be undone."""
    _check_name(tenant)
    if tenant.lower() in PROTECTED_TENANTS:
        raise ValueError(f"Tenant '{tenant}' is protected and cannot be deleted.")
    _req("POST", "/api/mo/uni.json",
         json={"fvTenant": {"attributes": {"name": tenant, "status": "deleted"}}})
    return {"deleted": f"uni/tn-{tenant}"}


if __name__ == "__main__":
    mcp.run()
