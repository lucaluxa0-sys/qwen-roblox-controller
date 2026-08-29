# NEXT CHAT HANDOFF — Qwen Roblox Agent + Roblox Specialist Model

**Date:** 2026-08-29  
**Repository:** `lucaluxa0-sys/qwen-roblox-controller`  
**Private heartbeat repository:** `lucaluxa0-sys/roblox-proxy`  
**Heartbeat issue:** issue #1, title `[AUTO-HEARTBEAT] Qwen Roblox Agent`

This file is the first thing the next ChatGPT should read before changing anything.

---

## 1. User's current goal

There are now two connected goals.

### A. Existing autonomous Roblox agent

Keep the current Qwen + LM Studio + official Roblox MCP system usable as the training-data generator, benchmark runner, recovery system, and regression evaluator.

Current architecture:

```text
Qwen 3.5 local model
        ↓
LM Studio
        ↓
custom controller / supervisor
        ↓
official Roblox MCP
        ↓
Roblox Studio
```

The controller is intentionally strict: authoritative rereads, mutation gates, Play/Output verification, anti-loop rules, benchmark run integrity, and structured decision traces.

### B. New long-term model goal

Build a **small, very fast Roblox Studio specialist model** using **Qwen3.5-4B as the base**.

Desired finished deployment:

```text
Roblox specialist model
        ↓
LM Studio
        ↓
official Roblox MCP
        ↓
Roblox Studio
```

The large custom controller should eventually stop being the permanent runtime brain. Use it now as a **training-data factory + evaluator** so the behavior becomes learned by the model.

Do **not** train from scratch. Preferred plan:

1. Start from Qwen3.5-4B.
2. Generate complete verified Roblox Studio MCP trajectories.
3. Keep only objectively verified trajectories.
4. QLoRA / SFT on full agent interactions, not just Luau snippets.
5. Add preference training using successful vs failed/recovered actions.
6. Hold out 15–20% of projects/tasks for secret evaluation.
7. Export to GGUF.
8. Run the final specialist in LM Studio with official Roblox MCP.

A useful training example is:

```text
user task
→ inspect Studio
→ receive real state
→ decide next action
→ call MCP tool
→ receive result
→ test in Play
→ inspect Output
→ repair if needed
→ retest
→ verify success
```

Suggested dataset scale:
- prototype: 1k–2k verified examples
- useful v1: 5k–10k
- strong specialist: 20k+ diverse verified trajectories

The current controller/benchmark suite is valuable because it can generate and validate these trajectories.

---

## 2. User hardware / performance target

- Windows
- RTX 5060 laptop GPU
- 8 GB VRAM
- 32 GB system RAM
- User wants a small model that is much faster than the current 9B setup.
- Current existing model has been Qwen3.5-9B in LM Studio.
- Desired new base: **Qwen3.5-4B**.
- Prior 9B performance varied roughly from ~7 tok/s to ~10–12 tok/s depending on context/prompt size.
- Full GPU offload and shorter context are priorities for the future 4B specialist.

---

## 3. Important local Windows paths

User account name seen in paths: `zahia`.

```text
Root:
C:\Users\zahia\AppData\Local\QwenRobloxAgent

Controller:
C:\Users\zahia\AppData\Local\QwenRobloxAgent\qwen_roblox_enforced_proxy_current.py

Launcher:
C:\Users\zahia\AppData\Local\QwenRobloxAgent\qwen_controller_launcher.py

Python:
C:\Users\zahia\AppData\Local\Python\pythoncore-3.14-64\python.exe

Official Roblox MCP:
%LOCALAPPDATA%\Roblox\mcp.bat

LM Studio local server:
127.0.0.1:1234

Controller telemetry:
127.0.0.1:8766

LM Studio MCP config:
C:\Users\zahia\.lmstudio\mcp.json

LM Studio internal MCP state:
C:\Users\zahia\.lmstudio\.internal\last-synced-mcp-state.json

Windows login automation:
HKCU Run -> QwenRobloxFullAuto
```

Do not put secrets/tokens into the repo.

---

## 4. How the next ChatGPT should operate this project

When the user says things like `go`, `continue`, `check`, or `autonomous mode`:

1. Read the **fresh private heartbeat** first:
   - repo `lucaluxa0-sys/roblox-proxy`
   - issue #1
2. Read current:
   - `latest.json`
   - `autopilot/REMOTE_TASK.txt`
   - recent public `[AUTO-FAILURE]` issues
   - recent commits / CI if a release is in flight
3. Treat `benchmark_progress.source == "controller_verified"` as benchmark authority.
4. Do not claim a controller version is live until heartbeat proves live/disk match.
5. For controller patches:
   - make the smallest safe release
   - add regression/self-test
   - wait for CI
   - promote stable only if CI passes
   - publish the exact SHA-256 manifest
   - wait for heartbeat proof before continuing affected benchmark work
6. Never weaken safety guards just to make a benchmark pass.
7. Do not spam status narration. The user prefers silence on ordinary passes/progress and wants messages only for a real issue or the final success.

Important user preference: **implementation-first, minimal narration, autonomous work mode.**

---

## 5. Current live state at handoff

Fresh heartbeat at the time this handoff was written:

- live controller: **6.3.30**
- disk controller: **6.3.30**
- disk/live match: **true**
- Studio mode: **edit**
- current blocker: **none**
- current gate: **none**
- autopilot: running
- current benchmark authority:
  - `tests_seen: 80`
  - `pass: 80`
  - `partial: 0`
  - `fail: 0`
  - completed packs: **SP01–SP06**
  - authoritative run id: `scripting-s001-s024-structured-run6-20260829T0806Z`

SP07 is **not** controller-verified complete yet.

There was a long SP07 ModuleScript recovery/debugging sequence. Do not infer SP07 success from Qwen text or decision summaries.

Also note: S007's stored reason was accidentally changed by an older hold-task benchmark submission. Status remained PASS. Controller 6.3.28 later made existing benchmark decisions/evidence immutable to prevent repeats.

---

## 6. Current deliberate pause / shutdown work

The user paused autonomous Roblox benchmark work near the end of the previous chat.

Current remote task at handoff:

`hold-for-stop-button-6.3.31-20260829T0542PT`

It tells Qwen to do no Roblox work and wait until the Stop Qwen button controller is live.

### Controller 6.3.30

6.3.30 is current stable/live and added:

`supervisor_stop_local_qwen_stack`

It stops:
- full-auto manager
- autonomous runner
- controller/launcher/updaters
- LM Studio `llama-server.exe`

It preserves:
- Roblox Studio
- LM Studio GUI

### Controller 6.3.31 candidate

A candidate exists:

`releases/6.3.31/controller.py`

Commit:

`fd7e4cc69003101a30ca904f9fb4c31bc3fdfca9`

Purpose: add a clickable Windows **Stop Qwen** desktop shortcut plus local fallback:

`STOP_QWEN.ps1`

Expected shortcut:

`Desktop\Stop Qwen.lnk`

Expected controller tool:

`supervisor_install_stop_qwen_button`

At the moment this handoff was written:
- 6.3.31 exists in releases
- stable/live is still 6.3.30
- `latest.json` still points to 6.3.30

The next ChatGPT should check CI/promotion/manifest/heartbeat before doing anything with 6.3.31. Do not assume it was promoted.

---

## 7. LM Studio / llama-server diagnostic

The user ran:

```powershell
Get-CimInstance Win32_Process -Filter "name='llama-server.exe'" |
Select-Object ProcessId, CommandLine
```

At that moment it showed:

```text
ProcessId: 12824
CommandLine begins:
C:\Users\zahia\.lmstudio\extensions\backends\llama.cpp-win-x86_64-nvidia-cuda12-avx2-2.31.2\llama-server...
```

The PID is ephemeral; re-run the command instead of assuming PID 12824 is still valid.

This confirms the actual LM Studio backend process is `llama-server.exe` under `.lmstudio\extensions\backends`.

---

## 8. Benchmark suite

Canonical high-level roadmap:

1. Scripting / Luau
2. Instances / Explorer
3. Properties / Building
4. Assets
5. UI
6. Physics
7. Networking
8. Characters
9. World / Environment
10. Game Systems
11. Debugging
12. Testing / Play Mode
13. Performance
14. Project-wide Work

Scripting catalog:
- 280 capabilities, S001–S280
- full run grouped into 21 packs SP01–SP21

Files:

```text
benchmarks/ROBLOX_CAPABILITY_ROADMAP.md
benchmarks/01_scripting/catalog.json
benchmarks/01_scripting/execution_plan.json
benchmarks/01_scripting/tasks/
```

Exact deterministic task files for later packs already exist in `benchmarks/01_scripting/tasks/`.

Full scripting certification should ultimately require:
- S001–S280 all PASS
- SP01–SP21 complete
- 0 PARTIAL
- 0 FAIL
- controller-accepted full batch marker using prefix `scripting-s001-s280-`

Do not restart from scratch unless the user asks. Resume from the controller-verified state.

---

## 9. Benchmark artifact retention policy

The user explicitly wants completed benchmark scripts to remain visible in Roblox Studio.

Policy:
- preserve completed Script / LocalScript / ModuleScript harnesses
- do not delete completed harnesses
- temporary runtime objects may be destroyed for isolation
- completed harnesses may be disabled if needed to avoid contaminating later Play sessions
- preserve them so the user can inspect them later

Relevant benchmark folders may include:

```text
ServerScriptService.__QWEN_SCRIPT_BENCH__
ServerStorage.__QWEN_SCRIPT_BENCH__
ReplicatedStorage.__QWEN_SCRIPT_BENCH__
```

Do not promise a script exists in the exact Studio window without checking the current connected Studio session.

---

## 10. Structured Qwen decision trace

The user asked to see what Qwen is thinking.

The controller now exposes **structured operational summaries**, not hidden chain-of-thought.

Heartbeat field:

`qwen_decision_trace`

Typical fields:
- goal
- evidence
- decision
- expected_result
- actual_result
- next_action
- confidence
- blocker
- intended_script_class
- controller state

Do not claim raw hidden chain-of-thought is captured. It is not.

This trace is useful training data for the future 4B specialist, together with actual MCP actions/results and verification evidence.

---

## 11. Important controller behavior learned so far

Key guardrails that should remain:

- authoritative `script_read` before script mutation
- authoritative reread after mutation
- gameplay-affecting source requires Play/Output verification
- no `execute_luau` assignment to Script.Source
- no `execute_luau` creation of Script/LocalScript/ModuleScript
- deterministic missing-script bootstrap path
- ModuleScript/LocalScript creation must preserve exact intended class
- decision trace before meaningful mutation/Play/benchmark commit
- no repeated identical failed mutation without new evidence
- no pathless whole-tree benchmark dumps
- stale Studio/MCP recovery via `list_roblox_studios`
- controller-verified benchmark run IDs/results
- benchmark capability evidence immutable inside a run
- pack/batch completeness validated by controller
- full scripting history retention large enough for S001–S280 + pack markers

Do not remove these protections just to increase benchmark throughput.

---

## 12. GitHub repositories

Public:

`lucaluxa0-sys/qwen-roblox-controller`

Private heartbeat:

`lucaluxa0-sys/roblox-proxy`

Private heartbeat issue:

`#1 [AUTO-HEARTBEAT] Qwen Roblox Agent`

Remote autonomous task:

`autopilot/REMOTE_TASK.txt`

Use authenticated GitHub access. Never embed tokens/secrets.

---

## 13. What the next chat should probably do first

The user is ending the current chat because its context became too large.

Start the next chat by asking them to say **"read the GitHub handoff"** or, if repository tools are available, read this file immediately.

Then:

1. Fresh-check heartbeat issue #1.
2. Fresh-check `latest.json`, stable controller, 6.3.31 CI/promotion status, and current remote task.
3. Respect the user pause. Do not resume Roblox benchmarks unless the user explicitly resumes them.
4. If the user still wants the Stop Qwen button, finish 6.3.31 safely, prove it live, install the shortcut, then only shut the local stack down if the user explicitly authorizes shutdown.
5. For the new 4B specialist-model project, treat the current controller/benchmark stack as the **data-generation + evaluation infrastructure** and begin designing the verified trajectory dataset / fine-tuning pipeline.

---

## 14. Communication style

User preferences:
- concise
- direct
- practical
- fewer message tokens
- same care/analysis
- autonomous action instead of repeated explanations
- do not narrate normal benchmark passes
- surface real blockers/regressions
- user appreciates honesty about what is and is not actually verified

