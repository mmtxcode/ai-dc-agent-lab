"""
mcp_probe.py — test an MCP server directly, with NO AI model involved.

    python scripts/mcp_probe.py aci                              # connect + list tools
    python scripts/mcp_probe.py aci aci_get_fabric_health        # call one tool
    python scripts/mcp_probe.py aci aci_list_faults top=5 severity=major   # with arguments

Server names come from agent/servers.json (intersight, aci, nd). If this
works but the agent doesn't, the problem is the model side, not MCP.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))
from main import Agent, C  # noqa: E402


async def probe(server: str, tool: str | None, args: dict):
    agent = Agent([server])
    try:
        await agent.connect()
        if not agent.tools:
            sys.exit(1)
        if not tool:
            agent.print_tools()
            return
        if tool not in agent.tools:
            print(f"{C['red']}No tool '{tool}' on {server}.{C['off']}")
            sys.exit(1)
        _, session, _ = agent.tools[tool]
        result = await session.call_tool(tool, args)
        text = "\n".join(c.text for c in result.content if getattr(c, "type", "") == "text")
        color = C["red"] if result.isError else C["green"]
        print(f"{color}{'ERROR' if result.isError else 'OK'}{C['off']}")
        try:
            print(json.dumps(json.loads(text), indent=2)[:4000])
        except ValueError:
            print(text[:4000])
        if result.isError:
            sys.exit(1)
    finally:
        await agent.stack.aclose()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    # key=value pairs (PowerShell mangles quoted JSON). Numbers/lists parse as JSON.
    arguments = {}
    for pair in sys.argv[3:]:
        key, _, value = pair.partition("=")
        try:
            arguments[key] = json.loads(value)
        except ValueError:
            arguments[key] = value
    asyncio.run(probe(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None, arguments))
