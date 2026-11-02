# Connection test plan (instructor)

Run this on **one pod's Windows jump host** before the event, and again on
each pod during staging. It tests one layer at a time, so when something
fails you know exactly which layer broke:

```
1 Jump host → 2 .env → 3 Network → 4 API login → 5 MCP (no AI) → 6 AI model → 7 Agent → 8 Write path → 9 Final check
```

Don't skip ahead. Each step assumes the ones before it passed.

Placeholders: `<APIC_IP>`, `<ND_IP>`. The model gateway is
`ai.connection.com`.

---

## Step 1 — Jump host ready (5 min)

Open **PowerShell** and check the tools:

```powershell
git --version
python --version        # needs 3.11 or newer
code --version
```

✅ All three print a version. If `python` opens the Microsoft Store, install
Python from python.org and tick **Add python.exe to PATH**.

Get the repo and set it up:

```powershell
mkdir C:\lab -Force; cd C:\lab
git clone https://github.com/mmtxcode/ai-dc-agent-lab.git
cd ai-dc-agent-lab
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

✅ Ends with `Packages installed.` and `Created .env from .env.example`.

Turn on the virtual environment for this PowerShell window (VS Code's
terminal does this for you automatically):

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\.venv\Scripts\Activate.ps1
```

✅ The prompt now starts with `(.venv)`. Every `python` command below
assumes this.

## Step 2 — Fill in `.env` (5 min)

```powershell
code .env
```

| Section | What to enter |
|---------|---------------|
| AI model | Only `LLM_API_KEY` (your `api-key` header value) and `LLM_APP_KEY` (the `appkey` value). The URL, deployment and other settings already match your curl test. |
| Intersight | `INTERSIGHT_KEY_ID`, and save the secret key as `C:\lab\keys\intersight.pem` (or change the path). |
| ACI | `APIC_URL=https://<APIC_IP>`, `APIC_USER`, `APIC_PASSWORD` |
| Nexus Dashboard | `ND_URL=https://<ND_IP>` (no trailing slash), `ND_USER`, `ND_API_KEY` |

Create the ND API key in ND: **user menu (top right) → Manage API keys → Add
API key**. Copy it right away: ND only shows it once. `ND_USER` must be the
user the key belongs to.

✅ `python scripts\check_lab.py --only env` → `PASS .env all values set`

## Step 3 — Network reachability (2 min)

```powershell
Test-NetConnection <APIC_IP>        -Port 443 | Select ComputerName, TcpTestSucceeded
Test-NetConnection <ND_IP>          -Port 443 | Select ComputerName, TcpTestSucceeded
Test-NetConnection intersight.com   -Port 443 | Select ComputerName, TcpTestSucceeded
Test-NetConnection ai.connection.com -Port 443 | Select ComputerName, TcpTestSucceeded
```

✅ `TcpTestSucceeded : True` on all four.
❌ `False` is a routing, firewall, or proxy problem, not a lab-code problem.
If the jump host uses a proxy for internet access, Intersight and the model
may fail here and still work through the proxy; carry on to Step 4 and
see if they pass there.

## Step 4 — API login, one system at a time (3 min)

```powershell
python scripts\check_lab.py --only intersight
python scripts\check_lab.py --only aci
python scripts\check_lab.py --only nd
```

| Expected | What it proves |
|----------|----------------|
| `PASS Intersight  N servers visible` | Key ID and `.pem` are valid, key can read |
| `PASS ACI (APIC)  N fabric nodes` | APIC login works, REST is reachable |
| `PASS Nexus Dash. built-in MCP server answered with N tools` | ND API key + user are accepted by ND's MCP server at `/api/v1/mcp` |

Note the Intersight server count and ACI node count; Steps 5 and 7 should
agree with them.

## Step 5 — MCP servers with NO AI model (5 min)

This proves the MCP layer works on its own. If something breaks later,
you'll know it's the model, not MCP.

List each server's tools:

```powershell
python scripts\mcp_probe.py intersight
python scripts\mcp_probe.py aci
python scripts\mcp_probe.py nd
```

✅ `✓ intersight: 6 tools`, `✓ aci: 8 tools`, `✓ nd: 10 tools` (ND's count
comes from ND itself; write down what you see). Every tool should be tagged
`[read ]` except `aci_create_app_tenant` and `aci_delete_tenant`.

Call one real tool on each:

```powershell
python scripts\mcp_probe.py intersight intersight_list_servers top=5
python scripts\mcp_probe.py intersight intersight_list_alarms top=5
python scripts\mcp_probe.py aci aci_get_fabric_health
python scripts\mcp_probe.py aci aci_list_faults top=5
python scripts\mcp_probe.py nd time_now
python scripts\mcp_probe.py nd get_nd_fabric_summary
```

✅ Each prints `OK` and real data. Check the data against the GUIs: same
server names as Intersight, same health score as APIC, same fabric names as
ND.

Two Lab 05 checks while you're here. Pick a server name from the list:

```powershell
python scripts\mcp_probe.py intersight intersight_get_server_nics name=<SERVER_NAME>
```

✅ Returns vNICs with `MacAddress` values. **Write down the server name** —
it goes into Lab 05. If this returns `[]`, tell me; the vNIC lookup needs
adjusting for your Intersight setup.

## Step 6 — AI model (3 min)

```powershell
python scripts\test_llm_toolcall.py
```

It runs two tests with the agent's own model code:

1. **Plain chat:** the same quantum-computing question as your curl test.
   An answer means the URL, `api-key` header and app key are right.
2. **Tool calling:** the model is offered one tool and must ask to call it.
   The agent can't use MCP without this.

Expected:

```
Test 1 — plain chat
  PASS  Quantum computing uses qubits, which ...

Test 2 — tool calling
  PASS  model asked to call get_fabric_health({"fabric": "LAB-FABRIC"})

The model is ready for the agent.
```

| Result | Do this |
|--------|---------|
| Test 1 FAIL, HTTP 401 / 403 | `LLM_API_KEY` or `LLM_APP_KEY` is wrong. Run your curl test with the same values to compare. |
| Test 1 FAIL, HTTP 400 mentioning `model` | Leave `LLM_SEND_MODEL=false`; the deployment name in the URL picks the model. |
| Test 1 FAIL, HTTP 404 or asks for `api-version` | Set `LLM_API_VERSION` to the version the gateway wants. |
| Test 2 FAIL, HTTP 400 on `tools` | The gateway won't pass tools to this deployment. **Send me the output**; this is the one blocker for the lab. |
| Test 2 FAIL, `no tool call` | The model answered in text instead. Send me the output. |

✅ Then: `python scripts\check_lab.py --only llm` → `PASS AI model model replied: READY`

## Step 7 — Agent end to end, read only (5 min)

One domain at a time, one question each:

```powershell
python agent\main.py --servers intersight --ask "How many servers are there, and which have critical alarms?"
python agent\main.py --servers aci        --ask "What is the fabric health score and the top 3 faults?"
python agent\main.py --servers nd         --ask "Summarize the health of each fabric and any anomalies from the last 2 hours."
```

✅ Each shows at least one cyan `→ server.tool(...)` line, then an
`Agent:` answer that matches what you saw in Step 5.

Then all three together, the Lab 05 question:

```powershell
python agent\main.py --ask "Server <SERVER_NAME> has an alarm. Where does it connect in ACI, and is the fabric healthy there and in Nexus Dashboard?"
```

Expect it to call Intersight first, then ACI and ND. The ACI lookup will
say the MAC is **not found** until the Lab 05 seed script exists; that's
expected for now.

## Step 8 — Write path on ACI (5 min)

```powershell
python agent\main.py --servers aci
```

At the `You:` prompt:

1. `Create a tenant called conntest with VRF conntest-vrf, BD conntest-bd, gateway 10.99.99.1/24, app profile conntest-ap and EPGs web and db.`
   - ✅ A yellow **Approve? [y/N]** prompt appears. Type `y`.
   - ✅ In the APIC GUI, **Tenants → conntest** exists with the VRF, BD,
     subnet and both EPGs.
2. `Delete the common tenant.`
   - Approve with `y` — the server itself must refuse: ✅ `Tenant 'common'
     is protected`.
3. `Delete the conntest tenant.` → approve with `y` → ✅ gone from APIC.
4. `/quit`, then check the audit log:

```powershell
Get-Content logs\audit.jsonl -Tail 5
```

✅ One line per tool call, with `ok`, `error`, or `denied`.

## Step 9 — Final check

```powershell
python scripts\check_lab.py
```

✅ Five `PASS` lines. This is the same check attendees run in Lab 00.

---

## Record results

Copy this into the GitHub issue or your notes for each pod:

```
Pod: ____        Date: ____
[ ] 1 Jump host   git / python ___ / VS Code
[ ] 2 .env        filled
[ ] 3 Network     APIC / ND / Intersight / LLM
[ ] 4 API login   Intersight (__ servers) / ACI (__ nodes) / ND
[ ] 5 MCP probe   intersight __ tools / aci 8 tools / nd __ tools
                  vNIC MACs returned for server: ________
[ ] 6 Model       plain chat PASS / tool call PASS
[ ] 7 Agent       intersight / aci / nd / all three
[ ] 8 Write path  create / protected block / delete / audit log
[ ] 9 check_lab   5 x PASS
Notes:
```

## Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| `ModuleNotFoundError: No module named 'mcp'` | Virtual environment not active. Run the `Activate.ps1` line from Step 1. |
| Intersight `401` | Key ID doesn't match the `.pem`, or the `.pem` path is wrong. |
| Intersight `403` | Key works but its role can't read that resource. |
| APIC `401` / `403` on login | Wrong user or password, or the account needs a domain prefix (`apic#fallback\admin`). |
| APIC `SSLError` | Keep `APIC_VERIFY_TLS=false` for the simulator. |
| `✗ nd: could not start` with `401` / `403` | ND API key and `ND_USER` don't match, or the key was deleted. |
| `✗ nd` with `404` | ND isn't on 4.3.1, or `ND_URL` has a path or trailing slash. |
| `✗ nd` with SSL errors | Keep `ND_VERIFY_TLS=false` for the lab. |
| ND tools return empty anomalies | Nothing happened in the last 2 hours. Ask for a longer window, or stage a link flap in CML. |
| Agent loops and stops after 12 steps | The model kept calling tools. Send me the output; usually a tool description needs tightening. |
| Write tool runs with no approval prompt | `AGENT_AUTO_APPROVE=true` in `.env`. Set it back to `false`. |
