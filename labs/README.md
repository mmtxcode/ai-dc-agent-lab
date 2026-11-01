# Lab plan (instructor notes)

**Story:** "See it, understand it, change it safely, connect it." Each lab adds
one idea, and Lab 05 is the payoff: one plain-English question answered with
data from all three domains.

**Pod per attendee:** Windows jump host (VS Code, Python), ACI simulator,
virtual Nexus Dashboard with a CML NX-OS fabric onboarded, shared read-only
Intersight key, Gemini 3.1 Flash endpoint.

## Timing (55 min, 5 min buffer)

| # | Lab | Min | Key prompt(s) | Callout for the room |
|---|-----|-----|---------------|----------------------|
| 00 | Setup | 5 | `check_lab.py` | Pre-staged: repo cloned, `.env` filled. Attendees only run the check. |
| 01 | Meet MCP (Intersight) | 10 | "What alarms are active?" | The model sees tool *descriptions*, not code. Read-only key = least privilege. |
| 02 | Monitor ACI | 8 | "How healthy is the fabric? Summarize the top 3 faults and what to do." | The agent turns 200 faults into 3 actions. |
| 03 | Configure ACI | 10 | "Build a 3-tier app tenant called `shop`: web, app, db, gateway 10.10.10.1/24." | Approval prompt; verify in APIC GUI; try to delete `common` and watch the code-level block. |
| 04 | Nexus Dashboard | 8 | "Which fabrics does ND manage? Any switches or anomalies I should worry about?" | Same agent, new domain: just another server in `servers.json`. |
| 05 | Cross-domain | 10 | "Server `<name>` has an alarm. Where does it connect in ACI, and is the fabric healthy there and in ND?" | The agent chains Intersight → ACI → ND without being told the steps. |
| 06 | Wrap | 4 | Open `logs/audit.jsonl` | Every action is traceable. What production adds: RBAC, change tickets, rate limits. |

**Stretch (fast finishers):** open `.vscode/mcp.json` and use the same three
servers from VS Code's built-in agent mode. Point: MCP servers are reusable
across any MCP client.

## Build to-dos

- [ ] Confirm Gemini endpoint format with `scripts/test_llm_toolcall.py`
- [ ] Verify `ND_SWITCHES_PATH` and `ND_ANOMALIES_PATH` in ND's `/help-center/swagger/`
- [ ] **Lab 05 seed script:** the ACI simulator can't learn real endpoints, so
      write `scripts/seed_aci_endpoints.py` to pull vNIC MACs from Intersight
      and create matching static endpoints (`fvStCEp`) in a `prod` tenant.
      Then `intersight_get_server_nics` → `aci_find_endpoint` joins real data.
- [ ] **Lab 02 seed:** make sure each simulator has a few visible faults
      (e.g. a BD with no VRF, an EPG with no BD) so the summary has content
- [ ] Pick the Intersight server name for Lab 05 and put it in the guide
- [ ] Optional ND write lab (create a network on the CML fabric) — needs the
      ND 4.x API path for networks
- [ ] Screenshots for each lab into `labs/images/`
- [ ] Dry run end-to-end on one pod and time each lab
- [ ] Tag a release (`v1.0-event`) as the known-good state
