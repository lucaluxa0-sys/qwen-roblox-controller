# 01 — Scripting / Luau Capability Suite

This suite defines **280 scripting capabilities**, but they are **not 280 separate Roblox Studio missions**.

## Execution model

The default runner works in **domain packs**. Compatible tests share one benchmark harness, one setup, one cleanup, and the minimum number of Play sessions needed for authoritative evidence. A pack may prove 10–16 individual capabilities in one run.

Use the modes in `execution_plan.json`:

- **Smoke** — ~32 representative tests after important changes.
- **Core** — ~96 broad tests for regular confidence checks.
- **Deep** — only weak areas, adjacent cases, and saved regressions.
- **Full** — all 280 capabilities, grouped into 21 packs, used occasionally as certification.

The full catalog remains `catalog.json`. Keeping all 280 capability IDs gives precise regression tracking even though execution is grouped.

## Result protocol

Each proven capability emits one concrete result marker using its real numeric ID. Pack and batch completion markers are emitted only after the work is actually complete. Prompt examples must never themselves count as completion.

A PASS requires authoritative evidence. Runtime-dependent behavior requires runtime/Output evidence; source edits require authoritative reread. Cleanup is mandatory.

## Current optimization

For S001–S024, run **two grouped harnesses**:

- SP01: S001–S012 — syntax / expressions
- SP02: S013–S024 — scope / types / nil

This replaces up to 24 separate Studio missions with two shared harness runs.
