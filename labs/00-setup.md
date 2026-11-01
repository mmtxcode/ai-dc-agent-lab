# Lab 00 — Setup and lab check (5 min)

Your pod is already prepared: the repo is cloned to `C:\lab\ai-dc-agent-lab`
and `.env` holds your pod's addresses and credentials. You only need to
confirm everything is reachable.

## Steps

1. On the jump host desktop, open **VS Code**.
2. **File → Open Folder** → `C:\lab\ai-dc-agent-lab`.
3. Open a terminal: **Terminal → New Terminal**. It opens in the repo folder
   with the Python environment active (you'll see `(.venv)` in the prompt).
4. Run the lab check:

   ```powershell
   python scripts\check_lab.py
   ```

## Expected result

```
  PASS  .env         all values set
  PASS  Intersight   12 servers visible
  PASS  ACI (APIC)   4 fabric nodes
  PASS  Nexus Dash.  built-in MCP server answered with 10 tools
  PASS  AI model     model replied: READY

All checks passed — you are ready for Lab 01.
```

> ✅ **Checkpoint:** five PASS lines. If any line says FAIL, raise your hand —
> don't fix `.env` yourself.
