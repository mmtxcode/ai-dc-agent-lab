"""
test_llm_toolcall.py — check the AI model endpoint in two steps, using the
same model adapter as the agent (agent/llm.py), so a PASS here means the
agent will reach the model the same way.

    python scripts/test_llm_toolcall.py

Test 1  plain chat   — same as the curl access test: ask a question, get an answer
Test 2  tool calling — offer the model one tool; it must ask to call it.
                       The agent needs this to use MCP at all.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT / "agent"))
from llm import LLM  # noqa: E402

TOOL = {
    "name": "get_fabric_health",
    "description": "Return the health score of a data center fabric.",
    "parameters": {
        "type": "object",
        "properties": {"fabric": {"type": "string", "description": "Fabric name"}},
        "required": ["fabric"],
    },
}


def main():
    llm = LLM()
    print(f"Endpoint: {llm.url}\nFormat:   {llm.fmt}\n")

    print("Test 1 — plain chat")
    try:
        reply = llm.chat([{"role": "user",
                           "content": "Explain how quantum computing works in 100 words"}], [])
    except Exception as e:
        print(f"  FAIL  {e}")
        print("  The endpoint, key, or app key isn't right yet. Compare .env with your curl test.")
        sys.exit(1)
    print(f"  PASS  {reply['content'].strip()[:300]}\n")

    print("Test 2 — tool calling")
    try:
        reply = llm.chat([{"role": "user", "content":
                           "What is the health of the fabric named LAB-FABRIC? Use your tools."}], [TOOL])
    except Exception as e:
        print(f"  FAIL  {e}")
        print("  Plain chat works but the endpoint rejected the 'tools' field.")
        sys.exit(2)
    if reply["tool_calls"]:
        for c in reply["tool_calls"]:
            print(f"  PASS  model asked to call {c['name']}({json.dumps(c['args'])})")
        print("\nThe model is ready for the agent.")
    else:
        print(f"  FAIL  no tool call. Model said: {reply['content'][:300] or '(empty)'}")
        print("  The endpoint accepted the tool but the model didn't use it. Send this output to Claude.")
        sys.exit(2)


if __name__ == "__main__":
    main()
