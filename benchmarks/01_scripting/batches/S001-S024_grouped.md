# Grouped batch S001–S024

Run these 24 scripting capabilities as **two capability packs**, not 24 independent Studio missions.

## Required new-script bootstrap sequence

Controller 6.3.11 defines one deterministic way to create a new benchmark Script:

1. Create/reuse the benchmark Folder only.
2. Create the new Script with Source exactly:
   `-- QWEN_CONTROLLER_SCRIPT_BOOTSTRAP`
3. Call `script_read` on the exact new Script path.
4. Use the official transactional script edit tool to replace that exact bootstrap line with the pack harness.
5. Reread the authoritative source.
6. Run the minimum Play/Output verification needed for the pack.

Never use `execute_luau` to write Script.Source. Never retry `multi_edit` after a missing-cache block; perform the required `script_read` first.

If a particular repair test would require deliberately seeding invalid source that the controller correctly refuses to create, mark that test PARTIAL with an UNSAFE/guardrail reason and continue. Do not fight the guardrail or loop.

## SP01 — S001–S012: syntax / expressions

Use one shared benchmark harness where possible. Multiple assertions can execute in one Play session and one Output collection. Only create an extra benchmark Script when a test truly needs independent source evidence.

## SP02 — S013–S024: scope / types / nil

Reuse the benchmark namespace and shared harness pattern. Prefer one source write + one authoritative reread + one Play/Output evidence pass for the pack when that can validly prove the individual cases.

## Efficiency rules

- Shared setup and cleanup.
- Minimum source writes.
- Minimum Play start/stop transitions.
- One Output retrieval may prove multiple runtime assertions when each assertion is clearly labeled.
- Emit one individual BENCH result per capability.
- Do not mark PASS merely because a neighboring test passed.
- Pack-complete marker only after all tests in that pack are individually decided.
- Batch-complete marker only after both packs and cleanup are complete.
