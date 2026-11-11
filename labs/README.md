# Lab plan (instructor notes)

**Story:** "See it, understand it, change it safely, connect it." Each lab adds
one idea, and Lab 05 is the payoff: one plain-English question answered with
data from all three domains.

**Pod per attendee:** Windows jump host (VS Code, Python), ACI simulator,
virtual Nexus Dashboard 4.3.1 with a CML NX-OS fabric onboarded and a per-pod
ND API key, shared read-only Intersight key, Gemini 3.1 Flash Lite via the AI gateway (`LLM_BASE_URL`).

## Timing (60 min)

| # | Lab | Min | Key prompt(s) | Callout for the room |
|---|-----|-----|---------------|----------------------|
| 00 | Setup | 5 | `check_lab.py` | Pre-staged: repo cloned, `.env` filled. Attendees only run the check. |
| 01 | Meet MCP (Intersight) | 10 | "What alarms are active?" | The model sees tool *descriptions*, not code. Read-only key = least privilege. |
| 02 | Monitor ACI | 7 | "How healthy is the fabric? Summarize the top 3 faults and what to do." | The agent turns 200 faults into 3 actions. |
| 03 | Configure ACI | 10 | "Build a 3-tier app tenant called `shop`: web, app, db, gateway 10.10.10.1/24." then "Allow https from web to app with a contract." | First a one-shot shortcut tool, then the agent chains granular tools (filter → contract → provider/consumer) with an approval per change. Verify in APIC GUI; try to delete `common` and watch the code-level block. |
| 04 | ND built-in MCP | 7 | "Summarize the health of each ND fabric and the top anomalies from the last 2 hours." then "Explain the root cause of the worst one." | No code to write: ND 4.3.1 ships its own MCP server. Look at `servers.json`: a URL and an API key instead of a script. The key's user RBAC applies; the server is GET-only. |
| 05 | Cross-domain | 10 | "Server `<name>` has an alarm. Where does it connect in ACI, and is the fabric healthy there and in ND?" | The agent chains Intersight → ACI → ND without being told the steps. |
| 06 | Build a report | 8 | "Create an environment health report covering health, inventory and topology." | Code collects the facts and builds the tables; the model writes only the summary (yellow AI-written boxes). Fact-check one number. Attendees leave with a document of their own pod. |
| 07 | Wrap | 3 | Open `logs/audit.jsonl` | Every action is traceable, including the report. What production adds: RBAC, change tickets, rate limits. |

**Stretch (fast finishers):** open `.vscode/mcp.json` and use the same three
servers from VS Code's built-in agent mode. Point: MCP servers are reusable
across any MCP client.

## Build to-dos

- [ ] Confirm tool calling works on the gateway with `scripts/test_llm_toolcall.py` (Test 2)
- [ ] Create an ND API key per pod (user menu → Manage API keys) for a
      read-only user, and put it in each pod's `.env`
- [ ] Confirm the agent reaches `https://<nd>/api/v1/mcp` from the jump host
      (`check_lab.py` lists the tool count). Lab uses `ND_VERIFY_TLS=false`;
      call out that production should use a CA-signed ND cert instead
- [ ] Make sure the CML fabric has anomalies in the last 2 hours at lab time
      (ND's anomaly tool defaults to a 2-hour window); stage a link flap if not
- [ ] **Lab 05 seed script:** the ACI simulator can't learn real endpoints, so
      write `scripts/seed_aci_endpoints.py` to pull vNIC MACs from Intersight
      and create matching static endpoints (`fvStCEp`) in a `prod` tenant.
      Then `intersight_get_server_nics` → `aci_find_endpoint` joins real data.
- [ ] **Lab 02 seed:** make sure each simulator has a few visible faults
      (e.g. a BD with no VRF, an EPG with no BD) so the summary has content
- [ ] Pick the Intersight server name for Lab 05 and put it in the guide
- [ ] Decide if ACI sim should also be onboarded to ND: then
      `get_nd_security_segmentation` shows tenants/EPGs, and Lab 03's new
      tenant appears in ND too — a nice cross-check
- [ ] **Lab 06:** run test plan Step 9 on a pod; confirm the PDF comes out
      through Edge, and whether ND's topology draws as a diagram or falls back
      to a table (its output format isn't documented — send a sample to tune)
- [ ] **Lab 06:** decide if attendees should take the report with them
      (email/USB/share), and set `REPORT_AUTO_OPEN=true` if you'd rather it
      opens by itself
- [ ] **ACI write tools:** the agent now sees ~40 tools with everything
      loaded. Re-run the Lab 05 cross-domain question and confirm Flash Lite
      still picks the right ones. If it struggles, set `ACI_WRITE_MODE=off`
      for the read-only labs, or move to a larger deployment
- [ ] Decide whether attendees' pods should use `ACI_WRITE_TENANT_PREFIX`
      (e.g. `lab-`), so writes can only land in lab tenants
- [ ] Screenshots for each lab into `labs/images/`
- [ ] Dry run end-to-end on one pod and time each lab
- [ ] Tag a release (`v1.0-event`) as the known-good state
