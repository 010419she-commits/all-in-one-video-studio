"""Probe how ComfyUI's loader maps an NVFP4 transformer checkpoint:
print detected model config and the first missing/unexpected keys.

Read-only: never writes a model file. The checkpoint path is a required
command-line argument. Run with the ComfyUI environment Python from the
ComfyUI checkout so `comfy` imports resolve.
"""
import argparse
from pathlib import Path
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("checkpoint", help="待探测的 .safetensors 检查点绝对路径")
args = parser.parse_args()

CKPT = Path(args.checkpoint)
if not CKPT.is_file():
    sys.exit("检查点不存在：" + str(CKPT))

import comfy.sd
import comfy.utils

sd = comfy.utils.load_torch_file(str(CKPT))
print("state dict keys:", len(sd))
sample = [k for k in sd if "attn" in k][:10]
print("attn key sample:", sample)
model_config = comfy.model_detection.model_config_from_unet(sd, "")
print("detected image_model:", getattr(model_config, "image_model", None))
print("detected unet_config keys:", sorted(getattr(model_config, "unet_config", {}).keys())[:20])
model = model_config.get_model({})
missing, unexpected = model.load_state_dict(sd, strict=False)
print("missing:", len(missing), "unexpected:", len(unexpected))
print("missing sample:", missing[:8])
print("unexpected sample:", unexpected[:8])
