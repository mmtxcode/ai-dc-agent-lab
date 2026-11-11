"""
ACI (APIC) MCP server — read AND write.

Read tools are marked readOnlyHint=True. Write tools are not, so the agent
asks for approval before each one; delete tools are also destructiveHint=True.
Guardrails live in this code, so they hold whatever the model asks for:

  ACI_WRITE_MODE=on|off        off = write tools aren't offered at all (read-only server)
  ACI_DRY_RUN=true|false       true = show the exact JSON that would be sent, change nothing
  ACI_PROTECTED_TENANTS=...    tenants writes may never touch (default: common,infra,mgmt)
  ACI_WRITE_TENANT_PREFIX=...  optional: writes only in tenants starting with this, e.g. lab-

Every write returns the exact payload it sent, so it shows in the audit log.

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
PROTECTED_TENANTS = {t.strip().lower() for t in
                     os.getenv("ACI_PROTECTED_TENANTS", "common,infra,mgmt").split(",") if t.strip()}
WRITE_MODE = os.getenv("ACI_WRITE_MODE", "on").lower() not in ("off", "false", "0", "no")
DRY_RUN = os.getenv("ACI_DRY_RUN", "false").lower() == "true"
TENANT_PREFIX = os.getenv("ACI_WRITE_TENANT_PREFIX", "")
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
    """Everything inside one tenant: VRFs, bridge domains, subnets, app profiles,
    EPGs, contracts, filters, and EPG bindings (contracts, domains, static ports).
    Use it to check a change after making it."""
    _check_name(tenant)
    return _flat(_req("GET", f"/api/mo/uni/tn-{tenant}.json?query-target=subtree"
                             "&target-subtree-class=fvCtx,fvBD,fvRsCtx,fvSubnet,fvAp,fvAEPg,fvRsBd,"
                             "vzBrCP,vzSubj,vzRsSubjFiltAtt,vzFilter,vzEntry,"
                             "fvRsProv,fvRsCons,fvRsDomAtt,fvRsPathAtt"))


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
# Defined as plain functions, then registered at the bottom only when
# ACI_WRITE_MODE is on. With it off, the model never even sees them.

def _guard_tenant(tenant: str):
    _check_name(tenant)
    if tenant.lower() in PROTECTED_TENANTS:
        raise ValueError(f"Tenant '{tenant}' is protected and cannot be modified.")
    if TENANT_PREFIX and not tenant.startswith(TENANT_PREFIX):
        raise ValueError(f"Writes are limited to tenants starting with '{TENANT_PREFIX}'.")


def _post(parent_dn: str, body: dict, summary: dict) -> dict:
    """Send one object to APIC under parent_dn (APIC merges, so re-running is safe)."""
    result = {**summary, "parent": parent_dn, "payload": body}
    if DRY_RUN:
        return {"dry_run": True, "note": "ACI_DRY_RUN is on: nothing was sent.", **result}
    _req("POST", f"/api/mo/{parent_dn}.json", json=body)
    return result


def _mo(cls: str, children: list | None = None, **attrs) -> dict:
    body = {cls: {"attributes": {k: v for k, v in attrs.items() if v not in (None, "")}}}
    if children:
        body[cls]["children"] = children
    return body


def _check_cidr(ip: str):
    if not re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}/\d{1,2}", ip or ""):
        raise ValueError(f"Gateway must be an IPv4 address with mask, e.g. 10.1.1.1/24 (got '{ip}').")


SUBNET_SCOPES = {"private", "public", "shared", "public,shared", "private,shared"}


def aci_create_app_tenant(tenant: str, vrf: str, bridge_domain: str, gateway: str,
                          app_profile: str, epgs: list[str]) -> dict:
    """Shortcut: create a tenant with one VRF, one bridge domain + gateway subnet,
    and an application profile with the given EPGs (e.g. ["web","app","db"]),
    all in one change. gateway is CIDR, e.g. 10.1.1.1/24. Safe to re-run."""
    _guard_tenant(tenant)
    _check_name(vrf, bridge_domain, app_profile, *epgs)
    _check_cidr(gateway)
    body = _mo("fvTenant", [
        _mo("fvCtx", name=vrf),
        _mo("fvBD", [_mo("fvRsCtx", tnFvCtxName=vrf), _mo("fvSubnet", ip=gateway, scope="public")],
            name=bridge_domain),
        _mo("fvAp", [_mo("fvAEPg", [_mo("fvRsBd", tnFvBDName=bridge_domain)], name=e) for e in epgs],
            name=app_profile)],
        name=tenant, descr="Created by AI agent lab")
    return _post("uni", body, {"created": f"uni/tn-{tenant}", "vrf": vrf, "bridge_domain": bridge_domain,
                               "gateway": gateway, "app_profile": app_profile, "epgs": epgs})


def aci_create_tenant(tenant: str, description: str = "") -> dict:
    """Create an empty tenant."""
    _guard_tenant(tenant)
    return _post("uni", _mo("fvTenant", name=tenant, descr=description), {"created": f"uni/tn-{tenant}"})


def aci_create_vrf(tenant: str, vrf: str, description: str = "") -> dict:
    """Create a VRF (fvCtx) in a tenant."""
    _guard_tenant(tenant)
    _check_name(vrf)
    return _post(f"uni/tn-{tenant}", _mo("fvCtx", name=vrf, descr=description),
                 {"created": f"uni/tn-{tenant}/ctx-{vrf}"})


def aci_create_bridge_domain(tenant: str, bridge_domain: str, vrf: str, gateway: str = "",
                             subnet_scope: str = "private") -> dict:
    """Create a bridge domain tied to a VRF, optionally with a gateway subnet.
    gateway: CIDR, e.g. 10.1.1.1/24. subnet_scope: private, public, shared,
    public,shared (public = advertised out of the fabric through an L3Out)."""
    _guard_tenant(tenant)
    _check_name(bridge_domain, vrf)
    children = [_mo("fvRsCtx", tnFvCtxName=vrf)]
    if gateway:
        _check_cidr(gateway)
        if subnet_scope not in SUBNET_SCOPES:
            raise ValueError(f"subnet_scope must be one of {sorted(SUBNET_SCOPES)}")
        children.append(_mo("fvSubnet", ip=gateway, scope=subnet_scope))
    return _post(f"uni/tn-{tenant}", _mo("fvBD", children, name=bridge_domain),
                 {"created": f"uni/tn-{tenant}/BD-{bridge_domain}", "vrf": vrf, "gateway": gateway or None})


def aci_create_subnet(tenant: str, bridge_domain: str, gateway: str, scope: str = "private") -> dict:
    """Add a gateway subnet to an existing bridge domain. gateway: CIDR, e.g. 10.1.2.1/24."""
    _guard_tenant(tenant)
    _check_name(bridge_domain)
    _check_cidr(gateway)
    if scope not in SUBNET_SCOPES:
        raise ValueError(f"scope must be one of {sorted(SUBNET_SCOPES)}")
    return _post(f"uni/tn-{tenant}/BD-{bridge_domain}", _mo("fvSubnet", ip=gateway, scope=scope),
                 {"created": f"uni/tn-{tenant}/BD-{bridge_domain}/subnet-[{gateway}]"})


def aci_create_app_profile(tenant: str, app_profile: str, description: str = "") -> dict:
    """Create an application profile in a tenant."""
    _guard_tenant(tenant)
    _check_name(app_profile)
    return _post(f"uni/tn-{tenant}", _mo("fvAp", name=app_profile, descr=description),
                 {"created": f"uni/tn-{tenant}/ap-{app_profile}"})


def aci_create_epg(tenant: str, app_profile: str, epg: str, bridge_domain: str,
                   description: str = "") -> dict:
    """Create an EPG in an application profile, attached to a bridge domain."""
    _guard_tenant(tenant)
    _check_name(app_profile, epg, bridge_domain)
    body = _mo("fvAEPg", [_mo("fvRsBd", tnFvBDName=bridge_domain)], name=epg, descr=description)
    return _post(f"uni/tn-{tenant}/ap-{app_profile}", body,
                 {"created": f"uni/tn-{tenant}/ap-{app_profile}/epg-{epg}", "bridge_domain": bridge_domain})


def aci_create_filter(tenant: str, filter_name: str, entries: list[dict]) -> dict:
    """Create a filter with one or more entries. Each entry is
    {"name": "https", "protocol": "tcp", "port": 443}; protocol is tcp, udp,
    icmp or unspecified; port is optional (a number, or "8000-8080" for a range)."""
    _guard_tenant(tenant)
    _check_name(filter_name)
    if not entries:
        raise ValueError("Give at least one entry, e.g. [{\"name\": \"https\", \"protocol\": \"tcp\", \"port\": 443}]")
    kids = []
    for e in entries:
        name, prot = str(e.get("name", "")), str(e.get("protocol", "unspecified")).lower()
        _check_name(name)
        if prot not in ("tcp", "udp", "icmp", "unspecified"):
            raise ValueError(f"protocol must be tcp, udp, icmp or unspecified (got '{prot}')")
        lo = hi = None
        if e.get("port") not in (None, ""):
            if prot not in ("tcp", "udp"):
                raise ValueError("Ports only apply to tcp or udp entries.")
            m = re.fullmatch(r"(\d{1,5})(?:-(\d{1,5}))?", str(e["port"]))
            if not m or not all(0 < int(x) < 65536 for x in m.groups() if x):
                raise ValueError(f"Bad port '{e['port']}': use a number or a range like 8000-8080")
            lo, hi = m.group(1), m.group(2) or m.group(1)
        kids.append(_mo("vzEntry", name=name, etherT="ip" if prot != "unspecified" else "unspecified",
                        prot=prot, dFromPort=lo, dToPort=hi))
    return _post(f"uni/tn-{tenant}", _mo("vzFilter", kids, name=filter_name),
                 {"created": f"uni/tn-{tenant}/flt-{filter_name}", "entries": len(kids)})


def aci_create_contract(tenant: str, contract: str, filters: list[str], scope: str = "context",
                        description: str = "") -> dict:
    """Create a contract with one subject that uses the given filters (create the
    filters first). scope: context (VRF, default), tenant, application-profile, global.
    The subject applies the filters in both directions."""
    _guard_tenant(tenant)
    _check_name(contract, *filters)
    if scope not in ("context", "tenant", "application-profile", "global"):
        raise ValueError("scope must be context, tenant, application-profile or global")
    subj = _mo("vzSubj", [_mo("vzRsSubjFiltAtt", tnVzFilterName=f) for f in filters],
               name=f"{contract}-subj", revFltPorts="yes")
    return _post(f"uni/tn-{tenant}", _mo("vzBrCP", [subj], name=contract, scope=scope, descr=description),
                 {"created": f"uni/tn-{tenant}/brc-{contract}", "filters": filters, "scope": scope})


def aci_bind_epg_contract(tenant: str, app_profile: str, epg: str, contract: str, role: str) -> dict:
    """Make an EPG a provider or consumer of a contract. role: provider or consumer.
    Example: web consumes the contract that app provides."""
    _guard_tenant(tenant)
    _check_name(app_profile, epg, contract)
    role = role.lower()
    if role not in ("provider", "consumer"):
        raise ValueError("role must be provider or consumer")
    cls = "fvRsProv" if role == "provider" else "fvRsCons"
    return _post(f"uni/tn-{tenant}/ap-{app_profile}/epg-{epg}", _mo(cls, tnVzBrCPName=contract),
                 {"bound": f"epg-{epg} {'provides' if role == 'provider' else 'consumes'} {contract}"})


def aci_bind_epg_domain(tenant: str, app_profile: str, epg: str, domain: str,
                        domain_type: str = "physical") -> dict:
    """Attach an EPG to a domain. domain_type: physical (bare metal / static ports)
    or vmm (VMware vCenter domain, creates the port group)."""
    _guard_tenant(tenant)
    _check_name(app_profile, epg, domain)
    if domain_type not in ("physical", "vmm"):
        raise ValueError("domain_type must be physical or vmm")
    tdn = f"uni/phys-{domain}" if domain_type == "physical" else f"uni/vmmp-VMware/dom-{domain}"
    return _post(f"uni/tn-{tenant}/ap-{app_profile}/epg-{epg}", _mo("fvRsDomAtt", tDn=tdn),
                 {"bound": f"epg-{epg} -> {tdn}"})


def aci_bind_epg_static_port(tenant: str, app_profile: str, epg: str, node: int, interface: str,
                             vlan: int, mode: str = "trunk", pod: int = 1) -> dict:
    """Map an EPG to a leaf port with a VLAN (static path binding). The EPG needs a
    physical domain whose VLAN pool includes this VLAN.
    interface: e.g. eth1/10. mode: trunk (tagged), access (untagged) or native (802.1p)."""
    _guard_tenant(tenant)
    _check_name(app_profile, epg)
    if not re.fullmatch(r"eth\d+/\d+(/\d+)?", interface):
        raise ValueError("interface must look like eth1/10")
    if not 1 <= int(vlan) <= 4094:
        raise ValueError("vlan must be 1-4094")
    modes = {"trunk": "regular", "access": "untagged", "native": "native"}
    if mode not in modes:
        raise ValueError("mode must be trunk, access or native")
    tdn = f"topology/pod-{int(pod)}/paths-{int(node)}/pathep-[{interface}]"
    return _post(f"uni/tn-{tenant}/ap-{app_profile}/epg-{epg}",
                 _mo("fvRsPathAtt", tDn=tdn, encap=f"vlan-{int(vlan)}", mode=modes[mode]),
                 {"bound": f"epg-{epg} -> node {node} {interface} vlan-{vlan} ({mode})"})


DELETE_KINDS = {"vrf": "ctx-{n}", "bridge_domain": "BD-{n}", "app_profile": "ap-{n}",
                "epg": "ap-{ap}/epg-{n}", "contract": "brc-{n}", "filter": "flt-{n}"}


def aci_delete_object(tenant: str, kind: str, name: str, app_profile: str = "") -> dict:
    """Delete one object inside a tenant. kind: vrf, bridge_domain, app_profile,
    epg, contract or filter. For an epg, also give app_profile. Cannot be undone."""
    _guard_tenant(tenant)
    _check_name(name)
    if kind not in DELETE_KINDS:
        raise ValueError(f"kind must be one of {sorted(DELETE_KINDS)}")
    if kind == "epg":
        _check_name(app_profile)
    dn = f"uni/tn-{tenant}/" + DELETE_KINDS[kind].format(n=name, ap=app_profile)
    if DRY_RUN:
        return {"dry_run": True, "note": "ACI_DRY_RUN is on: nothing was deleted.", "would_delete": dn}
    _req("DELETE", f"/api/mo/{dn}.json")
    return {"deleted": dn}


def aci_delete_tenant(tenant: str) -> dict:
    """Delete a tenant and everything in it. Cannot be undone."""
    _guard_tenant(tenant)
    if DRY_RUN:
        return {"dry_run": True, "note": "ACI_DRY_RUN is on: nothing was deleted.",
                "would_delete": f"uni/tn-{tenant}"}
    _req("DELETE", f"/api/mo/uni/tn-{tenant}.json")
    return {"deleted": f"uni/tn-{tenant}"}


WRITE_TOOLS = [aci_create_app_tenant, aci_create_tenant, aci_create_vrf, aci_create_bridge_domain,
               aci_create_subnet, aci_create_app_profile, aci_create_epg, aci_create_filter,
               aci_create_contract, aci_bind_epg_contract, aci_bind_epg_domain, aci_bind_epg_static_port]
DELETE_TOOLS = [aci_delete_object, aci_delete_tenant]

if WRITE_MODE:
    for fn in WRITE_TOOLS:
        mcp.tool(annotations=WRITE)(fn)
    for fn in DELETE_TOOLS:
        mcp.tool(annotations=DESTROY)(fn)


if __name__ == "__main__":
    mcp.run()
