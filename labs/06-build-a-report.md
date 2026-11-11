# Lab 06 — Build an environment report (8 min)

**Goal:** have the agent document your whole pod as an HTML page and a PDF, with
health, inventory and topology from Intersight, ACI and Nexus Dashboard.

## What you'll learn

- **Code handles the facts, the model handles the story.** The report server
  collects data straight from the APIs and builds every table from it. The
  model writes only the summary, findings and recommendations.
- **An MCP server doesn't have to wrap infrastructure.** This one adds a
  capability (making reports) that any MCP client could reuse.
- **Writing local files isn't the same as changing the network.** These tools
  don't ask for approval, but every call still lands in the audit log.

## Step 1 — Meet the report tools (1 min)

Start the agent with all servers:

```powershell
python agent\main.py
```

Expected:

```
✓ intersight: 6 tools
✓ aci: 20 tools
✓ nd: <n> tools        (the count your ND reports)
✓ report: 2 tools
```

Type `/tools` and find the two report tools:

```
  [read ] report_collect  (report)
  [local] report_build    (report)
```

| Tool | What it does | Who supplies the content |
|------|--------------|--------------------------|
| `report_collect` | Calls Intersight, ACI and ND, saves an exact **snapshot**, and hands the model a short **digest** | Code |
| `report_build` | Turns the snapshot into HTML and PDF | Code builds the tables; the model supplies the summary text |

`[local]` means the tool writes files on the jump host only, so there's no
approval prompt.

## Step 2 — Ask for the report (3 min)

```
Create an environment health report for my pod covering health, inventory and topology. Title it "Pod health report".
```

Watch the two calls:

```
→ report.report_collect({"scope": ["health", "inventory", "topology"]})
  ← ok in 2400 ms: { "snapshot_id": "20261108-140422-a1b2", "digest": { ...
→ report.report_build({"snapshot_id": "20261108-140422-a1b2", "title": "Pod health report",
                        "executive_summary": "The ACI fabric health score is ...", ...})
  ← ok in 3100 ms: { "html": "...\reports\...html", "pdf": "...\reports\...pdf" }
```

Look closely at the second call. The model passes only **text**: a title, a
summary, findings and recommendations. It never passes the tables. Those come
from the snapshot, so a number can't be mistyped on the way into the report.

> ✅ **Checkpoint:** the agent's answer lists an `html` and a `pdf` path.

Type `/quit`.

## Step 3 — Open the report (2 min)

Open the newest HTML and PDF:

```powershell
ii (Get-ChildItem reports\*.html | Sort-Object LastWriteTime | Select-Object -Last 1)
ii (Get-ChildItem reports\*.pdf  | Sort-Object LastWriteTime | Select-Object -Last 1)
```

Scan the sections:

| Section | Where it comes from |
|---------|---------------------|
| Executive summary, Findings and recommendations (yellow boxes, **AI-written**) | The model, writing from the digest |
| Health: ACI health and faults, Intersight alarms, ND fabrics and anomalies | APIs, through `report_collect` |
| Inventory: UCS servers, ACI fabric nodes | APIs |
| Topology: ACI fabric diagram, ND topology | APIs, drawn by code |
| Data sources | Which API call filled each section, and when |

**Fact-check the AI:** pick one number in the executive summary, such as the
fabric health score or a fault count, and find it in the tables below. It
should match. If it doesn't, the yellow "verify against the data below" label
is doing its job.

## Step 4 — Ask for a focused report (2 min)

Reports don't have to be big. Start the agent again and ask:

```
Make a topology-only report titled "Pod fabric map".
```

The model calls `report_collect` with `scope: ["topology"]`, so the report
has only the diagrams and node tables. Open it with the same `ii` command.

> 💡 **Optional:** open the newest file in `reports\.snapshots\`. That JSON is
> everything the report was built from: the exact API data, kept for audit.

## Recap

| Idea | Where you saw it |
|------|------------------|
| Code handles facts, model handles narrative | Tables from the snapshot; yellow AI-written boxes |
| One question, two tool calls | `report_collect` → `report_build` |
| MCP server as a capability | A report server, not an infrastructure API |
| Local writes, no approval, still audited | `[local]` tag; `logs\audit.jsonl` |

**Next:** Lab 07 — wrap-up: guardrails and the audit log.
