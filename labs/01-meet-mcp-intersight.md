# Lab 01 — Meet MCP with Intersight (10 min)

**Goal:** see exactly what an AI agent does when it "uses a tool," using
one MCP server: Intersight, read-only.

## What you'll learn

- An **MCP server** wraps an API as a set of named **tools**, each with a
  description and input schema.
- The **model** only sees those names and descriptions. It never sees your
  credentials or the server's code.
- The **agent** is the go-between: it runs the tool the model asks for and
  hands back the result.

## Step 1 — Look at a tool (2 min)

Open `mcp_servers/intersight_server.py` and find `intersight_list_alarms`:

```python
@mcp.tool(annotations=READ)
def intersight_list_alarms(severity: str = "", top: int = 25) -> list[dict]:
    """List active (not cleared) Intersight alarms, newest first.
    severity: optional filter — Critical, Warning, or Info."""
```

Three things to notice:

1. **The docstring is the tool's description.** That's what the model reads
   to decide whether this tool fits your question.
2. **The function arguments become the input schema.** The model has to
   supply `severity` and `top` in the right types.
3. **`annotations=READ`** marks it read-only. The agent uses that to decide
   whether to ask for your approval (Lab 03).

## Step 2 — Start the agent with one server (1 min)

In the VS Code terminal:

```powershell
python agent\main.py --servers intersight
```

Expected:

```
✓ intersight: 6 tools

Ask a question. Commands: /tools  /reset  /quit
```

The agent started the Intersight MCP server and asked it for its tool list.
That request is MCP's `list_tools`.

## Step 3 — See what the model sees (1 min)

Type:

```
/tools
```

You'll see six tools, each tagged `[read ]`. This list, names plus
descriptions, is everything the model knows about Intersight.

## Step 4 — Ask your first question (3 min)

```
What alarms are active in Intersight right now?
```

Watch the cyan line that appears before the answer:

```
→ intersight.intersight_list_alarms({})
  ← ok in 640 ms: [{"Severity": "Warning", "Code": ...
```

That is one round trip:

1. The model read your question and the tool list, and replied
   "call `intersight_list_alarms`" instead of an answer.
2. The agent ran it on the MCP server (MCP's `call_tool`), which called the
   Intersight API.
3. The result went back to the model, which wrote the summary you see.

> ✅ **Checkpoint:** you see at least one `→ intersight.` line and a summary
> of alarms. No alarms is a valid answer, too.

## Step 5 — Let it chain tools (2 min)

```
Which server has the most critical alarms? Show me its model, firmware, and vNIC MAC addresses.
```

This time the agent makes **two or three calls in a row**, for example
`intersight_list_alarms` → `intersight_get_server` → `intersight_get_server_nics`.
You didn't tell it the steps. It planned them from the tool descriptions.

> 💡 **Remember those MAC addresses.** In Lab 05 the agent uses them to find
> where this server plugs into the ACI fabric.

## Step 6 — Try to change something (1 min)

```
Power off that server.
```

The agent can't. No Intersight tool changes anything, and the API key is
read-only as well. That's **least privilege at two layers**: the tools you
expose and the credential behind them.

Type `/quit` to exit.

## Recap

| Concept | Where you saw it |
|---------|------------------|
| MCP server | `intersight_server.py`: an API wrapped as tools |
| Tool description | The docstring the model reads |
| `list_tools` | Agent startup, `/tools` |
| `call_tool` | The cyan `→` lines |
| Least privilege | No write tools + read-only key |

**Next:** Lab 02 — add the ACI server and monitor the fabric.
