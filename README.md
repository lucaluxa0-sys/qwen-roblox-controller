# Qwen Roblox Controller

Automated Roblox Studio controller/supervisor, telemetry bridge, and release channel for the local Qwen agent.

## Security

- Never commit telemetry secrets, tunnel tokens, API tokens, passwords, or local state.
- The telemetry bridge binds to `127.0.0.1` and is read-only.
- Local secrets live under `%LOCALAPPDATA%\QwenRobloxEnforcedProxy`.

## Layout

```text
stable/
  controller.py
bridge/
  qwen_telemetry_server.py
releases/
tests/
latest.json
```

Current stable controller: see **latest.json** and the private heartbeat; do not rely on a hard-coded README version.


## Next Chat / Project Handoff

The project has a detailed continuation guide for the next ChatGPT session:

**[NEXT_CHAT_HANDOFF.md](./NEXT_CHAT_HANDOFF.md)**

It contains the live architecture, Windows paths, heartbeat/benchmark workflow, controller safety rules, current release state, Stop Qwen work, and the new Qwen3.5-4B Roblox-specialist model plan.

> The version number below can become stale. Treat `latest.json` plus the private heartbeat as authority for the current live controller.
