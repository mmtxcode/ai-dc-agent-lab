"""
check_lab.py — confirm every lab system is reachable before starting.
Run from the repo root:  python scripts/check_lab.py
Each check prints PASS or FAIL with the reason.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import requests
import urllib3
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
urllib3.disable_warnings()
results = []


def check(name):
    def wrap(fn):
        try:
            detail = fn()
            results.append(True)
            print(f"  PASS  {name:<12} {detail or ''}")
        except Exception as e:
            results.append(False)
            print(f"  FAIL  {name:<12} {str(e)[:200]}")
        return fn
    return wrap


print("Checking lab systems...\n")


@check(".env")
def _():
    missing = [k for k, v in os.environ.items() if v == "CHANGE-ME"]
    if not (ROOT / ".env").exists():
        raise RuntimeError("no .env — run scripts/setup.ps1")
    if missing:
        raise RuntimeError("still CHANGE-ME: " + ", ".join(sorted(missing)))
    return "all values set"


@check("Intersight")
def _():
    from intersight_auth import IntersightAuth
    auth = IntersightAuth(api_key_id=os.getenv("INTERSIGHT_KEY_ID"),
                          secret_key_filename=os.getenv("INTERSIGHT_SECRET_KEY_FILE"))
    base = os.getenv("INTERSIGHT_BASE_URL", "https://intersight.com").rstrip("/")
    r = requests.get(f"{base}/api/v1/compute/PhysicalSummaries?$count=true", auth=auth, timeout=30)
    r.raise_for_status()
    return f"{r.json().get('Count', '?')} servers visible"


@check("ACI (APIC)")
def _():
    s = requests.Session()
    s.verify = os.getenv("APIC_VERIFY_TLS", "false").lower() == "true"
    url = os.getenv("APIC_URL", "").rstrip("/")
    s.post(f"{url}/api/aaaLogin.json", timeout=30, json={"aaaUser": {"attributes": {
        "name": os.getenv("APIC_USER"), "pwd": os.getenv("APIC_PASSWORD")}}}).raise_for_status()
    r = s.get(f"{url}/api/class/fabricNode.json", timeout=30)
    r.raise_for_status()
    return f"{len(r.json()['imdata'])} fabric nodes"


@check("Nexus Dash.")
def _():
    import asyncio
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    verify = os.getenv("ND_VERIFY_TLS", "false").lower() == "true"
    url = os.getenv("ND_URL", "").rstrip("/") + "/api/v1/mcp"
    headers = {"X-Nd-Apikey": os.getenv("ND_API_KEY", ""), "X-Nd-Username": os.getenv("ND_USER", "")}

    def factory(headers=None, timeout=None, auth=None):
        return httpx.AsyncClient(headers=headers, timeout=timeout, auth=auth, verify=verify)

    async def probe():
        async with streamablehttp_client(url, headers=headers, httpx_client_factory=factory) as (r, w, _):
            async with ClientSession(r, w) as session:
                await session.initialize()
                return len((await session.list_tools()).tools)

    return f"built-in MCP server answered with {asyncio.run(probe())} tools"


@check("AI model")
def _():
    sys.path.insert(0, str(ROOT / "agent"))
    from llm import LLM
    reply = LLM().chat([{"role": "user", "content": "Reply with the single word READY."}], [])
    return f"model replied: {reply['content'].strip()[:40]}"


ok = all(results)
print(f"\n{'All checks passed — you are ready for Lab 01.' if ok else 'Fix the FAIL lines, then re-run.'}")
sys.exit(0 if ok else 1)
