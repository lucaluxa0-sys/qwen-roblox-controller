#!/usr/bin/env python3
"""
Build an approximate Roblox Coder V2 -> Qwen3.5-4B PEFT LoRA adapter.

Designed for Linux/WSL/Google Colab with a GPU and plenty of disk space.
It downloads public weights, reconstructs V2 from Q8 GGUF to HF safetensors,
extracts a rank-64 LoRA delta with MergeKit, and creates scaled variants.

The creator's original LoRA was not published, so this is an approximation.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from huggingface_hub import hf_hub_download, snapshot_download
from safetensors.torch import load_file, save_file


def run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    print("+", " ".join(str(x) for x in cmd), flush=True)
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=check)


def ensure_ungguf(root: Path) -> Path:
    repo = root / "ungguf"
    if repo.exists():
        return repo
    run(["git", "clone", "--depth", "1", "https://github.com/dreamfast/ungguf.git", str(repo)])
    run([sys.executable, "-m", "pip", "install", "-r", str(repo / "requirements.txt")])
    return repo


def download_models(root: Path) -> tuple[Path, Path]:
    base = root / "qwen35_4b_base"
    v2_dir = root / "roblox_coder_v2"
    base.mkdir(parents=True, exist_ok=True)
    v2_dir.mkdir(parents=True, exist_ok=True)

    snapshot_download(
        repo_id="Qwen/Qwen3.5-4B",
        local_dir=str(base),
        allow_patterns=[
            "config.json",
            "chat_template.jinja",
            "model.safetensors*",
            "tokenizer.json",
            "tokenizer_config.json",
            "merges.txt",
            "vocab.json",
            "preprocessor_config.json",
            "video_preprocessor_config.json",
        ],
    )
    gguf = Path(
        hf_hub_download(
            repo_id="preferredev/Roblox-Coder-v2_gguf",
            filename="Qwen3.5-4B.Q8_0.gguf",
            local_dir=str(v2_dir),
        )
    )
    return base, gguf


def reconstruct_v2(root: Path, ungguf: Path, base: Path, gguf: Path) -> Path:
    out = root / "roblox_coder_v2_reconstructed_hf"
    if out.exists():
        shutil.rmtree(out)

    # Qwen3.6 uses the same Qwen3.5 hybrid architecture. This converter is
    # intentionally used because it copies MTP/vision tensors missing from
    # the text GGUF from the stock reference model.
    converter = ungguf / "src" / "gguf_to_safetensors_qwen36.py"
    run([
        sys.executable,
        str(converter),
        "--gguf", str(gguf),
        "--output", str(out),
        "--reference-model", str(base),
        "--shard-size-mb", "4000",
    ])

    verifier = ungguf / "src" / "verify_conversion_qwen35.py"
    run([
        sys.executable,
        str(verifier),
        "--gguf", str(gguf),
        "--converted", str(out),
        "--reference", str(base),
        "--output", str(root / "conversion_verification.json"),
    ])
    return out


def extract_lora(root: Path, base: Path, finetuned: Path, rank: int) -> Path:
    out = root / f"roblox_v2_extracted_lora_r{rank}"
    if out.exists():
        shutil.rmtree(out)

    cuda_cmd = [
        "mergekit-extract-lora",
        "--model", str(finetuned),
        "--base-model", str(base),
        "--out-path", str(out),
        "--max-rank", str(rank),
        "--cuda",
    ]
    result = run(cuda_cmd, check=False)
    if result.returncode != 0:
        print("CUDA extraction failed; retrying on CPU.", flush=True)
        run([
            "mergekit-extract-lora",
            "--model", str(finetuned),
            "--base-model", str(base),
            "--out-path", str(out),
            "--max-rank", str(rank),
        ])
    return out


def scale_adapters(root: Path, adapter: Path) -> Path:
    out = root / "scaled_adapters"
    out.mkdir(parents=True, exist_ok=True)

    weights = load_file(str(adapter / "adapter_model.safetensors"), device="cpu")
    config = json.loads((adapter / "adapter_config.json").read_text())

    for strength in (0.25, 0.50, 0.75, 1.00):
        tag = f"{int(strength * 100):03d}pct"
        dst = out / tag
        if dst.exists():
            shutil.rmtree(dst)
        dst.mkdir(parents=True)

        scaled = {}
        b_count = 0
        for name, tensor in weights.items():
            if "lora_B" in name:
                scaled[name] = tensor * strength
                b_count += 1
            else:
                scaled[name] = tensor.clone()

        save_file(scaled, str(dst / "adapter_model.safetensors"))
        (dst / "adapter_config.json").write_text(json.dumps(config, indent=2))
        (dst / "TRANSFER_STRENGTH.txt").write_text(
            f"Roblox Coder V2 reconstructed delta strength: {strength}\n"
        )
        print(f"Created {tag}: scaled {b_count} LoRA B tensors")

    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-dir", default="./roblox_v2_transfer")
    parser.add_argument("--rank", type=int, default=64)
    args = parser.parse_args()

    root = Path(args.work_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)

    print("Installing/updating required Python tools is expected before running:")
    print("  pip install -U huggingface_hub safetensors transformers accelerate peft mergekit")
    print()

    ungguf = ensure_ungguf(root)
    base, gguf = download_models(root)
    v2_hf = reconstruct_v2(root, ungguf, base, gguf)
    adapter = extract_lora(root, base, v2_hf, args.rank)
    scaled = scale_adapters(root, adapter)

    print("\nDONE")
    print("Full extracted adapter:", adapter)
    print("Scaled variants:", scaled)
    print("Start testing with:", scaled / "050pct")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
