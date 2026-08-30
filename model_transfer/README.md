# Roblox Coder V2 -> Qwen3.5-4B Transfer Adapter

This folder is for reconstructing an **approximate PEFT LoRA adapter** from the public Roblox Coder V2 Q8_0 GGUF and applying it to the official Qwen3.5-4B checkpoint.

## Why approximate

Roblox Coder V2 was published as quantized GGUF, not as the creator's original QLoRA adapter. The original adapter therefore cannot be recovered bit-for-bit. The transfer pipeline:

1. Downloads `Qwen/Qwen3.5-4B` as the reference/base.
2. Downloads `preferredev/Roblox-Coder-v2_gguf` Q8_0.
3. Reconstructs the V2 language weights back into Hugging Face safetensors with Qwen3.5-aware GGUF mapping.
4. Copies unchanged stock-Qwen MTP/vision tensors from the reference checkpoint.
5. Uses MergeKit LoRA extraction (SVD) to approximate the fine-tune delta.
6. Produces 25%, 50%, 75%, and 100% strength variants.

## Recommended first candidate

Start at **50%**. Stock Qwen3.5-4B showed correct native Roblox Studio MCP tool calling, while Roblox Coder V2 itself emitted EOS instead of tool calls in the control test. The goal is to add Roblox specialization without importing too much of that regression.

After merging/exporting a candidate, attach the official `roblox-studio` MCP and test:

> Use a Roblox Studio tool immediately. Inspect the currently open Studio project. Do not answer in text before calling a tool.

If 50% loses native tool calling, try 25%. If it preserves tool calling easily, test 75%.

## Vision

The transfer is intended to modify only the language-model weights. Keep the existing Qwen3.5-4B F16 mmproj for vision.

The runnable Colab notebook is `Roblox_V2_to_Qwen35_Transfer_Adapter.ipynb`.
