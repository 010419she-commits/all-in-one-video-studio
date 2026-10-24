"""Quantize fp16 weights to ComfyUI's scaled-fp8-e4m3 checkpoint format,
preserving the exact fused-qkv key layout.

Per quantized Linear layer the file gains:
  X.weight        float8_e4m3fn (2D)
  X.weight_scale  float32 scalar  = amax/448
  X.input_scale   float32 scalar  = 1.0
  X.comfy_quant   uint8 JSON      = {"format": "float8_e4m3fn"}
Everything else keeps its original dtype. Only 2D Linear weights inside
double_blocks/single_blocks are quantized (95%+ of the parameters).

Both paths are required command-line arguments; running without arguments
never writes a model file.
"""
import argparse
import json
from pathlib import Path
import sys
import time

import torch
from safetensors.torch import load_file, save_file

FP8_MAX = 448.0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="源 fp16 .safetensors 绝对路径")
    parser.add_argument("destination", help="输出 fp8_scaled .safetensors 绝对路径（不得已存在）")
    args = parser.parse_args()
    src, dst = Path(args.source), Path(args.destination)
    if not src.is_file():
        sys.exit("源模型不存在：" + str(src))
    if src.resolve() == dst.resolve():
        sys.exit("输出路径不能与源文件相同")
    if dst.exists():
        sys.exit("输出文件已存在，未覆盖：" + str(dst))
    t0 = time.time()
    sd = load_file(str(src))
    out = {}
    quantized = kept = 0
    for name, tensor in sd.items():
        block = name.startswith(("double_blocks.", "single_blocks."))
        if block and name.endswith(".weight") and tensor.ndim == 2:
            w = tensor.to(torch.float32)
            scale = w.abs().amax().clamp(min=1e-8) / FP8_MAX
            out[name] = (w / scale).clamp(-FP8_MAX, FP8_MAX).to(torch.float8_e4m3fn)
            out[name + "_scale"] = scale.to(torch.float32)
            prefix = name[: -len("weight")]
            out[prefix + "input_scale"] = torch.ones((), dtype=torch.float32)
            conf = torch.tensor(list(json.dumps({"format": "float8_e4m3fn"}).encode("utf-8")), dtype=torch.uint8)
            out[prefix + "comfy_quant"] = conf
            quantized += 1
        else:
            out[name] = tensor
            kept += 1
    save_file(out, str(dst))
    print("quantized %d layers, kept %d, %ds" % (quantized, kept, time.time() - t0))


if __name__ == "__main__":
    main()
