import json
import sys

import workflow_builder as wb

qwen_graph = {
    "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "qwen_image_fp8_e4m3fn.safetensors", "weight_dtype": "default"}},
    "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen_2.5_vl_7b_fp8_scaled.safetensors", "type": "qwen_image", "device": "default"}},
    "3": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_vae.safetensors"}},
    "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": "test"}},
    "5": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["4", 0]}},
    "6": {"class_type": "EmptySD3LatentImage", "inputs": {"width": 1328, "height": 1328, "batch_size": 1}},
    "7": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": "Qwen-Image-Lightning-8steps-V1.0.safetensors", "strength_model": 1.0}},
    "8": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["7", 0], "shift": 3.1}},
    "9": {"class_type": "KSampler", "inputs": {"model": ["8", 0], "positive": ["4", 0], "negative": ["5", 0], "latent_image": ["6", 0], "seed": 42, "steps": 8, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
    "10": {"class_type": "VAEDecode", "inputs": {"samples": ["9", 0], "vae": ["3", 0]}},
    "11": {"class_type": "SaveImage", "inputs": {"images": ["10", 0], "filename_prefix": "studio/toonflow/testimg"}},
}

h3_graph = {
    "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "minimax_h3_fl2va_pruned_int8_convrot.safetensors", "weight_dtype": "default"}},
    "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors", "type": "minimax", "device": "default"}},
    "3": {"class_type": "VAELoader", "inputs": {"vae_name": "minimax_h3_video_vae_int8_convrot.safetensors"}},
    "4": {"class_type": "VAELoader", "inputs": {"vae_name": "minimax_h3_audio_vae_fp32.safetensors"}},
    "5": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors", "strength_model": 1.0}},
    "6": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {"clip": ["2", 0], "vae": ["3", 0], "prompt": "test", "width": 1344, "height": 768, "length": 124}},
    "7": {"class_type": "BasicGuider", "inputs": {"model": ["5", 0], "conditioning": ["6", 0]}},
    "9": {"class_type": "RandomNoise", "inputs": {"noise_seed": 42}},
    "11": {"class_type": "BasicScheduler", "inputs": {"model": ["5", 0], "scheduler": "simple", "steps": 8, "denoise": 1.0}},
    "12": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
    "8": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["9", 0], "guider": ["7", 0], "sampler": ["12", 0], "sigmas": ["11", 0], "latent_image": ["6", 1]}},
    "80": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
    "81": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["8", 1], "vae": ["4", 0]}},
    "90": {"class_type": "CreateVideo", "inputs": {"images": ["80", 0], "fps": 24.0, "bit_depth": 8, "color_space": "sRGB", "audio": ["81", 0]}},
    "92": {"class_type": "SaveVideo", "inputs": {"video": ["90", 0], "filename_prefix": "studio/toonflow/testvid", "format": "mp4", "format.codec": "h264"}},
}

for name, graph in (("qwen-image", qwen_graph), ("h3-video", h3_graph)):
    try:
        wb.validate_graph(graph, check_files=False)
        print(name, "VALID")
    except Exception as exc:
        print(name, "INVALID:", str(exc)[:300])
