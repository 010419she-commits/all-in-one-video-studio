Video Studio workflow recipes

workflow_builder.py is the authoritative offline builder. It emits API-format
node graphs, not ComfyUI UI workflows. No workflow is submitted automatically.

Video presets: hunyuan_t2v/i2v, ltx_t2v/i2v (LTX-2.3),
ltx25_t2v/i2v (LTX-2.5), and h3_t2v/i2v (MiniMax H3).
Tool presets: musetalk_lipsync, cosyvoice_tts, and cosyvoice_lipsync.

Video models support text-to-video and image-to-video variants. Choose an image
only for an image-input preset. Images are decoded and copied into
ComfyUI/input/studio; originals remain unchanged. Video output is MP4/H.264
under ComfyUI/output/studio.

Default base rendering: 768 x 512, 24 fps, approximately 5 seconds. Duration is
rounded to the model's temporal grid: Hunyuan 4n+1, LTX 8n+1 frames. Base width
and height must be multiples of 16 (Hunyuan) or 32 (LTX). Width/height refer to
the base pass, not the upscaled output. Video generation may use CPU offloading;
these presets do not claim a benchmark or a tested runtime memory limit.

Hunyuan: official 20-step Euler CFG 6, sampling shift 7, tiled VAE decode.
Optional upscale: Hunyuan's 1080p SR weights and latent upsampler, aspect ratio
preserved, short edge aligned near 1080 (1088 pixels), official 8-step split
refinement. This can exceed 1920 on the long edge for wide source ratios.

LTX-2.3: FP8 checkpoint, FP4 Gemma text encoder, distilled 1.1 model LoRA at
0.5, official 8-step sigma schedule and CFG 1. Produces both video and generated
audio. Optional upscale: spatial x2 and the official 3-step refinement schedule.
Gemma prompt-enhancement LoRA is cataloged but not loaded by these recipes;
the selected agent supplies the final prompt instead. LTX-2.5 and H3 have their
own model files and recipes; workflow_builder.py is always authoritative.

Structural, node-input, dynamic-widget, model-file, image, and graph-link checks
run offline. GPU inference has NOT been run as part of installation. A successful
preflight means the local inputs are ready; it is not proof of a rendered clip.

Primary sources inspected on 2026-10-19:
https://docs.comfy.org/tutorials/video/hunyuan/hunyuan-video-1-5
https://docs.comfy.org/tutorials/video/ltx/ltx-2-3
Installed official templates:
ComfyUI/venv/Lib/site-packages/comfyui_workflow_templates_json/templates/
  video_hunyuan_video_1.5_720p_t2v.json
  video_hunyuan_video_1.5_720p_i2v.json
  video_ltx2_3_t2v.json
  video_ltx2_3_i2v.json
The local ComfyUI node definitions are the schema authority. While ComfyUI is
running, execute update_object_info.py to refresh the Git-ignored object_info.json
snapshot from the loopback /object_info endpoint.