"""
test_llm_toolcall.py — verify the Gemini endpoint supports tool/function calling.

Usage (PowerShell on the jump host):
    pip install requests python-dotenv
    python test_llm_toolcall.py

Configure in .env (never commit it):
    LLM_URL=https://...            # base URL you were given
    LLM_FORMAT=openai              # "openai" (chat/completions) or "gemini" (generateContent)
    LLM_MODEL=gemini-3.1-flash     # model name as the endpoint expects it
    LLM_BEARER_TOKEN=...
    LLM_APP_KEY=...
    LLM_APP_KEY_HEADER=            # optional: header name for the app key (e.g. x-api-key)
    LLM_APP_KEY_FIELD=             # optional: JSON body field for the app key (e.g. user)
    LLM_VERIFY_TLS=true
"""
import json
import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()

URL = os.environ["LLM_URL"].rstrip("/")
FMT = os.getenv("LLM_FORMAT", "openai").lower()
MODEL = os.getenv("LLM_MODEL", "gemini-3.1-flash")
TOKEN = os.getenv("LLM_BEARER_TOKEN", "")
APP_KEY = os.getenv("LLM_APP_KEY", "")
KEY_HEADER = os.getenv("LLM_APP_KEY_HEADER", "")
KEY_FIELD = os.getenv("LLM_APP_KEY_FIELD", "")
VERIFY = os.getenv("LLM_VERIFY_TLS", "true").lower() == "true"

# One fake tool, shaped like what our MCP servers will expose
TOOL = {
    "name": "get_fabric_health",
    "description": "Return the health score of a data center fabric.",
    "parameters": {
        "type": "object",
        "properties": {"fabric": {"type": "string", "description": "Fabric name"}},
        "required": ["fabric"],
    },
}
PROMPT = "What is the health of the fabric named LAB-FABRIC? Use your tools."


def headers():
    h = {"Content-Type": "application/json"}
    if TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    if APP_KEY and KEY_HEADER:
        h[KEY_HEADER] = APP_KEY
    return h


def add_app_key(body):
    if APP_KEY and KEY_FIELD:
        body[KEY_FIELD] = APP_KEY
    return body


def call_openai():
    endpoint = URL if URL.endswith("/chat/completions") else f"{URL}/chat/completions"
    body = add_app_key({
        "model": MODEL,
        "messages": [{"role": "user", "content": PROMPT}],
        "tools": [{"type": "function", "function": TOOL}],
    })
    r = requests.post(endpoint, headers=headers(), json=body, verify=VERIFY, timeout=60)
    r.raise_for_status()
    msg = r.json()["choices"][0]["message"]
    calls = msg.get("tool_calls") or []
    return [(c["function"]["name"], c["function"]["arguments"]) for c in calls], msg.get("content")


def call_gemini():
    endpoint = URL if ":generateContent" in URL else f"{URL}/v1beta/models/{MODEL}:generateContent"
    body = add_app_key({
        "contents": [{"role": "user", "parts": [{"text": PROMPT}]}],
        "tools": [{"functionDeclarations": [TOOL]}],
    })
    r = requests.post(endpoint, headers=headers(), json=body, verify=VERIFY, timeout=60)
    r.raise_for_status()
    parts = r.json()["candidates"][0]["content"]["parts"]
    calls = [(p["functionCall"]["name"], json.dumps(p["functionCall"].get("args", {})))
             for p in parts if "functionCall" in p]
    text = " ".join(p.get("text", "") for p in parts if "text" in p)
    return calls, text


def main():
    print(f"Endpoint: {URL}\nFormat:   {FMT}\nModel:    {MODEL}\n")
    try:
        calls, text = call_openai() if FMT == "openai" else call_gemini()
    except requests.HTTPError as e:
        print(f"HTTP error {e.response.status_code}:\n{e.response.text[:1000]}")
        sys.exit(1)
    except (KeyError, IndexError) as e:
        print(f"Unexpected response shape (missing {e}). Try the other LLM_FORMAT.")
        sys.exit(1)

    if calls:
        print("PASS — model requested a tool call:")
        for name, args in calls:
            print(f"  {name}({args})")
    else:
        print("FAIL — no tool call returned. Model said:")
        print(f"  {text or '(empty)'}")
        sys.exit(2)


if __name__ == "__main__":
    main()
