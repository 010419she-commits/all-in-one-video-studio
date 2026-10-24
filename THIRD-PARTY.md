# 第三方组件与外部资源说明

本仓库不重新分发下列第三方库、程序、模型或权重。这里列出的是安装和运行时的依赖关系，不表示这些项目为本项目背书。

## Python 依赖

`video-stack/requirements.txt` 中版本已在本机验证；各依赖的完整许可证文本以其上游发行包为准。

| 组件 | 用途 | 许可证 | 上游 |
| --- | --- | --- | --- |
| psutil 7.2.2 | 本机进程与 GPU 交接 | BSD-3-Clause | https://github.com/giampaolo/psutil |
| Pillow 12.3.0 | 图片解码与处理 | MIT-CMU | https://github.com/python-pillow/Pillow |
| PyAV 19.0.1 | 视频首帧与媒体核验 | BSD-3-Clause | https://github.com/PyAV-Org/PyAV |
| aiohttp 3.14.5 | ComfyUI 进度连接 | Apache-2.0 AND MIT（发行元数据） | https://github.com/aio-libs/aiohttp |

可选工具：

- PyTorch：BSD-3-Clause，https://github.com/pytorch/pytorch 。量化脚本使用；请按显卡/CUDA 环境单独安装，不作为核心依赖强制下载。
- safetensors：Apache-2.0，https://github.com/huggingface/safetensors 。量化脚本使用。
- edge-tts：主要为 LGPL-3.0，`src/edge_tts/srt_composer.py` 单独采用 MIT，https://github.com/rany2/edge-tts 。仅作为独立可执行程序调用；本项目不链接或内嵌其代码。

## 系统与外部程序

- .NET Framework、Windows API、Windows Media Player：由 Windows 提供的系统组件，不随仓库再分发。
- ComfyUI：GPL-3.0，https://github.com/Comfy-Org/ComfyUI 。作为独立安装的外部推理程序使用。
- 工作流配方参考 ComfyUI 官方视频模板和文档重新构建；相关来源记录在 `video-stack/studio_templates/README.txt`。

## 自定义节点

部分工作流需要额外节点包，包括但不限于：

- KJNodes：https://github.com/kijai/ComfyUI-KJNodes
- ComfyUI-VideoHelperSuite：https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite
- comfyui_controlnet_aux：https://github.com/Fannovel16/comfyui_controlnet_aux
- ComfyUI-LTXVideo：https://github.com/Lightricks/ComfyUI-LTXVideo
- MuseTalk：https://github.com/TMElyralab/MuseTalk
- CosyVoice：https://github.com/FunAudioLLM/CosyVoice
- MiniMax H3 对应节点与模型组件

这些节点的来源和许可证并不完全相同，本仓库不打包其代码。安装前应查看实际上游仓库和对应版本的 LICENSE，不要把本文件当作其许可证的替代品。

## 模型与权重

仓库中的文件名引用不等于再分发或许可。HunyuanVideo、LTX、MiniMax H3、Qwen、Gemma、MuseTalk、CosyVoice、Lightning/LightX2V LoRA、YOLOX/DWPose 等模型、编码器、量化权重和 LoRA 均需单独获取。

其中一些模型使用社区许可、收入门槛、地域/用途限制或单独使用条款，并非都按 OSI 开源许可证发布。下载、商用和再分发前，必须审查具体模型及版本的官方条款。不要将本项目的 GPL-3.0 套用到这些外部资源。

## 图标

`VideoStudio/studio.png` 和 `VideoStudio/studio.ico` 由本项目的 `make_icon.py` 生成，使用 Pillow 作为开发期工具；图标不包含第三方图片素材。
