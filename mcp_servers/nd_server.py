"""
Nexus Dashboard 4.x MCP server — READ ONLY (for now).

Auth and the fabrics endpoint follow Cisco's ND 4.x "Getting Started" API guide:
    POST /api/v1/infra/login  -> jwttoken, sent as Cookie: AuthCookie=<token>
    GET  /api/v1/manage/fabrics

The switch and anomaly paths below are placeholders until they're checked
against your ND's own API reference at https://<nd>/help-center/swagger/ .
Override them in .env without touching code.

Env (.env):
    ND_URL=https://10.10.10.20
    ND_USER=admin
    ND_PASSWORD=...
    ND_DOMAIN=DefaultAuth
    ND_VERIFY_TLS=false
    ND_SWITCHES_PATH=/api/v1/manage/fabrics/{fabric}/switches     # VERIFY
    ND_ANOMALIES_PATH=/api/v1/analyze/anomalies                     # VERIFY
"""
from __future__ import annotations

import os
from pathlib import Path

import requests
import urllib3
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

URL = os.getenv("ND_URL", "").rstrip("/")
VERIFY = os.getenv("ND_VERIFY_TLS", "false").lower() == "true"
SWITCHES_PATH = os.getenv("ND_SWITCHES_PATH", "/api/v1/manage/fabrics/{fabric}/switches")
ANOMALIES_PATH = os.getenv("ND_ANOMALIES_PATH", "/api/v1/analyze/anomalies")
if not VERIFY:
    urllib3.disable_warnings()

READ = ToolAnnotations(readOnlyHint=True)
mcp = FastMCP("nexus-dashboard", log_level="WARNING")
_s = requests.Session()
_s.verify = VERIFY


def _login():
    body = {"userName": os.getenv("ND_USER"), "userPasswd": os.getenv("ND_PASSWORD"),
            "domain": os.getenv("ND_DOMAIN", "DefaultAuth")}
    r = _s.post(f"{URL}/api/v1/infra/login", json=body, timeout=30)
    r.raise_for_status()
    _s.cookies.set("AuthCookie", r.json()["jwttoken"])


def _get(path: str, params: dict | None = None):
    for attempt in (1, 2):
        r = _s.get(f"{URL}{path}", params=params, timeout=60)
        if r.status_code in (401, 403) and attempt == 1:
            _login()
            continue
        if r.status_code >= 400:
            raise RuntimeError(f"ND {r.status_code} on {path}: {r.text[:500]}")
        return r.json()
    return None


def _trim(data, limit: int = 50):
    """Keep responses small enough for the model: cap long lists."""
    if isinstance(data, list):
        return data[:limit]
    if isinstance(data, dict):
        return {k: _trim(v, limit) for k, v in data.items()}
    return data


@mcp.tool(annotations=READ)
def nd_list_fabrics() -> dict | list:
    """List fabrics managed by Nexus Dashboard (name, type, state, health)."""
    return _trim(_get("/api/v1/manage/fabrics"))


@mcp.tool(annotations=READ)
def nd_list_switches(fabric: str) -> dict | list:
    """List switches in one fabric: name, model, serial, role, software version, status."""
    return _trim(_get(SWITCHES_PATH.format(fabric=fabric)))


@mcp.tool(annotations=READ)
def nd_list_anomalies(fabric: str = "", top: int = 25) -> dict | list:
    """Active anomalies (problems ND has detected), optionally for one fabric."""
    params = {"fabricName": fabric} if fabric else None
    return _trim(_get(ANOMALIES_PATH, params), limit=int(top))


@mcp.tool(annotations=READ)
def nd_api_get(path: str) -> dict | list:
    """Generic Nexus Dashboard REST read, e.g. path='/api/v1/manage/fabrics'.
    Only GET is allowed. Use when no specific tool fits."""
    if not path.startswith("/"):
        raise ValueError("path must start with /")
    return _trim(_get(path))


if __name__ == "__main__":
    mcp.run()
