"""Offline, local ComfyUI API graph builder. Never starts a process or uses the GPU.

Node recipes are flattened from the installed ComfyUI official templates. The
base LTX recipe uses the distilled LoRA's first pass; optional upscale adds its
second pass. Prompt enhancement is handled by the selected agent, not a second
Gemma generation inside the video graph.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import shutil
import struct
import time
import uuid

import studio_config

ROOT = Path(__file__).resolve().parent
COMFY_ROOT = studio_config.comfyui_root() or ROOT.parent / "ComfyUI"
MODEL_ROOT = COMFY_ROOT / "models"
INPUT_ROOT = COMFY_ROOT / "input"
OBJECT_INFO = ROOT / "object_info.json"
MAX_SECONDS = 30
DURATION_CHOICES = (3, 4, 5, 6, 8, 10, 12, 15, 20, 30)
DEFAULT_COMPUTE_PATH = "hd"
COMPUTE_PATHS = ("fast", "hd")
COMPUTE_PATH_META = "video_studio_compute_path"
_model_cache = {}


def validate_compute_path(value):
    if not isinstance(value, str) or value not in COMPUTE_PATHS:
        raise ValueError("计算引擎请选择快速或高清")
    return value


def graph_compute_path(graph):
    """Read controller metadata only; it never changes sampling parameters."""
    paths = set()
    for node in graph.values():
        meta = node.get("_meta", {}) if isinstance(node, dict) else {}
        if isinstance(meta, dict) and COMPUTE_PATH_META in meta:
            paths.add(validate_compute_path(meta[COMPUTE_PATH_META]))
    if len(paths) > 1:
        raise ValueError("同一工作流不能使用多个计算引擎")
    # Existing workflows retain the memory-saving engine introduced by the OOM fix.
    return next(iter(paths), DEFAULT_COMPUTE_PATH)


def _set_compute_path(graph, compute_path):
    for node in graph.values():
        if node.get("class_type") in ("SaveVideo", "VHS_VideoCombine", "SaveImage"):
            node.setdefault("_meta", {})[COMPUTE_PATH_META] = compute_path
    return graph

FILES = {
    "hv_t2v": ("diffusion_models", "hunyuanvideo1.5_720p_t2v_fp16.safetensors"),
    "hv_i2v": ("diffusion_models", "hunyuanvideo1.5_720p_i2v_fp16.safetensors"),
    "hv_sr": ("diffusion_models", "hunyuanvideo1.5_1080p_sr_distilled_fp16.safetensors"),
    "hv_te": ("text_encoders", "qwen_2.5_vl_7b_fp8_scaled.safetensors"),
    "hv_byt5": ("text_encoders", "byt5_small_glyphxl_fp16.safetensors"),
    "hv_vae": ("vae", "hunyuanvideo15_vae_fp16.safetensors"),
    "hv_vae_fast": ("vae_approx", "lighttaehy1_5.safetensors"),
    "hv_vision": ("clip_vision", "sigclip_vision_patch14_384.safetensors"),
    "hv_up": ("latent_upscale_models", "hunyuanvideo15_latent_upsampler_1080p.safetensors"),
    "ltx": ("checkpoints", "ltx-2.3-22b-dev-fp8.safetensors"),
    "ltx_te": ("text_encoders", "gemma_3_12B_it_fp4_mixed.safetensors"),
    "ltx_lora": ("loras", "ltx_2.3_22b_distilled_1.1_lora_dynamic_fro09_avg_rank_111_bf16.safetensors"),
    "ltx_prompt_lora": ("loras", "gemma-3-12b-it-abliterated_lora_rank64_bf16.safetensors"),
    "ltx_up": ("latent_upscale_models", "ltx-2.3-spatial-upscaler-x2-1.1.safetensors"),
    "ltx_ic": ("loras", "ltx-2.3-22b-ic-lora-union-control-ref0.5.safetensors"),
    "hv_i2v_fast": ("diffusion_models", "hunyuanvideo1.5_720p_i2v_cfg_distilled_fp8_scaled.safetensors"),
    "ltx25": ("diffusion_models", "ltx-2.5-22b-distilled-transformer-nvfp4.safetensors"),
    "ltx25_te": ("text_encoders", "gemma4-12b-with-proj-ltx-2.5-bf16.safetensors"),
    "ltx25_vae": ("vae", "ltx-2.5-video-vae-bf16.safetensors"),
    "ltx25_avae": ("vae", "ltx-2.5-audio-vae-bf16.safetensors"),
    "ltx25_up": ("latent_upscale_models", "ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors"),
    "hv_fast_lora": ("loras", "hunyuanvideo1.5_t2v_480p_lightx2v_4step_lora_rank_32_bf16.safetensors"),
    "mt_vae": ("vae", "vae-ft-mse-840000-ema-pruned.safetensors"),
    "h3": ("diffusion_models", "minimax_h3_fl2va_pruned_int8_convrot.safetensors"),
    "h3_te": ("text_encoders", "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"),
    "h3_vae": ("vae", "minimax_h3_video_vae_int8_convrot.safetensors"),
    "h3_avae": ("vae", "minimax_h3_audio_vae_fp32.safetensors"),
    "h3_lora": ("loras", "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors"),
}
_PART_SUFFIXES = (".part", ".parts", ".partial", ".incomplete", ".tmp", ".download", ".downloading")
ALIASES = {"hunyuan_t2v": ("hunyuan15", "t2v"), "hunyuan_i2v": ("hunyuan15", "i2v"),
           "ltx_t2v": ("ltx23", "t2v"), "ltx_i2v": ("ltx23", "i2v"),
           "ltx25_t2v": ("ltx25", "t2v"), "ltx25_i2v": ("ltx25", "i2v"),
           "h3_t2v": ("h3", "t2v"), "h3_i2v": ("h3", "i2v")}


def _complete_model(key):
    """Check final location and safetensors extents, reading only its header."""
    path = MODEL_ROOT.joinpath(*FILES[key])
    try:
        if not path.is_file() or any(Path(str(path) + ext).exists() for ext in _PART_SUFFIXES):
            return False
        resolved = path.resolve()
        if MODEL_ROOT.resolve() not in resolved.parents or any(p.startswith(".") for p in path.relative_to(MODEL_ROOT).parts):
            return False
        stat = path.stat()
        signature = (str(resolved), stat.st_size, stat.st_mtime_ns)
        if signature in _model_cache:
            return _model_cache[signature]
        with path.open("rb") as stream:
            header_size = struct.unpack("<Q", stream.read(8))[0]
            if not 2 <= header_size <= 64 * 1024 * 1024 or header_size + 8 >= stat.st_size:
                return False
            header = json.loads(stream.read(header_size))
        tensors = [v for k, v in header.items() if k != "__metadata__"]
        ends = [v["data_offsets"][1] for v in tensors]
        ok = bool(ends) and max(ends) + header_size + 8 == stat.st_size
        ok = ok and all(isinstance(v["data_offsets"], list) and len(v["data_offsets"]) == 2
                        and 0 <= v["data_offsets"][0] <= v["data_offsets"][1] <= stat.st_size - header_size - 8 for v in tensors)
        _model_cache[signature] = ok
        return ok
    except (OSError, ValueError, KeyError, TypeError, struct.error):
        return False


def _dependencies(model_id, image, upscale, video=False, fast=False):
    if model_id == "hunyuan15":
        keys = ["hv_i2v" if image or video else "hv_t2v", "hv_te", "hv_byt5", "hv_vae"]
        if fast:
            keys[0] = "hv_i2v_fast" if image or video else "hv_t2v"
            if not image and not video:
                keys.append("hv_fast_lora")
        if image or video:
            keys.append("hv_vision")
        if upscale:
            keys += ["hv_sr", "hv_up"]
        return keys
    if model_id == "ltx23":
        keys = ["ltx", "ltx_te", "ltx_lora"]
        if video:
            keys.append("ltx_ic")
        if upscale:
            keys.append("ltx_up")
        return keys
    if model_id == "ltx25":
        keys = ["ltx25", "ltx25_te", "ltx25_vae", "ltx25_avae"]
        if upscale:
            keys.append("ltx25_up")
        return keys
    if model_id == "h3":
        return ["h3", "h3_te", "h3_vae", "h3_avae", "h3_lora"]
    raise ValueError("不支持的视频模型，请选择 HunyuanVideo 1.5、LTX-2.3、LTX-2.5 或 MiniMax H3")


def catalog():
    entries = []
    for model_id, name, prefix in [("hunyuan15", "HunyuanVideo 1.5", "hv_"), ("ltx23", "LTX-2.3", "ltx"), ("ltx25", "LTX-2.5", "ltx25_"), ("h3", "MiniMax H3", "h3")]:
        modes = {}
        for mode in ("t2v", "i2v"):
            missing = [FILES[k][1] for k in _dependencies(model_id, mode == "i2v", False) if not _complete_model(k)]
            modes[mode] = {"ready": not missing, "missing": missing}
        missing = sorted(set(modes["t2v"]["missing"] + modes["i2v"]["missing"]))
        detail = "文生视频 / 图生视频已就绪" if not missing else "待下载完成：" + "、".join(missing)
        pack = [k for k in FILES if (k.startswith(prefix) or (prefix == "ltx25_" and k == "ltx25")) and (prefix == "ltx25_" or not k.startswith("ltx25"))]
        entries.append({"id": model_id, "name": name, "label": name, "ready": not missing, "available": not missing,
                        "detail": detail, "missing": missing, "modes": modes,
                        "defaults": {"width": 768, "height": 512, "seconds": 5, "fps": 24, "seed": 0},
                        "seconds_min": 1, "seconds_max": MAX_SECONDS, "seconds_choices": list(DURATION_CHOICES),
                        "upscale_supported": True,
                        "upscale_detail": "1080p SR 权重，保持画幅比例" if model_id == "hunyuan15" else "2 倍空间放大 + 二次采样",
                        "pack_files": [{"name": FILES[k][1], "ready": _complete_model(k),
                                        "used_by_default": k in _dependencies(model_id, False, False)} for k in pack],
                        "runtime_validated": False})
    expanded = []
    for alias, (family, mode) in ALIASES.items():
        entry = next(entry for entry in entries if entry["id"] == family)
        current = entry["modes"][mode]
        name = entry["name"] + (" · 图生视频" if mode == "i2v" else " · 文生视频")
        expanded.append({**entry, "id": alias, "family": family, "mode": mode, "name": name, "label": name,
                         "ready": current["ready"], "available": current["ready"], "missing": current["missing"],
                         "requires_image": mode == "i2v",
                         "detail": "本地模型文件已就绪" if current["ready"] else "待下载完成：" + "、".join(current["missing"])})
    for tool_id, tool_name in (("musetalk_lipsync", "MuseTalk · 对口型"), ("cosyvoice_tts", "CosyVoice · 配音"), ("cosyvoice_lipsync", "CosyVoice · 配音+对口型")):
        missing = _tools_missing(tool_id)
        expanded.append({"id": tool_id, "family": "tools", "mode": "tool", "name": tool_name, "label": tool_name,
                         "ready": not missing, "available": not missing, "missing": missing,
                         "requires_image": False, "requires_video": tool_id != "cosyvoice_tts",
                         "requires_audio": True, "prompt_optional": tool_id == "musetalk_lipsync",
                         "detail": "本地模型文件已就绪" if not missing else "待下载完成：" + "、".join(missing)})
    return expanded


def _copy_image(image_path):
    from PIL import Image, ImageOps
    source = Path(image_path).expanduser()
    if not source.is_absolute() or not source.is_file() or source.stat().st_size > 100 * 1024 * 1024:
        raise ValueError("请选择本机的有效图片文件（最大 100 MB）")
    # Decode/re-encode into our own PNG; preserve the original file and discard
    # unrelated metadata. This also handles EXIF orientation consistently.
    try:
        with Image.open(source) as image:
            if image.width * image.height > 40_000_000:
                raise ValueError("首帧图片最多支持 4000 万像素")
            image.load()
            converted = ImageOps.exif_transpose(image).convert("RGB")
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError("首帧图片无法解码，请改用 PNG、JPEG 或 WebP") from exc
    destination = INPUT_ROOT / "studio"
    destination.mkdir(parents=True, exist_ok=True)
    if INPUT_ROOT.resolve() not in destination.resolve().parents:
        raise ValueError("图片目录超出 ComfyUI 输入目录")
    name = uuid.uuid4().hex + ".png"
    with (destination / name).open("xb") as stream:
        converted.save(stream, format="PNG")
    return "studio/" + name


def _copy_video(video_path):
    source = Path(video_path).expanduser()
    if not source.is_absolute() or not source.is_file() or source.stat().st_size > 2 * 1024 * 1024 * 1024:
        raise ValueError("请选择本机的有效视频文件（最大 2 GB）")
    if source.suffix.lower() not in (".mp4", ".mov", ".webm", ".mkv"):
        raise ValueError("参考视频仅支持 MP4、MOV、WebM 或 MKV")
    destination = INPUT_ROOT / "studio"
    destination.mkdir(parents=True, exist_ok=True)
    if INPUT_ROOT.resolve() not in destination.resolve().parents:
        raise ValueError("视频目录超出 ComfyUI 输入目录")
    name = uuid.uuid4().hex + source.suffix.lower()
    with source.open("rb") as src, (destination / name).open("xb") as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)
    return "studio/" + name


TOOL_PRESETS = ("musetalk_lipsync", "cosyvoice_tts", "cosyvoice_lipsync")
MUSE_UNET = MODEL_ROOT / "musetalk" / "musetalk" / "pytorch_model.bin"
COSY_DIR = MODEL_ROOT / "cosyvoice" / "Fun-CosyVoice3-0.5B-2512"
LIPSYNC_TEMPLATE = ROOT / "workflows" / "musetalk_lipsync_template.api.json"


def _tools_missing(model_id):
    missing = []
    if model_id in ("musetalk_lipsync", "cosyvoice_lipsync"):
        if not MUSE_UNET.is_file() or MUSE_UNET.stat().st_size < 1024:
            missing.append("musetalk/pytorch_model.bin")
        if not _complete_model("mt_vae"):
            missing.append(FILES["mt_vae"][1])
    if model_id in ("cosyvoice_tts", "cosyvoice_lipsync"):
        if not COSY_DIR.is_dir() or not (COSY_DIR / "llm.pt").is_file():
            missing.append("cosyvoice/Fun-CosyVoice3-0.5B-2512")
    return missing


def _copy_audio(audio_path, kind="音频"):
    source = Path(audio_path).expanduser()
    if not source.is_absolute() or not source.is_file() or source.stat().st_size > 500 * 1024 * 1024:
        raise ValueError("请选择本机的有效%s文件（最大 500 MB）" % kind)
    if source.suffix.lower() not in (".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac"):
        raise ValueError("%s仅支持 WAV、MP3、M4A、FLAC、OGG、AAC" % kind)
    destination = INPUT_ROOT / "studio"
    destination.mkdir(parents=True, exist_ok=True)
    if INPUT_ROOT.resolve() not in destination.resolve().parents:
        raise ValueError("音频目录超出 ComfyUI 输入目录")
    name = uuid.uuid4().hex + source.suffix.lower()
    with source.open("rb") as src, (destination / name).open("xb") as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)
    return "studio/" + name


def _audio_seconds(path):
    import av
    with av.open(str(INPUT_ROOT / path)) as container:
        return float(container.duration or 0) / av.time_base


def _node(kind, **inputs):
    return {"class_type": kind, "inputs": inputs}


def _ref(number, output=0):
    return [str(number), output]


def _finish(graph, latent, vae, fps, prefix, audio=None):
    graph["90"] = _node("VAEDecodeTiled", samples=latent, vae=vae, tile_size=512, overlap=64,
                        temporal_size=64, temporal_overlap=8)
    graph["91"] = _node("CreateVideo", images=_ref(90), fps=fps, bit_depth=8, color_space="sRGB")
    if audio:
        graph["91"]["inputs"]["audio"] = audio
    graph["92"] = _node("SaveVideo", video=_ref(91), filename_prefix=prefix, format="mp4", **{"format.codec": "h264"})
    return graph


def _hunyuan(prompt, negative, image, video, width, height, frames, seed, upscale, prefix, fast=False, upscale_to=None, images=None):
    n, r = _node, _ref
    unet = FILES["hv_i2v_fast" if fast and (image or video) else ("hv_i2v" if image or video else "hv_t2v")][1]
    g = {"1": n("UNETLoader", unet_name=unet, weight_dtype="default"),
         "2": n("DualCLIPLoader", clip_name1=FILES["hv_te"][1], clip_name2=FILES["hv_byt5"][1], type="hunyuan_video_15", device="default"),
         "3": n("VAELoader", vae_name=FILES["hv_vae"][1]),
         "4": n("CLIPTextEncode", clip=r(2), text=prompt),
         "5": n("CLIPTextEncode", clip=r(2), text=negative),
         "10": n("ModelSamplingSD3", model=r(1), shift=7),
         "11": n("BasicScheduler", model=r(1), scheduler="simple", steps=4 if fast and not image and not video else 20, denoise=1.0),
         "12": n("RandomNoise", noise_seed=seed),
         "13": n("KSamplerSelect", sampler_name="euler")}
    model = r(10)
    if fast and not image and not video:
        g["70"] = n("LoraLoaderModelOnly", model=model, lora_name=FILES["hv_fast_lora"][1], strength_model=1.0)
        model = r(70)
    if fast:
        g["71"] = n("PathchSageAttentionKJ", model=model, sage_attention="sageattn_qk_int8_pv_fp8_cuda++")
        model = r(71)
        g["72"] = n("VAELoader", vae_name=FILES["hv_vae_fast"][1])
    positive, negative_ref = r(4), r(5)
    if image or video:
        if video:
            g["60"] = n("LoadVideo", file=video)
            g["61"] = n("GetVideoComponents", video=r(60))
            g["62"] = n("ImageFromBatch", image=r(61), batch_index=0, length=1)
            start = r(62)
            g["7"] = n("CLIPVisionLoader", clip_name=FILES["hv_vision"][1])
            g["8"] = n("CLIPVisionEncode", clip_vision=r(7), image=start, crop="center")
            g["9"] = n("HunyuanVideo15ImageToVideo", positive=r(4), negative=r(5), vae=r(3), start_image=start,
                       clip_vision_output=r(8), width=width, height=height, length=frames, batch_size=1)
        else:
            # 参考理解模式：照片只做 sigclip 视觉语义条件，不锚定首帧；
            # 多图按 batch 一起编码，模型同时理解所有参考主体。
            refs = images or [image]
            for i, ref in enumerate(refs):
                g[str(60 + i)] = n("LoadImage", image=ref)
            start = r(60)
            for i in range(1, len(refs)):
                g[str(74 + i)] = n("ImageBatch", image1=start, image2=r(60 + i))
                start = r(74 + i)
            g["7"] = n("CLIPVisionLoader", clip_name=FILES["hv_vision"][1])
            g["8"] = n("CLIPVisionEncode", clip_vision=r(7), image=start, crop="center")
            g["9"] = n("HunyuanVideo15ImageToVideo", positive=r(4), negative=r(5), vae=r(3),
                       clip_vision_output=r(8), width=width, height=height, length=frames, batch_size=1)
        positive, negative_ref, latent = r(9), r(9, 1), r(9, 2)
    else:
        g["9"] = n("EmptyHunyuanVideo15Latent", width=width, height=height, length=frames, batch_size=1)
        latent = r(9)
    g["14"] = n("CFGGuider", model=model, positive=positive, negative=negative_ref, cfg=1.0 if fast else 6.0)
    g["15"] = n("SamplerCustomAdvanced", noise=r(12), guider=r(14), sampler=r(13), sigmas=r(11), latent_image=latent)
    latent = r(15)
    if upscale:
        # Keep aspect ratio, scale the shorter side close to 1080p, aligned to VAE.
        if upscale_to:
            sw, sh = round(upscale_to[0] / 16) * 16, round(upscale_to[1] / 16) * 16
        else:
            ratio = 1080 / min(width, height)
            sw, sh = round(width * ratio / 16) * 16, round(height * ratio / 16) * 16
        g["30"] = n("UNETLoader", unet_name=FILES["hv_sr"][1], weight_dtype="default")
        g["31"] = n("LatentUpscaleModelLoader", model_name=FILES["hv_up"][1])
        g["32"] = n("HunyuanVideo15LatentUpscaleWithModel", model=r(31), samples=r(15), upscale_method="bilinear", width=sw, height=sh, crop="disabled")
        g["33"] = n("HunyuanVideo15SuperResolution", positive=r(4), negative=r(5), latent=r(32), noise_augmentation=0.7)
        if video:
            g["33"]["inputs"].update(vae=r(3), start_image=start, clip_vision_output=r(8))
        elif image:
            g["33"]["inputs"]["clip_vision_output"] = r(8)
        g["34"] = n("ModelSamplingSD3", model=r(30), shift=2.0)
        g["35"] = n("BasicScheduler", model=r(30), scheduler="simple", steps=8, denoise=1.0)
        g["36"] = n("SplitSigmas", sigmas=r(35), step=4)
        g["37"] = n("CFGGuider", model=r(34), positive=r(33), negative=r(33, 1), cfg=1.0)
        g["38"] = n("SamplerCustomAdvanced", noise=r(12), guider=r(37), sampler=r(13), sigmas=r(36), latent_image=r(33, 2))
        g["39"] = n("DisableNoise")
        g["40"] = n("CFGGuider", model=r(30), positive=r(4), negative=r(5), cfg=1.0)
        g["41"] = n("SamplerCustomAdvanced", noise=r(39), guider=r(40), sampler=r(13), sigmas=r(36, 1), latent_image=r(38))
        latent = r(41)
    return _finish(g, latent, r(72) if fast else r(3), 24, prefix)


def _ltx(prompt, negative, image, video, width, height, frames, seed, upscale, prefix, fast=False, upscale_to=None):
    n, r = _node, _ref
    g = {"1": n("CheckpointLoaderSimple", ckpt_name=FILES["ltx"][1]),
         "2": n("LTXAVTextEncoderLoader", text_encoder=FILES["ltx_te"][1], ckpt_name=FILES["ltx"][1], device="default"),
         "3": n("LTXVAudioVAELoader", ckpt_name=FILES["ltx"][1]),
         "4": n("LoraLoaderModelOnly", model=r(1), lora_name=FILES["ltx_lora"][1], strength_model=0.5),
         "5": n("CLIPTextEncode", clip=r(2), text=prompt),
         "6": n("CLIPTextEncode", clip=r(2), text=negative),
         "7": n("LTXVConditioning", positive=r(5), negative=r(6), frame_rate=24.0),
         "8": n("EmptyLTXVLatentVideo", width=width, height=height, length=frames, batch_size=1),
         "9": n("LTXVEmptyLatentAudio", audio_vae=r(3), frames_number=frames, frame_rate=24.0, batch_size=1),
         "14": n("RandomNoise", noise_seed=seed),
         "16": n("KSamplerSelect", sampler_name="euler"),
         "17": n("ManualSigmas", sigmas="1.0, 0.99375, 0.9875, 0.98125, 0.975, 0.909375, 0.725, 0.421875, 0.0")}
    model, positive, negative_ref = r(4), r(7), r(7, 1)
    latent = r(8)
    guide_image = None
    if image:
        g["10"] = n("LoadImage", image=image)
        g["11"] = n("LTXVPreprocess", image=r(10), img_compression=18)
        g["12"] = n("LTXVImgToVideoInplace", vae=r(1, 2), image=r(11), latent=r(8), strength=0.7, bypass=False)
        latent = r(12)
        guide_image = r(11)
    elif video:
        # 参考视频：取首帧锚定画面，并挂 union-control IC-LoRA 参考控制。
        g["50"] = n("LoadVideo", file=video)
        g["51"] = n("GetVideoComponents", video=r(50))
        g["52"] = n("ImageFromBatch", image=r(51), batch_index=0, length=1)
        g["53"] = n("LTXVPreprocess", image=r(52), img_compression=18)
        g["54"] = n("LTXVImgToVideoInplace", vae=r(1, 2), image=r(53), latent=r(8), strength=1.0, bypass=False)
        g["55"] = n("LoraLoaderModelOnly", model=r(4), lora_name=FILES["ltx_ic"][1], strength_model=1.0)
        g["56"] = n("GetICLoRAParameters", iclora_model=r(55))
        g["57"] = n("LTXVAddGuide", positive=r(7), negative=r(7, 1), vae=r(1, 2), latent=r(54), image=r(53),
                    frame_idx=0, strength=1.0, iclora_parameters=r(56))
        model, positive, negative_ref, latent = r(55), r(57), r(57, 1), r(57, 2)
        guide_image = r(53)
    if fast:
        g["70"] = n("PathchSageAttentionKJ", model=model, sage_attention="sageattn_qk_int8_pv_fp8_cuda++")
        model = r(70)
    g["13"] = n("LTXVConcatAVLatent", video_latent=latent, audio_latent=r(9))
    g["15"] = n("CFGGuider", model=model, positive=positive, negative=negative_ref, cfg=1.0)
    g["18"] = n("SamplerCustomAdvanced", noise=r(14), guider=r(15), sampler=r(16), sigmas=r(17), latent_image=r(13))
    g["19"] = n("LTXVSeparateAVLatent", av_latent=r(18))
    latent, audio = r(19), r(19, 1)
    if upscale:
        g["30"] = n("LatentUpscaleModelLoader", model_name=FILES["ltx_up"][1])
        g["31"] = n("LTXVLatentUpsampler", samples=r(19), upscale_model=r(30), vae=r(1, 2))
        latent = r(31)
        if guide_image is not None:
            g["32"] = n("LTXVImgToVideoInplace", vae=r(1, 2), image=guide_image, latent=r(31), strength=1.0, bypass=False)
            latent = r(32)
        g["33"] = n("LTXVCropGuides", positive=positive, negative=negative_ref, latent=r(19))
        g["34"] = n("CFGGuider", model=model, positive=r(33), negative=r(33, 1), cfg=1.0)
        g["35"] = n("LTXVConcatAVLatent", video_latent=latent, audio_latent=r(19, 1))
        g["36"] = n("ManualSigmas", sigmas="0.85, 0.7250, 0.4219, 0.0")
        g["37"] = n("RandomNoise", noise_seed=(seed + 1) % (2 ** 64))
        g["38"] = n("SamplerCustomAdvanced", noise=r(37), guider=r(34), sampler=r(16), sigmas=r(36), latent_image=r(35))
        g["39"] = n("LTXVSeparateAVLatent", av_latent=r(38))
        latent, audio = r(39), r(39, 1)
    g["80"] = n("LTXVAudioVAEDecode", samples=audio, audio_vae=r(3))
    return _finish(g, latent, r(1, 2), 24, prefix, r(80))


def _ltx25(prompt, negative, image, video, width, height, frames, seed, upscale, prefix, fast=False, upscale_to=None, images=None):
    # LTX-2.5: NVFP4 distilled transformer + gemma4 encoder + split video/audio VAEs.
    # DualCFG at 1.0 (distilled), euler_ancestral, official two-stage sigma recipes.
    # Multi-image: first anchors the start; extras become LTXVAddGuide keyframes at
    # 8-aligned evenly spaced frame indices (last image lands on the final frame).
    n, r = _node, _ref
    negative = negative or "pc game, console game, video game, cartoon, childish, ugly"
    g = {"1": n("UNETLoader", unet_name=FILES["ltx25"][1], weight_dtype="default"),
         "2": n("CLIPLoader", clip_name=FILES["ltx25_te"][1], type="ltxv", device="default"),
         "3": n("VAELoader", vae_name=FILES["ltx25_vae"][1]),
         "4": n("VAELoader", vae_name=FILES["ltx25_avae"][1]),
         "5": n("CLIPTextEncode", clip=r(2), text=prompt),
         "6": n("CLIPTextEncode", clip=r(2), text=negative),
         "7": n("LTXVConditioning", positive=r(5), negative=r(6), frame_rate=24.0),
         "8": n("EmptyLTXVLatentVideo", width=width, height=height, length=frames, batch_size=1),
         "9": n("LTXVEmptyLatentAudio", audio_vae=r(4), frames_number=frames, frame_rate=25.0, batch_size=1),
         "14": n("RandomNoise", noise_seed=seed),
         "16": n("KSamplerSelect", sampler_name="euler_ancestral"),
         "17": n("ManualSigmas", sigmas="1.0, 0.99375, 0.9875, 0.98125, 0.975, 0.909375, 0.725, 0.421875, 0.0")}
    model, positive, negative_ref = r(1), r(7), r(7, 1)
    latent = r(8)
    refs = images or ([image] if image else [])
    if image or video:
        if video:
            g["50"] = n("LoadVideo", file=video)
            g["51"] = n("GetVideoComponents", video=r(50))
            g["52"] = n("ImageFromBatch", image=r(51), batch_index=0, length=1)
            start = r(52)
        else:
            g["50"] = n("LoadImage", image=refs[0])
            start = r(50)
        g["53"] = n("LTXVPreprocess", image=start, img_compression=18)
        g["54"] = n("LTXVImgToVideoInplace", vae=r(3), image=r(53), latent=r(8), strength=1.0, bypass=False)
        latent = r(54)
        if not video and len(refs) > 1:
            for i, ref in enumerate(refs[1:], start=1):
                idx = min(frames - 1, round(i * (frames - 1) / (len(refs) - 1)) // 8 * 8)
                li, pr, gd = str(60 + i * 3), str(61 + i * 3), str(62 + i * 3)
                g[li] = n("LoadImage", image=ref)
                g[pr] = n("LTXVPreprocess", image=r(li), img_compression=18)
                g[gd] = n("LTXVAddGuide", positive=positive, negative=negative_ref, vae=r(3), latent=latent,
                          image=r(pr), frame_idx=idx, strength=1.0)
                positive, negative_ref, latent = r(gd), r(gd, 1), r(gd, 2)
    if fast:
        g["70"] = n("PathchSageAttentionKJ", model=model, sage_attention="sageattn_qk_int8_pv_fp8_cuda++")
        model = r(70)
    g["13"] = n("LTXVConcatAVLatent", video_latent=latent, audio_latent=r(9))
    g["15"] = n("LTXVDualCFGGuider", model=model, positive=positive, negative=negative_ref, video_cfg=1.0, audio_cfg=1.0)
    g["18"] = n("SamplerCustomAdvanced", noise=r(14), guider=r(15), sampler=r(16), sigmas=r(17), latent_image=r(13))
    g["19"] = n("LTXVSeparateAVLatent", av_latent=r(18))
    latent, audio = r(19), r(19, 1)
    if upscale:
        g["30"] = n("LatentUpscaleModelLoader", model_name=FILES["ltx25_up"][1])
        g["31"] = n("LTXVLatentUpsampler", samples=r(19), upscale_model=r(30), vae=r(3))
        latent = r(31)
        if image or video:
            g["32"] = n("LTXVImgToVideoInplace", vae=r(3), image=r(53), latent=r(31), strength=1.0, bypass=False)
            latent = r(32)
        g["33"] = n("LTXVCropGuides", positive=positive, negative=negative_ref, latent=r(19))
        g["34"] = n("LTXVDualCFGGuider", model=model, positive=r(33), negative=r(33, 1), video_cfg=1.0, audio_cfg=1.0)
        g["35"] = n("LTXVConcatAVLatent", video_latent=latent, audio_latent=r(19, 1))
        g["36"] = n("ManualSigmas", sigmas="0.85, 0.7250, 0.4219, 0.0")
        g["37"] = n("RandomNoise", noise_seed=(seed + 1) % (2 ** 64))
        g["38"] = n("SamplerCustomAdvanced", noise=r(37), guider=r(34), sampler=r(16), sigmas=r(36), latent_image=r(35))
        g["39"] = n("LTXVSeparateAVLatent", av_latent=r(38))
        latent, audio = r(39), r(39, 1)
    g["80"] = n("LTXVAudioVAEDecode", samples=audio, audio_vae=r(4))
    return _finish(g, latent, r(3), 24, prefix, r(80))


def _lipsync(video, audio, prefix, fps=24):
    """MuseTalk 对口型：参考视频 + 音频 → 嘴唇跟随音频的 MP4。
    复用已验证模板；移除 BatchUncrop 合成支路（帧数强耦合易碎），保留 256 直接输出。"""
    g = json.loads(LIPSYNC_TEMPLATE.read_text(encoding="utf-8"))
    for nid in ("94", "95", "96", "98", "99", "100"):
        g.pop(nid, None)
    cap = max(5, int(_audio_seconds(audio) * fps // 4) * 4 + 1)
    g["89"]["inputs"]["video"] = video
    g["89"]["inputs"]["frame_load_cap"] = cap
    g["28"]["inputs"]["audio_file"] = audio
    g["30"]["inputs"]["filename_prefix"] = prefix + "_30"
    return g


def _tts(text, instruct, voice, prefix, seed=42, seconds_hint=8.0):
    """CosyVoice 配音：台词 + 情绪指令 + 参考音色 → 音频，并以黑场 MP4 封装便于成片库回放。"""
    n, r = _node, _ref
    seconds = max(3.0, min(30.0, float(seconds_hint)))
    g = {"1": n("FL_CosyVoice3_ModelLoader", model_version="Fun-CosyVoice3-0.5B", download_source="ModelScope",
                device="auto", force_redownload=False, force_reload=False),
         "2": n("LoadAudio", audio=voice),
         "3": n("FL_CosyVoice3_Instruct2", model=r(1), text=text, instruct_text=instruct or "自然清晰地说话",
                reference_audio=r(2), speed=1.0, seed=seed, text_frontend=True),
         "10": n("EmptyImage", width=256, height=256, batch_size=max(24, int(round(seconds * 24))), color=0),
         "11": n("VHS_VideoCombine", images=r(10), audio=r(3), frame_rate=24.0, loop_count=0,
                 filename_prefix=prefix, format="video/h264-mp4", pingpong=False, save_output=True)}
    return g


def _tts_lipsync(text, instruct, voice, video, prefix, seed=42):
    """配音+对口型链：CosyVoice 生成配音，直接驱动 MuseTalk 对参考视频对口型。
    移除模板的文件音频支路与 BatchUncrop 合成支路（配音时长构建期未知），保留 256 直接输出。"""
    g = json.loads(LIPSYNC_TEMPLATE.read_text(encoding="utf-8"))
    for nid in ("28", "94", "95", "96", "98", "99", "100"):
        g.pop(nid, None)
    n, r = _node, _ref
    g["1t"] = n("FL_CosyVoice3_ModelLoader", model_version="Fun-CosyVoice3-0.5B", download_source="ModelScope",
                device="auto", force_redownload=False, force_reload=False)
    g["2t"] = n("LoadAudio", audio=voice)
    g["3t"] = n("FL_CosyVoice3_Instruct2", model=r("1t"), text=text, instruct_text=instruct or "自然清晰地说话",
                reference_audio=r("2t"), speed=1.0, seed=seed, text_frontend=True)
    g["27"]["inputs"]["audio"] = r("3t")
    g["30"]["inputs"]["audio"] = r("3t")
    g["30"]["inputs"]["filename_prefix"] = prefix + "_30"
    g["89"]["inputs"]["video"] = video
    g["89"]["inputs"]["frame_load_cap"] = 0
    return g


def build_tool_workflow(model_id, prompt, video_path=None, audio_path=None, instruct_text="", seed=42):
    if model_id not in TOOL_PRESETS:
        raise ValueError("未知的工具预设：" + str(model_id))
    missing = _tools_missing(model_id)
    if missing:
        raise ValueError("模型尚未完整下载：" + "、".join(missing))
    prompt = (prompt or "").strip()
    if model_id != "musetalk_lipsync" and not prompt:
        raise ValueError("配音需要输入台词文本")
    if model_id == "musetalk_lipsync" and not video_path:
        raise ValueError("对口型需要选择参考视频")
    if model_id in ("cosyvoice_tts", "cosyvoice_lipsync") and not audio_path:
        raise ValueError("配音需要选择参考音色音频（3–10 秒清晰人声）")
    if model_id == "cosyvoice_lipsync" and not video_path:
        raise ValueError("配音+对口型需要选择参考视频")
    video = _copy_video(video_path) if video_path else None
    audio = _copy_audio(audio_path) if audio_path else None
    prefix = "studio/" + time.strftime("%Y%m%d") + "/" + model_id.split("_")[0] + "_" + uuid.uuid4().hex[:12]
    if model_id == "musetalk_lipsync":
        graph = _lipsync(video, audio, prefix)
    elif model_id == "cosyvoice_tts":
        graph = _tts(prompt, instruct_text, audio, prefix, seed=seed)
    else:
        graph = _tts_lipsync(prompt, instruct_text, audio, video, prefix, seed=seed)
    validate_graph(graph)
    return graph


def _h3(prompt, negative, image, video, width, height, frames, seed, upscale, prefix, fast=False, upscale_to=None, images=None):
    # MiniMax H3 omni-modal: pruned INT8 ConvRot base + 8-step turbo LoRA,
    # res_multistep/simple sampling, dual video+audio VAEs, native stereo audio.
    # NOTE: no SageAttention patch — sage kernels corrupt H3 int8_convrot attention
    # (verified: produces grid-noise output). Default attention is already fast.
    # Multi-image: every photo becomes a reference input of ReferenceToVideo
    # (identity fusion, up to 6); prompt tags <Picture i> address them in order.
    n, r = _node, _ref
    g = {"1": n("UNETLoader", unet_name=FILES["h3"][1], weight_dtype="default"),
         "2": n("CLIPLoader", clip_name=FILES["h3_te"][1], type="minimax", device="default"),
         "3": n("VAELoader", vae_name=FILES["h3_vae"][1]),
         "4": n("VAELoader", vae_name=FILES["h3_avae"][1]),
         "5": n("LoraLoaderModelOnly", model=r(1), lora_name=FILES["h3_lora"][1], strength_model=1.0)}
    model = r(5)
    inputs = {"clip": r(2), "vae": r(3), "prompt": prompt, "width": width, "height": height, "length": frames}
    refs = images or ([image] if image else [])
    if video:
        g["50"] = n("LoadVideo", file=video)
        g["51"] = n("GetVideoComponents", video=r(50))
        g["52"] = n("ImageFromBatch", image=r(51), batch_index=0, length=1)
        inputs["first_frame"] = r(52)
        g["6"] = n("MiniMaxH3ImageToVideo", **inputs)
    elif refs:
        # 多图参考融合：全部照片作为 ref_image_N 进入 ReferenceToVideo，模型同时理解
        # 每张参考的主体并合成到同一视频；提示词用 <Picture i> 按顺序指代。
        inputs["ref_image_size"] = "match"
        if "<Picture" not in prompt:
            inputs["prompt"] = prompt + "（<Picture 1> 至 <Picture %d> 为按顺序给出的参考图，让其中的主体共同出现在视频中。）" % len(refs)
        for i, ref in enumerate(refs):
            node_id = str(50 + i)
            g[node_id] = n("LoadImage", image=ref)
            inputs["ref_images.ref_image_%d" % i] = r(node_id)
        g["6"] = n("MiniMaxH3ReferenceToVideo", **inputs)
    else:
        g["6"] = n("MiniMaxH3ImageToVideo", **inputs)
    g["7"] = n("BasicGuider", model=model, conditioning=r(6))
    g["9"] = n("RandomNoise", noise_seed=seed)
    g["11"] = n("BasicScheduler", model=model, scheduler="simple", steps=8, denoise=1.0)
    g["12"] = n("KSamplerSelect", sampler_name="res_multistep")
    g["8"] = n("SamplerCustomAdvanced", noise=r(9), guider=r(7), sampler=r(12), sigmas=r(11), latent_image=r(6, 1))
    g["80"] = n("VAEDecode", samples=r(8), vae=r(3))
    g["81"] = n("VAEDecodeAudio", samples=r(8, 1), vae=r(4))
    g["90"] = n("CreateVideo", images=r(80), fps=24.0, bit_depth=8, color_space="sRGB", audio=r(81))
    g["92"] = n("SaveVideo", video=r(90), filename_prefix=prefix, format="mp4", **{"format.codec": "h264"})
    return g


def validate_graph(graph, *, check_files=True):
    """Offline node/port/type/dynamic-widget checks; no model imports or CUDA."""
    info = json.loads(OBJECT_INFO.read_text(encoding="utf-8-sig"))
    if not isinstance(graph, dict) or not graph:
        raise ValueError("工作流为空")
    graph_compute_path(graph)
    def specs_for(specs, values, prefix=""):
        result = {}
        for required in ("required", "optional"):
            for field, spec in specs.get(required, {}).items():
                key = prefix + field
                if spec[0] == "COMFY_AUTOGROW_V3":
                    grow = spec[1]["template"]
                    grow_prefix = prefix + field + "." + grow["prefix"]
                    grow_max = grow.get("max")
                    grow_fields = {}
                    for grow_required in ("required", "optional"):
                        grow_fields.update(grow["input"].get(grow_required, {}))
                    grown = [k for k in values if k.startswith(grow_prefix)]
                    if grow_max is not None and len(grown) > grow_max:
                        raise ValueError(kind + " 的 " + grow_prefix + "* 最多 " + str(grow_max) + " 个")
                    for grown_key in grown:
                        result[grown_key] = next(iter(grow_fields.values()))
                    continue
                if required == "required" and key not in values:
                    raise ValueError("缺少节点输入：" + key)
                if key not in values:
                    continue
                result[key] = spec
                if spec[0] == "COMFY_DYNAMICCOMBO_V3":
                    selected = next((opt for opt in spec[1]["options"] if opt["key"] == values[key]), None)
                    if not selected:
                        raise ValueError("无效动态选项：" + key)
                    result.update(specs_for(selected["inputs"], values, key + "."))
        return result
    visited, visiting = set(), set()
    for ident, node in graph.items():
        kind, values = node["class_type"], node["inputs"]
        if kind not in info:
            raise ValueError("本机缺少节点：" + kind)
        specs = specs_for(info[kind]["input"], values)
        for key, value in values.items():
            if key not in specs:
                raise ValueError(kind + " 含未知输入：" + key)
            spec = specs[key]
            if isinstance(value, list) and len(value) == 2 and isinstance(value[1], int):
                source = graph.get(str(value[0]))
                if not source or not 0 <= value[1] < len(info[source["class_type"]]["output"]):
                    raise ValueError("无效节点连线")
                output_type = info[source["class_type"]]["output"][value[1]]
                if isinstance(spec[0], str) and output_type not in spec[0].split(",") and spec[0] != "*":
                    raise ValueError(kind + "." + key + " 连线类型不匹配")
            elif spec[0] in ("INT", "FLOAT", "FLOAT,INT"):
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError("无效数值：" + key)
                if spec[0] == "INT" and not isinstance(value, int):
                    raise ValueError("需要整数：" + key)
                opts = spec[1] if len(spec) > 1 else {}
                if value < opts.get("min", -math.inf) or value > opts.get("max", math.inf):
                    raise ValueError("数值超出范围：" + key)
            elif isinstance(spec[0], list) or spec[0] == "COMBO":
                opts = spec[0] if isinstance(spec[0], list) else spec[1].get("options", [])
                # Cached model/file options can lag the downloader; validate actual
                # files independently rather than rejecting a newly arrived file.
                model = next((k for k, (_, name) in FILES.items() if value == name), None)
                if model:
                    if check_files and not _complete_model(model):
                        raise ValueError("模型未下载完成：" + value)
                elif kind in ("LoadImage", "LoadVideo", "LoadAudio", "VHS_LoadVideo", "VHS_LoadAudio") and key in ("image", "file", "audio", "video", "audio_file"):
                    if check_files:
                        path = (INPUT_ROOT / value).resolve()
                        if INPUT_ROOT.resolve() not in path.parents or not path.is_file():
                            raise ValueError("参考素材不存在")
                elif value not in opts:
                    raise ValueError(kind + "." + key + " 选项无效")
    def visit(ident):
        if ident in visiting:
            raise ValueError("工作流存在循环")
        if ident in visited:
            return
        visiting.add(ident)
        for value in graph[ident]["inputs"].values():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[1], int):
                visit(str(value[0]))
        visiting.remove(ident)
        visited.add(ident)
    for ident in graph:
        visit(ident)
    outputs = [node for node in graph.values() if node["class_type"] == "SaveVideo"]
    vhs_outputs = [node for node in graph.values() if node["class_type"] == "VHS_VideoCombine"
                   and node["inputs"].get("save_output", True) is True
                   and "mp4" in str(node["inputs"].get("format", "")).lower()]
    image_outputs = [node for node in graph.values() if node["class_type"] == "SaveImage"]
    if not outputs and not vhs_outputs and not image_outputs:
        raise ValueError("需要一个明确的输出节点")
    if image_outputs:
        if len(image_outputs) != 1:
            raise ValueError("需要一个明确的图片输出节点")
        prefix = image_outputs[0]["inputs"]["filename_prefix"]
        if not isinstance(prefix, str) or not prefix.startswith("studio/img/") or any(c in prefix for c in ("..", "\\", ":", "%")):
            raise ValueError("图片输出路径必须位于 studio/img 目录")
        return True
    if len(outputs) > 1 or (not outputs and not vhs_outputs):
        raise ValueError("需要一个明确的 MP4 输出节点")
    prefix = (outputs or vhs_outputs)[0]["inputs"]["filename_prefix"]
    if not isinstance(prefix, str) or not prefix.startswith("studio/") or any(c in prefix for c in ("..", "\\", ":", "%")):
        raise ValueError("输出路径必须位于 studio 目录")
    return True


def build_workflow(model_id, prompt, image_path=None, image_paths=None, video_path=None, width=None, height=None, seconds=5, seed=0, negative_prompt="", upscale=False, fast=False, audio_path=None, instruct_text="", compute_path=DEFAULT_COMPUTE_PATH):
    """Build a validated API graph; reference image(s) or video selects the i2v family.

    Up to 4 reference images: the first anchors the start frame; H3 uses the last as
    end frame, LTX-2.5 anchors extras as evenly spaced keyframe guides.
    Width/height are base rendering dimensions. Upscale increases the output."""
    compute_path = validate_compute_path(compute_path)
    if model_id in TOOL_PRESETS:
        graph = build_tool_workflow(model_id, prompt, video_path=video_path, audio_path=audio_path,
                                    instruct_text=instruct_text, seed=seed if isinstance(seed, int) else 42)
        return _set_compute_path(graph, compute_path)
    if image_paths is not None:
        if not isinstance(image_paths, (list, tuple)) or len(image_paths) > 6 or not all(isinstance(p, str) and p for p in image_paths):
            raise ValueError("参考图像最多 6 张")
    paths = list(image_paths) if image_paths else ([image_path] if image_path else [])
    # Frames are rounded to each VAE's temporal grid; output duration is frames/24.
    # A reference video contributes its first frame; LTX graphs additionally attach
    # the union-control IC-LoRA as in-context reference. fast=True cuts Hunyuan t2v
    # to a 4-step lightx2v LoRA, switches Hunyuan i2v to FP8 CFG-distilled weights,
    # decodes with LightTAE, and patches either family with SageAttention2++.
    if model_id in ALIASES:
        model_id, mode = ALIASES[model_id]
        if mode == "i2v" and not paths and not video_path:
            raise ValueError("图生视频需要选择首帧图片或参考视频")
        if mode == "t2v" and paths:
            raise ValueError("文生视频无需首帧；请移除图片或改选图生视频")
    if paths and video_path:
        raise ValueError("参考图像与参考视频请二选一")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 16000:
        raise ValueError("请输入 1–16000 字符的视频提示词")
    if not isinstance(negative_prompt, str) or len(negative_prompt) > 8000:
        raise ValueError("反向提示词最多支持 8000 字符")
    if type(upscale) is not bool:
        raise ValueError("放大选项必须是布尔值")
    if type(fast) is not bool:
        raise ValueError("加速选项必须是布尔值")
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or not 1 <= seconds <= MAX_SECONDS:
        raise ValueError("时长请选择 1–30 秒")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2 ** 64:
        raise ValueError("种子必须是 0–18446744073709551615 的整数")
    width, height = 768 if width is None else width, 512 if height is None else height
    grid = 32 if model_id in ("ltx23", "ltx25", "h3") else 16
    for value in (width, height):
        if type(value) is not int or not 256 <= value <= 3840 or value % grid:
            raise ValueError("宽高应为 256–3840 范围内、%d 的倍数" % grid)
    if max(width, height) / min(width, height) > 2.0:
        raise ValueError("画面比例请保持在 1:2 至 2:1 范围")
    # 超过 1920 的目标尺寸走放大管线：基线按目标一半渲染，再放大到目标
    upscale_to = None
    if model_id in ("hunyuan15", "ltx23", "ltx25") and max(width, height) > 1920:
        upscale = True
    if upscale and model_id in ("hunyuan15", "ltx23", "ltx25") and max(width, height) > 1920:
        upscale_to = (width, height)
        width = max(256, round(width / 2 / grid) * grid)
        height = max(256, round(height / 2 / grid) * grid)
    missing = [FILES[k][1] for k in _dependencies(model_id, bool(paths), upscale, video=bool(video_path), fast=fast) if not _complete_model(k)]
    if missing:
        raise ValueError("模型尚未完整下载：" + "、".join(missing))
    images = [_copy_image(p) for p in paths]
    image = images[0] if images else None
    video = _copy_video(video_path) if video_path else None
    if model_id == "h3":
        frames = max(22, round(float(seconds) * 24 / 17) * 17 + 5)
    else:
        temporal = 8 if model_id in ("ltx23", "ltx25") else 4
        frames = max(temporal + 1, round(float(seconds) * 24 / temporal) * temporal + 1)
    prefix = "studio/" + time.strftime("%Y%m%d") + "/" + model_id + "_" + uuid.uuid4().hex[:12]
    builder = _h3 if model_id == "h3" else (_ltx25 if model_id == "ltx25" else (_ltx if model_id == "ltx23" else _hunyuan))
    extra = {"images": images} if model_id in ("h3", "ltx25", "hunyuan15") else {}
    graph = builder(prompt.strip(), negative_prompt, image, video, width, height, frames, seed, upscale, prefix, fast=fast, upscale_to=upscale_to, **extra)
    _set_compute_path(graph, compute_path)
    validate_graph(graph)
    return graph


if __name__ == "__main__":
    print(json.dumps(catalog(), ensure_ascii=False, indent=2))
