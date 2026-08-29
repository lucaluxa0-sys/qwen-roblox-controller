from __future__ import annotations

"""
Qwen Roblox Enforced Proxy V6 (telemetry + transactional enforcement engine)
=========================================================

Purpose
-------
Make the debugging supervisor mandatory WITHOUT asking the model to call a
second MCP server.  LM Studio connects only to this process.  This process
launches Roblox's official Studio MCP server as a child and transparently
forwards its tools while enforcing a small deterministic debugging state
machine.

No third-party Python package is required.

Important design rule
---------------------
Do NOT leave a separate direct `roblox-studio` MCP integration enabled in
LM Studio.  If the model can call Roblox directly, it can bypass enforcement.
"""

import argparse
import copy
import difflib
import json
import os
import queue
import hashlib
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

APP_NAME = "Qwen Roblox Enforced Proxy V6.3.2"
VERSION = "6.3.2"

LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
STATE_DIR = LOCALAPPDATA / "QwenRobloxEnforcedProxy"
STATE_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = STATE_DIR / "state.json"
LOG_FILE = STATE_DIR / "proxy.log"

ROBLOX_MCP_BAT = LOCALAPPDATA / "Roblox" / "mcp.bat"
RESUME_FILE = STATE_DIR / "resume.txt"
CHECKPOINT_FILE = STATE_DIR / "checkpoint.json"

# V6 telemetry foundation. These files are deliberately separate from MCP stdout
# so they can later be exposed by a read-only HTTPS bridge without touching the
# controller's JSON-RPC transport. Telemetry failures are always non-fatal.
TELEMETRY_DIR = STATE_DIR / "telemetry"
TELEMETRY_DIR.mkdir(parents=True, exist_ok=True)
TELEMETRY_STATUS_FILE = TELEMETRY_DIR / "status.json"
TELEMETRY_FAILURE_FILE = TELEMETRY_DIR / "latest_failure.json"
TELEMETRY_FAILURE_HISTORY_FILE = TELEMETRY_DIR / "failure_history.jsonl"
TELEMETRY_ACTION_HISTORY_FILE = TELEMETRY_DIR / "action_history.jsonl"
TELEMETRY_HEALTH_FILE = TELEMETRY_DIR / "controller_health.json"
TELEMETRY_TEST_RESULTS_FILE = TELEMETRY_DIR / "test_results.json"
TELEMETRY_AUTOPILOT_FILE = TELEMETRY_DIR / "autopilot_runs.jsonl"
TELEMETRY_FAILURE_PACKET_FILE = TELEMETRY_DIR / "failure_packet.json"
TELEMETRY_REGRESSION_CASES_FILE = TELEMETRY_DIR / "regression_cases.jsonl"
TELEMETRY_GITHUB_REPORTER_FILE = TELEMETRY_DIR / "github_reporter_status.json"
TELEMETRY_SCHEMA_VERSION = 1

# Optional automatic GitHub failure handoff. No token is embedded in the
# controller. The reporter uses the user's existing authenticated GitHub CLI
# session (gh auth login) and is inert if gh is unavailable or unauthenticated.
GITHUB_FAILURE_REPORTING = os.environ.get("QWEN_GITHUB_FAILURE_REPORTING", "1") != "0"
GITHUB_FAILURE_REPO = os.environ.get(
    "QWEN_GITHUB_FAILURE_REPO",
    "lucaluxa0-sys/qwen-roblox-controller",
).strip()
GITHUB_FAILURE_LABEL = os.environ.get("QWEN_GITHUB_FAILURE_LABEL", "controller-failure").strip()
GITHUB_FAILURE_TIMEOUT = int(os.environ.get("QWEN_GITHUB_FAILURE_TIMEOUT", "20"))
DEADLOCK_BLOCK_WINDOW = int(os.environ.get("QWEN_DEADLOCK_BLOCK_WINDOW", "8"))
DEADLOCK_REPEAT_LIMIT = int(os.environ.get("QWEN_DEADLOCK_REPEAT_LIMIT", "3"))
TELEMETRY_MAX_STRING = int(os.environ.get("QWEN_TELEMETRY_MAX_STRING", "12000"))
TELEMETRY_HISTORY_TAIL = int(os.environ.get("QWEN_TELEMETRY_HISTORY_TAIL", "30"))

# Context management. In normal LM Studio UI/MCP mode the proxy cannot see the
# model's private reasoning or the complete chat transcript, so the meter there
# is a conservative heuristic. In --autopilot mode the LM Studio v1 REST API
# returns exact input_tokens and rollover is exact.
CONTEXT_WINDOW_TOKENS = int(os.environ.get("QWEN_CONTEXT_WINDOW_TOKENS", "40000"))
CONTEXT_ROLLOVER_TRIGGER = int(os.environ.get("QWEN_CONTEXT_ROLLOVER_TRIGGER", "36000"))
MCP_CHARS_PER_TOKEN = float(os.environ.get("QWEN_MCP_CHARS_PER_TOKEN", "4.0"))
MCP_REASONING_ALLOWANCE_PER_TOOL = int(os.environ.get("QWEN_REASONING_ALLOWANCE_PER_TOOL", "300"))
MAX_ACTION_HISTORY = int(os.environ.get("QWEN_MAX_ACTION_HISTORY", "80"))

# V5 transactional policy. Script writes are treated as untrusted proposals.
# They must be reproducible against the latest cached source and pass every
# deterministic preflight before they can reach Studio.
V5_REQUIRE_SIMULATED_SCRIPT_WRITES = os.environ.get("QWEN_V5_REQUIRE_SIMULATED_WRITES", "1") != "0"
V5_MAX_ATOMIC_EDIT_PAIRS = int(os.environ.get("QWEN_V5_MAX_EDIT_PAIRS", "6"))
V5_MAX_CHANGED_LINE_RATIO = float(os.environ.get("QWEN_V5_MAX_CHANGED_LINE_RATIO", "0.55"))
V5_MAX_SOURCE_BYTES = int(os.environ.get("QWEN_V5_MAX_SOURCE_BYTES", "500000"))
V5_STRICT_UNDEFINED_CALLS = os.environ.get("QWEN_V5_STRICT_UNDEFINED_CALLS", "1") != "0"

# Optional controller-owned LM Studio REST agent mode. This is the only mode
# that can truly roll to a brand-new stateful LM Studio API conversation
# automatically because MCP servers do not control the LM Studio chat UI.
LM_STUDIO_BASE_URL = os.environ.get("LM_STUDIO_BASE_URL", "http://127.0.0.1:1234").rstrip("/")
LM_STUDIO_MODEL = os.environ.get("LM_STUDIO_MODEL", "qwen/qwen3.5-9b")
LM_STUDIO_API_TOKEN = os.environ.get("LM_STUDIO_API_TOKEN", "")
MCP_INTEGRATION_ID = os.environ.get("QWEN_MCP_INTEGRATION_ID", "mcp/qwen-roblox-enforced")


def configure_stdio_utf8() -> None:
    """Force MCP stdio to UTF-8 on Windows.

    LM Studio and MCP use UTF-8 JSON-RPC, but Windows Python can otherwise wrap
    redirected stdio with cp1252.  A Unicode character in a Roblox tool
    description/result (for example →) would then crash the stdout forwarding
    thread and leave LM Studio waiting forever.
    """
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


configure_stdio_utf8()

# -----------------------------------------------------------------------------
# Logging -- NEVER print logs to stdout; stdout is reserved for MCP JSON-RPC.
# -----------------------------------------------------------------------------

_log_lock = threading.Lock()


def log(message: str) -> None:
    try:
        with _log_lock:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            with LOG_FILE.open("a", encoding="utf-8") as f:
                f.write(f"{stamp} {message}\n")
    except Exception:
        pass


# -----------------------------------------------------------------------------
# Persistent enforcement state
# -----------------------------------------------------------------------------

_state_lock = threading.RLock()

# In-memory caches are rebuilt naturally from tools/list and script_read calls.
# Persistent state keeps only compact hashes/evidence so long-running sessions stay small.
SOURCE_CACHE: dict[str, str] = {}
TOOL_SCHEMAS: dict[str, dict[str, Any]] = {}


def new_state() -> dict[str, Any]:
    return {
        "version": VERSION,
        "started_at": time.time(),
        "updated_at": time.time(),
        "studio_mode": "unknown",  # unknown | edit | play
        "play_session": 0,
        "mutation_epoch": 0,
        "last_script_target": "",
        "current_blocker": None,
        "gate": None,
        "last_mutation": None,
        "failed_mutation_signatures": [],
        "blocked_count": 0,
        "forwarded_count": 0,
        "tool_error_count": 0,
        "runtime_error_count": 0,
        "last_note": "",
        "runtime_evidence": {
            # Evidence intentionally survives Play/Edit toggles. It is invalidated
            # selectively only by successful writes that could change that fact.
            "last_play_session": 0,
            "accessory_seen": False,
            "handle_seen": False,
            "handle_size_seen": False,
            "head_seen": False,
            "humanoid_seen": False,
            "body_part_size_seen": False,
            "non_head_body_size_seen": False,
            "details": {},
        },
        "action_history": [],
        "context_estimate": {
            "mcp_chars": 0,
            "tool_calls": 0,
            "estimated_tokens": 0,
            "window_tokens": CONTEXT_WINDOW_TOKENS,
            "rollover_trigger": CONTEXT_ROLLOVER_TRIGGER,
            "handoff_recommended": False,
            "handoff_notified": False,
            "exact_input_tokens": None,
        },
        "task_checkpoint": {
            "goal": "",
            "next_action": "",
            "last_compacted_at": 0.0,
        },
        "telemetry": {
            "last_failure_id": "",
            "last_failure_at": 0.0,
            "last_failure_kind": "",
            "last_event_at": 0.0,
        },
        "known_rules": [
            "Do not use Accessory.RootPart; Accessory has no RootPart property.",
            "Do not classify body parts/accessories with name keywords when class/hierarchy can identify them.",
            "BodyDepthScale is a Humanoid child NumberValue; use .Value, not GetAttribute and not direct assignment.",
            "Roblox child instances can be resolved by dot-name indexing; instance.OriginalSize.Value is valid when OriginalSize is an actual child.",
            "After a script edit, re-read the edited source before another write.",
            "After a gameplay script edit, playtest and check Output before another write.",
            "Visual changes require visual verification before another write/finish attempt.",
            "After a concrete runtime/tool error, gather direct evidence before another write.",
            "A syntax-error blocker must never prevent stopping Play, reading the implicated script, or making one same-script structural repair.",
            "Do not repeat an identical failed mutation without new evidence.",
            "Current script_read source is authoritative; never reason from an intended edit that is not present on reread.",
            "If post-edit reread reveals a structural defect, allow one narrow corrective edit before playtest instead of testing knowingly broken source.",
            "Accessory-writing changes require runtime Accessory/Handle evidence when the live avatar can be inspected.",
            "Official MCP tool arguments must satisfy the advertised tool schema and Studio datamodel mode.",
            "Do not re-inspect already verified runtime facts merely because Play was stopped and restarted; only invalidate evidence after a relevant write.",
            "V5 transaction invariant: no script mutation reaches Studio unless the controller can simulate the exact resulting source first.",
            "V5 compiler invariant: candidate source must pass lexical, delimiter, block, symbol, and high-confidence type checks before commit.",
            "V5 atomicity invariant: broad rewrites are rejected when a narrow edit can preserve unrelated working code.",
            "V5 symbol invariant: bare helper calls must resolve to a declaration/parameter/known Luau global in the resulting script.",
            "V5 type invariant: Instance-returning calls such as FindFirstChild/WaitForChild cannot be treated directly as Vector3 values.",
        ],
    }


def load_state() -> dict[str, Any]:
    with _state_lock:
        if not STATE_FILE.exists():
            state = new_state()
            save_state(state)
            return state
        try:
            raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("state is not an object")
            base = new_state()
            base.update(raw)
            base["version"] = VERSION
            fresh = new_state()
            # Merge newly-added nested keys during upgrades without discarding
            # evidence/checkpoints from older controller versions.
            for nested_key in ("runtime_evidence", "context_estimate", "task_checkpoint"):
                merged = dict(fresh[nested_key])
                if isinstance(raw.get(nested_key), dict):
                    merged.update(raw[nested_key])
                if nested_key == "runtime_evidence":
                    details = dict(fresh[nested_key].get("details", {}))
                    if isinstance((raw.get(nested_key) or {}).get("details"), dict):
                        details.update(raw[nested_key]["details"])
                    merged["details"] = details
                base[nested_key] = merged
            if not isinstance(base.get("action_history"), list):
                base["action_history"] = []
            base["action_history"] = base["action_history"][-MAX_ACTION_HISTORY:]
            return base
        except Exception as exc:
            log(f"state load failed: {exc!r}")
            state = new_state()
            save_state(state)
            return state


def save_state(state: dict[str, Any]) -> None:
    with _state_lock:
        state["updated_at"] = time.time()
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(STATE_FILE)


STATE = load_state()


def state_update(fn) -> Any:
    global STATE
    with _state_lock:
        result = fn(STATE)
        save_state(STATE)
        return result


def _context_recompute(state: dict[str, Any]) -> None:
    meter = state.setdefault("context_estimate", {})
    chars = int(meter.get("mcp_chars", 0) or 0)
    calls = int(meter.get("tool_calls", 0) or 0)
    heuristic = int(chars / max(MCP_CHARS_PER_TOKEN, 1.0)) + calls * MCP_REASONING_ALLOWANCE_PER_TOOL
    meter["estimated_tokens"] = heuristic
    meter["window_tokens"] = CONTEXT_WINDOW_TOKENS
    meter["rollover_trigger"] = CONTEXT_ROLLOVER_TRIGGER
    if heuristic >= CONTEXT_ROLLOVER_TRIGGER:
        meter["handoff_recommended"] = True


def account_context_traffic(*, chars: int = 0, tool_call: bool = False) -> None:
    def mutate(state: dict[str, Any]):
        meter = state.setdefault("context_estimate", {})
        meter["mcp_chars"] = int(meter.get("mcp_chars", 0) or 0) + max(0, int(chars))
        if tool_call:
            meter["tool_calls"] = int(meter.get("tool_calls", 0) or 0) + 1
        _context_recompute(state)
    state_update(mutate)


def record_action(kind: str, name: str, args: dict[str, Any] | None = None, note: str = "") -> None:
    target = extract_target(args or {}) if "extract_target" in globals() else ""
    sig_raw = json.dumps({"name": name, "args": args or {}}, ensure_ascii=True, sort_keys=True)
    sig = hashlib.sha256(sig_raw.encode("utf-8", errors="replace")).hexdigest()[:16]
    def mutate(state: dict[str, Any]):
        history = list(state.get("action_history") or [])
        history.append({
            "at": time.time(),
            "kind": kind,
            "name": name,
            "target": target,
            "sig": sig,
            "mutation_epoch": int(state.get("mutation_epoch", 0) or 0),
            "play_session": int(state.get("play_session", 0) or 0),
            "note": str(note or "")[:500],
        })
        state["action_history"] = history[-MAX_ACTION_HISTORY:]
    state_update(mutate)
    try:
        with _state_lock:
            epoch = int(STATE.get("mutation_epoch", 0) or 0)
            play_session = int(STATE.get("play_session", 0) or 0)
        helper = globals().get("telemetry_record_action")
        if callable(helper):
            helper({
                "at": time.time(),
                "kind": kind,
                "name": name,
                "target": target,
                "sig": sig,
                "mutation_epoch": epoch,
                "play_session": play_session,
                "arguments": args or {},
                "note": str(note or "")[:2000],
            })
    except Exception as exc:
        log(f"telemetry action hook failed: {exc!r}")


def reset_context_meter_for_new_chat(exact_input_tokens: int | None = None) -> None:
    def mutate(state: dict[str, Any]):
        state["context_estimate"] = {
            "mcp_chars": 0,
            "tool_calls": 0,
            "estimated_tokens": 0,
            "window_tokens": CONTEXT_WINDOW_TOKENS,
            "rollover_trigger": CONTEXT_ROLLOVER_TRIGGER,
            "handoff_recommended": False,
            "handoff_notified": False,
            "exact_input_tokens": exact_input_tokens,
        }
    state_update(mutate)


def evidence_summary(state: dict[str, Any], max_items: int = 8) -> list[str]:
    ev = state.get("runtime_evidence") or {}
    details = ev.get("details") if isinstance(ev, dict) else {}
    rows: list[tuple[float, str]] = []
    if isinstance(details, dict):
        for key, item in details.items():
            if not isinstance(item, dict):
                continue
            summary = str(item.get("summary") or "").strip()
            if not summary:
                continue
            rows.append((float(item.get("observed_at") or 0.0), f"{key}: {summary}"))
    rows.sort(key=lambda x: x[0], reverse=True)
    return [row[1] for row in rows[:max_items]]


def next_required_action_from_state(state: dict[str, Any]) -> str:
    blocker = state.get("current_blocker")
    mode = state.get("studio_mode")
    if isinstance(blocker, dict):
        kind = blocker.get("classification")
        stage = blocker.get("stage")
        path = blocker.get("path") or state.get("last_script_target") or "the implicated script"
        if kind == "syntax_error":
            if mode == "play":
                return "Stop Play, then read the implicated script source."
            if stage in {"need_evidence", "need_source_read"}:
                return f"Read {path}; then make one narrow structural repair."
            if stage in {"ready_for_edit", "ready_for_repair"}:
                return f"Make one narrow syntax repair to {path}, then reread it."
        if stage == "need_evidence":
            return blocker_required_message(blocker) if "blocker_required_message" in globals() else "Gather direct evidence for the active blocker."
    gate = state.get("gate")
    if isinstance(gate, dict):
        stage = gate.get("stage")
        target = gate.get("target") or "the edited script"
        return {
            "need_reread": f"Reread {target}.",
            "repair_allowed": f"Repair only the verified structural defect in {target}, then reread.",
            "need_playtest": "Start Play.",
            "need_output": "Check Output before stopping Play or writing again.",
            "need_visual": "Capture/observe the visual result before another write.",
        }.get(stage, "Follow the active verification gate.")
    return "Continue from current verified evidence; prefer one small evidence-based action over speculation."


# -----------------------------------------------------------------------------
# V6 structured telemetry (safe side-channel for HTTPS/GitHub automation)
# -----------------------------------------------------------------------------

_telemetry_lock = threading.RLock()
_health_lock = threading.RLock()
_last_failure_fingerprint = ""

_CONTROLLER_HEALTH: dict[str, Any] = {
    "schema_version": TELEMETRY_SCHEMA_VERSION,
    "controller_started_at": time.time(),
    "controller_pid": os.getpid(),
    "controller_running": True,
    "roblox_child_pid": None,
    "roblox_child_running": False,
    "last_roblox_stderr": "",
    "last_exception": "",
    "last_health_update": time.time(),
}

_SENSITIVE_KEY_RE = re.compile(
    r"^(?:authorization|proxy_authorization|password|passwd|secret|client_secret|api[_-]?key|cookie|set_cookie|access_token|refresh_token|id_token|bearer_token|lm_studio_api_token)$",
    re.I,
)


def _telemetry_sanitize(value: Any, depth: int = 0) -> Any:
    """Make telemetry JSON-safe, bounded, and safer to expose read-only later."""
    if depth > 8:
        return "[max-depth]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) > TELEMETRY_MAX_STRING:
            return value[:TELEMETRY_MAX_STRING] + f"...[clipped {len(value) - TELEMETRY_MAX_STRING} chars]"
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            key = str(k)
            if _SENSITIVE_KEY_RE.search(key):
                out[key] = "[REDACTED]"
            else:
                out[key] = _telemetry_sanitize(v, depth + 1)
        return out
    if isinstance(value, (list, tuple, set)):
        rows = list(value)
        if len(rows) > 200:
            rows = rows[-200:]
        return [_telemetry_sanitize(v, depth + 1) for v in rows]
    return _telemetry_sanitize(str(value), depth + 1)


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(_telemetry_sanitize(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _append_jsonl(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(_telemetry_sanitize(payload), ensure_ascii=False, separators=(",", ":"))
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def update_controller_health(**fields: Any) -> None:
    try:
        with _health_lock:
            _CONTROLLER_HEALTH.update(_telemetry_sanitize(fields))
            _CONTROLLER_HEALTH["last_health_update"] = time.time()
        refresh_controller_health_file()
    except Exception as exc:
        log(f"telemetry health update failed: {exc!r}")


def controller_health_payload() -> dict[str, Any]:
    with _health_lock:
        health = copy.deepcopy(_CONTROLLER_HEALTH)
    health.update({
        "app": APP_NAME,
        "version": VERSION,
        "state_dir": str(STATE_DIR),
        "telemetry_dir": str(TELEMETRY_DIR),
        "generated_at": time.time(),
    })
    return health


def refresh_controller_health_file() -> None:
    try:
        with _telemetry_lock:
            _atomic_write_json(TELEMETRY_HEALTH_FILE, controller_health_payload())
    except Exception as exc:
        log(f"controller health telemetry write failed: {exc!r}")


def telemetry_status_payload(state: dict[str, Any] | None = None) -> dict[str, Any]:
    if state is None:
        with _state_lock:
            state = copy.deepcopy(STATE)
    history = list(state.get("action_history") or [])[-max(1, TELEMETRY_HISTORY_TAIL):]
    meter = state.get("context_estimate") or {}
    telemetry_state = state.get("telemetry") or {}
    return {
        "schema_version": TELEMETRY_SCHEMA_VERSION,
        "generated_at": time.time(),
        "app": APP_NAME,
        "version": VERSION,
        "controller_pid": os.getpid(),
        "enforcement_active": True,
        "studio_mode": state.get("studio_mode"),
        "play_session": state.get("play_session", 0),
        "mutation_epoch": state.get("mutation_epoch", 0),
        "current_blocker": state.get("current_blocker"),
        "gate": state.get("gate"),
        "last_script_target": state.get("last_script_target"),
        "last_mutation": state.get("last_mutation"),
        "blocked_count": state.get("blocked_count", 0),
        "forwarded_count": state.get("forwarded_count", 0),
        "tool_error_count": state.get("tool_error_count", 0),
        "runtime_error_count": state.get("runtime_error_count", 0),
        "failed_mutation_signatures": list(state.get("failed_mutation_signatures") or [])[-20:],
        "next_required_action": next_required_action_from_state(state),
        "verified_evidence": evidence_summary(state, max_items=12),
        "context_estimate": meter,
        "task_checkpoint": state.get("task_checkpoint") or {},
        "telemetry_state": telemetry_state,
        "action_history_tail": history,
        "files": {
            "status": str(TELEMETRY_STATUS_FILE),
            "latest_failure": str(TELEMETRY_FAILURE_FILE),
            "failure_history": str(TELEMETRY_FAILURE_HISTORY_FILE),
            "action_history": str(TELEMETRY_ACTION_HISTORY_FILE),
            "controller_health": str(TELEMETRY_HEALTH_FILE),
            "test_results": str(TELEMETRY_TEST_RESULTS_FILE),
            "autopilot_runs": str(TELEMETRY_AUTOPILOT_FILE),
            "failure_packet": str(TELEMETRY_FAILURE_PACKET_FILE),
            "regression_cases": str(TELEMETRY_REGRESSION_CASES_FILE),
            "github_reporter_status": str(TELEMETRY_GITHUB_REPORTER_FILE),
        },
    }


def refresh_telemetry_files() -> None:
    """Refresh current snapshots. Never raise into the MCP transport."""
    try:
        with _state_lock:
            state_copy = copy.deepcopy(STATE)
        with _telemetry_lock:
            _atomic_write_json(TELEMETRY_STATUS_FILE, telemetry_status_payload(state_copy))
            _atomic_write_json(TELEMETRY_HEALTH_FILE, controller_health_payload())
            if not TELEMETRY_TEST_RESULTS_FILE.exists():
                _atomic_write_json(TELEMETRY_TEST_RESULTS_FILE, {
                    "schema_version": TELEMETRY_SCHEMA_VERSION,
                    "generated_at": time.time(),
                    "controller_version": VERSION,
                    "status": "not_run",
                    "tests": [],
                })
    except Exception as exc:
        log(f"telemetry snapshot write failed: {exc!r}")


def telemetry_record_action(event: dict[str, Any]) -> None:
    try:
        payload = {
            "schema_version": TELEMETRY_SCHEMA_VERSION,
            "event": "controller_action",
            **event,
        }
        with _telemetry_lock:
            _append_jsonl(TELEMETRY_ACTION_HISTORY_FILE, payload)
        def mutate(state: dict[str, Any]):
            tel = state.setdefault("telemetry", {})
            tel["last_event_at"] = float(payload.get("at") or time.time())
        state_update(mutate)
    except Exception as exc:
        log(f"telemetry action write failed: {exc!r}")


def _failure_classification(kind: str, message: str, state: dict[str, Any]) -> str:
    low = f"{kind} {message}".lower()
    gate = state.get("gate")
    blocker = state.get("current_blocker")
    if kind in {"controller_deadlock", "controller_state_conflict", "controller_internal_error"}:
        return "controller_bug"
    if "mcp" in low or "studio" in low and "disconnect" in low:
        return "mcp_or_environment"
    if kind in {"tool_result_error", "runtime_error"} or "runtime" in low:
        return "runtime_or_tool_error"
    if isinstance(gate, dict) and isinstance(blocker, dict):
        if gate.get("stage") == "need_playtest" and blocker.get("classification") == "static_source_defect" and blocker.get("stage") == "repair_applied":
            return "controller_bug"
    if kind == "controller_block":
        return "model_or_policy_block"
    return "needs_review"


def _compact_failure_packet(kind: str, message: str, tool_name: str, arguments: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    history = list(state.get("action_history") or [])[-12:]
    classification = _failure_classification(kind, message, state)
    raw = json.dumps({
        "kind": kind,
        "classification": classification,
        "tool": tool_name,
        "blocker": state.get("current_blocker"),
        "gate": state.get("gate"),
        "tail": [(x.get("kind"), x.get("name"), x.get("sig")) for x in history if isinstance(x, dict)],
    }, ensure_ascii=True, sort_keys=True, default=str)
    regression_id = hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:20]
    return {
        "schema_version": TELEMETRY_SCHEMA_VERSION,
        "generated_at": time.time(),
        "regression_id": regression_id,
        "version": VERSION,
        "classification": classification,
        "kind": kind,
        "message": str(message)[:2000],
        "tool_name": tool_name,
        "arguments": arguments or {},
        "current_blocker": state.get("current_blocker"),
        "gate": state.get("gate"),
        "next_required_action": next_required_action_from_state(state),
        "studio_mode": state.get("studio_mode"),
        "mutation_epoch": state.get("mutation_epoch", 0),
        "play_session": state.get("play_session", 0),
        "action_history_tail": history,
        "verified_evidence": evidence_summary(state, max_items=8),
    }


def _write_failure_packet(packet: dict[str, Any], capture_regression: bool = False) -> None:
    try:
        _atomic_write_json(TELEMETRY_FAILURE_PACKET_FILE, packet)
        if capture_regression:
            with _telemetry_lock:
                existing_ids = set()
                if TELEMETRY_REGRESSION_CASES_FILE.exists():
                    try:
                        for line in TELEMETRY_REGRESSION_CASES_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]:
                            row = json.loads(line)
                            if isinstance(row, dict) and row.get("regression_id"):
                                existing_ids.add(str(row.get("regression_id")))
                    except Exception:
                        pass
                if str(packet.get("regression_id")) not in existing_ids:
                    _append_jsonl(TELEMETRY_REGRESSION_CASES_FILE, packet)
    except Exception as exc:
        log(f"failure packet write failed: {exc!r}")



_github_report_lock = threading.Lock()


def _github_should_report(packet: dict[str, Any]) -> bool:
    return (
        GITHUB_FAILURE_REPORTING
        and bool(GITHUB_FAILURE_REPO)
        and packet.get("classification") == "controller_bug"
        and bool(packet.get("regression_id"))
    )


def _github_cli_path() -> str:
    found = shutil.which("gh")
    if found:
        return found
    candidates = [
        LOCALAPPDATA / "Microsoft" / "WinGet" / "Links" / "gh.exe",
        Path(os.environ.get("ProgramFiles", "C:\\Program Files")) / "GitHub CLI" / "gh.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return ""


def _github_run(args: list[str]) -> subprocess.CompletedProcess[str]:
    kwargs: dict[str, Any] = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": max(5, GITHUB_FAILURE_TIMEOUT),
    }
    if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.run(args, **kwargs)


def _github_reporter_status(status: str, **fields: Any) -> None:
    try:
        payload = {
            "schema_version": TELEMETRY_SCHEMA_VERSION,
            "generated_at": time.time(),
            "controller_version": VERSION,
            "status": status,
            "repo": GITHUB_FAILURE_REPO,
            **fields,
        }
        _atomic_write_json(TELEMETRY_GITHUB_REPORTER_FILE, payload)
    except Exception as exc:
        log(f"github reporter status write failed: {exc!r}")


def _github_failure_issue_body(packet: dict[str, Any]) -> str:
    safe = _telemetry_sanitize(packet)
    regression_id = str(safe.get("regression_id") or "")
    summary = {
        "controller_version": safe.get("version"),
        "classification": safe.get("classification"),
        "kind": safe.get("kind"),
        "message": safe.get("message"),
        "tool_name": safe.get("tool_name"),
        "studio_mode": safe.get("studio_mode"),
        "mutation_epoch": safe.get("mutation_epoch"),
        "play_session": safe.get("play_session"),
        "next_required_action": safe.get("next_required_action"),
        "current_blocker": safe.get("current_blocker"),
        "gate": safe.get("gate"),
        "verified_evidence": safe.get("verified_evidence"),
        "action_history_tail": safe.get("action_history_tail"),
    }
    packet_json = json.dumps(summary, ensure_ascii=False, indent=2, default=str)
    return (
        f"<!-- qwen-controller-regression-id:{regression_id} -->\n"
        "# Automated controller failure\n\n"
        "This issue was created automatically by the local Qwen Roblox controller. "
        "The payload is telemetry-sanitized before upload.\n\n"
        f"- **Regression ID:** {regression_id}\n"
        f"- **Controller:** {safe.get('version')}\n"
        f"- **Classification:** {safe.get('classification')}\n"
        f"- **Kind:** {safe.get('kind')}\n\n"
        "## Failure packet\n\n"
        "~~~json\n"
        f"{packet_json}\n"
        "~~~\n"
    )


def _report_failure_to_github(packet: dict[str, Any]) -> None:
    if not _github_should_report(packet):
        return
    regression_id = str(packet.get("regression_id") or "")
    with _github_report_lock:
        gh = _github_cli_path()
        if not gh:
            _github_reporter_status(
                "gh_missing",
                regression_id=regression_id,
                detail="Install GitHub CLI and authenticate once with gh auth login.",
            )
            return

        try:
            auth = _github_run([gh, "auth", "status"])
        except Exception as exc:
            _github_reporter_status("auth_check_failed", regression_id=regression_id, detail=str(exc)[:500])
            return
        if auth.returncode != 0:
            _github_reporter_status(
                "gh_not_authenticated",
                regression_id=regression_id,
                detail="GitHub CLI is installed but not authenticated. Run gh auth login once.",
            )
            return

        try:
            existing = _github_run([
                gh, "issue", "list",
                "--repo", GITHUB_FAILURE_REPO,
                "--state", "all",
                "--search", f"{regression_id} in:body",
                "--json", "number,url,title",
                "--limit", "5",
            ])
            if existing.returncode == 0:
                rows = json.loads(existing.stdout or "[]")
                if isinstance(rows, list) and rows:
                    _github_reporter_status(
                        "already_reported",
                        regression_id=regression_id,
                        issue_url=str(rows[0].get("url") or ""),
                    )
                    return
        except Exception as exc:
            log(f"github issue dedupe search failed: {exc!r}")

        title = f"[AUTO-FAILURE] {packet.get('kind') or 'controller_bug'} [{regression_id}]"
        body = _github_failure_issue_body(packet)
        body_path = TELEMETRY_DIR / f".github_issue_{regression_id}.md"
        try:
            body_path.write_text(body, encoding="utf-8")
            args = [
                gh, "issue", "create",
                "--repo", GITHUB_FAILURE_REPO,
                "--title", title,
                "--body-file", str(body_path),
            ]
            if GITHUB_FAILURE_LABEL:
                args += ["--label", GITHUB_FAILURE_LABEL]
            created = _github_run(args)
            label_applied = bool(GITHUB_FAILURE_LABEL)
            if created.returncode != 0 and GITHUB_FAILURE_LABEL:
                created = _github_run([
                    gh, "issue", "create",
                    "--repo", GITHUB_FAILURE_REPO,
                    "--title", title,
                    "--body-file", str(body_path),
                ])
                label_applied = False

            if created.returncode == 0:
                issue_url = (created.stdout or "").strip().splitlines()[-1] if (created.stdout or "").strip() else ""
                _github_reporter_status(
                    "reported",
                    regression_id=regression_id,
                    issue_url=issue_url,
                    label_applied=label_applied,
                )
            else:
                _github_reporter_status(
                    "create_failed",
                    regression_id=regression_id,
                    detail=(created.stderr or created.stdout or "unknown gh error")[:1000],
                )
        except Exception as exc:
            _github_reporter_status("report_failed", regression_id=regression_id, detail=str(exc)[:1000])
        finally:
            try:
                body_path.unlink(missing_ok=True)
            except Exception:
                pass


def _queue_github_failure_report(packet: dict[str, Any]) -> None:
    if not _github_should_report(packet):
        return
    try:
        threading.Thread(
            target=_report_failure_to_github,
            args=(copy.deepcopy(packet),),
            daemon=True,
            name="qwen-github-failure-reporter",
        ).start()
    except Exception as exc:
        _github_reporter_status(
            "queue_failed",
            regression_id=str(packet.get("regression_id") or ""),
            detail=str(exc)[:500],
        )


def _detect_block_deadlock(reason: str, name: str, args: dict[str, Any] | None) -> tuple[str, str] | None:
    with _state_lock:
        state = copy.deepcopy(STATE)
    history = [x for x in list(state.get("action_history") or [])[-max(3, DEADLOCK_BLOCK_WINDOW):] if isinstance(x, dict)]
    blocker = state.get("current_blocker")
    gate = state.get("gate")
    if isinstance(gate, dict) and isinstance(blocker, dict):
        if (
            gate.get("stage") == "need_playtest"
            and blocker.get("classification") == "static_source_defect"
            and blocker.get("stage") == "repair_applied"
            and target_matches(blocker.get("path") or "", gate.get("target") or "")
        ):
            return ("controller_state_conflict", "Gate requires Play but a repaired static-source blocker still forbids Play for the same target.")
    blocks = [x for x in history if x.get("kind") == "block"]
    if len(blocks) >= DEADLOCK_REPEAT_LIMIT:
        epoch = blocks[-1].get("mutation_epoch")
        session = blocks[-1].get("play_session")
        same_state = [x for x in blocks if x.get("mutation_epoch") == epoch and x.get("play_session") == session]
        if len(same_state) >= DEADLOCK_REPEAT_LIMIT:
            sigs = [x.get("sig") for x in same_state[-DEADLOCK_REPEAT_LIMIT:]]
            notes = [str(x.get("note") or "") for x in same_state[-DEADLOCK_REPEAT_LIMIT:]]
            if len(set(sigs)) <= 2 or len(set(notes)) <= 2:
                return ("controller_deadlock", f"Detected {len(same_state)} blocked actions with no mutation/play progress. Stop retrying this loop and review the failure packet.")
    return None


def telemetry_record_failure(
    kind: str,
    message: str,
    *,
    tool_name: str = "",
    arguments: dict[str, Any] | None = None,
    response_excerpt: str = "",
    severity: str = "error",
    extra: dict[str, Any] | None = None,
) -> str:
    """Persist one deduplicated failure record for remote diagnosis/regression capture."""
    global _last_failure_fingerprint
    try:
        with _state_lock:
            state_copy = copy.deepcopy(STATE)
        blocker = state_copy.get("current_blocker")
        fingerprint_raw = json.dumps({
            "kind": kind,
            "message": str(message)[:2000],
            "tool": tool_name,
            "blocker": blocker,
            "mutation_epoch": state_copy.get("mutation_epoch", 0),
        }, ensure_ascii=True, sort_keys=True, default=str)
        fingerprint = hashlib.sha256(fingerprint_raw.encode("utf-8", errors="replace")).hexdigest()[:20]
        with _telemetry_lock:
            if fingerprint == _last_failure_fingerprint:
                return fingerprint
            _last_failure_fingerprint = fingerprint
            now = time.time()
            failure_id = f"fail-{int(now * 1000)}-{fingerprint[:8]}"
            payload = {
                "schema_version": TELEMETRY_SCHEMA_VERSION,
                "failure_id": failure_id,
                "at": now,
                "severity": severity,
                "kind": kind,
                "message": message,
                "tool_name": tool_name,
                "arguments": arguments or {},
                "response_excerpt": response_excerpt,
                "current_blocker": blocker,
                "gate": state_copy.get("gate"),
                "studio_mode": state_copy.get("studio_mode"),
                "play_session": state_copy.get("play_session", 0),
                "mutation_epoch": state_copy.get("mutation_epoch", 0),
                "last_script_target": state_copy.get("last_script_target"),
                "last_mutation": state_copy.get("last_mutation"),
                "next_required_action": next_required_action_from_state(state_copy),
                "action_history_tail": list(state_copy.get("action_history") or [])[-20:],
                "verified_evidence": evidence_summary(state_copy, max_items=10),
                "extra": extra or {},
            }
            _atomic_write_json(TELEMETRY_FAILURE_FILE, payload)
            _append_jsonl(TELEMETRY_FAILURE_HISTORY_FILE, payload)
        def mutate(state: dict[str, Any]):
            tel = state.setdefault("telemetry", {})
            tel["last_failure_id"] = failure_id
            tel["last_failure_at"] = now
            tel["last_failure_kind"] = kind
            tel["last_event_at"] = now
        state_update(mutate)
        packet = _compact_failure_packet(kind, message, tool_name, arguments or {}, state_copy)
        _write_failure_packet(packet, capture_regression=packet.get("classification") == "controller_bug")
        _queue_github_failure_report(packet)
        refresh_telemetry_files()
        return failure_id
    except Exception as exc:
        log(f"telemetry failure write failed: {exc!r}")
        return ""


def telemetry_record_autopilot(event: str, **fields: Any) -> None:
    try:
        payload = {
            "schema_version": TELEMETRY_SCHEMA_VERSION,
            "event": event,
            "at": time.time(),
            "controller_version": VERSION,
            **fields,
        }
        with _telemetry_lock:
            _append_jsonl(TELEMETRY_AUTOPILOT_FILE, payload)
        refresh_telemetry_files()
    except Exception as exc:
        log(f"autopilot telemetry write failed: {exc!r}")


def telemetry_write_test_results(payload: dict[str, Any]) -> None:
    try:
        body = {
            "schema_version": TELEMETRY_SCHEMA_VERSION,
            "generated_at": time.time(),
            "controller_version": VERSION,
            **payload,
        }
        with _telemetry_lock:
            _atomic_write_json(TELEMETRY_TEST_RESULTS_FILE, body)
        refresh_telemetry_files()
    except Exception as exc:
        log(f"test telemetry write failed: {exc!r}")


def build_resume_packet(state: dict[str, Any] | None = None) -> str:
    if state is None:
        with _state_lock:
            state = copy.deepcopy(STATE)
    blocker = state.get("current_blocker")
    gate = state.get("gate")
    last_mut = state.get("last_mutation")
    meter = state.get("context_estimate") or {}
    lines = [
        f"QWEN ROBLOX CONTROLLER RESUME v{VERSION}",
        "Use only mcp/qwen-roblox-enforced. Current Studio/source/tool evidence is authoritative.",
        f"Studio mode: {state.get('studio_mode', 'unknown')}",
        f"Last script target: {state.get('last_script_target') or 'none'}",
    ]
    if isinstance(blocker, dict):
        lines.append(
            "Active blocker: "
            + f"{blocker.get('classification')} at {blocker.get('path') or '?'}:{blocker.get('line') or '?'} "
            + f"stage={blocker.get('stage')} message={str(blocker.get('message') or '')[:240]}"
        )
    else:
        lines.append("Active blocker: none")
    if isinstance(gate, dict):
        lines.append(f"Verification gate: {gate.get('stage')} target={gate.get('target') or '?'}")
    else:
        lines.append("Verification gate: clear")
    if isinstance(last_mut, dict):
        lines.append(f"Last mutation: {last_mut.get('tool')} target={last_mut.get('target')} visual={last_mut.get('visual')}")
    facts = evidence_summary(state, max_items=10)
    if facts:
        lines.append("Verified evidence (do not re-inspect unless a relevant write invalidated it):")
        lines.extend(f"- {x}" for x in facts)
    lines.append("Next required action: " + next_required_action_from_state(state))
    lines.append(
        "Permanent rules: do not guess Roblox hierarchy/API; no name-keyword avatar classification; "
        "Accessory uses Handle/Attachments; BodyDepthScale is a NumberValue child; "
        "instance.OriginalSize.Value is valid when OriginalSize is a real child; current script_read is truth."
    )
    exact = meter.get("exact_input_tokens")
    if exact is None:
        lines.append(f"Context meter: ~{meter.get('estimated_tokens', 0)} heuristic tokens (MCP UI mode cannot see private reasoning/full chat).")
    else:
        lines.append(f"Context meter: {exact} exact API input tokens.")
    return "\n".join(lines)[:7000]


def refresh_checkpoint_files() -> None:
    try:
        with _state_lock:
            state_copy = copy.deepcopy(STATE)
        packet = build_resume_packet(state_copy)
        RESUME_FILE.write_text(packet + "\n", encoding="utf-8")
        CHECKPOINT_FILE.write_text(
            json.dumps({"version": VERSION, "saved_at": time.time(), "resume": packet, "state": state_copy}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        log(f"checkpoint write failed: {exc!r}")
    try:
        refresh_telemetry_files()
    except Exception as exc:
        log(f"checkpoint telemetry refresh failed: {exc!r}")


def context_handoff_note_once() -> str:
    note = ""
    def mutate(state: dict[str, Any]):
        nonlocal note
        meter = state.setdefault("context_estimate", {})
        if meter.get("handoff_recommended") and not meter.get("handoff_notified"):
            meter["handoff_notified"] = True
            note = (
                "CONTEXT CHECKPOINT: compact state saved. MCP cannot create a new LM Studio UI chat itself. "
                "In UI mode, start a fresh chat before 40k and call supervisor_resume(new_chat=true). "
                "For zero-click rollover, run this same file with --autopilot; API mode uses exact LM Studio input_tokens and starts a new stateful chat automatically."
            )
    state_update(mutate)
    if note:
        refresh_checkpoint_files()
    return note


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

MUTATION_WORDS = (
    "edit", "write", "patch", "create", "delete", "remove", "insert", "set_",
    "rename", "move", "clone", "duplicate", "replace", "apply",
)

READ_EVIDENCE_TOOLS = {
    "script_read",
    "read_script_range",
    "inspect_instance",
    "get_instance_properties",
    "get_attributes",
    "search_game_tree",
    "find_instances",
    "script_search",
    "api_get_class_schema",
    "api_get_member_schema",
    "api_search_surface",
    "get_studio_state",
}

OUTPUT_TOOLS = {"get_console_output", "get_output_log"}
VISUAL_TOOLS = {"screen_capture", "agent_observe"}
PLAY_TOOLS = {"start_stop_play"}

# These are treated as script mutations even if the name classifier changes later.
SCRIPT_MUTATION_NAMES = {
    "multi_edit",
    "patch_script",
    "script_edit",
    "script_write",
    "write_script",
    "replace_script",
}

# Common Roblox tools which can mutate state but should not be mistaken for reads.
KNOWN_MUTATION_NAMES = {
    "create_instances",
    "set_instance_properties",
    "api_set_property",
    "api_invoke_method",
    "execute_luau",
    "execute_scene_phase",
}


def norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip()).lower()


def clip(text: Any, limit: int = 1200) -> str:
    value = str(text or "")
    return value if len(value) <= limit else value[: limit - 20] + " ...[clipped]"


def json_text(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except Exception:
        return str(value)


def tool_is_mutation(name: str, args: dict[str, Any] | None = None) -> bool:
    n = (name or "").lower()
    if n in READ_EVIDENCE_TOOLS or n in OUTPUT_TOOLS or n in VISUAL_TOOLS or n in PLAY_TOOLS:
        return False
    if n in SCRIPT_MUTATION_NAMES or n in KNOWN_MUTATION_NAMES:
        return True
    if any(word in n for word in MUTATION_WORDS):
        return True
    # execute-style tools are generally mutating unless clearly named get/read/search.
    if n.startswith("execute_"):
        return True
    return False


def tool_is_script_mutation(name: str, args: dict[str, Any] | None = None) -> bool:
    n = (name or "").lower()
    if n in SCRIPT_MUTATION_NAMES:
        return True
    # Some MCP versions may expose generic edit/write names.
    if ("script" in n and any(x in n for x in ("edit", "write", "patch", "replace"))):
        return True
    # execute_luau can rewrite Source. Detect that specifically.
    if n == "execute_luau":
        t = norm(json_text(args or {}))
        if ".source" in t or "source =" in t or "source=" in t:
            return True
    return False


def extract_target(args: dict[str, Any] | None) -> str:
    if not isinstance(args, dict):
        return ""
    preferred = (
        "target_file", "file_path", "script_path", "target_path", "path", "instance_path",
        "file", "script", "target",
    )
    for key in preferred:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    # Search one level deep for common edit payloads.
    for value in args.values():
        if isinstance(value, dict):
            found = extract_target(value)
            if found:
                return found
    return ""


def mutation_signature(name: str, args: dict[str, Any] | None) -> str:
    raw = json_text({"tool": name, "arguments": args or {}})
    # Exact deterministic signature is enough; no crypto dependency needed.
    return re.sub(r"\s+", "", raw)[:12000]


def payload_strings(value: Any) -> list[str]:
    out: list[str] = []
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for v in value.values():
            out.extend(payload_strings(v))
    elif isinstance(value, list):
        for v in value:
            out.extend(payload_strings(v))
    return out


def joined_payload(args: dict[str, Any] | None) -> str:
    return "\n".join(payload_strings(args or {}))


def proposed_payload_strings(value: Any, parent_key: str = "") -> list[str]:
    """Extract proposed/new mutation content while ignoring old/search match text.

    Edit tools commonly send both old_text and new_text.  The old text may contain
    the exact bug we are trying to remove, so policy checks must not reject a good
    repair merely because the match side contains a known-bad pattern.
    """
    excluded = {
        "old", "old_text", "old_string", "old_code", "original", "before",
        "search", "find", "match", "expected", "needle", "from",
    }
    preferred = {
        "new", "new_text", "new_string", "new_code", "replacement", "replace_with",
        "code", "source", "content", "text", "value", "script_source",
    }
    key = (parent_key or "").lower()
    if key in excluded or key.startswith("old_") or key.startswith("search_"):
        return []
    if isinstance(value, str):
        return [value]
    out: list[str] = []
    if isinstance(value, dict):
        # Prefer explicitly proposed fields when present at this level.
        present_preferred = [k for k in value if str(k).lower() in preferred]
        if present_preferred:
            for k in present_preferred:
                out.extend(proposed_payload_strings(value[k], str(k)))
            # Still walk nested edit arrays/objects, but not unrelated scalar metadata.
            for k, v in value.items():
                if k in present_preferred:
                    continue
                if isinstance(v, (dict, list)):
                    out.extend(proposed_payload_strings(v, str(k)))
            return out
        for k, v in value.items():
            out.extend(proposed_payload_strings(v, str(k)))
    elif isinstance(value, list):
        for v in value:
            out.extend(proposed_payload_strings(v, key))
    return out


def proposed_payload(args: dict[str, Any] | None) -> str:
    values = proposed_payload_strings(args or {})
    # Fallback for an unknown mutation schema, but still exclude obvious old/search keys.
    if not values:
        values = payload_strings(args or {})
    return "\n".join(values)




def canonical_target(target: str) -> str:
    return norm(target).replace("game.", "").strip()


def normalize_source(source: str) -> str:
    text = (source or "").replace("\r\n", "\n").replace("\r", "\n")
    # Preserve semantic whitespace but ignore trailing spaces and a final newline.
    return "\n".join(line.rstrip() for line in text.split("\n")).rstrip("\n")


def source_hash(source: str) -> str:
    return hashlib.sha256(normalize_source(source).encode("utf-8", errors="replace")).hexdigest()


def extract_script_source(text: str) -> str:
    """Convert official script_read numbered output back into plain source."""
    if not text:
        return ""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    saw_numbered = False
    for line in lines:
        m = re.match(r"^\s*\d+\s*[→>](.*)$", line)
        if m:
            saw_numbered = True
            out.append(m.group(1))
        elif saw_numbered:
            # script_read results are normally entirely numbered; keep any continuation
            # text that is not a supervisor note.
            if line.startswith("SUPERVISOR NOTE"):
                break
            out.append(line)
    if saw_numbered:
        return normalize_source("\n".join(out))
    return normalize_source(text)


def source_cache_get(target: str) -> str:
    wanted = canonical_target(target)
    if not wanted:
        return ""
    for key, value in list(SOURCE_CACHE.items()):
        k = canonical_target(key)
        if k == wanted or k.endswith(wanted) or wanted.endswith(k) or k.split(".")[-1] == wanted.split(".")[-1]:
            return value
    return ""


def source_cache_set(target: str, source: str) -> None:
    if target and source:
        SOURCE_CACHE[target] = normalize_source(source)


def edit_pairs(args: dict[str, Any] | None) -> list[tuple[str, str]]:
    """Extract old/new string replacements from common official multi_edit payloads."""
    args = args or {}
    edits = args.get("edits")
    if not isinstance(edits, list):
        return []
    pairs: list[tuple[str, str]] = []
    old_keys = ("old_string", "old_text", "old", "search", "find")
    new_keys = ("new_string", "new_text", "new", "replacement", "replace_with")
    for item in edits:
        if not isinstance(item, dict):
            continue
        old = None
        new = None
        for k in old_keys:
            if isinstance(item.get(k), str):
                old = item[k]
                break
        for k in new_keys:
            if isinstance(item.get(k), str):
                new = item[k]
                break
        if old is not None and new is not None:
            pairs.append((old, new))
    return pairs


LUA_DIRECT_CALL_BUILTINS = {
    "assert", "error", "getmetatable", "ipairs", "next", "pairs", "pcall", "print",
    "rawequal", "rawget", "rawset", "require", "select", "setmetatable", "tonumber",
    "tostring", "type", "typeof", "unpack", "warn", "wait", "spawn", "delay",
}


def local_helper_defs(source: str) -> set[str]:
    defs = set(re.findall(r"\blocal\s+function\s+([A-Za-z_]\w*)\s*\(", source))
    defs.update(re.findall(r"\blocal\s+([A-Za-z_]\w*)\s*=\s*function\b", source))
    # Forward-declared local helper assigned later: local foo ... foo = function(...)
    forward = set(re.findall(r"\blocal\s+([A-Za-z_]\w*)\s*(?:;|\n|$)", source))
    assigned = set(re.findall(r"(?m)^\s*([A-Za-z_]\w*)\s*=\s*function\b", source))
    defs.update(forward & assigned)
    return defs


def direct_function_calls(source: str) -> set[str]:
    # Ignore method/property calls (obj:foo(), obj.foo()) and function declarations.
    calls: set[str] = set()
    for m in re.finditer(r"(?<![\.:])\b([A-Za-z_]\w*)\s*\(", source):
        name = m.group(1)
        prefix = source[max(0, m.start()-24):m.start()]
        if re.search(r"function\s+$", prefix):
            continue
        if name in {"if", "for", "while", "function", "return"}:
            continue
        calls.add(name)
    return calls


def introduced_direct_calls(args: dict[str, Any] | None) -> set[str]:
    text = proposed_payload(args)
    return direct_function_calls(text)


def _strip_luau_noncode(source: str) -> str:
    """Best-effort lexer that removes comments/strings while preserving newlines.

    It is deliberately conservative: the goal is not to fully parse Luau, only to
    catch obvious partial-edit block imbalance before broken source reaches Studio.
    """
    s = source or ""
    out: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        nxt = s[i + 1] if i + 1 < n else ""
        # Line/block comments.
        if ch == "-" and nxt == "-":
            if i + 3 < n and s[i + 2:i + 4] == "[[":
                out.extend("    ")
                i += 4
                while i < n and s[i:i + 2] != "]]":
                    out.append("\n" if s[i] == "\n" else " ")
                    i += 1
                if i < n:
                    out.extend("  ")
                    i += 2
                continue
            out.extend("  ")
            i += 2
            while i < n and s[i] != "\n":
                out.append(" ")
                i += 1
            continue
        # Quoted strings.
        if ch in {"'", '"'}:
            quote = ch
            out.append(" ")
            i += 1
            escaped = False
            while i < n:
                c = s[i]
                if c == "\n":
                    out.append("\n")
                else:
                    out.append(" ")
                if escaped:
                    escaped = False
                elif c == "\\":
                    escaped = True
                elif c == quote:
                    i += 1
                    break
                i += 1
            continue
        # Simple long-bracket string [[...]].
        if s[i:i + 2] == "[[":
            out.extend("  ")
            i += 2
            while i < n and s[i:i + 2] != "]]":
                out.append("\n" if s[i] == "\n" else " ")
                i += 1
            if i < n:
                out.extend("  ")
                i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)



LUA_GLOBAL_CALLS = LUA_DIRECT_CALL_BUILTINS | {
    "collectgarbage", "gcinfo", "getfenv", "setfenv", "loadstring", "newproxy",
    "xpcall", "tick", "time", "elapsedTime", "settings", "UserSettings", "version",
}


def luau_lexical_defects(source: str) -> list[str]:
    """High-confidence lexical/delimiter checks without requiring a Luau runtime."""
    defects: list[str] = []
    s = source or ""
    stack: list[tuple[str, int, int]] = []
    pairs = {')': '(', ']': '[', '}': '{'}
    i = 0
    line = 1
    col = 1
    n = len(s)

    def adv(ch: str) -> None:
        nonlocal line, col
        if ch == "\n":
            line += 1
            col = 1
        else:
            col += 1

    def long_bracket_at(idx: int) -> tuple[int, str] | None:
        if idx >= n or s[idx] != '[':
            return None
        j = idx + 1
        while j < n and s[j] == '=':
            j += 1
        if j < n and s[j] == '[':
            eq = j - idx - 1
            return (j - idx + 1, ']' + ('=' * eq) + ']')
        return None

    while i < n:
        ch = s[i]
        nxt = s[i + 1] if i + 1 < n else ''

        # Comments, including generalized --[=[...]=] blocks.
        if ch == '-' and nxt == '-':
            lb = long_bracket_at(i + 2)
            if lb:
                opener_len, closer = lb
                start_line = line
                for c in s[i:i + 2 + opener_len]:
                    adv(c)
                i += 2 + opener_len
                k = s.find(closer, i)
                if k < 0:
                    defects.append(f"unclosed block comment starting at line {start_line}")
                    break
                for c in s[i:k + len(closer)]:
                    adv(c)
                i = k + len(closer)
                continue
            while i < n and s[i] != '\n':
                adv(s[i]); i += 1
            continue

        # Quoted strings.
        if ch in ('\"', "'"):
            quote = ch
            start_line = line
            adv(ch); i += 1
            escaped = False
            closed = False
            while i < n:
                c = s[i]
                if c == '\n' and not escaped:
                    defects.append(f"newline before closing quoted string starting at line {start_line}")
                    break
                adv(c); i += 1
                if escaped:
                    escaped = False
                elif c == '\\':
                    escaped = True
                elif c == quote:
                    closed = True
                    break
            if not closed and not any(f"starting at line {start_line}" in d for d in defects):
                defects.append(f"unclosed quoted string starting at line {start_line}")
            continue

        # Long-bracket strings.
        lb = long_bracket_at(i)
        if lb:
            opener_len, closer = lb
            start_line = line
            for c in s[i:i + opener_len]:
                adv(c)
            i += opener_len
            k = s.find(closer, i)
            if k < 0:
                defects.append(f"unclosed long-bracket string starting at line {start_line}")
                break
            for c in s[i:k + len(closer)]:
                adv(c)
            i = k + len(closer)
            continue

        if ch in '([{':
            stack.append((ch, line, col))
        elif ch in ')]}':
            if not stack or stack[-1][0] != pairs[ch]:
                defects.append(f"mismatched delimiter {ch!r} at line {line}, column {col}")
            else:
                stack.pop()
        adv(ch); i += 1

    for opener, ln, cl in stack[-6:]:
        defects.append(f"unclosed delimiter {opener!r} from line {ln}, column {cl}")
    return defects[:10]


def luau_block_stack_defects(source: str) -> list[str]:
    """Line-aware statement block stack for missing/extra end/until mistakes.

    It recognizes inline Roblox/Luau callbacks such as
    `signal:Connect(function() if ok then ... end end)` while excluding the
    common Luau conditional-expression forms (`x = if ... then ... else ...`).
    """
    code = _strip_luau_noncode(source)
    stack: list[tuple[str, int]] = []
    defects: list[str] = []

    for lineno, raw in enumerate(code.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue

        opens: list[str] = []
        function_count = 0 if re.match(r"^\s*type\b", line) else len(re.findall(r"\bfunction\b", line))
        opens.extend(["function"] * function_count)

        # Count all if...then forms on the line, then subtract obvious Luau
        # conditional expressions. `elseif` is a distinct token and is not counted.
        all_ifs = len(re.findall(r"(?<!else)\bif\b[^;\n]*?\bthen\b", line))
        expr_ifs = len(re.findall(r"(?:=|\(|,|\breturn\b)\s*if\b[^;\n]*?\bthen\b", line))
        opens.extend(["if"] * max(0, all_ifs - expr_ifs))

        for_count = len(re.findall(r"\bfor\b[^;\n]*?\bdo\b", line))
        while_count = len(re.findall(r"\bwhile\b[^;\n]*?\bdo\b", line))
        opens.extend(["for"] * for_count)
        opens.extend(["while"] * while_count)
        repeat_count = len(re.findall(r"\brepeat\b", line))
        opens.extend(["repeat"] * repeat_count)
        if re.match(r"^do\s*(?:;|$)", line):
            opens.append("do")

        for kind in opens:
            stack.append((kind, lineno))

        closers = [(m.start(), m.group(0)) for m in re.finditer(r"\bend\b|\buntil\b", line)]
        for _, token in sorted(closers):
            if token == "until":
                if not stack:
                    defects.append(f"unexpected 'until' at line {lineno}")
                else:
                    idx = next((i for i in range(len(stack)-1, -1, -1) if stack[i][0] == 'repeat'), -1)
                    if idx < 0:
                        defects.append(f"'until' at line {lineno} has no matching repeat")
                    elif idx != len(stack)-1:
                        kind, opened = stack[-1]
                        defects.append(f"'until' at line {lineno} crosses open {kind} from line {opened}")
                    else:
                        stack.pop()
            else:
                if not stack:
                    defects.append(f"unexpected 'end' at line {lineno}")
                else:
                    stack.pop()

    for kind, lineno in stack[-8:]:
        closer = "until" if kind == "repeat" else "end"
        defects.append(f"unclosed {kind} block from line {lineno}; expected '{closer}'")
    return defects[:12]

def luau_declared_identifiers(source: str) -> set[str]:
    code = _strip_luau_noncode(source)
    names: set[str] = set()
    names.update(re.findall(r"\blocal\s+function\s+([A-Za-z_]\w*)\s*\(", code))
    names.update(re.findall(r"(?m)^\s*function\s+([A-Za-z_]\w*)\s*\(", code))
    names.update(re.findall(r"\blocal\s+([A-Za-z_]\w*)\s*=\s*function\b", code))
    names.update(re.findall(r"(?m)^\s*([A-Za-z_]\w*)\s*=\s*function\b", code))

    # General local declarations, including `local a, b = ...`.
    for m in re.finditer(r"(?m)^\s*local\s+([^\n=]+?)(?:\s*=|$)", code):
        chunk = m.group(1)
        if "function" in chunk:
            continue
        for part in chunk.split(','):
            ident = re.match(r"\s*([A-Za-z_]\w*)", part)
            if ident:
                names.add(ident.group(1))

    # for-loop variables.
    for m in re.finditer(r"(?m)^\s*for\s+(.+?)\s+in\s+", code):
        for part in m.group(1).split(','):
            ident = re.match(r"\s*([A-Za-z_]\w*)", part)
            if ident:
                names.add(ident.group(1))
    for m in re.finditer(r"(?m)^\s*for\s+([A-Za-z_]\w*)\s*=", code):
        names.add(m.group(1))

    # Function parameters; a global set is conservative and avoids false positives
    # from scope analysis while still catching truly undeclared bare helpers.
    for m in re.finditer(r"\bfunction(?:\s+[A-Za-z_]\w*)?\s*\(([^)]*)\)", code):
        for raw in m.group(1).split(','):
            ident = re.match(r"\s*([A-Za-z_]\w*)", raw)
            if ident and ident.group(1) != "...":
                names.add(ident.group(1))
    return names


def luau_symbol_defects(source: str) -> list[str]:
    if not V5_STRICT_UNDEFINED_CALLS:
        return []
    code = _strip_luau_noncode(source)
    declared = luau_declared_identifiers(source)
    declared |= set(re.findall(r"\blocal\s+function\s+([A-Za-z_]\w*)", code))
    defects: list[str] = []

    for m in re.finditer(r"(?<![\.:])\b([A-Za-z_]\w*)\s*\(", code):
        name = m.group(1)
        prefix = code[max(0, m.start()-40):m.start()]
        if re.search(r"\bfunction(?:\s+[A-Za-z_]\w*)?\s*$", prefix):
            continue
        if name in {"if", "for", "while", "function", "return", "typeof"}:
            continue
        if name in LUA_GLOBAL_CALLS or name in declared:
            continue
        # Uppercase identifiers are commonly constructors/modules supplied by a
        # framework; require stronger evidence before rejecting them.
        if name[:1].isupper():
            continue
        line = code.count('\n', 0, m.start()) + 1
        defects.append(f"bare call '{name}(...)' at line {line} has no declaration/parameter/known Luau global in this script")
    return sorted(set(defects))[:10]




def luau_operator_syntax_defects(source: str) -> list[str]:
    """Catch common non-Luau operators/statement forms models hallucinate."""
    code = _strip_luau_noncode(source)
    defects: list[str] = []
    checks = (
        (r":=", "invalid ':=' operator; Luau assignment uses '='"),
        (r"!=", "invalid '!=' operator; Luau inequality uses '~='"),
        (r"&&", "invalid '&&' operator; Luau uses 'and'"),
        (r"\|\|", "invalid '||' operator; Luau uses 'or'"),
        (r"===", "invalid '===' operator; Luau equality uses '=='"),
        (r"!==", "invalid '!==' operator; Luau inequality uses '~='"),
        (r"\+\+", "invalid '++' operator; use += 1 or explicit addition"),
        (r"\bfor\s*\(", "C/JavaScript-style for(...) syntax is not Luau"),
    )
    for pattern, message in checks:
        m = re.search(pattern, code)
        if m:
            line = code.count("\n", 0, m.start()) + 1
            defects.append(f"{message} at line {line}")
    return defects[:10]

def luau_local_order_defects(source: str) -> list[str]:
    """Catch local helper references that occur before the local enters scope."""
    code = _strip_luau_noncode(source)
    defects: list[str] = []
    decl_pos: dict[str, int] = {}
    for pat in (
        r"\blocal\s+function\s+([A-Za-z_]\w*)\s*\(",
        r"\blocal\s+([A-Za-z_]\w*)\s*=\s*function\b",
        r"(?m)^\s*local\s+([A-Za-z_]\w*)\s*(?:;|$)",
    ):
        for dm in re.finditer(pat, code):
            name = dm.group(1)
            decl_pos[name] = min(decl_pos.get(name, dm.start()), dm.start())

    for cm in re.finditer(r"(?<![\.:])\b([A-Za-z_]\w*)\s*\(", code):
        name = cm.group(1)
        if name not in decl_pos or cm.start() >= decl_pos[name]:
            continue
        prefix = code[max(0, cm.start()-50):cm.start()]
        if re.search(r"\bfunction(?:\s+[A-Za-z_]\w*)?\s*$", prefix):
            continue
        line = code.count("\n", 0, cm.start()) + 1
        decl_line = code.count("\n", 0, decl_pos[name]) + 1
        defects.append(
            f"local helper '{name}(...)' is referenced at line {line} before its local declaration at line {decl_line}; "
            "move the declaration earlier or forward-declare the local before the referencing function"
        )
    return defects[:10]

def luau_instance_value_defects(source: str) -> list[str]:
    """Catch high-confidence Instance-vs-value mistakes such as FindFirstChild().X."""
    code = _strip_luau_noncode(source)
    defects: list[str] = []
    lines = code.splitlines()
    instance_methods = (
        "FindFirstChild", "WaitForChild", "FindFirstChildWhichIsA", "FindFirstChildOfClass",
        "FindFirstAncestor", "FindFirstAncestorWhichIsA", "FindFirstAncestorOfClass", "GetService",
    )
    meth = "|".join(instance_methods)
    assign_re = re.compile(rf"\blocal\s+([A-Za-z_]\w*)\s*=\s*[^\n]*?(?::|\.)\s*(?:{meth})\s*\(")

    for i, line in enumerate(lines):
        m = assign_re.search(line)
        if not m:
            continue
        var = m.group(1)
        # Check a narrow forward window and stop if the variable is reassigned.
        for j in range(i + 1, min(len(lines), i + 14)):
            row = lines[j]
            if re.search(rf"^\s*{re.escape(var)}\s*=", row):
                break
            bad = re.search(rf"\b{re.escape(var)}\s*\.\s*(X|Y|Z|Magnitude|Unit)\b", row)
            if bad:
                defects.append(
                    f"'{var}' comes from an Instance-returning lookup at line {i+1} but is used as value member .{bad.group(1)} at line {j+1}; "
                    "inspect its class and use the appropriate value property (for example Vector3Value.Value.X)"
                )
                break
    return defects[:8]


def changed_line_ratio(previous: str, candidate: str) -> float:
    a = normalize_source(previous).splitlines()
    b = normalize_source(candidate).splitlines()
    if not a and not b:
        return 0.0
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    unchanged = sum(block.size for block in sm.get_matching_blocks())
    base = max(len(a), len(b), 1)
    return max(0.0, min(1.0, 1.0 - (unchanged / base)))


def source_transaction_defects(candidate: str, previous: str, name: str, args: dict[str, Any] | None) -> list[str]:
    defects: list[str] = []
    if len(candidate.encode('utf-8', errors='replace')) > V5_MAX_SOURCE_BYTES:
        defects.append(f"candidate source exceeds V5 safety limit of {V5_MAX_SOURCE_BYTES} bytes")
    defects.extend(luau_lexical_defects(candidate))
    defects.extend(luau_block_stack_defects(candidate))
    defects.extend(luau_symbol_defects(candidate))
    defects.extend(luau_instance_value_defects(candidate))

    # Atomicity/destructive-diff guard for patch-style edits. Full explicit source
    # replacements are permitted but still pass compiler checks.
    n = (name or '').lower()
    if previous and n in SCRIPT_MUTATION_NAMES and n not in {"script_write", "write_script", "replace_script"}:
        ratio = changed_line_ratio(previous, candidate)
        if ratio > V5_MAX_CHANGED_LINE_RATIO:
            defects.append(
                f"atomic edit changes about {ratio:.0%} of script lines (limit {V5_MAX_CHANGED_LINE_RATIO:.0%}); "
                "split the repair into a smaller exact edit that preserves unrelated working code"
            )
    return defects[:16]

def approx_luau_block_balance(source: str) -> int:
    """Approximate statement block balance. 0 is expected for normal full source.

    Conditional *expressions* (`local x = if ... then ... else ...`) are not
    counted because only line-leading `if` statements are recognized.
    """
    code = _strip_luau_noncode(source)
    opens = 0
    closes = 0
    opens += len(re.findall(r"\bfunction\b", code))
    opens += len(re.findall(r"(?m)^\s*if\b[^\n]*\bthen\b", code))
    opens += len(re.findall(r"(?m)^\s*for\b[^\n]*\bdo\b", code))
    opens += len(re.findall(r"(?m)^\s*while\b[^\n]*\bdo\b", code))
    opens += len(re.findall(r"(?m)^\s*repeat\b", code))
    opens += len(re.findall(r"(?m)^\s*do\s*(?:--.*)?$", code))
    closes += len(re.findall(r"\bend\b", code))
    closes += len(re.findall(r"(?m)^\s*until\b", code))
    return opens - closes


def block_balance_defect(candidate: str, previous: str = "") -> str | None:
    cb = approx_luau_block_balance(candidate)
    if not previous:
        return f"approximate Luau block balance is {cb}, expected 0" if cb != 0 else None
    pb = approx_luau_block_balance(previous)
    # If the old source looked balanced, do not allow a replacement to make it
    # obviously unbalanced. If the old source is already broken, allow edits
    # that move the balance toward zero (syntax-repair exception).
    if pb == 0 and cb != 0:
        return f"edit would change Luau block balance from 0 to {cb} (likely missing/extra end)"
    if pb != 0 and abs(cb) > abs(pb):
        return f"edit would worsen existing Luau block imbalance from {pb} to {cb}"
    return None




def avatar_name_classification_detected(text: str) -> bool:
    """Detect name-based *geometry classification* without banning normal player/object name logic."""
    lines = (text or "").lower().splitlines()
    geometry_terms = ("basepart", "accessory", "handle", "head", "torso", "arm", "leg", "hand", "foot", "bodypart", "body part", "flatten", "flat_depth")
    for i, line in enumerate(lines):
        if not re.search(r"\.\s*name\s*(?:==|~=)\s*[\"'][^\"']+[\"']", line):
            continue
        window = " ".join(lines[max(0, i-2):min(len(lines), i+3)])
        if any(term in line for term in geometry_terms) or any(term in window for term in ("flatten", "bodypart", "accessory", "basepart")):
            return True
    return False

def source_policy_defects(source: str) -> list[str]:
    """High-confidence Roblox/project semantic mistakes visible in complete source.

    This intentionally inspects raw source so string-literal member/name tests remain
    visible. The checks are narrow enough that an occasional commented example is
    safer to block than a known-bad avatar/API pattern reaching Studio.
    """
    low = (source or "").lower()
    defects: list[str] = []
    avatar_context = any(k in low for k in ("flatten", "accessory", "humanoid", "bodypart", "body part", "basepart", "character", "head", "torso"))
    if avatar_context and avatar_name_classification_detected(source):
        defects.append("avatar geometry is classified with Instance.Name equality instead of class/hierarchy/identity")
    if re.search(r"getattribute\s*\(\s*[\"']bodydepthscale[\"']\s*\)", low):
        defects.append("BodyDepthScale is treated as an Attribute instead of a child NumberValue")
    if re.search(r"\bhumanoid\s*\.\s*bodydepthscale\s*=", low):
        defects.append("Humanoid.BodyDepthScale is assigned directly instead of BodyDepthScale.Value")
    if re.search(r"\b(?:accessory|acc)\w*\s*\.\s*rootpart\b", low):
        defects.append("Accessory.RootPart is referenced even though it is not the verified accessory structure")
    if "accessory" in low and re.search(r"\.\s*primarypart\b", low):
        defects.append("Accessory.PrimaryPart is referenced instead of the verified Handle structure")
    if re.search(r"\bIsA\s*\(\s*[\"\']HumanoidRootPart[\"\']\s*\)", source, re.I):
        defects.append("HumanoidRootPart is an instance name, not a Roblox class; do not use IsA(\"HumanoidRootPart\")")
    if "accessory" in low and re.search(r"\bhandle\s*\.\s*originalsize\s*\.\s*value\b", low):
        defects.append("Accessory Handle.OriginalSize is indexed without first proving the child exists; use FindFirstChild and verify Vector3Value before .Value")
    return defects[:10]


def raw_static_source_defects(source: str) -> list[str]:
    defects: list[str] = []
    defects.extend(luau_lexical_defects(source))
    defects.extend(luau_block_stack_defects(source))
    defects.extend(luau_symbol_defects(source))
    defects.extend(luau_operator_syntax_defects(source))
    defects.extend(luau_local_order_defects(source))
    defects.extend(luau_instance_value_defects(source))
    defects.extend(source_policy_defects(source))
    names = re.findall(r"\blocal\s+function\s+([A-Za-z_]\w*)\s*\(", _strip_luau_noncode(source))
    for name in sorted(set(names)):
        if names.count(name) > 1:
            defects.append(f"duplicate local function definition '{name}'")
    out: list[str] = []
    seen: set[str] = set()
    for item in defects:
        if item and item not in seen:
            seen.add(item); out.append(item)
    return out[:20]


def defect_key(defect: str) -> str:
    low = norm(defect)
    m = re.search(r"bare call '([a-z_]\w*)", low)
    if m:
        return "undefined_call:" + m.group(1)
    m = re.search(r"local helper '([a-z_]\w*)\(\.\.\.\)' is referenced", low)
    if m:
        return "local_order:" + m.group(1)
    m = re.search(r"'([a-z_]\w*)' comes from an instance-returning", low)
    if m:
        return "instance_value:" + m.group(1)
    if "unclosed" in low and "block" in low:
        # Keep block kind but not line number.
        m = re.search(r"unclosed ([a-z_]+) block", low)
        return "unclosed_block:" + (m.group(1) if m else "unknown")
    if "expected 0" in low and "block balance" in low:
        return "block_balance"
    if "invalid ':=' operator" in low:
        return "invalid_operator::="
    if "invalid '!=' operator" in low or "invalid '!==' operator" in low:
        return "invalid_operator:inequality"
    if "invalid '&&' operator" in low:
        return "invalid_operator:and"
    if "invalid '||' operator" in low:
        return "invalid_operator:or"
    if "invalid '===' operator" in low:
        return "invalid_operator:equality"
    if "invalid '++' operator" in low:
        return "invalid_operator:increment"
    if "style for" in low:
        return "invalid_syntax:c_for"
    if "unclosed delimiter" in low:
        return "unclosed_delimiter:" + (re.search(r"delimiter '([^']+)'", low).group(1) if re.search(r"delimiter '([^']+)'", low) else "unknown")
    if "mismatched delimiter" in low:
        return "mismatched_delimiter"
    if "avatar geometry is classified" in low:
        return "avatar_name_classification"
    if "bodydepthscale" in low and "attribute" in low:
        return "bodydepthscale_attribute"
    if "bodydepthscale" in low and "assigned directly" in low:
        return "bodydepthscale_direct_assignment"
    if "accessory.rootpart" in low:
        return "accessory_rootpart"
    if "accessory.primarypart" in low:
        return "accessory_primarypart"
    if "duplicate local function" in low:
        m = re.search(r"'([^']+)'", low)
        return "duplicate_function:" + (m.group(1) if m else low)
    # Remove volatile line/column numbers.
    return re.sub(r"\b(?:line|column)\s+\d+\b", "", low)

def structural_source_defects(candidate: str, previous: str = "", args: dict[str, Any] | None = None, tool_name: str = "") -> list[str]:
    """V5 compiler/static checks with defect-debt repair semantics.

    Clean source may not become defective. Already-broken source may be repaired
    incrementally, but every transaction must strictly reduce existing defect debt
    and may not introduce a new defect category.
    """
    candidate_static = raw_static_source_defects(candidate)
    previous_static = raw_static_source_defects(previous) if previous else []
    defects: list[str] = []

    if previous_static:
        prev_by_key = {defect_key(d): d for d in previous_static}
        cand_by_key = {defect_key(d): d for d in candidate_static}
        introduced = [cand_by_key[k] for k in cand_by_key.keys() - prev_by_key.keys()]
        if introduced:
            defects.extend(introduced)
        elif len(cand_by_key) >= len(prev_by_key):
            # Unrelated edits are not allowed while the source already owes repairs.
            defects.append(
                "existing source already has static defect debt and this transaction does not reduce it; "
                "repair at least one existing defect before making semantic/unrelated edits"
            )
        # If defect count strictly decreases and no new category appears, the repair
        # is allowed even though other pre-existing defects remain for later passes.
    else:
        defects.extend(candidate_static)

    # A removed helper that is still called is a transaction-specific regression.
    current_defs = local_helper_defs(candidate)
    current_calls = direct_function_calls(candidate)
    previous_defs = local_helper_defs(previous) if previous else set()
    for name in sorted((previous_defs - current_defs) & current_calls):
        defects.append(f"stale call to removed local helper '{name}(...)'")

    # Destructive diff/size checks are independent of static syntax debt.
    if len(candidate.encode('utf-8', errors='replace')) > V5_MAX_SOURCE_BYTES:
        defects.append(f"candidate source exceeds V5 safety limit of {V5_MAX_SOURCE_BYTES} bytes")
    n = (tool_name or '').lower()
    if previous and n in SCRIPT_MUTATION_NAMES and n not in {"script_write", "write_script", "replace_script"}:
        ratio = changed_line_ratio(previous, candidate)
        if ratio > V5_MAX_CHANGED_LINE_RATIO:
            defects.append(
                f"atomic edit changes about {ratio:.0%} of script lines (limit {V5_MAX_CHANGED_LINE_RATIO:.0%}); "
                "split the repair into a smaller exact edit that preserves unrelated working code"
            )

    out: list[str] = []
    seen: set[str] = set()
    for item in defects:
        if item and item not in seen:
            seen.add(item); out.append(item)
    return out[:16]

def _find_full_source_payload(name: str, args: dict[str, Any] | None) -> str | None:
    """Extract a complete replacement source only for explicit write/replace tools."""
    n = (name or '').lower()
    if n not in {"script_write", "write_script", "replace_script"}:
        return None
    args = args or {}
    for key in ("source", "script_source", "content", "text", "new_source"):
        value = args.get(key)
        if isinstance(value, str):
            return value
    return None


def _generic_edit_pairs(args: dict[str, Any] | None) -> list[tuple[str, str]]:
    """Find exact old/new replacements in common nested edit/patch schemas."""
    pairs = edit_pairs(args)
    if pairs:
        return pairs
    out: list[tuple[str, str]] = []
    old_keys = ("old_string", "old_text", "old", "search", "find", "match", "expected")
    new_keys = ("new_string", "new_text", "new", "replacement", "replace_with")

    def walk(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                walk(item)
            return
        if not isinstance(value, dict):
            return
        old = next((value[k] for k in old_keys if isinstance(value.get(k), str)), None)
        new = next((value[k] for k in new_keys if isinstance(value.get(k), str)), None)
        if old is not None and new is not None:
            out.append((old, new))
        for child in value.values():
            if isinstance(child, (dict, list)):
                walk(child)
    walk(args or {})
    return out


def build_expected_source(name: str, args: dict[str, Any] | None) -> tuple[str | None, str | None, list[str]]:
    """V5 transactional preflight: produce the exact candidate or reject the write."""
    if not tool_is_script_mutation(name, args):
        return None, None, []

    n = (name or '').lower()
    target = extract_target(args)
    current = source_cache_get(target)

    # Source rewriting through execute_luau is impossible to simulate safely from
    # arbitrary code. Force the model through a script edit tool instead.
    if n == "execute_luau":
        return None, (
            "Blocked by V5 transaction invariant: execute_luau may not rewrite Script.Source. "
            "Use the official script edit/write tool after script_read so the controller can simulate and validate the exact candidate first."
        ), []

    if not target:
        return None, (
            "Blocked by V5 transaction invariant: script mutation has no deterministic target path. "
            "Use a script edit tool with an explicit script path/target."
        ), []
    if not current:
        return None, (
            f"Blocked by V5 transaction invariant: no authoritative source snapshot is cached for {target}. "
            "Call script_read first; no blind script writes are allowed."
        ), []

    full = _find_full_source_payload(name, args)
    pairs = _generic_edit_pairs(args)
    if full is not None:
        candidate = normalize_source(full)
    elif pairs:
        if len(pairs) > V5_MAX_ATOMIC_EDIT_PAIRS:
            return None, (
                f"Blocked by V5 atomicity invariant: one mutation contains {len(pairs)} replacements; limit is {V5_MAX_ATOMIC_EDIT_PAIRS}. "
                "Split it into a smaller coherent edit and verify between transactions."
            ), []
        candidate = current
        for old, new in pairs:
            count = candidate.count(old)
            if count == 0:
                return None, (
                    "Blocked: the proposed edit is based on stale source. Its old_string is not present in the latest script_read. "
                    "Re-read current source and edit what actually exists."
                ), []
            if count > 1:
                return None, (
                    "Blocked: the proposed old_string matches multiple locations. The transaction is ambiguous; use a narrower exact string/range."
                ), []
            candidate = candidate.replace(old, new, 1)
        candidate = normalize_source(candidate)
    else:
        if V5_REQUIRE_SIMULATED_SCRIPT_WRITES:
            return None, (
                "Blocked by V5 transaction invariant: the controller cannot deterministically simulate this script-mutation schema. "
                "Use multi_edit/patch with exact old+new text, or an explicit full-source write tool. Nothing was written to Studio."
            ), []
        return None, None, []

    defects = structural_source_defects(candidate, current, args, name)
    if defects:
        return candidate, (
            "Blocked by V5 compiler transaction: the proposed resulting source failed deterministic preflight. "
            + " | ".join(defects)
            + ". Nothing was written to Studio. Repair the proposal itself, then retry."
        ), defects
    return candidate, None, []

def _schema_type_ok(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def _validate_schema_value(value: Any, schema: dict[str, Any], path: str = "arguments") -> str | None:
    """Validate the useful JSON-Schema subset advertised by MCP tools.

    This is intentionally deterministic and side-effect free. It prevents malformed
    calls from reaching Roblox while remaining permissive for schema features the
    official MCP does not use.
    """
    if not isinstance(schema, dict):
        return None

    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        return f"Blocked: {path} must be one of {enum}; got {value!r}."

    # oneOf/anyOf: succeed if any branch validates.
    for key in ("oneOf", "anyOf"):
        branches = schema.get(key)
        if isinstance(branches, list) and branches:
            if any(_validate_schema_value(value, b, path) is None for b in branches if isinstance(b, dict)):
                return None
            return f"Blocked: {path} does not satisfy any allowed {key} schema."

    expected = schema.get("type")
    if isinstance(expected, list):
        if not any(_schema_type_ok(value, str(t)) for t in expected):
            return f"Blocked: {path} has wrong type; expected one of {expected}, got {type(value).__name__}."
    elif isinstance(expected, str) and not _schema_type_ok(value, expected):
        return f"Blocked: {path} has wrong type; expected {expected}, got {type(value).__name__}."

    if isinstance(value, dict):
        required = schema.get("required") or []
        if isinstance(required, list):
            missing = [str(k) for k in required if k not in value]
            if missing:
                return f"Blocked: {path} is missing required field(s): {', '.join(missing)}."
        props = schema.get("properties")
        if isinstance(props, dict):
            if schema.get("additionalProperties") is False:
                extras = [str(k) for k in value if k not in props]
                if extras:
                    return f"Blocked: {path} contains unsupported field(s): {', '.join(extras)}."
            for key, child in value.items():
                spec = props.get(key)
                if isinstance(spec, dict):
                    reason = _validate_schema_value(child, spec, f"{path}.{key}")
                    if reason:
                        return reason

    if isinstance(value, list):
        min_items = schema.get("minItems")
        max_items = schema.get("maxItems")
        if isinstance(min_items, int) and len(value) < min_items:
            return f"Blocked: {path} requires at least {min_items} item(s)."
        if isinstance(max_items, int) and len(value) > max_items:
            return f"Blocked: {path} allows at most {max_items} item(s)."
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for i, item in enumerate(value):
                reason = _validate_schema_value(item, item_schema, f"{path}[{i}]")
                if reason:
                    return reason

    if isinstance(value, str):
        min_len = schema.get("minLength")
        max_len = schema.get("maxLength")
        if isinstance(min_len, int) and len(value) < min_len:
            return f"Blocked: {path} is shorter than the required {min_len} characters."
        if isinstance(max_len, int) and len(value) > max_len:
            return f"Blocked: {path} exceeds the allowed {max_len} characters."
    return None


def validate_tool_args(name: str, args: dict[str, Any] | None) -> str | None:
    """Validate calls against the official MCP's advertised input schema."""
    schema = TOOL_SCHEMAS.get(name)
    if not isinstance(schema, dict):
        return None
    return _validate_schema_value(args or {}, schema, "arguments")

def mode_mismatch_reason(name: str, args: dict[str, Any] | None) -> str | None:
    args = args or {}
    n = (name or "").lower()
    dm = args.get("datamodel_type")
    with _state_lock:
        mode = STATE.get("studio_mode")

    # start_stop_play is the escape hatch for mode deadlocks and must never be
    # rejected merely because the current mode is stale/undesired.
    if n == "start_stop_play":
        return None

    if mode == "play" and n == "script_read":
        return "Blocked: script_read needs Edit mode. Stop Play with start_stop_play(is_start=false), then retry the same script_read path."
    if mode == "play" and dm == "Edit":
        return (
            f"Blocked: {name} requested datamodel_type='Edit' while Studio is in Play mode. "
            "Use Client/Server runtime data or stop Play first."
        )
    if mode == "edit" and dm in {"Client", "Server"}:
        return (
            f"Blocked: {name} requested datamodel_type='{dm}' while Studio is in Edit mode. "
            "Start Play first or use datamodel_type='Edit'."
        )
    return None


def mutation_needs_accessory_evidence(name: str, args: dict[str, Any] | None) -> bool:
    if not tool_is_script_mutation(name, args):
        return False
    text = proposed_payload(args)
    return bool(
        re.search(r"IsA\s*\(\s*[\"']Accessory[\"']\s*\)", text, re.I)
        or re.search(r"FindFirstAncestorWhichIsA\s*\(\s*[\"']Accessory[\"']", text, re.I)
        or re.search(r"\bAccessoryType\b", text)
        or re.search(r"\bhandle\s*\.\s*Size\b", text, re.I)
    )


def mutation_needs_head_scale_evidence(name: str, args: dict[str, Any] | None) -> bool:
    if not tool_is_script_mutation(name, args):
        return False
    text = proposed_payload(args)
    return bool(
        re.search(r"\bBodyDepthScale\b|\bHeadScale\b", text)
        or (re.search(r"\bHead\b", text) and re.search(r"\.Size\b|FLAT_DEPTH|flatten", text, re.I))
        or (re.search(r"flatten", text, re.I) and re.search(r"character|body", text, re.I))
    )


def mutation_needs_body_geometry_evidence(name: str, args: dict[str, Any] | None) -> bool:
    if not tool_is_script_mutation(name, args):
        return False
    text = proposed_payload(args)
    return bool(
        re.search(r"flatten|FLAT_DEPTH|BodyDepthScale", text, re.I)
        and re.search(r"character|body|BasePart|Size", text, re.I)
    )


def evidence_block_reason(name: str, args: dict[str, Any] | None) -> str | None:
    with _state_lock:
        ev = dict(STATE.get("runtime_evidence") or {})
    if mutation_needs_accessory_evidence(name, args):
        if not (ev.get("accessory_seen") and ev.get("handle_seen") and ev.get("handle_size_seen")):
            return (
                "Blocked: accessory-writing logic requires current runtime evidence first. In Play mode inspect an actual Accessory, "
                "its Handle, and Handle.Size. Do not infer avatar hierarchy from memory."
            )
    if mutation_needs_head_scale_evidence(name, args):
        if not (ev.get("head_seen") and ev.get("humanoid_seen")):
            return (
                "Blocked: this head/scale/avatar-flatten edit needs runtime evidence first. Inspect the actual Head (including Size) and Humanoid "
                "scaling state before changing the script. Observation must come before causal edits."
            )
    if mutation_needs_body_geometry_evidence(name, args):
        if not ev.get("non_head_body_size_seen"):
            return (
                "Blocked: this body-geometry edit needs one actual non-Head runtime body BasePart Size measurement first "
                "(for example UpperTorso). Do not infer full-body depth from Head/accessory evidence."
            )
    return None


def update_runtime_evidence(name: str, args: dict[str, Any], text: str) -> None:
    n = name.lower()
    if n not in READ_EVIDENCE_TOOLS:
        return
    with _state_lock:
        mode = STATE.get("studio_mode")
        play_session = int(STATE.get("play_session", 0) or 0)
        epoch = int(STATE.get("mutation_epoch", 0) or 0)
    if mode != "play":
        return

    target_raw = extract_target(args)
    target = norm(target_raw)
    low = norm(text)
    compact = clip(re.sub(r"\s+", " ", text).strip(), 700)

    def remember(ev: dict[str, Any], key: str, summary: str) -> None:
        details = ev.setdefault("details", {})
        details[key] = {
            "summary": summary[:700],
            "target": target_raw,
            "tool": name,
            "observed_at": time.time(),
            "play_session": play_session,
            "mutation_epoch": epoch,
        }
        # Keep the ledger bounded; newest entries survive.
        if len(details) > 40:
            ordered = sorted(details.items(), key=lambda kv: float((kv[1] or {}).get("observed_at", 0)))
            for old_key, _ in ordered[:-40]:
                details.pop(old_key, None)

    def mutate(state: dict[str, Any]):
        ev = state.setdefault("runtime_evidence", {})
        ev["last_play_session"] = play_session

        accessory_hit = (
            '"classname":"accessory"' in low
            or "classname accessory" in low
            or ("accessory" in low and "handle" in low)
            or ".accessory" in target
        )
        if accessory_hit:
            ev["accessory_seen"] = True
            remember(ev, "accessory_structure", compact)

        if target.endswith(".handle") or '"name":"handle"' in low or "name handle" in low:
            ev["handle_seen"] = True
            remember(ev, f"handle:{target_raw or 'runtime'}", compact)
            if "size" in low:
                ev["handle_size_seen"] = True
                remember(ev, f"handle_size:{target_raw or 'runtime'}", compact)

        if target.endswith(".head") or '"name":"head"' in low or "name head" in low:
            if "size" in low:
                ev["head_seen"] = True
                remember(ev, "head_size", compact)

        if target.endswith(".humanoid") or '"classname":"humanoid"' in low:
            if any(k in low for k in ("bodydepthscale", "headscale", "automaticscalingenabled", "rigtype", "bodywidthscale", "bodyheightscale")):
                ev["humanoid_seen"] = True
                remember(ev, "humanoid_scaling", compact)

        if any(k in target for k in ("bodydepthscale", "headscale", "bodywidthscale", "bodyheightscale")):
            ev["humanoid_seen"] = True
            remember(ev, f"scale:{target_raw}", compact)

        # Any inspected runtime BasePart-like target with a Size field counts as
        # body/geometry evidence. This is not used to guess class; it only keeps
        # Qwen from re-querying the exact same measurements over and over.
        if "size" in low and target and not target.endswith(".handle"):
            ev["body_part_size_seen"] = True
            if not target.endswith(".head"):
                ev["non_head_body_size_seen"] = True
            remember(ev, f"part_size:{target_raw}", compact)

    state_update(mutate)


def invalidate_runtime_evidence_for_mutation(state: dict[str, Any], name: str, args: dict[str, Any] | None) -> None:
    """Invalidate only evidence a successful write could plausibly make stale."""
    ev = state.setdefault("runtime_evidence", {})
    details = ev.setdefault("details", {})
    text = norm(proposed_payload(args) + " " + extract_target(args))

    accessory = any(k in text for k in ("accessory", "handle", "attachment"))
    avatar_scale = any(k in text for k in (
        "head", "bodydepthscale", "headscale", "humanoid", "flat_depth", "flattenbody",
        "uppertorso", "lowertorso", "upperarm", "lowerarm", "upperleg", "lowerleg", "hand", "foot", ".size"
    ))

    if accessory:
        ev["accessory_seen"] = False
        ev["handle_seen"] = False
        ev["handle_size_seen"] = False
        for key in list(details):
            if key.startswith("accessory") or key.startswith("handle"):
                details.pop(key, None)
    if avatar_scale:
        ev["head_seen"] = False
        ev["humanoid_seen"] = False
        ev["body_part_size_seen"] = False
        ev["non_head_body_size_seen"] = False
        for key in list(details):
            if key == "head_size" or key == "humanoid_scaling" or key.startswith("scale:") or key.startswith("part_size:"):
                details.pop(key, None)

def known_bad_code_reason(name: str, args: dict[str, Any] | None) -> str | None:
    """Block a few high-confidence mistakes already observed from the model."""
    text = proposed_payload(args)
    low = text.lower()
    if not text:
        return None

    # 1) Hallucinated Accessory.RootPart.
    if re.search(r"\b(?:accessory|acc)\w*\s*\.\s*rootpart\b", low):
        return (
            "Blocked: Accessory.RootPart is not a Roblox Accessory property. "
            "Inspect the Accessory/Handle/Attachment hierarchy instead of inventing RootPart."
        )
    if "findfirstchild(\"rootpart\")" in low or "findfirstchild('rootpart')" in low:
        if "accessory" in low:
            return (
                "Blocked: this treats RootPart as part of an Accessory. "
                "Use Accessory -> Handle -> Attachment and inspect the matching character attachment."
            )

    # 2) Bad BodyDepthScale API patterns.
    if re.search(r"getattribute\s*\(\s*[\"']bodydepthscale[\"']\s*\)", low):
        return (
            "Blocked: BodyDepthScale is a Humanoid child NumberValue, not an Attribute. "
            "Find/use the NumberValue and set BodyDepthScale.Value."
        )
    if re.search(r"\bhumanoid\s*\.\s*bodydepthscale\s*=", low):
        return (
            "Blocked: direct assignment to Humanoid.BodyDepthScale is wrong for the scale NumberValue. "
            "Set humanoid.BodyDepthScale.Value (after verifying the child exists)."
        )

    # 3) Name-based body/accessory classification, specifically the failure pattern from this project.
    visual_character_context = any(
        word in low for word in (
            "accessory", "flatten", "flat_depth", "bodypart", "body part", "basepart", "humanoid", "character", "head", "torso"
        )
    )
    name_find = re.search(r"\.\s*name\s*:\s*find\s*\(", low)
    string_name_find = re.search(r"string\s*\.\s*find\s*\([^\n]{0,80}\.\s*name", low)
    if visual_character_context and (name_find or string_name_find):
        return (
            "Blocked: name-based classification is a known failed approach for body parts/accessories. "
            "Use Instance class, direct character hierarchy, Accessory ancestry, Handle, and Attachments instead."
        )

    # 4) V5: any name equality used to classify avatar geometry is fragile. Exact
    # object identity/class/hierarchy is available and should be used instead.
    if visual_character_context and avatar_name_classification_detected(text):
        return (
            "Blocked by V5 avatar invariant: do not classify character/body/accessory geometry with Instance.Name comparisons. "
            "Use IsA(), direct-character-child identity, Accessory ancestry, Handle, Attachments, or explicit instance equality."
        )

    # 5) Accessory.PrimaryPart is another observed hallucination. Accessory is not
    # Model; use its actual Handle child/attachments after inspection.
    if "accessory" in low and re.search(r"\.\s*primarypart\b", low):
        return (
            "Blocked: Accessory.PrimaryPart is not the verified accessory structure. "
            "Use the inspected Accessory -> Handle -> Attachment structure."
        )

    return None




def mutation_runtime_requirements(name: str, args: dict[str, Any] | None) -> dict[str, bool]:
    text = norm(proposed_payload(args) + " " + extract_target(args))
    avatar = any(k in text for k in ("character", "humanoid", "flatten", "flat_depth", "bodypart", "head", "accessory"))
    return {
        "head_scale": mutation_needs_head_scale_evidence(name, args),
        "accessory": mutation_needs_accessory_evidence(name, args),
        "body_geometry": mutation_needs_body_geometry_evidence(name, args),
    }


def runtime_requirements_satisfied(state: dict[str, Any], req: dict[str, Any] | None) -> bool:
    req = req or {}
    ev = state.get("runtime_evidence") or {}
    if req.get("head_scale") and not (ev.get("head_seen") and ev.get("humanoid_seen")):
        return False
    if req.get("accessory") and not (ev.get("accessory_seen") and ev.get("handle_seen") and ev.get("handle_size_seen")):
        return False
    if req.get("body_geometry") and not ev.get("non_head_body_size_seen"):
        return False
    return True


def runtime_requirement_message(req: dict[str, Any] | None) -> str:
    req = req or {}
    bits: list[str] = []
    if req.get("head_scale"):
        bits.append("re-inspect runtime Head.Size and Humanoid scaling state")
    if req.get("body_geometry"):
        bits.append("re-inspect at least one non-Head body BasePart Size")
    if req.get("accessory"):
        bits.append("re-inspect an actual Accessory.Handle and Handle.Size")
    return "; ".join(bits) if bits else "gather the required post-edit runtime evidence"

def visual_change_likely(name: str, args: dict[str, Any] | None, target: str = "") -> bool:
    text = norm(joined_payload(args) + " " + target)
    keywords = (
        "flat", "size", "cframe", "position", "rotation", "orientation", "accessory",
        "handle", "attachment", "head", "character", "mesh", "transparency", "color",
        "material", "gui", "camera", "visual", "bodydepthscale",
    )
    return any(k in text for k in keywords)


def result_to_text(result_obj: Any) -> str:
    """Extract text from an MCP tools/call result or generic JSON-RPC result."""
    if not isinstance(result_obj, dict):
        return str(result_obj or "")
    result = result_obj.get("result")
    if isinstance(result, dict):
        content = result.get("content")
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
            if parts:
                return "\n".join(parts)
        return json_text(result)
    err = result_obj.get("error")
    if err is not None:
        return json_text(err)
    return json_text(result_obj)


def mcp_tool_error_response(request_id: Any, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "content": [{"type": "text", "text": "SUPERVISOR BLOCK\n" + message}],
            "isError": True,
        },
    }


def mcp_tool_ok_response(request_id: Any, payload: Any) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}],
            "isError": False,
        },
    }


def append_tool_note(response: dict[str, Any], note: str) -> dict[str, Any]:
    if not note:
        return response
    try:
        result = response.get("result")
        if not isinstance(result, dict):
            return response
        content = result.get("content")
        if not isinstance(content, list):
            return response
        content.append({"type": "text", "text": "SUPERVISOR NOTE\n" + note})
        return response
    except Exception:
        return response


def parse_studio_mode(text: str) -> str:
    low = norm(text)
    if "current studio mode: play" in low:
        return "play"
    if "current studio mode: edit" in low:
        return "edit"
    if "game stopped" in low:
        return "edit"
    if "game started" in low or "game start" in low:
        return "play"
    return ""


def classify_error_text(text: str) -> dict[str, Any] | None:
    low = norm(text)
    if not text:
        return None
    # Ignore stale scratch AssistantCommand errors when reading the general Output log.
    # They are often generated by an earlier execute_luau/assistant command and should
    # not derail the user's project unless they are the immediate tool failure.
    cleaned_lines = [
        line for line in text.splitlines()
        if not line.strip().lower().startswith("assistantcommand:")
        and "script 'assistantcommand'" not in line.strip().lower()
    ]
    clean = "\n".join(cleaned_lines)
    clean_low = norm(clean)

    patterns = [
        ("nil_call", r"attempt to call a nil value"),
        ("nil_index", r"attempt to index (?:a )?nil value|attempt to index nil"),
        ("invalid_member", r"is not a valid member of"),
        ("syntax_error", r"syntax error|unexpected symbol|expected .+ got"),
        ("infinite_yield", r"infinite yield possible"),
    ]
    classification = ""
    for kind, pat in patterns:
        if re.search(pat, clean_low, re.I):
            classification = kind
            break
    if not classification:
        return None

    line_no = 0
    path = ""
    message = ""
    for line in clean.splitlines():
        m = re.search(r"(?P<path>[A-Za-z0-9_.' /\\-]+?):(?P<line>\d+):\s*(?P<msg>.+)", line)
        if m:
            path = m.group("path").strip()
            line_no = int(m.group("line"))
            message = m.group("msg").strip()
            break
    return {
        "classification": classification,
        "path": path,
        "line": line_no,
        "message": message or clip(clean, 600),
        "error_text": clip(clean, 1600),
        "stage": "need_evidence",
        "created_at": time.time(),
    }


def blocker_required_message(blocker: dict[str, Any]) -> str:
    kind = blocker.get("classification", "runtime_error")
    path = blocker.get("path") or "the implicated script/object"
    line = blocker.get("line") or "?"
    if kind == "nil_call":
        return (
            f"Active blocker: nil call at {path}:{line}. "
            "Read the exact failing line/surrounding script and identify the callable that is nil before writing anything."
        )
    if kind == "invalid_member":
        return (
            f"Active blocker: invalid member at {path}:{line}. "
            "Inspect the actual class/member/child structure before writing anything."
        )
    if kind == "syntax_error":
        return (
            f"Active blocker: syntax error at {path}:{line}. "
            "If Studio is in Play mode, STOP Play first. Then read the implicated source. "
            "One narrow same-script structural repair is allowed after that read; the blocker must never prevent the recovery actions themselves."
        )
    if kind == "static_source_defect":
        return (
            f"Active blocker: deterministic source defects remain in {path}. "
            "Make one narrow same-script repair that reduces the recorded defect debt, then reread before any semantic edit."
        )
    return (
        f"Active blocker: {kind} at {path}:{line}. "
        "Gather direct evidence with script_read/inspect/search before another write."
    )


def target_matches(needed: str, actual: str) -> bool:
    if not needed or not actual:
        return True
    a = norm(needed).replace("game.", "")
    b = norm(actual).replace("game.", "")
    return a == b or a.endswith(b) or b.endswith(a) or a.split(".")[-1] == b.split(".")[-1]

def repaired_static_blocker_is_stale(blocker: Any, gate: Any) -> bool:
    """A repair_applied static blocker must not veto post-repair verification.

    Persisted controller state can legitimately contain the old blocker across a
    controller/agent restart even after the authoritative reread advanced the
    verification gate. Once the gate is in the verification pipeline for the
    same target, the gate is authoritative and the stale blocker is ignored.
    """
    return (
        isinstance(gate, dict)
        and gate.get("stage") in {"need_playtest", "need_output", "need_runtime_verify", "need_visual"}
        and isinstance(blocker, dict)
        and blocker.get("classification") == "static_source_defect"
        and blocker.get("stage") == "repair_applied"
        and target_matches(blocker.get("path") or "", gate.get("target") or "")
    )


def _call_sig_short(name: str, args: dict[str, Any] | None) -> str:
    raw = json.dumps({"name": name, "args": args or {}}, ensure_ascii=True, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:16]


def cached_evidence_for_target(target: str) -> str:
    wanted = canonical_target(target)
    if not wanted:
        return ""
    with _state_lock:
        details = copy.deepcopy((STATE.get("runtime_evidence") or {}).get("details") or {})
    best: tuple[float, str] | None = None
    for item in details.values():
        if not isinstance(item, dict):
            continue
        t = canonical_target(str(item.get("target") or ""))
        if not t or not (t == wanted or t.endswith(wanted) or wanted.endswith(t)):
            continue
        row = (float(item.get("observed_at") or 0), str(item.get("summary") or ""))
        if best is None or row[0] > best[0]:
            best = row
    return best[1][:600] if best else ""


def loop_guard_reason(name: str, args: dict[str, Any] | None) -> str | None:
    n = (name or "").lower()
    args = args or {}
    sig = _call_sig_short(name, args)
    with _state_lock:
        state = copy.deepcopy(STATE)
    history = state.get("action_history") or []
    epoch = int(state.get("mutation_epoch", 0) or 0)
    same_epoch = [x for x in history if isinstance(x, dict) and int(x.get("mutation_epoch", -1)) == epoch]

    # Re-reading source and checking Output are intentionally exempt; they are
    # authoritative verification actions and may legitimately repeat.
    if n in READ_EVIDENCE_TOOLS - {"script_read", "read_script_range", "get_studio_state"}:
        repeats = [x for x in same_epoch[-24:] if x.get("kind") == "forward" and x.get("sig") == sig]
        if len(repeats) >= 2:
            target = extract_target(args)
            cached = cached_evidence_for_target(target)
            suffix = f" Cached evidence: {cached}" if cached else ""
            return (
                "Blocked by loop guard: this exact inspection was already performed twice with no successful write in between. "
                "Use the verified evidence and proceed to the next required action instead of re-inspecting." + suffix
            )

    if n == "start_stop_play":
        toggles = [x for x in same_epoch[-20:] if x.get("kind") == "forward" and x.get("name", "").lower() == "start_stop_play"]
        blocker = state.get("current_blocker")
        gate = state.get("gate")
        escape_stop = args.get("is_start") is False and isinstance(blocker, dict)
        required_start = args.get("is_start") is True and isinstance(gate, dict) and gate.get("stage") == "need_playtest"
        if len(toggles) >= 6 and not escape_stop and not required_start:
            return (
                f"Blocked by mode-loop guard: Play/Edit has been toggled {len(toggles)} times without a successful write. "
                f"Current recorded mode is {state.get('studio_mode')}. Follow the active blocker/gate instead of toggling again."
            )

    if n in VISUAL_TOOLS:
        shots = [x for x in same_epoch[-16:] if x.get("kind") == "forward" and x.get("name", "").lower() in VISUAL_TOOLS]
        gate = state.get("gate")
        if len(shots) >= 2 and not (isinstance(gate, dict) and gate.get("stage") == "need_visual"):
            return "Blocked by loop guard: visual evidence was already captured twice with no intervening write. Use it instead of taking another redundant capture."
    return None


# -----------------------------------------------------------------------------
# Gate engine
# -----------------------------------------------------------------------------


def block_reason_for_call(name: str, args: dict[str, Any] | None) -> str | None:
    n = (name or "").lower()
    args = args or {}

    schema_reason = validate_tool_args(name, args)
    if schema_reason:
        return schema_reason

    with _state_lock:
        state = copy.deepcopy(STATE)
    blocker = state.get("current_blocker")
    gate = state.get("gate")
    mode = state.get("studio_mode")

    # V6.3.2 defensive reconciliation for persisted state: once a repaired
    # static-source blocker has handed control to the post-repair verification
    # gate, it must not veto Play, Output, runtime evidence, or visual checks.
    # This is especially important across controller/autopilot restarts.
    if repaired_static_blocker_is_stale(blocker, gate):
        blocker = None

    repair_override = False
    required_action = False

    # In desktop UI mode the MCP server cannot create a new LM Studio chat. Near
    # the ceiling, preserve only safe recovery/status actions rather than letting
    # Qwen burn the final context in another loop. Autopilot mode performs true
    # automatic API-chat rollover separately.
    meter = state.get("context_estimate") or {}
    est = int(meter.get("estimated_tokens", 0) or 0)
    hard_ui_stop = max(CONTEXT_ROLLOVER_TRIGGER + 2000, CONTEXT_WINDOW_TOKENS - 1200)
    if est >= hard_ui_stop and n not in {"supervisor_resume", "supervisor_status", "get_studio_state"}:
        return (
            f"Blocked by V5 context safety at ~{est} estimated tokens. The MCP server cannot create a new LM Studio desktop chat itself. "
            "Open a fresh chat and call supervisor_resume(new_chat=true), or run this controller with --autopilot for true automatic rollover. "
            "Verified task state is already checkpointed; do not paste the old transcript."
        )

    # ------------------------------------------------------------------
    # Deadlock-free blocker recovery.  Stopping Play is ALWAYS an escape
    # action. Syntax errors specifically must not lock out the read/repair
    # operations required to fix the syntax error.
    # ------------------------------------------------------------------
    if isinstance(blocker, dict):
        kind = blocker.get("classification")
        stage = blocker.get("stage")
        bpath = blocker.get("path") or ""

        if n == "start_stop_play" and args.get("is_start") is False:
            required_action = True
            # Never block the escape stop because of the blocker itself.

        elif kind == "script_read_not_found":
            if stage == "need_studio_state":
                if n == "get_studio_state":
                    required_action = True
                elif n != "supervisor_status":
                    return (
                        "Blocked: script_read reported 'Script not found'. Call get_studio_state first. "
                        "If Studio is in Play mode, stop Play and retry the SAME path before inventing a new path."
                    )
            elif stage == "need_stop_play":
                if n == "start_stop_play" and args.get("is_start") is False:
                    required_action = True
                elif n != "supervisor_status":
                    return "Blocked: stop Play with start_stop_play(is_start=false), then retry the same script_read path."

        elif kind == "static_source_defect":
            if n in {"supervisor_status", "supervisor_resume", "get_studio_state"}:
                required_action = True
            elif n == "start_stop_play" and args.get("is_start") is False:
                required_action = True
            elif n in {"script_read", "read_script_range"}:
                actual_target = extract_target(args)
                if bpath and not target_matches(bpath, actual_target):
                    return blocker_required_message(blocker)
                required_action = True
            elif tool_is_script_mutation(name, args):
                actual_target = extract_target(args)
                if bpath and not target_matches(bpath, actual_target):
                    return blocker_required_message(blocker)
                repair_override = True
                required_action = True
            else:
                return (
                    blocker_required_message(blocker)
                    + " Runtime exploration is intentionally paused until static source debt is reduced; do not inspect unrelated objects or start Play."
                )

        elif kind == "syntax_error":
            if n in {"get_studio_state", "script_read", "read_script_range"}:
                required_action = True
            elif stage in {"ready_for_edit", "ready_for_repair"} and tool_is_script_mutation(name, args):
                actual_target = extract_target(args)
                if not target_matches(bpath, actual_target):
                    return (
                        f"Blocked: syntax recovery is limited to the implicated script {bpath or 'unknown'}. "
                        "Repair that script first."
                    )
                repair_override = True
                required_action = True
            elif stage != "repair_applied" and (tool_is_mutation(name, args) or (n == "start_stop_play" and args.get("is_start") is True)):
                return blocker_required_message(blocker)

        elif stage == "need_evidence":
            if tool_is_mutation(name, args) or (n == "start_stop_play" and args.get("is_start") is True):
                return blocker_required_message(blocker)
        elif stage in {"ready_for_edit", "ready_for_repair"} and tool_is_script_mutation(name, args):
            actual_target = extract_target(args)
            if bpath and not target_matches(bpath, actual_target):
                return blocker_required_message(blocker)
            repair_override = True
            required_action = True

    # Datamodel checks happen AFTER recognizing escape/recovery operations.
    mode_reason = mode_mismatch_reason(name, args)
    if mode_reason:
        return mode_reason

    # ------------------------------------------------------------------
    # Mandatory post-edit state machine.
    # ------------------------------------------------------------------
    if isinstance(gate, dict):
        stage = gate.get("stage")
        target = gate.get("target") or "the edited script"
        if stage == "need_reread":
            if n == "script_read" and target_matches(target, extract_target(args)):
                required_action = True
            elif n == "start_stop_play" and args.get("is_start") is False:
                required_action = True
            elif n in {"supervisor_status", "supervisor_resume", "get_studio_state", "list_roblox_studios"}:
                required_action = True
            else:
                return (
                    f"Blocked by V6 exact-next-action gate: the previous edit to {target} has not been re-read. "
                    "Do not inspect, playtest, or mutate anything else. Reread that exact script first; current source is authoritative."
                )

        elif stage == "repair_allowed":
            if tool_is_script_mutation(name, args):
                actual_target = extract_target(args)
                if not target_matches(target, actual_target):
                    return (
                        f"Blocked: post-edit verification found a source defect in {target}. "
                        "The repair exception permits only a narrow corrective edit to that same script."
                    )
                repair_override = True
                required_action = True
            elif n == "script_read" and target_matches(target, extract_target(args)):
                required_action = True
            elif n == "start_stop_play" and args.get("is_start") is False:
                required_action = True
            elif n in {"supervisor_status", "supervisor_resume", "get_studio_state", "list_roblox_studios"}:
                required_action = True
            else:
                return (
                    f"Blocked by V6 exact-next-action gate: {target} still has verified source defect debt. "
                    "Repair that same script and reread it before Play, Output, visual checks, runtime inspection, or unrelated work."
                )

        elif stage == "need_playtest":
            if n == "start_stop_play" and args.get("is_start") is True:
                required_action = True
            elif n in {"supervisor_status", "supervisor_resume", "get_studio_state", "list_roblox_studios"}:
                required_action = True
            else:
                return (
                    f"Blocked by V6 exact-next-action gate: {target} was edited and reread cleanly. "
                    "Start Play now; do not inspect, write, check Output, or take screenshots before the required playtest starts."
                )

        elif stage == "need_output":
            if n in OUTPUT_TOOLS:
                required_action = True
            elif n in {"supervisor_status", "supervisor_resume", "get_studio_state", "list_roblox_studios"}:
                required_action = True
            else:
                return (
                    "Blocked by V6 exact-next-action gate: check Output for the current playtest now. "
                    "Do not stop Play, inspect other objects, mutate, or visually verify before Output is checked."
                )

        elif stage == "need_runtime_verify":
            req = gate.get("runtime_requirements") or {}
            if n in (READ_EVIDENCE_TOOLS - {"script_read", "read_script_range", "get_studio_state"}):
                required_action = True
            elif n in {"supervisor_status", "supervisor_resume", "get_studio_state", "list_roblox_studios"}:
                required_action = True
            else:
                return (
                    "Blocked by V6 exact-next-action gate: post-edit runtime evidence is required before visual verification or another write. "
                    + runtime_requirement_message(req) + ". Use direct Studio evidence; do not speculate."
                )

        elif stage == "need_visual":
            if n in VISUAL_TOOLS:
                required_action = True
            elif n in {"supervisor_status", "supervisor_resume", "get_studio_state"} or n in OUTPUT_TOOLS:
                required_action = True
            else:
                return (
                    "Blocked by V6 exact-next-action gate: visual verification is the required next step. "
                    "Capture/observe the current result before another inspection, write, mode switch, or completion claim."
                )

    # High-confidence API/code mistakes are always rejected, even in a repair.
    bad = known_bad_code_reason(name, args)
    if bad and tool_is_mutation(name, args):
        return bad

    # Existing-script edits must be grounded in a current source snapshot.
    if tool_is_script_mutation(name, args):
        target = extract_target(args)
        if target and n in SCRIPT_MUTATION_NAMES and not source_cache_get(target):
            return (
                f"Blocked: no authoritative source is cached for {target}. "
                "Call script_read first, then edit the source that actually exists in Studio."
            )

    # Evidence-first avatar gates are bypassed ONLY for a narrow structural
    # repair already authorized by a concrete blocker/post-edit reread.
    if not repair_override:
        ev_reason = evidence_block_reason(name, args)
        if ev_reason:
            return ev_reason

    if tool_is_mutation(name, args):
        sig = mutation_signature(name, args)
        if sig in state.get("failed_mutation_signatures", []):
            return (
                "Blocked: this identical mutation already failed. Gather new evidence or change the fix before retrying."
            )

    # Stop Qwen from spending the entire context on identical inspections and
    # Play/Edit ping-pong. Never block the action currently required by a gate.
    if not required_action:
        loop_reason = loop_guard_reason(name, args)
        if loop_reason:
            return loop_reason

    return None


def on_local_block(reason: str, name: str = "", args: dict[str, Any] | None = None) -> None:
    def mutate(state: dict[str, Any]):
        state["blocked_count"] = int(state.get("blocked_count", 0)) + 1
        state["last_note"] = reason
    state_update(mutate)
    account_context_traffic(chars=len(reason) + len(json_text(args or {})), tool_call=True)
    record_action("block", name or "blocked_call", args, reason)
    severity = "warning" if "exact-next-action gate" in reason.lower() else "error"
    telemetry_record_failure(
        "controller_block", reason, tool_name=name or "blocked_call", arguments=args or {}, severity=severity
    )
    deadlock = _detect_block_deadlock(reason, name or "blocked_call", args or {})
    if deadlock:
        deadlock_kind, deadlock_message = deadlock
        telemetry_record_failure(
            deadlock_kind, deadlock_message, tool_name=name or "blocked_call", arguments=args or {}, severity="critical",
            extra={"automatic": True, "retry_limit": DEADLOCK_REPEAT_LIMIT},
        )
    refresh_checkpoint_files()
    log("BLOCK " + reason)


def on_forwarded_call(name: str, args: dict[str, Any] | None) -> None:
    def mutate(state: dict[str, Any]):
        state["forwarded_count"] = int(state.get("forwarded_count", 0)) + 1
        target = extract_target(args)
        if target and ("script" in name.lower() or name.lower() in SCRIPT_MUTATION_NAMES):
            state["last_script_target"] = target
    state_update(mutate)
    account_context_traffic(chars=len(name) + len(json_text(args or {})), tool_call=True)
    record_action("forward", name, args)


def on_tool_result(name: str, args: dict[str, Any] | None, response: dict[str, Any], mutation_plan: dict[str, Any] | None = None) -> str:
    """Update state from a child tool result. Return a concise note to append."""
    n = (name or "").lower()
    args = args or {}
    text = result_to_text(response)
    low = norm(text)
    is_error = False
    try:
        if isinstance(response.get("error"), dict):
            is_error = True
        result = response.get("result")
        if isinstance(result, dict) and result.get("isError") is True:
            is_error = True
    except Exception:
        pass

    notes: list[str] = []

    # Keep Studio mode current.
    mode = parse_studio_mode(text)
    if mode:
        state_update(lambda s: s.__setitem__("studio_mode", mode))

    # start_stop_play args are more reliable than text when success is terse.
    if n == "start_stop_play" and not is_error:
        wanted = args.get("is_start")
        if wanted is True:
            def began_play(state: dict[str, Any]):
                state["studio_mode"] = "play"
                state["play_session"] = int(state.get("play_session", 0)) + 1
                # IMPORTANT V4: do NOT erase evidence just because a new Play
                # session started. Evidence is invalidated only by relevant
                # successful writes. This prevents endless re-inspection loops.
                ev = state.setdefault("runtime_evidence", {})
                ev["last_play_session"] = state["play_session"]
            state_update(began_play)
        elif wanted is False:
            state_update(lambda s: s.__setitem__("studio_mode", "edit"))

    # Capture runtime inspection evidence automatically; the model never has to log it.
    if not is_error:
        update_runtime_evidence(name, args, text)
        def maybe_finish_runtime_verify(state: dict[str, Any]):
            gate = state.get("gate")
            if isinstance(gate, dict) and gate.get("stage") == "need_runtime_verify":
                req = gate.get("runtime_requirements") or {}
                if runtime_requirements_satisfied(state, req):
                    gate["stage"] = "need_visual" if gate.get("visual") else "runtime_verified"
        state_update(maybe_finish_runtime_verify)
        with _state_lock:
            g_after_evidence = copy.deepcopy(STATE.get("gate"))
        if isinstance(g_after_evidence, dict) and g_after_evidence.get("stage") == "need_visual" and n in READ_EVIDENCE_TOOLS:
            notes.append("Post-edit runtime evidence requirements are now satisfied. MANDATORY NEXT: visually verify the result.")
        elif isinstance(g_after_evidence, dict) and g_after_evidence.get("stage") == "runtime_verified":
            state_update(lambda s: s.__setitem__("gate", None))
            notes.append("Post-edit runtime evidence requirements are satisfied; non-visual verification gate is clear.")

    # Tool-level error/failure.
    if is_error:
        def fail_mutate(state: dict[str, Any]):
            state["tool_error_count"] = int(state.get("tool_error_count", 0)) + 1
            if tool_is_mutation(name, args):
                sig = mutation_signature(name, args)
                failures = list(state.get("failed_mutation_signatures", []))
                if sig not in failures:
                    failures.append(sig)
                state["failed_mutation_signatures"] = failures[-20:]
        state_update(fail_mutate)

    # Special failure already observed: script_read path error while Play/unknown.
    if n == "script_read" and "script not found at path" in low:
        def sr_fail(state: dict[str, Any]):
            state["current_blocker"] = {
                "classification": "script_read_not_found",
                "path": extract_target(args),
                "line": 0,
                "message": "script_read reported Script not found at path",
                "stage": "need_studio_state",
                "created_at": time.time(),
            }
        state_update(sr_fail)
        notes.append(
            "Do not assume the path is wrong yet. Check get_studio_state first; if Studio is in Play mode, stop Play and retry the same script_read."
        )

    # Resolve script_read_not_found diagnostic gate deterministically.
    if n == "get_studio_state":
        with _state_lock:
            blocker = STATE.get("current_blocker")
            current_mode = STATE.get("studio_mode")
        if isinstance(blocker, dict) and blocker.get("classification") == "script_read_not_found":
            if current_mode == "play":
                def need_stop(state: dict[str, Any]):
                    b = state.get("current_blocker")
                    if isinstance(b, dict):
                        b["stage"] = "need_stop_play"
                state_update(need_stop)
                notes.append("Studio is in Play mode. Stop Play before retrying script_read.")
            else:
                def ready_retry(state: dict[str, Any]):
                    state["current_blocker"] = None
                state_update(ready_retry)
                notes.append("Studio is not in Play mode; retry the same script_read before changing the path.")

    if n == "start_stop_play" and args.get("is_start") is False and not is_error:
        with _state_lock:
            blocker = copy.deepcopy(STATE.get("current_blocker"))
        if isinstance(blocker, dict) and blocker.get("classification") == "script_read_not_found":
            state_update(lambda s: s.__setitem__("current_blocker", None))
            notes.append("Play is stopped. Retry the same script_read path now.")
        elif isinstance(blocker, dict) and blocker.get("classification") == "syntax_error":
            def syntax_stopped(state: dict[str, Any]):
                b = state.get("current_blocker")
                if isinstance(b, dict) and b.get("classification") == "syntax_error":
                    b["stage"] = "need_source_read"
            state_update(syntax_stopped)
            notes.append("Play is stopped. Read the implicated script now; one narrow syntax repair will then be allowed.")

    # Any successful script mutation creates a mandatory verification gate.
    if tool_is_script_mutation(name, args) and not is_error:
        target = extract_target(args) or STATE.get("last_script_target") or "edited script"
        visual = visual_change_likely(name, args, target)
        runtime_req = mutation_runtime_requirements(name, args)
        sig = mutation_signature(name, args)
        plan = mutation_plan or {}
        expected_source = plan.get("expected_source") if isinstance(plan, dict) else None
        previous_source = plan.get("previous_source") if isinstance(plan, dict) else None
        def after_edit(state: dict[str, Any]):
            prior_blocker = state.get("current_blocker")
            prior_gate = state.get("gate")
            structural_repair = (
                isinstance(prior_blocker, dict) and prior_blocker.get("classification") == "syntax_error"
            ) or (isinstance(prior_gate, dict) and prior_gate.get("stage") == "repair_allowed")

            state["mutation_epoch"] = int(state.get("mutation_epoch", 0) or 0) + 1
            state["last_mutation"] = {
                "tool": name,
                "target": target,
                "signature": sig,
                "at": time.time(),
                "visual": visual,
                "expected_hash": source_hash(expected_source) if isinstance(expected_source, str) and expected_source else "",
                "structural_repair": structural_repair,
            }
            state["gate"] = {
                "stage": "need_reread",
                "target": target,
                "visual": visual,
                "created_at": time.time(),
                "expected_source": expected_source if isinstance(expected_source, str) and len(expected_source) <= 200000 else None,
                "expected_hash": source_hash(expected_source) if isinstance(expected_source, str) and expected_source else "",
                "previous_source": previous_source if isinstance(previous_source, str) and len(previous_source) <= 200000 else None,
                "repair_reason": "",
                "runtime_requirements": runtime_req,
            }
            # A pure syntax/structural repair does not invalidate the runtime
            # measurements that justified the semantic fix. Normal writes do.
            if not structural_repair:
                invalidate_runtime_evidence_for_mutation(state, name, args)
            if isinstance(prior_blocker, dict):
                prior_blocker["stage"] = "repair_applied"
                state["current_blocker"] = prior_blocker
        state_update(after_edit)
        notes.append(f"MANDATORY NEXT: re-read {target} before any further write. Current source on reread is authoritative.")

    # Successful script_read can satisfy evidence and/or verify the actual post-edit source.
    if n == "script_read" and not is_error and "script not found at path" not in low:
        actual = extract_target(args)
        actual_source = extract_script_source(text)
        if actual and actual_source:
            source_cache_set(actual, actual_source)

        # V5 validates every authoritative read, not only proposed writes. This
        # turns pre-existing broken source into an explicit repair debt instead of
        # letting Qwen stack semantic edits on top of it.
        read_static_defects = raw_static_source_defects(actual_source) if actual_source else []
        if read_static_defects:
            def mark_static_debt(state: dict[str, Any]):
                existing = state.get("current_blocker")
                # Preserve a more specific concrete runtime/syntax blocker.
                if not isinstance(existing, dict) or existing.get("classification") in {"static_source_defect"}:
                    state["current_blocker"] = {
                        "classification": "static_source_defect",
                        "path": actual,
                        "line": 0,
                        "message": "; ".join(read_static_defects[:6]),
                        "stage": "ready_for_repair",
                        "created_at": time.time(),
                    }
            state_update(mark_static_debt)
            notes.append(
                "V6 STATIC SOURCE DEFECT DEBT: " + "; ".join(read_static_defects[:6])
                + ". Repair these incrementally; each write must reduce defect debt and may not introduce a new defect category."
            )

        verification_note = ""
        def after_read(state: dict[str, Any]):
            nonlocal verification_note
            blocker = state.get("current_blocker")
            if isinstance(blocker, dict) and blocker.get("stage") in {"need_evidence", "need_source_read"}:
                path = blocker.get("path") or ""
                if target_matches(path, actual):
                    blocker["stage"] = "ready_for_edit"

            gate = state.get("gate")
            if isinstance(gate, dict) and gate.get("stage") == "need_reread":
                if target_matches(gate.get("target") or "", actual):
                    expected = gate.get("expected_source")
                    previous = gate.get("previous_source") or ""
                    defects = structural_source_defects(actual_source, previous, None, str((state.get("last_mutation") or {}).get("tool") or "multi_edit")) if actual_source else []
                    remaining_static = raw_static_source_defects(actual_source) if actual_source else []
                    if isinstance(expected, str) and expected:
                        if source_hash(actual_source) != source_hash(expected):
                            gate["stage"] = "repair_allowed"
                            gate["repair_reason"] = "post-edit reread does not match the source the edit was expected to create"
                            gate["actual_hash"] = source_hash(actual_source)
                            verification_note = (
                                "EDIT VERIFICATION FAILED: script_read does not match the intended post-edit source. "
                                "The reread is truth. One narrow corrective edit to this same script is allowed before playtest."
                            )
                        elif defects:
                            gate["stage"] = "repair_allowed"
                            gate["repair_reason"] = "; ".join(defects)
                            verification_note = (
                                "POST-EDIT STRUCTURAL DEFECT: " + "; ".join(defects) + ". "
                                "Do not playtest knowingly broken source. One narrow corrective edit to this same script is allowed."
                            )
                        elif remaining_static:
                            gate["stage"] = "repair_allowed"
                            gate["repair_reason"] = "; ".join(remaining_static[:8])
                            state["current_blocker"] = {
                                "classification": "static_source_defect",
                                "path": actual,
                                "line": 0,
                                "message": "; ".join(remaining_static[:8]),
                                "stage": "ready_for_repair",
                                "created_at": time.time(),
                            }
                            verification_note = (
                                "V6 REPAIR DEBT REMAINS: " + "; ".join(remaining_static[:8]) + ". "
                                "The last repair was valid, but do not playtest yet; reduce the next static defect with one narrow same-script edit."
                            )
                        else:
                            gate["stage"] = "need_playtest"
                            gate["repair_reason"] = ""
                            verification_note = "Edit reread matches the intended source and passed all V6 static checks."
                    else:
                        # Unknown edit schema: reread is still authoritative. Run conservative structural checks.
                        if defects or remaining_static:
                            debt = defects or remaining_static
                            gate["stage"] = "repair_allowed"
                            gate["repair_reason"] = "; ".join(debt[:8])
                            state["current_blocker"] = {
                                "classification": "static_source_defect",
                                "path": actual,
                                "line": 0,
                                "message": "; ".join(debt[:8]),
                                "stage": "ready_for_repair",
                                "created_at": time.time(),
                            }
                            verification_note = (
                                "POST-EDIT STATIC DEFECT: " + "; ".join(debt[:8]) + ". "
                                "One narrow corrective edit is allowed before playtest."
                            )
                        else:
                            gate["stage"] = "need_playtest"
                            verification_note = "Edit reread recorded as authoritative current source and passed V6 static checks."

            # V6.1 invariant: once the authoritative reread is clean and the
            # gate advances to need_playtest, a stale static-source blocker for
            # that same script must not survive. Otherwise the blocker forbids
            # Play while the gate requires it, creating an impossible loop.
            if isinstance(gate, dict) and gate.get("stage") == "need_playtest":
                active = state.get("current_blocker")
                if (
                    isinstance(active, dict)
                    and active.get("classification") == "static_source_defect"
                    and target_matches(active.get("path") or "", actual)
                    and not (raw_static_source_defects(actual_source) if actual_source else [])
                ):
                    state["current_blocker"] = None
        state_update(after_read)
        with _state_lock:
            gate = STATE.get("gate")
            blocker = STATE.get("current_blocker")
        if isinstance(blocker, dict) and blocker.get("stage") == "ready_for_edit":
            notes.append("Direct evidence gathered for the active blocker. One evidence-based repair edit is now allowed.")
        if verification_note:
            notes.append(verification_note)
        if isinstance(gate, dict) and gate.get("stage") == "need_playtest":
            notes.append("MANDATORY NEXT: start Play before another write.")
        elif isinstance(gate, dict) and gate.get("stage") == "repair_allowed":
            notes.append(
                "REPAIR EXCEPTION ACTIVE: fix only the verified source defect in the same script, then re-read again. "
                "Do not broaden scope or playtest yet."
            )

    # Other inspect/read evidence can satisfy a generic blocker if there is no exact script target.
    if n in READ_EVIDENCE_TOOLS and n != "script_read" and not is_error:
        def generic_evidence(state: dict[str, Any]):
            blocker = state.get("current_blocker")
            if isinstance(blocker, dict) and blocker.get("stage") == "need_evidence":
                if not blocker.get("path"):
                    blocker["stage"] = "ready_for_edit"
        state_update(generic_evidence)

    # Playtest gate.
    if n == "start_stop_play" and args.get("is_start") is True and not is_error:
        def after_play(state: dict[str, Any]):
            gate = state.get("gate")
            if isinstance(gate, dict) and gate.get("stage") == "need_playtest":
                gate["stage"] = "need_output"
                # The successful required Play transition proves the repaired
                # source has entered runtime verification. Retire any matching
                # persisted repair_applied blocker so Output cannot be deadlocked.
                blocker = state.get("current_blocker")
                if repaired_static_blocker_is_stale(blocker, gate):
                    state["current_blocker"] = None
        state_update(after_play)
        with _state_lock:
            gate = STATE.get("gate")
        if isinstance(gate, dict) and gate.get("stage") == "need_output":
            notes.append("Playtest started. MANDATORY NEXT: check Output before another write.")

    # Console/output: detect relevant runtime errors, otherwise advance gate.
    if n in OUTPUT_TOOLS and not is_error:
        with _state_lock:
            gate_snapshot = dict(STATE.get("gate") or {}) if isinstance(STATE.get("gate"), dict) else None
            last_target = STATE.get("last_script_target") or ""
        detected = classify_error_text(text)
        relevant = False
        if detected:
            error_path = detected.get("path") or ""
            target = (gate_snapshot or {}).get("target") or last_target
            # An error from the script we just changed is relevant.  AssistantCommand
            # was already filtered by classify_error_text.
            relevant = not target or not error_path or target_matches(target, error_path)
        if detected and relevant:
            def runtime_fail(state: dict[str, Any]):
                state["runtime_error_count"] = int(state.get("runtime_error_count", 0)) + 1
                fresh = dict(detected)
                if fresh.get("classification") == "syntax_error":
                    fresh["stage"] = "need_stop_play" if state.get("studio_mode") == "play" else "need_source_read"
                state["current_blocker"] = fresh
                # Runtime error supersedes the normal post-edit progression.
                state["gate"] = None
                last = state.get("last_mutation")
                if isinstance(last, dict) and last.get("signature"):
                    failures = list(state.get("failed_mutation_signatures", []))
                    if last["signature"] not in failures:
                        failures.append(last["signature"])
                    state["failed_mutation_signatures"] = failures[-20:]
            state_update(runtime_fail)
            notes.append("RUNTIME BLOCKER DETECTED. " + blocker_required_message(detected))
        else:
            def output_ok(state: dict[str, Any]):
                gate = state.get("gate")
                if isinstance(gate, dict) and gate.get("stage") == "need_output":
                    req = gate.get("runtime_requirements") or {}
                    if any(bool(v) for v in req.values()):
                        gate["stage"] = "need_runtime_verify"
                    elif gate.get("visual"):
                        gate["stage"] = "need_visual"
                    else:
                        state["gate"] = None
                        # If an old blocker was a repair that now survived Output,
                        # resolve it mechanically.
                        b = state.get("current_blocker")
                        if isinstance(b, dict) and b.get("stage") == "repair_applied":
                            state["current_blocker"] = None
            state_update(output_ok)
            with _state_lock:
                gate = STATE.get("gate")
            if isinstance(gate, dict) and gate.get("stage") == "need_runtime_verify":
                notes.append(
                    "No relevant runtime error detected. MANDATORY NEXT: "
                    + runtime_requirement_message(gate.get("runtime_requirements"))
                    + ". Do not visually verify or write again until these post-edit measurements are recorded."
                )
            elif isinstance(gate, dict) and gate.get("stage") == "need_visual":
                notes.append("No relevant runtime error detected. MANDATORY NEXT: visually verify with screen_capture before another write/claim of success.")
            else:
                notes.append("No relevant runtime error detected for the edited script. Runtime verification gate passed.")

    # Visual verification completes the post-edit gate.
    if n in VISUAL_TOOLS and not is_error:
        def visual_done(state: dict[str, Any]):
            gate = state.get("gate")
            if isinstance(gate, dict) and gate.get("stage") == "need_visual":
                state["gate"] = None
                b = state.get("current_blocker")
                if isinstance(b, dict) and b.get("stage") == "repair_applied":
                    state["current_blocker"] = None
        state_update(visual_done)
        notes.append("Visual verification step recorded. The post-edit gate is clear; continue only if the visual evidence actually matches the goal.")

    # If a read/inspect tool itself returns an obvious runtime error, record it too.
    if n not in OUTPUT_TOOLS and not is_error:
        immediate = classify_error_text(text)
        if immediate and "assistantcommand" not in norm(text):
            def immediate_fail(state: dict[str, Any]):
                state["runtime_error_count"] = int(state.get("runtime_error_count", 0)) + 1
                fresh = dict(immediate)
                if fresh.get("classification") == "syntax_error":
                    fresh["stage"] = "need_stop_play" if state.get("studio_mode") == "play" else "need_source_read"
                state["current_blocker"] = fresh
            state_update(immediate_fail)
            notes.append("Runtime blocker detected from tool result. " + blocker_required_message(immediate))

    # New direct evidence unlocks an exact retry. The rule is therefore:
    # no identical retry WITHOUT new evidence, rather than "never retry ever".
    if n in READ_EVIDENCE_TOOLS and not is_error:
        state_update(lambda s: s.__setitem__("failed_mutation_signatures", []))

    # Account for result text in the UI-mode heuristic context meter and write
    # a compact resume checkpoint after every completed tool action.
    account_context_traffic(chars=len(text) + sum(len(x) for x in notes), tool_call=False)
    record_action("result_error" if is_error else "result", name, args, notes[-1] if notes else "")
    handoff_note = context_handoff_note_once()
    if handoff_note:
        notes.append(handoff_note)
    if notes:
        state_update(lambda s: s.__setitem__("last_note", " ".join(notes)))
    try:
        with _state_lock:
            blocker_snapshot = copy.deepcopy(STATE.get("current_blocker"))
        if is_error:
            telemetry_record_failure(
                "tool_error",
                f"Roblox MCP tool {name} returned an error",
                tool_name=name,
                arguments=args,
                response_excerpt=clip(text, 6000),
                severity="error",
            )
        elif isinstance(blocker_snapshot, dict):
            telemetry_record_failure(
                "active_blocker",
                str(blocker_snapshot.get("message") or blocker_snapshot.get("classification") or "Controller blocker"),
                tool_name=name,
                arguments=args,
                response_excerpt=clip(text, 6000),
                severity="warning" if blocker_snapshot.get("classification") == "static_source_defect" else "error",
                extra={"blocker": blocker_snapshot},
            )
    except Exception as exc:
        log(f"tool-result telemetry hook failed: {exc!r}")
    refresh_checkpoint_files()
    return "\n".join(notes)


# -----------------------------------------------------------------------------
# Tool list augmentation
# -----------------------------------------------------------------------------

SUPERVISOR_STATUS_TOOL = {
    "name": "supervisor_status",
    "description": (
        "Optional visibility only. Returns the mandatory proxy's current deterministic gate/state. "
        "You never need to call this for enforcement to work."
    ),
    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
}


SUPERVISOR_RESUME_TOOL = {
    "name": "supervisor_resume",
    "description": (
        "Returns the controller's compact persistent engineering checkpoint: active blocker, verification gate, verified evidence, "
        "last mutation, and exact next action. In a fresh chat call with new_chat=true so the UI-mode context meter resets without "
        "discarding task evidence. Enforcement itself is still automatic."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "new_chat": {"type": "boolean", "description": "Set true once at the beginning of a fresh LM Studio chat."}
        },
        "additionalProperties": False,
    },
}


def augment_tools_list(response: dict[str, Any]) -> dict[str, Any]:
    try:
        result = response.get("result")
        if not isinstance(result, dict):
            return response
        tools = result.get("tools")
        if not isinstance(tools, list):
            return response

        seen = set()
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            name = str(tool.get("name", ""))
            seen.add(name)
            schema = tool.get("inputSchema")
            if name and isinstance(schema, dict):
                TOOL_SCHEMAS[name] = schema
            desc = str(tool.get("description", ""))
            suffix = ""
            if tool_is_script_mutation(name, {}):
                suffix = (
                    "\n\nENFORCED: after a successful script edit, this proxy requires a script re-read, "
                    "playtest, Output check, and visual verification when applicable before another write."
                )
            elif name == "script_read":
                suffix = (
                    "\n\nENFORCED DEBUGGING NOTE: if this reports Script not found, check Studio mode before changing the path; "
                    "script reads may need Edit mode."
                )
            elif name in OUTPUT_TOOLS:
                suffix = (
                    "\n\nENFORCED: relevant runtime errors become active blockers and require direct evidence before another write."
                )
            if suffix and suffix not in desc:
                tool["description"] = desc + suffix

        if "supervisor_status" not in seen:
            tools.append(SUPERVISOR_STATUS_TOOL)
        if "supervisor_resume" not in seen:
            tools.append(SUPERVISOR_RESUME_TOOL)
        return response
    except Exception as exc:
        log(f"augment tools failed: {exc!r}")
        return response


def status_payload() -> dict[str, Any]:
    with _state_lock:
        s = dict(STATE)
    gate = s.get("gate")
    blocker = s.get("current_blocker")
    return {
        "enforcement_active": True,
        "proxy": APP_NAME,
        "version": VERSION,
        "studio_mode": s.get("studio_mode"),
        "current_blocker": blocker,
        "gate": gate,
        "last_script_target": s.get("last_script_target"),
        "blocked_count": s.get("blocked_count", 0),
        "forwarded_count": s.get("forwarded_count", 0),
        "tool_error_count": s.get("tool_error_count", 0),
        "runtime_error_count": s.get("runtime_error_count", 0),
        "runtime_evidence": s.get("runtime_evidence", {}),
        "context_estimate": s.get("context_estimate", {}),
        "mutation_epoch": s.get("mutation_epoch", 0),
        "next_required_action": next_required_action_from_state(s),
        "cached_tool_schemas": len(TOOL_SCHEMAS),
        "cached_script_sources": len(SOURCE_CACHE),
        "last_note": s.get("last_note", ""),
        "resume_file": str(RESUME_FILE),
        "checkpoint_file": str(CHECKPOINT_FILE),
        "state_file": str(STATE_FILE),
        "log_file": str(LOG_FILE),
        "telemetry_dir": str(TELEMETRY_DIR),
        "telemetry_status_file": str(TELEMETRY_STATUS_FILE),
        "telemetry_latest_failure_file": str(TELEMETRY_FAILURE_FILE),
        "telemetry_test_results_file": str(TELEMETRY_TEST_RESULTS_FILE),
        "controller_health": controller_health_payload(),
        "important": (
            "Enforcement is automatic. The model does NOT need to call supervisor tools. "
            "Direct roblox-studio integration must remain disabled or the model can bypass this proxy."
        ),
    }


# -----------------------------------------------------------------------------
# Transparent JSON-RPC stdio proxy
# -----------------------------------------------------------------------------

_pending_lock = threading.Lock()
# id -> {method, tool_name, arguments}
PENDING: dict[str, dict[str, Any]] = {}

_stdout_lock = threading.Lock()


def emit(obj: dict[str, Any]) -> None:
    # ASCII-escaped JSON is valid UTF-8 JSON and prevents Windows locale
    # encodings (notably cp1252) from ever killing the MCP stdout thread.
    line = json.dumps(obj, ensure_ascii=True, separators=(",", ":"))
    with _stdout_lock:
        try:
            # Prefer raw UTF-8 bytes so TextIOWrapper locale settings cannot
            # corrupt or reject a Roblox response.
            buffer = getattr(sys.stdout, "buffer", None)
            if buffer is not None:
                buffer.write((line + "\n").encode("utf-8"))
                buffer.flush()
            else:
                sys.stdout.write(line + "\n")
                sys.stdout.flush()
        except Exception:
            log("emit exception:\n" + traceback.format_exc())
            raise


def request_key(request_id: Any) -> str:
    return json.dumps(request_id, sort_keys=True, ensure_ascii=False)


def start_child() -> subprocess.Popen[str]:
    if os.name == "nt":
        if not ROBLOX_MCP_BAT.exists():
            raise FileNotFoundError(f"Roblox MCP launcher not found: {ROBLOX_MCP_BAT}")
        cmd = ["cmd.exe", "/d", "/c", str(ROBLOX_MCP_BAT)]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        # This proxy is intended for the user's Windows LM Studio setup.
        raise RuntimeError("This proxy must run on Windows because Roblox Studio MCP uses mcp.bat.")

    log("Launching official Roblox MCP child: " + " ".join(cmd))
    return subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=creationflags,
    )


def child_stderr_loop(child: subprocess.Popen[str]) -> None:
    assert child.stderr is not None
    try:
        for line in child.stderr:
            if line:
                clean = line.rstrip()
                log("ROBLOX STDERR " + clean)
                update_controller_health(last_roblox_stderr=clip(clean, 4000))
    except Exception:
        err = traceback.format_exc()
        update_controller_health(last_exception=clip(err, 6000))
        log("stderr thread exception:\n" + err)


def handle_child_message(message: dict[str, Any]) -> dict[str, Any] | None:
    # Responses have id but no method.  Notifications/requests from child are passed through.
    if "id" not in message or "method" in message:
        return message

    key = request_key(message.get("id"))
    with _pending_lock:
        pending = PENDING.pop(key, None)
    if not pending:
        return message

    method = pending.get("method", "")
    if method == "tools/list":
        return augment_tools_list(message)

    if method == "initialize":
        try:
            result = message.get("result")
            if isinstance(result, dict):
                old = str(result.get("instructions", "")).strip()
                extra = (
                    "A mandatory deterministic Roblox executive proxy V6 is active. Use official Roblox tools normally; enforcement is automatic. "
                    "It preserves official tool names/schemas, validates datamodel mode, caches verified evidence across Play/Edit toggles, blocks redundant loops, "
                    "preflights script edits against current script_read source, rejects obvious unbalanced partial Luau edits, and verifies every write by reread. "
                    "Concrete syntax errors have a deadlock-free recovery path: stop Play -> read implicated source -> one narrow same-script repair -> reread. "
                    "Do not repeatedly re-inspect evidence already verified unless a relevant successful write invalidated it. "
                    "Do not use name-based body/accessory classification, Accessory.RootPart, or unverified Roblox members. "
                    "Roblox child dot indexing is valid: instance.OriginalSize.Value can be correct when OriginalSize is a real child; do not rewrite it solely because it is a child. "
                    "In a fresh chat, call supervisor_resume(new_chat=true) once to restore compact task state without old narration. "
                    "Follow short SUPERVISOR BLOCK/NEXT messages literally instead of reasoning around them."
                )
                result["instructions"] = (old + "\n\n" + extra).strip()
        except Exception:
            pass
        return message

    if method == "tools/call":
        name = pending.get("tool_name", "")
        args = pending.get("arguments", {})
        mutation_plan = pending.get("mutation_plan") if isinstance(pending, dict) else None
        note = on_tool_result(name, args, message, mutation_plan=mutation_plan)
        if note:
            message = append_tool_note(message, note)
        return message

    return message


def child_stdout_loop(child: subprocess.Popen[str]) -> None:
    assert child.stdout is not None
    try:
        for raw in child.stdout:
            line = raw.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except Exception:
                # Never leak non-JSON child stdout into MCP stdout.
                log("ROBLOX NONJSON STDOUT " + clip(line, 2000))
                continue
            if not isinstance(message, dict):
                log("ROBLOX unexpected JSON stdout " + clip(message, 1000))
                continue
            out = handle_child_message(message)
            if out is not None:
                emit(out)
    except Exception:
        err = traceback.format_exc()
        update_controller_health(last_exception=clip(err, 6000))
        telemetry_record_failure("roblox_mcp_stdout_exception", "Roblox MCP stdout forwarding thread crashed", response_excerpt=clip(err, 6000), severity="critical")
        log("stdout thread exception:\n" + err)
    finally:
        rc = child.poll()
        update_controller_health(roblox_child_running=False, roblox_child_returncode=rc)
        telemetry_record_failure("roblox_mcp_disconnected", f"Roblox MCP child stdout closed (return code {rc})", severity="critical", extra={"returncode": rc})
        log(f"Roblox child stdout closed rc={rc}")


def forward_to_child(child: subprocess.Popen[str], message: dict[str, Any]) -> None:
    assert child.stdin is not None
    child.stdin.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
    child.stdin.flush()


def handle_parent_message(child: subprocess.Popen[str], message: dict[str, Any]) -> None:
    method = str(message.get("method", ""))
    request_id = message.get("id", None)

    if method == "tools/call":
        params = message.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        name = str(params.get("name", ""))
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            args = {}

        # Local controller tools. They never touch Roblox and cannot be bypassed.
        if name == "supervisor_status":
            if "id" in message:
                emit(mcp_tool_ok_response(request_id, status_payload()))
            return
        if name == "supervisor_resume":
            if args.get("new_chat") is True:
                reset_context_meter_for_new_chat()
                refresh_checkpoint_files()
            packet = build_resume_packet()
            if "id" in message:
                emit(mcp_tool_ok_response(request_id, packet))
            return

        reason = block_reason_for_call(name, args)
        if reason:
            on_local_block(reason, name, args)
            if "id" in message:
                emit(mcp_tool_error_response(request_id, reason))
            return

        mutation_plan: dict[str, Any] | None = None
        if tool_is_script_mutation(name, args):
            candidate, preflight_reason, defects = build_expected_source(name, args)
            if preflight_reason:
                on_local_block(preflight_reason, name, args)
                if "id" in message:
                    emit(mcp_tool_error_response(request_id, preflight_reason))
                return
            target = extract_target(args)
            previous = source_cache_get(target)
            mutation_plan = {
                "target": target,
                "previous_source": previous or None,
                "expected_source": candidate,
                "defects": defects,
            }

        on_forwarded_call(name, args)

        if "id" in message:
            with _pending_lock:
                PENDING[request_key(request_id)] = {
                    "method": method,
                    "tool_name": name,
                    "arguments": args,
                    "mutation_plan": mutation_plan,
                    "at": time.time(),
                }
        forward_to_child(child, message)
        return

    # Cache method for response transformation.
    if "id" in message and method:
        with _pending_lock:
            PENDING[request_key(request_id)] = {"method": method, "at": time.time()}

    forward_to_child(child, message)


# -----------------------------------------------------------------------------
# Optional controller-owned LM Studio REST autopilot
# -----------------------------------------------------------------------------

AUTOPILOT_SYSTEM_PROMPT = """You are Qwen operating Roblox Studio through the mandatory mcp/qwen-roblox-enforced controller.
The controller is your executive function and the official Roblox Studio MCP is your hands.
Work autonomously and prefer tool evidence over speculation.
Follow every SUPERVISOR BLOCK/NEXT instruction literally.
Do not repeatedly explain plans or re-inspect evidence already stored by the controller.
Current Studio/source/controller state is authoritative, not previous narration.
In a fresh API chat, call supervisor_resume with new_chat=true before continuing.
Emit [TASK_COMPLETE] only when the controller gate is clear, no relevant blocker remains, Output was checked after the last gameplay edit, and required visual verification passed.
"""


def _lmstudio_http_json(url: str, payload: dict[str, Any], token: str = "", timeout: int = 3600) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        raise RuntimeError(f"LM Studio API HTTP {exc.code}: {body[:2000]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach LM Studio API at {url}: {exc}") from exc
    obj = json.loads(raw)
    if not isinstance(obj, dict):
        raise RuntimeError("LM Studio API returned non-object JSON")
    return obj


def _autopilot_messages(response: dict[str, Any]) -> list[str]:
    messages: list[str] = []
    output = response.get("output")
    if isinstance(output, list):
        for item in output:
            if isinstance(item, dict) and item.get("type") == "message" and isinstance(item.get("content"), str):
                messages.append(item["content"])
    return messages


def _autopilot_checkpoint_text() -> str:
    try:
        if RESUME_FILE.exists():
            text = RESUME_FILE.read_text(encoding="utf-8", errors="replace").strip()
            if text:
                return text[:7000]
    except Exception:
        pass
    # Fall back to disk state rather than this process's possibly stale in-memory
    # STATE when an MCP child process is updating the same persistent files.
    try:
        if STATE_FILE.exists():
            raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return build_resume_packet(raw)
    except Exception:
        pass
    return "No controller checkpoint was available; start from current Studio state and inspect before guessing."


def _autopilot_prompt_from_args(ns: argparse.Namespace) -> str:
    if ns.prompt:
        return ns.prompt
    if ns.prompt_file:
        return Path(ns.prompt_file).read_text(encoding="utf-8", errors="replace")
    if RESUME_FILE.exists():
        return (
            "Continue the current Roblox task autonomously from the controller checkpoint. "
            "Call supervisor_resume(new_chat=true) first. Do not reconstruct old narration."
        )
    if sys.stdin.isatty():
        sys.stderr.write("Enter the Roblox task prompt, then press Enter:\n> ")
        sys.stderr.flush()
        return sys.stdin.readline().strip()
    return "Continue the current Roblox task autonomously from current Studio state. Inspect before guessing."


def autopilot_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog=Path(sys.argv[0]).name + " --autopilot",
        description="Controller-owned Qwen/Roblox agent loop with exact LM Studio context rollover.",
    )
    parser.add_argument("--autopilot", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--prompt", default="", help="Initial task prompt.")
    parser.add_argument("--prompt-file", default="", help="Read initial task prompt from a UTF-8 text file.")
    parser.add_argument("--model", default=LM_STUDIO_MODEL)
    parser.add_argument("--base-url", default=LM_STUDIO_BASE_URL)
    parser.add_argument("--api-token", default=LM_STUDIO_API_TOKEN)
    parser.add_argument("--integration", default=MCP_INTEGRATION_ID)
    parser.add_argument("--context-length", type=int, default=CONTEXT_WINDOW_TOKENS)
    parser.add_argument("--rollover-at", type=int, default=CONTEXT_ROLLOVER_TRIGGER)
    parser.add_argument("--reasoning", choices=["off", "low", "medium", "high", "on"], default="off")
    parser.add_argument("--temperature", type=float, default=0.1)
    parser.add_argument("--max-output-tokens", type=int, default=4096)
    parser.add_argument("--max-cycles", type=int, default=200)
    parser.add_argument("--once", action="store_true", help="Run only one LM Studio API turn.")
    ns = parser.parse_args(argv)

    if ns.context_length < 4096:
        raise SystemExit("--context-length must be at least 4096")
    # Do not wait until the exact ceiling; leave enough room for the response and
    # tool outputs. A requested value >= context length is automatically clamped.
    rollover_at = min(ns.rollover_at, max(2048, ns.context_length - 2000))
    url = ns.base_url.rstrip("/") + "/api/v1/chat"
    current_input = _autopilot_prompt_from_args(ns).strip()
    if not current_input:
        raise SystemExit("No task prompt provided.")

    previous_response_id: str | None = None
    rollovers = 0
    cycles = 0
    run_id = f"run-{int(time.time() * 1000)}-{os.getpid()}"
    telemetry_record_autopilot(
        "autopilot_start",
        run_id=run_id,
        model=ns.model,
        integration=ns.integration,
        context_length=ns.context_length,
        rollover_at=rollover_at,
        max_cycles=ns.max_cycles,
        prompt=clip(current_input, 8000),
    )
    print(f"[AUTOPILOT] model={ns.model} context={ns.context_length} rollover_at={rollover_at}")
    print(f"[AUTOPILOT] integration={ns.integration}")
    print("[AUTOPILOT] Ctrl+C stops the loop.\n")

    while cycles < ns.max_cycles:
        cycles += 1
        body: dict[str, Any] = {
            "model": ns.model,
            "input": current_input,
            "system_prompt": AUTOPILOT_SYSTEM_PROMPT,
            "integrations": [ns.integration],
            "context_length": ns.context_length,
            "temperature": ns.temperature,
            "reasoning": ns.reasoning,
            "max_output_tokens": ns.max_output_tokens,
            "store": True,
        }
        if previous_response_id:
            body["previous_response_id"] = previous_response_id

        try:
            response = _lmstudio_http_json(url, body, ns.api_token)
        except KeyboardInterrupt:
            telemetry_record_autopilot("autopilot_stopped", run_id=run_id, cycle=cycles, reason="keyboard_interrupt")
            print("\n[AUTOPILOT] stopped by user.")
            return 130
        except Exception as exc:
            telemetry_record_failure(
                "lm_studio_api_error",
                str(exc),
                severity="critical",
                extra={"run_id": run_id, "cycle": cycles, "model": ns.model, "url": url},
            )
            telemetry_record_autopilot("autopilot_api_error", run_id=run_id, cycle=cycles, error=str(exc))
            print(f"[AUTOPILOT] API error: {exc}", file=sys.stderr)
            return 3

        messages = _autopilot_messages(response)
        for msg in messages:
            print(msg)

        stats = response.get("stats") if isinstance(response.get("stats"), dict) else {}
        input_tokens = int(stats.get("input_tokens", 0) or 0)
        output_tokens = int(stats.get("total_output_tokens", 0) or 0)
        reasoning_tokens = int(stats.get("reasoning_output_tokens", 0) or 0)
        rid = response.get("response_id")
        if isinstance(rid, str) and rid.startswith("resp_"):
            previous_response_id = rid

        print(
            f"[AUTOPILOT] cycle={cycles} input_tokens={input_tokens} "
            f"output_tokens={output_tokens} reasoning_tokens={reasoning_tokens} rollovers={rollovers}",
            file=sys.stderr,
        )
        telemetry_record_autopilot(
            "autopilot_cycle",
            run_id=run_id,
            cycle=cycles,
            response_id=previous_response_id or "",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            rollovers=rollovers,
            messages=[clip(m, 6000) for m in messages[-4:]],
        )

        # Stop only on the explicit marker. The system prompt tells Qwen not to
        # emit it until the controller's verification state is actually clean.
        if any("[TASK_COMPLETE]" in msg for msg in messages):
            telemetry_record_autopilot("autopilot_complete", run_id=run_id, cycle=cycles, rollovers=rollovers)
            print("[AUTOPILOT] task complete.", file=sys.stderr)
            return 0
        if ns.once:
            telemetry_record_autopilot("autopilot_once_complete", run_id=run_id, cycle=cycles)
            return 0

        # Exact automatic rollover: LM Studio's native API reports input_tokens
        # including prior messages/tool definitions. When near the 40k context
        # ceiling, omit previous_response_id and seed a new chat only with the
        # compact persistent controller checkpoint.
        projected = input_tokens + output_tokens
        if projected >= rollover_at:
            rollovers += 1
            checkpoint = _autopilot_checkpoint_text()
            previous_response_id = None
            current_input = (
                "AUTOMATIC CONTEXT ROLLOVER. This is a fresh chat.\n"
                "Call supervisor_resume(new_chat=true) first, then continue autonomously.\n"
                "Do not repeat verified evidence or reconstruct the previous transcript.\n\n"
                + checkpoint
            )
            telemetry_record_autopilot(
                "autopilot_rollover", run_id=run_id, cycle=cycles, rollovers=rollovers, projected_context=projected
            )
            print(
                f"[AUTOPILOT] automatic new API chat #{rollovers} at projected_context={projected} tokens.",
                file=sys.stderr,
            )
            continue

        current_input = (
            "Continue autonomously from current controller/Studio state. "
            "Do not repeat verified evidence or long plans. Follow the controller's exact next required action. "
            "If and only if the task is fully verified, emit [TASK_COMPLETE]."
        )

    telemetry_record_failure(
        "autopilot_max_cycles",
        f"Autopilot reached max cycles ({ns.max_cycles}) without completion marker",
        severity="error",
        extra={"run_id": run_id, "cycles": cycles, "rollovers": rollovers, "model": ns.model},
    )
    telemetry_record_autopilot("autopilot_max_cycles", run_id=run_id, cycles=cycles, rollovers=rollovers)
    print(f"[AUTOPILOT] reached max cycles ({ns.max_cycles}) without completion marker.", file=sys.stderr)
    return 4



def self_test_main() -> int:
    """Offline V6 regression tests for the mistakes observed in this project."""
    failures: list[str] = []

    def expect_reject(label: str, previous: str, candidate: str, contains: str = "") -> None:
        defects = structural_source_defects(candidate, previous, {"edits": []}, "multi_edit")
        joined = " | ".join(defects)
        if not defects or (contains and contains.lower() not in joined.lower()):
            failures.append(f"{label}: expected rejection containing {contains!r}; got {joined!r}")

    def expect_accept(label: str, previous: str, candidate: str) -> None:
        defects = structural_source_defects(candidate, previous, {"edits": []}, "multi_edit")
        if defects:
            failures.append(f"{label}: expected acceptance; got {' | '.join(defects)}")

    good = """local function flattenBodyParts(character)\n\tfor _, descendant in ipairs(character:GetChildren()) do\n\t\tif descendant:IsA("BasePart") then\n\t\t\tlocal original = descendant:FindFirstChild("OriginalSize")\n\t\t\tif original and original:IsA("Vector3Value") then\n\t\t\t\tlocal originalSize = original.Value\n\t\t\t\tdescendant.Size = Vector3.new(originalSize.X, originalSize.Y, 0.01)\n\t\t\tend\n\t\tend\n\tend\nend"""
    expect_accept("valid flatten helper", good, good)

    missing_end = """local function flattenBodyParts(character)\n\tfor _, descendant in ipairs(character:GetChildren()) do\n\t\tif descendant:IsA("BasePart") then\n\t\t\tprint(descendant)\n\t\tend\n\tend"""
    expect_reject("missing end", good, missing_end, "unclosed function")

    undefined_helper = """local function run(character)\n\tif isTypicalBodyPart(character.Name) then\n\t\tprint(character)\n\tend\nend"""
    expect_reject("undefined helper", good, undefined_helper, "isTypicalBodyPart")

    instance_as_vector = """local function run(part)\n\tlocal original = part:FindFirstChild("OriginalSize")\n\tif original then\n\t\tpart.Size = Vector3.new(original.X, original.Y, 0.01)\n\tend\nend"""
    expect_reject("Instance used as Vector3", good, instance_as_vector, "Instance-returning")

    invalid_operator = """local x = 1\nx := 2"""
    expect_reject("invalid Luau operator", good, invalid_operator, "invalid ':='")

    unclosed_paren = """local function run()\n\tprint((1 + 2)\nend"""
    expect_reject("unclosed delimiter", good, unclosed_paren, "unclosed delimiter")

    # Verify exact transactional simulation catches a malformed one-line proposal.
    target = "ServerScriptService.FlatCharacterScript"
    SOURCE_CACHE.clear(); source_cache_set(target, good)
    args = {"path": target, "edits": [{"old_string": "\t\t\tlocal originalSize = original.Value", "new_string": "\t\t\tif original.Value then\n\t\t\t\tlocal originalSize = original.Value"}]}
    candidate, reason, _ = build_expected_source("multi_edit", args)
    if not reason or "compiler transaction" not in reason.lower():
        failures.append(f"transaction malformed edit: expected compiler rejection; got {reason!r}")

    # Broken source may be repaired incrementally: removing one existing defect
    # without introducing a new category must be allowed.
    broken = missing_end + "\n\nisTypicalBodyPart(nil)"
    repaired_one = missing_end + "\nend\n\nisTypicalBodyPart(nil)"
    debt_result = structural_source_defects(repaired_one, broken, {"edits": []}, "multi_edit")
    if debt_result:
        failures.append(f"incremental defect repair was incorrectly blocked: {debt_result}")

    # But an unrelated edit that leaves all existing debt unchanged is blocked.
    unchanged_debt = broken + "\nprint('unrelated')"
    debt_result = structural_source_defects(unchanged_debt, broken, {"edits": []}, "multi_edit")
    if not any("does not reduce" in x for x in debt_result):
        failures.append(f"unchanged defect debt was not blocked: {debt_result}")

    # execute_luau source mutation is never allowed as an unsimulatable bypass.
    _, reason, _ = build_expected_source("execute_luau", {"code": "script.Source = 'print(1)'"})
    if not reason or "execute_luau" not in reason:
        failures.append("execute_luau Source bypass was not rejected")

    invalid_isa = """local function run(descendant)\n\tif descendant:IsA("HumanoidRootPart") then\n\t\tprint(descendant)\n\tend\nend"""
    expect_reject("invalid HumanoidRootPart IsA", good, invalid_isa, "instance name")

    unsafe_original_size = """local function flattenAccessories(character)\n\tfor _, descendant in ipairs(character:GetDescendants()) do\n\t\tif descendant:IsA("Accessory") then\n\t\t\tlocal handle = descendant.Handle\n\t\t\tif handle and handle.OriginalSize.Value then\n\t\t\t\tprint(handle.OriginalSize.Value)\n\t\t\tend\n\t\tend\n\tend\nend"""
    expect_reject("unsafe accessory OriginalSize", good, unsafe_original_size, "FindFirstChild")

    sanitized_meter = _telemetry_sanitize({
        "estimated_tokens": 1234,
        "window_tokens": 40000,
        "exact_input_tokens": 999,
        "access_token": "secret-value",
    })
    if sanitized_meter.get("estimated_tokens") != 1234 or sanitized_meter.get("window_tokens") != 40000 or sanitized_meter.get("exact_input_tokens") != 999:
        failures.append(f"telemetry token metrics were incorrectly redacted: {sanitized_meter}")
    if sanitized_meter.get("access_token") != "[REDACTED]":
        failures.append("sensitive access_token was not redacted")

    # V6.2 regression: repaired source blocker + need_playtest is a controller conflict.
    conflict_state = new_state()
    conflict_state["gate"] = {"stage": "need_playtest", "target": "game.ServerScriptService.Test"}
    conflict_state["current_blocker"] = {
        "classification": "static_source_defect",
        "stage": "repair_applied",
        "path": "game.ServerScriptService.Test",
    }
    if _failure_classification("controller_state_conflict", "gate/blocker conflict", conflict_state) != "controller_bug":
        failures.append("controller state conflict was not classified as controller_bug")
    packet = _compact_failure_packet("controller_state_conflict", "gate/blocker conflict", "start_stop_play", {"is_start": True}, conflict_state)
    if packet.get("classification") != "controller_bug" or not packet.get("regression_id"):
        failures.append(f"compact failure packet invalid: {packet}")

    # V6.3.2 regression: a persisted repair_applied static blocker must not
    # deadlock the post-repair verification pipeline after Play starts.
    persisted = new_state()
    persisted["studio_mode"] = "play"
    persisted["gate"] = {"stage": "need_output", "target": "game.ServerScriptService.Test"}
    persisted["current_blocker"] = {
        "classification": "static_source_defect",
        "stage": "repair_applied",
        "path": "game.ServerScriptService.Test",
        "message": "stale repaired defect",
    }
    if not repaired_static_blocker_is_stale(persisted["current_blocker"], persisted["gate"]):
        failures.append("V6.3.2 stale repaired blocker was not recognized during need_output")

    with _state_lock:
        saved_state = copy.deepcopy(STATE)
        STATE.clear()
        STATE.update(copy.deepcopy(persisted))
    try:
        reason = block_reason_for_call("get_console_output", {})
        if reason:
            failures.append(f"V6.3.2 get_console_output was blocked by stale repaired blocker: {reason}")
    finally:
        with _state_lock:
            STATE.clear()
            STATE.update(saved_state)

    # V6.3 regression: controller-bug packets are eligible for automatic
    # GitHub handoff, while non-controller failures are not.
    report_packet = dict(packet)
    if not _github_should_report(report_packet):
        failures.append("controller_bug packet was not eligible for GitHub reporting")
    no_report_packet = dict(report_packet)
    no_report_packet["classification"] = "runtime_or_tool_error"
    if _github_should_report(no_report_packet):
        failures.append("non-controller failure was incorrectly eligible for GitHub reporting")
    issue_body = _github_failure_issue_body(report_packet)
    if str(report_packet.get("regression_id")) not in issue_body or "controller_bug" not in issue_body:
        failures.append("GitHub failure issue body omitted required regression metadata")

    if failures:
        telemetry_write_test_results({
            "suite": "controller_self_test",
            "status": "failed",
            "passed": False,
            "failure_count": len(failures),
            "failures": failures,
        })
        telemetry_record_failure(
            "controller_self_test_failed",
            f"Controller self-test failed with {len(failures)} failure(s)",
            severity="critical",
            extra={"failures": failures},
        )
        print("V6.2 SELF-TEST FAILED")
        for row in failures:
            print(" -", row)
        return 1
    telemetry_write_test_results({
        "suite": "controller_self_test",
        "status": "passed",
        "passed": True,
        "failure_count": 0,
        "failures": [],
    })
    print("V6.2 SELF-TEST PASSED")
    print(" - missing end rejected")
    print(" - unclosed delimiters rejected")
    print(" - common non-Luau operators rejected")
    print(" - undefined bare helper rejected")
    print(" - local helper declaration-order bug rejected")
    print(" - FindFirstChild result used as Vector3 rejected")
    print(" - unsimulatable Source mutation rejected")
    print(" - valid guarded Vector3Value code accepted")
    return 0

def telemetry_smoke_test_main() -> int:
    """Create and validate the V6 telemetry snapshots without starting Roblox MCP."""
    refresh_telemetry_files()
    required = [TELEMETRY_STATUS_FILE, TELEMETRY_HEALTH_FILE, TELEMETRY_TEST_RESULTS_FILE]
    problems: list[str] = []
    for path in required:
        if not path.exists():
            problems.append(f"missing {path.name}")
            continue
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(parsed, dict):
                problems.append(f"{path.name} is not a JSON object")
        except Exception as exc:
            problems.append(f"{path.name} parse failed: {exc}")
    if problems:
        print("V6 TELEMETRY SMOKE TEST FAILED")
        for row in problems:
            print(" -", row)
        return 1
    print("V6.2 TELEMETRY SMOKE TEST PASSED")
    print(f" - telemetry directory: {TELEMETRY_DIR}")
    print(f" - status: {TELEMETRY_STATUS_FILE.name}")
    print(f" - health: {TELEMETRY_HEALTH_FILE.name}")
    print(f" - tests: {TELEMETRY_TEST_RESULTS_FILE.name}")
    return 0


def main() -> int:
    log(f"START {APP_NAME} v{VERSION} stdio=utf-8 ascii_json=true")
    update_controller_health(controller_running=True, controller_pid=os.getpid(), controller_started_at=time.time())
    refresh_telemetry_files()
    try:
        child = start_child()
    except Exception as exc:
        update_controller_health(controller_running=False, roblox_child_running=False, last_exception=repr(exc))
        telemetry_record_failure("roblox_mcp_start_failed", str(exc), severity="critical")
        log("FAILED TO START CHILD: " + repr(exc))
        # stderr is okay for launcher diagnostics; stdout must stay JSON only.
        sys.stderr.write(f"{APP_NAME}: {exc}\n")
        sys.stderr.flush()
        return 2

    update_controller_health(roblox_child_pid=child.pid, roblox_child_running=True, roblox_child_returncode=None)
    refresh_telemetry_files()
    threading.Thread(target=child_stdout_loop, args=(child,), daemon=True).start()
    threading.Thread(target=child_stderr_loop, args=(child,), daemon=True).start()

    try:
        for raw in sys.stdin:
            line = raw.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except Exception as exc:
                log(f"PARENT invalid JSON: {exc!r} :: {clip(line, 2000)}")
                continue
            if not isinstance(message, dict):
                log("PARENT unexpected JSON: " + clip(message, 1000))
                continue
            try:
                handle_parent_message(child, message)
            except BrokenPipeError:
                log("Roblox child pipe closed")
                break
            except Exception:
                err = traceback.format_exc()
                update_controller_health(last_exception=clip(err, 6000))
                telemetry_record_failure("controller_internal_error", "Unhandled controller exception while processing parent MCP message", response_excerpt=clip(err, 6000), severity="critical", extra={"method": message.get("method")})
                log("handle_parent_message exception:\n" + err)
                if "id" in message:
                    emit({
                        "jsonrpc": "2.0",
                        "id": message.get("id"),
                        "error": {"code": -32603, "message": "Enforced proxy internal error; see proxy.log"},
                    })
    finally:
        try:
            if child.stdin:
                child.stdin.close()
        except Exception:
            pass
        try:
            child.terminate()
        except Exception:
            pass
        update_controller_health(controller_running=False, roblox_child_running=False, roblox_child_returncode=child.poll())
        refresh_telemetry_files()
        log("STOP")
    return 0


if __name__ == "__main__":
    if "--telemetry-smoke-test" in sys.argv[1:]:
        raise SystemExit(telemetry_smoke_test_main())
    if "--self-test" in sys.argv[1:]:
        raise SystemExit(self_test_main())
    if "--autopilot" in sys.argv[1:]:
        raise SystemExit(autopilot_main(sys.argv[1:]))
    raise SystemExit(main())
