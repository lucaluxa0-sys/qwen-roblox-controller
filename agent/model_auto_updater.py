#!/usr/bin/env python3
"""Automatic Qwen model/config updater for QwenRobloxAgent.

Checks a declarative GitHub manifest, safely downloads a requested LM Studio
model before switching, retires only the autonomous runner/controller, unloads
only the previous manager-owned model, updates full_auto_config.json atomically,
and lets the existing full-auto manager reload Qwen and resume from checkpoint.
The updater also self-updates from the same hash-pinned manifest.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request

if os.name == "nt":
    import msvcrt

VERSION = "1.0.0"
ROOT = Path(__file__).resolve().parent
CONFIG_FILE = ROOT / "full_auto_config.json"
MODEL_LOAD_STATE = ROOT / "model_load_state.json"
FULL_AUTO_HEALTH = ROOT / "full_auto_health.json"
STATE_FILE = ROOT / "model_auto_updater_state.json"
LOG_FILE = ROOT / "model_auto_updater.log"
LOCK_FILE = ROOT / ".model_auto_updater.lock"
SELF_FILE = Path(__file__).resolve()

REPO = "lucaluxa0-sys/qwen-roblox-controller"
BRANCH = "main"
MANIFEST_PATH = "agent/model_auto_updater_latest.json"
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}"
USER_AGENT = "QwenRobloxModelAutoUpdater/1.0"
DEFAULT_INTERVAL = 300

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0)
CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


def log(message: str) -> None:
    try:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"{stamp} {message}\n")
    except Exception:
        pass


def read_json(path: Path) -> dict:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def write_state(status: str, **fields) -> None:
    payload = read_json(STATE_FILE)
    payload.update({
        "schema_version": 1,
        "updater_version": VERSION,
        "status": status,
        "updated_at": time.time(),
        **fields,
    })
    try:
        atomic_json(STATE_FILE, payload)
    except Exception:
        pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    try:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


def fetch_repo_file(path: str, timeout: int = 30) -> bytes:
    clean = str(path or "").strip().lstrip("/")
    if not clean:
        raise RuntimeError("empty repository path")
    nonce = str(int(time.time() * 1000))
    encoded = urllib.parse.quote(clean, safe="/")
    url = f"{RAW_BASE}/{encoded}?qwen_no_cache={nonce}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def hidden_run(args: list[str], timeout: int = 60) -> subprocess.CompletedProcess[str]:
    kwargs = dict(
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        stdin=subprocess.DEVNULL,
    )
    if os.name == "nt":
        kwargs["creationflags"] = CREATE_NO_WINDOW
    return subprocess.run(args, **kwargs)


def hidden_popen(args: list[str]) -> subprocess.Popen:
    kwargs = dict(
        cwd=str(ROOT),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if os.name == "nt":
        kwargs["creationflags"] = CREATE_NO_WINDOW | DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen(args, **kwargs)


def acquire_single_instance():
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    fh = LOCK_FILE.open("a+b")
    if os.name != "nt":
        return fh
    try:
        fh.seek(0)
        if fh.read(1) == b"":
            fh.write(b"0")
            fh.flush()
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        return fh
    except OSError:
        fh.close()
        return None


def find_lms() -> str:
    found = shutil.which("lms") or shutil.which("lms.exe")
    if found:
        return found
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    candidates = [
        local / "LM Studio" / "bin" / "lms.exe",
        local / "Programs" / "LM Studio" / "bin" / "lms.exe",
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    try:
        hits = list(local.glob("Programs/LM Studio/**/lms.exe"))
        if hits:
            return str(hits[0])
    except Exception:
        pass
    return ""


def lms_json(lms: str, args: list[str], timeout: int = 30):
    p = hidden_run([lms, *args], timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout or "lms command failed")[-1500:])
    text = (p.stdout or "").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except Exception:
        starts = [x for x in (text.find("{"), text.find("[")) if x >= 0]
        if starts:
            return json.loads(text[min(starts):])
        raise


def model_rows(obj) -> list[dict]:
    if isinstance(obj, list):
        return [x for x in obj if isinstance(x, dict)]
    if isinstance(obj, dict):
        for key in ("models", "data", "items"):
            if isinstance(obj.get(key), list):
                return [x for x in obj[key] if isinstance(x, dict)]
    return []


def model_available_on_disk(lms: str, desired: str) -> bool:
    rows = model_rows(lms_json(lms, ["ls", "--llm", "--json"], timeout=45))
    needle = str(desired or "").lower().replace("_", "-")
    for row in rows:
        values = [
            row.get("modelKey"), row.get("model_key"), row.get("path"),
            row.get("displayName"), row.get("model"),
        ]
        normalized = {str(v).lower().replace("_", "-") for v in values if v}
        if needle in normalized or any(needle in x for x in normalized if needle):
            return True
    return False


def validate_manifest(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise RuntimeError("manifest is not an object")
    updater = raw.get("updater") or {}
    model = raw.get("model") or {}
    if not isinstance(updater, dict) or not isinstance(model, dict):
        raise RuntimeError("manifest updater/model sections are required")

    updater_path = str(updater.get("path") or "").strip().lstrip("/")
    updater_sha = str(updater.get("sha256") or "").strip().lower()
    updater_version = str(updater.get("version") or "").strip()
    if not updater_path or len(updater_sha) != 64 or not updater_version:
        raise RuntimeError("invalid updater manifest entry")

    revision = int(model.get("revision") or 0)
    model_key = str(model.get("model_key") or "").strip()
    identifier = str(model.get("model_identifier") or model_key).strip()
    context_length = int(model.get("context_length") or 0)
    gpu = str(model.get("gpu") or "").strip()
    rollover = int(model.get("autopilot_rollover_at") or 0)
    quantization = str(model.get("quantization") or "").strip()
    fmt = str(model.get("format") or "gguf").strip().lower()

    if revision <= 0 or not model_key or not identifier:
        raise RuntimeError("invalid model revision/key/identifier")
    if context_length < 4096 or context_length > 262144:
        raise RuntimeError(f"unsafe context_length: {context_length}")
    try:
        gpu_f = float(gpu)
    except Exception as exc:
        raise RuntimeError(f"invalid gpu ratio: {gpu!r}") from exc
    if not 0.0 <= gpu_f <= 1.0:
        raise RuntimeError(f"gpu ratio out of range: {gpu}")
    if rollover < 4096 or rollover >= context_length:
        raise RuntimeError(f"invalid rollover/context pair: {rollover}/{context_length}")
    if fmt not in {"gguf", "mlx", "auto"}:
        raise RuntimeError(f"unsupported model format: {fmt}")

    return {
        "updater": {
            "version": updater_version,
            "path": updater_path,
            "sha256": updater_sha,
        },
        "model": {
            "revision": revision,
            "model_key": model_key,
            "model_identifier": identifier,
            "context_length": context_length,
            "gpu": gpu,
            "autopilot_rollover_at": rollover,
            "quantization": quantization,
            "format": fmt,
            "notes": str(model.get("notes") or "")[:1000],
        },
        "interval_seconds": max(60, int(raw.get("interval_seconds") or DEFAULT_INTERVAL)),
    }


def maybe_self_update(spec: dict) -> bool:
    updater = spec["updater"]
    expected = str(updater["sha256"])
    if sha256_file(SELF_FILE) == expected:
        return False
    data = fetch_repo_file(str(updater["path"]), timeout=45)
    actual = sha256_bytes(data)
    if actual != expected:
        raise RuntimeError(f"updater SHA mismatch: expected {expected}, got {actual}")
    stage = ROOT / ".model_auto_updater.new.py"
    stage.write_bytes(data)
    test = hidden_run([sys.executable, "-m", "py_compile", str(stage)], timeout=30)
    if test.returncode != 0:
        stage.unlink(missing_ok=True)
        raise RuntimeError("new model updater failed py_compile")
    os.replace(stage, SELF_FILE)
    write_state(
        "self_updated",
        installed_updater_version=updater["version"],
        installed_updater_sha256=actual,
    )
    hidden_popen([sys.executable, str(SELF_FILE), "--watch"])
    return True


def powershell_path() -> str:
    return os.path.join(
        os.environ.get("SystemRoot", r"C:\Windows"),
        "System32", "WindowsPowerShell", "v1.0", "powershell.exe",
    )


def commandline_pids(regex: str) -> list[int]:
    if os.name != "nt":
        return []
    escaped = regex.replace("'", "''")
    script = (
        "$rx='" + escaped + "'; "
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.CommandLine -and $_.CommandLine -match $rx } | "
        "ForEach-Object { $_.ProcessId }"
    )
    p = hidden_run([powershell_path(), "-NoProfile", "-Command", script], timeout=20)
    out = []
    for line in (p.stdout or "").splitlines():
        try:
            pid = int(line.strip())
            if pid > 0 and pid != os.getpid():
                out.append(pid)
        except Exception:
            pass
    return sorted(set(out))


def kill_pid_tree(pid: int) -> None:
    if pid > 0:
        try:
            hidden_run(["taskkill", "/PID", str(pid), "/T", "/F"], timeout=20)
        except Exception:
            pass


def retire_autopilot_controller() -> None:
    regex = (
        r"qwen_direct_autopilot_runner\.py|qwen_autopilot_runner\.py|"
        r"qwen_roblox_enforced_proxy_current\.py|qwen_controller_launcher\.py|"
        r"StudioMCP\.exe|\\Roblox\\mcp\.bat"
    )
    for pid in commandline_pids(regex):
        kill_pid_tree(pid)


def unload_previous_managed_model(lms: str) -> None:
    state = read_json(MODEL_LOAD_STATE)
    if str(state.get("loaded_by") or "") != "full_auto_manager":
        return
    targets = []
    for key in ("identifier", "model_key"):
        value = str(state.get(key) or "").strip()
        if value and value not in targets:
            targets.append(value)
    for target in targets:
        try:
            p = hidden_run([lms, "unload", target], timeout=120)
            if p.returncode == 0:
                break
        except Exception:
            pass
    try:
        MODEL_LOAD_STATE.unlink(missing_ok=True)
    except Exception:
        pass


def download_if_missing(lms: str, model: dict) -> str:
    desired = str(model["model_key"])
    if model_available_on_disk(lms, desired):
        return "already_on_disk"
    quant = str(model.get("quantization") or "").strip()
    if not quant:
        raise RuntimeError(
            "desired model is not on disk and manifest has no quantization; "
            "refusing an interactive lms get prompt"
        )
    ref = desired + "@" + quant
    args = [lms, "get", ref]
    fmt = str(model.get("format") or "auto")
    if fmt == "gguf":
        args.append("--gguf")
    elif fmt == "mlx":
        args.append("--mlx")
    write_state("downloading", desired_model=desired, download_ref=ref)
    p = hidden_run(args, timeout=7200)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout or "lms get failed")[-2000:])
    if not model_available_on_disk(lms, desired):
        raise RuntimeError("lms get succeeded but desired model is not visible on disk")
    return "downloaded"


def apply_model_spec(model: dict) -> tuple[bool, str]:
    cfg = read_json(CONFIG_FILE)
    desired = {
        "model_key": model["model_key"],
        "model_identifier": model["model_identifier"],
        "context_length": model["context_length"],
        "gpu": model["gpu"],
        "autopilot_rollover_at": model["autopilot_rollover_at"],
    }
    changed = any(str(cfg.get(k)) != str(v) for k, v in desired.items())
    state = read_json(STATE_FILE)
    applied_revision = int(state.get("applied_model_revision") or 0)
    if not changed and applied_revision == int(model["revision"]):
        return False, "up_to_date"

    lms = find_lms()
    if not lms:
        raise RuntimeError("lms CLI not found")
    download_status = download_if_missing(lms, model)

    if changed:
        retire_autopilot_controller()
        unload_previous_managed_model(lms)
        cfg.update(desired)
        tmp = CONFIG_FILE.with_suffix(".json.new")
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, CONFIG_FILE)

    write_state(
        "applied" if changed else "revision_recorded",
        applied_model_revision=int(model["revision"]),
        desired_model=model["model_key"],
        desired_identifier=model["model_identifier"],
        context_length=model["context_length"],
        gpu=model["gpu"],
        autopilot_rollover_at=model["autopilot_rollover_at"],
        download_status=download_status,
        config_changed=changed,
        notes=model.get("notes", ""),
    )
    return changed, "applied" if changed else "revision_recorded"


def activation_snapshot(model: dict) -> dict:
    health = read_json(FULL_AUTO_HEALTH)
    live_model = str(health.get("resolved_model_key") or "")
    desired = str(model.get("model_key") or "")
    loaded = bool(health.get("model_loaded"))
    return {
        "manager_version": health.get("manager_version"),
        "model_loaded": loaded,
        "resolved_model_key": live_model,
        "model_boot_proven": bool(health.get("model_boot_proven")),
        "autopilot_running": bool(health.get("autopilot_running")),
        "autopilot_status": health.get("autopilot_status"),
        "controller_live_version": health.get("controller_live_version"),
        "activated": bool(loaded and desired and desired.lower() in live_model.lower()),
    }


def check_once() -> tuple[int, int]:
    raw = json.loads(
        fetch_repo_file(MANIFEST_PATH, timeout=30).decode("utf-8-sig", errors="replace")
    )
    spec = validate_manifest(raw)
    if maybe_self_update(spec):
        return 0, int(spec["interval_seconds"])
    changed, result = apply_model_spec(spec["model"])
    snap = activation_snapshot(spec["model"])
    write_state(
        "applied_waiting_for_manager" if changed and not snap["activated"] else "up_to_date",
        applied_model_revision=int(spec["model"]["revision"]),
        desired_model=spec["model"]["model_key"],
        activation=snap,
        last_check_at=time.time(),
        check_result=result,
        manifest_interval_seconds=int(spec["interval_seconds"]),
    )
    return 0, int(spec["interval_seconds"])


def self_test() -> int:
    good = {
        "interval_seconds": 300,
        "updater": {
            "version": VERSION,
            "path": "agent/model_auto_updater.py",
            "sha256": "0" * 64,
        },
        "model": {
            "revision": 1,
            "model_key": "qwen/qwen3.5-9b",
            "model_identifier": "qwen/qwen3.5-9b",
            "context_length": 60000,
            "gpu": "0.59375",
            "autopilot_rollover_at": 50000,
            "format": "gguf",
            "quantization": "",
        },
    }
    failures = []
    try:
        spec = validate_manifest(good)
        if spec["model"]["context_length"] != 60000:
            failures.append("valid manifest changed unexpectedly")
    except Exception as exc:
        failures.append(f"valid manifest rejected: {exc!r}")
    for label, patch in [
        ("gpu", {"gpu": "1.1"}),
        ("rollover", {"autopilot_rollover_at": 60000}),
        ("model", {"model_key": "", "model_identifier": ""}),
    ]:
        bad = json.loads(json.dumps(good))
        bad["model"].update(patch)
        try:
            validate_manifest(bad)
            failures.append(label + " invalid case accepted")
        except Exception:
            pass
    if failures:
        print("MODEL AUTO UPDATER SELF-TEST FAILED")
        for row in failures:
            print(" -", row)
        return 1
    print("MODEL AUTO UPDATER SELF-TEST PASSED")
    print(" - manifest validation")
    print(" - GPU bounds")
    print(" - rollover/context safety")
    return 0


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    lock = acquire_single_instance()
    if lock is None:
        return 0
    watch = "--watch" in sys.argv or "--once" not in sys.argv
    interval = DEFAULT_INTERVAL
    while True:
        try:
            rc, interval = check_once()
            if rc != 0 and not watch:
                return rc
        except Exception as exc:
            write_state("failed", error=repr(exc), last_check_at=time.time())
            log(f"check failed: {exc!r}")
            if not watch:
                return 1
        if not watch:
            return 0
        time.sleep(max(60, int(interval)))


if __name__ == "__main__":
    raise SystemExit(main())
