"""
Intersight MCP server — READ ONLY.

Every tool here is marked readOnlyHint=True, and the lab uses a read-only
API key, so the agent can look but never touch. That's least privilege on
two levels: the tool layer and the credential.

Env (.env):
    INTERSIGHT_BASE_URL=https://intersight.com
    INTERSIGHT_KEY_ID=...
    INTERSIGHT_SECRET_KEY_FILE=C:\\lab\\keys\\intersight.pem
"""
from __future__ import annotations

import os
from pathlib import Path

import requests
from dotenv import load_dotenv
from intersight_auth import IntersightAuth
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

BASE = os.getenv("INTERSIGHT_BASE_URL", "https://intersight.com").rstrip("/") + "/api/v1"
READ = ToolAnnotations(readOnlyHint=True)
_auth = None

mcp = FastMCP("intersight", log_level="WARNING")


def _get_auth() -> IntersightAuth:
    """Load the API key on first use, so a bad key shows up as a tool error, not a crash."""
    global _auth
    if _auth is None:
        _auth = IntersightAuth(api_key_id=os.getenv("INTERSIGHT_KEY_ID"),
                               secret_key_filename=os.getenv("INTERSIGHT_SECRET_KEY_FILE"))
    return _auth


def _get(resource: str, **params) -> list[dict]:
    query = {f"${k}": v for k, v in params.items() if v not in (None, "")}
    r = requests.get(f"{BASE}/{resource.strip('/')}", auth=_get_auth(), params=query, timeout=60)
    r.raise_for_status()
    rows = r.json().get("Results") or []
    # Drop bookkeeping fields the model doesn't need
    return [{k: v for k, v in row.items() if k not in ("ClassId", "ObjectType")} for row in rows]


def _server_moid(name: str) -> str:
    rows = _get("compute/PhysicalSummaries", filter=f"Name eq '{name}'", select="Moid")
    if not rows:
        raise ValueError(f"No server named '{name}'. Use intersight_list_servers to see names.")
    return rows[0]["Moid"]


@mcp.tool(annotations=READ)
def intersight_list_servers(top: int = 50) -> list[dict]:
    """List UCS servers claimed in Intersight: name, model, serial, power, firmware, alarms."""
    return _get("compute/PhysicalSummaries", top=top,
                select="Name,Model,Serial,OperPowerState,Firmware,ManagementMode,"
                       "NumCpus,TotalMemory,AlarmSummary")


@mcp.tool(annotations=READ)
def intersight_list_alarms(severity: str = "", top: int = 25) -> list[dict]:
    """List active (not cleared) Intersight alarms, newest first.
    severity: optional filter — Critical, Warning, or Info."""
    flt = "Severity ne 'Cleared'"
    if severity:
        flt += f" and Severity eq '{severity.capitalize()}'"
    return _get("cond/Alarms", filter=flt, top=top, orderby="LastTransitionTime desc",
                select="Severity,Code,Description,AffectedMoDisplayName,LastTransitionTime")


@mcp.tool(annotations=READ)
def intersight_get_server(name: str) -> list[dict]:
    """Full summary of one server by its exact Intersight name."""
    return _get("compute/PhysicalSummaries", filter=f"Name eq '{name}'")


@mcp.tool(annotations=READ)
def intersight_get_server_nics(name: str) -> list[dict]:
    """vNICs on one server with their MAC addresses. Use the MACs to find
    where the server attaches in the network (aci_find_endpoint)."""
    moid = _server_moid(name)
    return _get("adapter/HostEthInterfaces",
                filter=f"Ancestors/any(t:t/Moid eq '{moid}')",
                select="Name,MacAddress,OperState,AdminState")


@mcp.tool(annotations=READ)
def intersight_hcl_status(top: int = 50) -> list[dict]:
    """Hardware Compatibility List (HCL) status for servers: is the
    OS / firmware / driver combination validated?"""
    return _get("cond/HclStatuses", top=top,
                select="Status,Reason,HclOsVendor,HclOsVersion,HclFirmwareVersion,ManagedObject")


@mcp.tool(annotations=READ)
def intersight_query(resource: str, filter: str = "", select: str = "", top: int = 20) -> list[dict]:
    """Generic read of any Intersight API resource, e.g. resource='equipment/Chasses'
    or 'network/Elements'. filter/select use Intersight OData syntax."""
    return _get(resource, filter=filter, select=select, top=top)


if __name__ == "__main__":
    mcp.run()
