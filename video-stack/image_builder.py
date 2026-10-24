"""Qwen-Image API graph builder. Same offline-validation discipline as video."""
from __future__ import annotations

from pathlib import Path
import time
import uuid

from workflow_builder import FILES, INPUT_ROOT, _complete_model, _node, _ref

IMG_FILES = {
    "qi_unet": ("diffusion_models", "qwen_image_fp8_e4m3fn.safetensors"),
    "qi_te": ("text_encoders", "qwen_2.5_vl_7b_fp8_scaled.safetensors"),
    "qi_vae": ("vae", "qwen_image_vae.safetensors"),
    "qi_lightning": ("loras", "Qwen-Image-Lightning-8steps-V1.0.safetensors"),
}

RATIOS = {"1:1": (1328, 1328), "16:9": (1664, 928), "9:16": (928, 1664)}


def image_ready(lightning=False):
    keys = ["qi_unet", "qi_te", "qi_vae"] + (["qi_lightning"] if lightning else [])
    return [IMG_FILES[k][1] for k in keys if not _complete_model(k)]


def build_image_graph(prompt, width=1328, height=1328, seed=0, lightning=True):
    """Text-to-image graph for Qwen-Image; lightning=True uses the 8-step LoRA."""
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 8000:
        raise ValueError("请输入 1–8000 字符的出图提示词")
    if type(lightning) is not bool:
        raise ValueError("闪电选项必须是布尔值")
    for value in (width, height):
        if type(value) is not int or not 512 <= value <= 1664 or value % 16:
            raise ValueError("宽高应为 512–1664 范围内、16 的倍数")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2 ** 64:
        raise ValueError("种子必须是非负整数")
    missing = image_ready(lightning)
    if missing:
        raise ValueError("图像模型尚未完整下载：" + "、".join(missing))
    prefix = "studio/img/" + time.strftime("%Y%m%d") + "/qwen_" + uuid.uuid4().hex[:12]
    n, r = _node, _ref
    g = {"1": n("UNETLoader", unet_name=IMG_FILES["qi_unet"][1], weight_dtype="default"),
         "2": n("CLIPLoader", clip_name=IMG_FILES["qi_te"][1], type="qwen_image", device="default"),
         "3": n("VAELoader", vae_name=IMG_FILES["qi_vae"][1]),
         "4": n("CLIPTextEncode", clip=r(2), text=prompt.strip()),
         "5": n("ConditioningZeroOut", conditioning=r(4)),
         "6": n("EmptySD3LatentImage", width=width, height=height, batch_size=1)}
    model = r(1)
    if lightning:
        g["7"] = n("LoraLoaderModelOnly", model=model, lora_name=IMG_FILES["qi_lightning"][1], strength_model=1.0)
        model = r(7)
    g["8"] = n("ModelSamplingAuraFlow", model=model, shift=3.1)
    g["9"] = n("KSampler", model=r(8), positive=r(4), negative=r(5), latent_image=r(6),
               seed=seed, steps=8 if lightning else 20,
               cfg=1.0 if lightning else 4.0, sampler_name="euler", scheduler="simple", denoise=1.0)
    g["10"] = n("VAEDecode", samples=r(9), vae=r(3))
    g["11"] = n("SaveImage", images=r(10), filename_prefix=prefix)
    return g


FILES.update(IMG_FILES)
