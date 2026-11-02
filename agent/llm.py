"""
llm.py — one small adapter between the agent and the model endpoint.

The agent keeps its conversation in OpenAI-style messages. This module
translates to whatever the endpoint speaks, set by LLM_FORMAT in .env:

    openai  -> POST {LLM_URL}/chat/completions        (OpenAI-compatible gateway,
               including Azure-style /openai/deployments/<name>/chat/completions)
    gemini  -> POST {LLM_URL}/v1beta/models/{model}:generateContent  (native Gemini)

If the endpoint turns out to need something different, this is the only
file that should change.
"""
from __future__ import annotations

import json
import os
import uuid

import requests

# JSON Schema keys the native Gemini API rejects in function declarations
_GEMINI_DROP_KEYS = {"$schema", "additionalProperties", "title", "default", "examples"}


def _sanitize_schema(schema):
    """Strip schema keys that native Gemini function declarations don't accept."""
    if isinstance(schema, dict):
        return {k: _sanitize_schema(v) for k, v in schema.items() if k not in _GEMINI_DROP_KEYS}
    if isinstance(schema, list):
        return [_sanitize_schema(s) for s in schema]
    return schema


class LLM:
    def __init__(self):
        self.url = os.environ["LLM_URL"].rstrip("/")
        self.fmt = os.getenv("LLM_FORMAT", "openai").lower()
        self.model = os.getenv("LLM_MODEL", "gemini-3.1-flash-lite")
        # Main credential. LLM_API_KEY_HEADER picks how it's sent:
        #   Authorization (default) -> "Authorization: Bearer <key>"
        #   anything else, e.g. api-key -> "api-key: <key>"
        self.api_key = os.getenv("LLM_API_KEY") or os.getenv("LLM_BEARER_TOKEN", "")
        self.api_key_header = os.getenv("LLM_API_KEY_HEADER", "Authorization")
        # Optional app key: as a header, or as a body field. With
        # LLM_APP_KEY_JSON_KEY set, the field holds a JSON string, e.g.
        #   "user": "{\"appkey\": \"<key>\"}"
        self.app_key = os.getenv("LLM_APP_KEY", "")
        self.key_header = os.getenv("LLM_APP_KEY_HEADER", "")
        self.key_field = os.getenv("LLM_APP_KEY_FIELD", "")
        self.key_json_key = os.getenv("LLM_APP_KEY_JSON_KEY", "")
        # Azure-style deployment URLs carry the model in the path, so the
        # body usually shouldn't send "model" as well
        self.send_model = os.getenv("LLM_SEND_MODEL", "true").lower() == "true"
        self.api_version = os.getenv("LLM_API_VERSION", "")
        self.stop = [x for x in os.getenv("LLM_STOP", "").split(",") if x]
        self.verify = os.getenv("LLM_VERIFY_TLS", "true").lower() == "true"
        self.timeout = int(os.getenv("LLM_TIMEOUT", "120"))

    # ---- shared helpers -------------------------------------------------
    def _headers(self) -> dict:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            if self.api_key_header.lower() == "authorization":
                h["Authorization"] = f"Bearer {self.api_key}"
            else:
                h[self.api_key_header] = self.api_key
        if self.app_key and self.key_header:
            h[self.key_header] = self.app_key
        return h

    def _post(self, endpoint: str, body: dict) -> dict:
        if self.app_key and self.key_field:
            body[self.key_field] = (json.dumps({self.key_json_key: self.app_key})
                                    if self.key_json_key else self.app_key)
        params = {"api-version": self.api_version} if self.api_version else None
        r = requests.post(endpoint, headers=self._headers(), json=body, params=params,
                          verify=self.verify, timeout=self.timeout)
        if r.status_code >= 400:
            raise RuntimeError(f"LLM HTTP {r.status_code}: {r.text[:800]}")
        return r.json()

    def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        """
        messages: OpenAI-style. tools: [{"name","description","parameters"}].
        Returns {"content": str, "tool_calls": [{"id","name","args"}], "raw_message": dict}
        raw_message is appended to history as-is so provider-specific fields survive.
        """
        return self._chat_openai(messages, tools) if self.fmt == "openai" \
            else self._chat_gemini(messages, tools)

    # ---- OpenAI-compatible ----------------------------------------------
    def _chat_openai(self, messages, tools):
        endpoint = self.url if self.url.endswith("/chat/completions") else f"{self.url}/chat/completions"
        clean = [{k: v for k, v in m.items() if not k.startswith("_")} for m in messages]
        body = {"messages": clean}
        if self.send_model:
            body["model"] = self.model
        if self.stop:
            body["stop"] = self.stop
        if tools:
            body["tools"] = [{"type": "function", "function": t} for t in tools]
        msg = self._post(endpoint, body)["choices"][0]["message"]
        calls = []
        for c in msg.get("tool_calls") or []:
            args = c["function"].get("arguments") or "{}"
            calls.append({"id": c["id"], "name": c["function"]["name"],
                          "args": json.loads(args) if isinstance(args, str) else args})
        raw = {"role": "assistant", "content": msg.get("content")}
        if msg.get("tool_calls"):
            raw["tool_calls"] = msg["tool_calls"]
        return {"content": msg.get("content") or "", "tool_calls": calls, "raw_message": raw}

    # ---- Native Gemini --------------------------------------------------
    def _chat_gemini(self, messages, tools):
        endpoint = self.url if ":generateContent" in self.url \
            else f"{self.url}/v1beta/models/{self.model}:generateContent"
        system, contents = None, []
        for m in messages:
            if m["role"] == "system":
                system = {"parts": [{"text": m["content"]}]}
            elif m["role"] == "user":
                contents.append({"role": "user", "parts": [{"text": m["content"]}]})
            elif m["role"] == "assistant":
                # Replay the model's original parts: Gemini 3 models expect their
                # thought signatures back on function-call turns.
                parts = m.get("_gemini_parts") or [{"text": m.get("content") or ""}]
                contents.append({"role": "model", "parts": parts})
            elif m["role"] == "tool":
                part = {"functionResponse": {"name": m["name"],
                                             "response": {"result": m["content"]}}}
                # Consecutive tool results go in one user turn
                if contents and contents[-1]["role"] == "user" and \
                        "functionResponse" in contents[-1]["parts"][0]:
                    contents[-1]["parts"].append(part)
                else:
                    contents.append({"role": "user", "parts": [part]})
        body = {"contents": contents}
        if system:
            body["systemInstruction"] = system
        if tools:
            body["tools"] = [{"functionDeclarations": [
                {"name": t["name"], "description": t["description"],
                 "parameters": _sanitize_schema(t["parameters"])} for t in tools]}]
        cand = self._post(endpoint, body)["candidates"][0]
        parts = cand.get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts if "text" in p and not p.get("thought"))
        calls = [{"id": f"call_{uuid.uuid4().hex[:8]}", "name": p["functionCall"]["name"],
                  "args": p["functionCall"].get("args", {})}
                 for p in parts if "functionCall" in p]
        raw = {"role": "assistant", "content": text, "_gemini_parts": parts}
        if calls:
            raw["tool_calls"] = [{"id": c["id"], "type": "function",
                                  "function": {"name": c["name"], "arguments": json.dumps(c["args"])}}
                                 for c in calls]
        return {"content": text, "tool_calls": calls, "raw_message": raw}
