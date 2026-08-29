# Grouped batch S001–S024

Run these 24 scripting capabilities as **two capability packs**, not 24 independent Studio missions.

## SP01 — S001–S012: syntax / expressions
Use one shared benchmark harness where possible. Multiple assertions can execute in one Play session and one Output collection. Only create an extra benchmark Script/ModuleScript when a test specifically requires source mutation or syntax-repair evidence.

## SP02 — S013–S024: scope / types / nil
Reuse the benchmark namespace and shared harness pattern. Prefer one source write + one authoritative reread + one Play/Output evidence pass for the pack when that can validly prove the individual cases.

## Efficiency rules
- Shared setup and cleanup.
- Minimum number of source writes.
- Minimum number of Play start/stop transitions.
- One Output retrieval may prove multiple runtime assertions when each assertion is clearly labeled.
- Still emit one individual BENCH result per capability.
- Do not mark PASS merely because a neighboring test passed.
- Pack-complete marker only after all tests in that pack are individually decided.
- Batch-complete marker only after both packs and cleanup are complete.
