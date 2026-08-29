# 01 — Scripting / Luau Capability Suite

This is the full scripting benchmark for the autonomous Qwen Roblox agent.

- **Total tests:** 280
- **Test IDs:** S001–S280
- **Execution:** run in deterministic batches, then integrated missions.
- **Isolation:** benchmark-created namespace only; unrelated user game content is read-only.
- **Progress markers:** after each test Qwen must emit `[BENCH:S###:PASS]`, `[BENCH:S###:PARTIAL:<reason>]`, or `[BENCH:S###:FAIL:<reason>]`.
- **Completion marker:** each batch ends with `[BENCH_BATCH_COMPLETE:<batch-id>]` and then `[TASK_COMPLETE]`.
- **Failure classification:** MODEL, CONTROLLER, MCP, ENV, or UNSAFE.
- **Mastery target:** >=95% in a domain across three varied runs, zero manual intervention, zero unsafe mutations, zero controller deadlocks, and no repeated failure family.

The suite intentionally separates small skills so failures become reusable regressions rather than getting buried inside one giant mission. The last ten tests are integrated autonomous missions.

The machine-readable catalog is `catalog.json`.
