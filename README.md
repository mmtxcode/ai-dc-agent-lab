# AI Agents for the Data Center — MCP Hands-On Lab

Use an AI agent to monitor, troubleshoot, and configure a data center built on
**Cisco Intersight**, **ACI**, and **Nexus Dashboard**. The agent connects to
each domain through **MCP (Model Context Protocol)** servers you can read
end to end.

**Time:** 45–60 minutes · **Level:** network engineers new to AI agents · **Format:** hands-on, one dedicated pod per attendee

## How it fits together

```
 You (VS Code terminal on the Windows jump host)
   │
   ▼
 agent/main.py ── HTTPS ──► Gemini 3.1 Flash  (decides which tool to call)
   │
   │  MCP (list_tools / call_tool)
   ├─ stdio ─► mcp_servers/intersight_server.py ──► Intersight API  (read-only key)   ┐ servers we
   ├─ stdio ─► mcp_servers/aci_server.py        ──► APIC simulator  (read + approved  ┘ built
   │                                                                  writes)
   └─ HTTPS ─► Nexus Dashboard 4.3.1 built-in MCP server  (/api/v1/mcp, API key)  ← vendor-provided
```

The lab shows both ways MCP shows up in real life: servers **you build**
around an API (Intersight, ACI) and a server **the vendor ships** inside the
product (Nexus Dashboard 4.3.1). The agent treats them the same.

The model never talks to your infrastructure directly. It can only ask for a
tool by name; the agent runs it, asks you first if it changes anything, and
logs every call to `logs/audit.jsonl`.

## Labs

| # | Lab | Time | You'll learn |
|---|-----|------|--------------|
| 00 | [Setup and lab check](labs/00-setup.md) | 5 min | Repo, `.env`, health check |
| 01 | [Meet MCP with Intersight](labs/01-meet-mcp-intersight.md) | 10 min | What a tool is, how the agent picks one |
| 02 | Monitor ACI | 8 min | Health, faults, summarising noise |
| 03 | Configure ACI safely | 10 min | Write tools, human approval, verify in the GUI |
| 04 | Nexus Dashboard's built-in MCP server | 8 min | Remote MCP, API keys, RBAC, anomalies |
| 05 | Cross-domain troubleshooting | 10 min | One question, three domains |
| 06 | Wrap-up: guardrails and the audit log | 4 min | What production would need |

See [labs/README.md](labs/README.md) for the full plan.

## Quick start (jump host)

```powershell
git clone https://github.com/mmtxcode/ai-dc-agent-lab.git
cd ai-dc-agent-lab
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
# fill in .env
.\.venv\Scripts\python.exe scripts\check_lab.py
.\.venv\Scripts\python.exe agent\main.py
```

**Instructors:** before the event, run [docs/connection-test-plan.md](docs/connection-test-plan.md) on each pod.

## Repo layout

```
agent/          main.py (agent loop), llm.py (model adapter), servers.json
mcp_servers/    intersight_server.py, aci_server.py  (ND's server runs on ND itself)
labs/           step-by-step guides (+ images/ for screenshots)
scripts/        setup.ps1, check_lab.py, mcp_probe.py, test_llm_toolcall.py
docs/           connection-test-plan.md (instructor pre-flight)
.vscode/        mcp.json (the same servers, usable from VS Code's own agent mode)
logs/           audit.jsonl (created at runtime, git-ignored)
```
