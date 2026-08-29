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

Current stable controller: **6.0.0**
