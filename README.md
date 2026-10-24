# All-in-One Video Studio · 视频控制台

一个 Windows 本地视频创作工作台：C# WinForms 桌面界面、Python 回环服务和独立 ComfyUI 推理环境。支持分镜草稿、任务队列、提示词优化、模型交接、配音/对口型和本地成片预览。

本仓库只包含源码、测试和小型原创资源。**不包含 ComfyUI、模型权重、Python 运行环境、用户密钥或私人创作数据。**

## 功能

- 11 个预设：HunyuanVideo 1.5、LTX-2.3、LTX-2.5、MiniMax H3 的文生/图生视频，以及 MuseTalk、CosyVoice 工具。
- 最多 12 个分镜，独立保存提示词、参考图/视频/音频和生成参数。
- 本地 GPU 交接、任务进度、MP4 核验和成片预览。
- Kimi API、Kimi Code、Qwen Code、Codex 提示词优化；提供本地 MCP 工具。
- Kimi API Key 由 Windows 当前用户 DPAPI 加密保存，不进入仓库。

## 目录

```text
VideoStudio/       C# 桌面程序、构建脚本和离线 UI 检查
video-stack/       Python 回环服务、MCP、工作流构建器和测试
LICENSE            GPL-3.0
THIRD-PARTY.md     第三方依赖、外部组件和模型条款说明
```

前端默认使用 `127.0.0.1:8900`，GPU 交接服务使用 `127.0.0.1:8899`，ComfyUI 使用 `127.0.0.1:8188`。不要把这三个端口暴露到局域网或公网。

## 环境要求

- Windows 10/11 x64，带 .NET Framework 4.x 的 `csc.exe`。
- Python 3.12（当前验证版本）。
- 独立安装的 ComfyUI，以及与所选预设匹配的模型和自定义节点。
- 生成视频时需要受支持的 NVIDIA 显卡和驱动；仅构建和运行离线测试不需要 GPU。
- Windows Media Player 用于应用内 MP4 播放；PyAV 用于生成视频首帧。

模型文件名、节点要求和上游链接见 `video-stack/studio_templates/README.txt` 与 `THIRD-PARTY.md`。模型及节点各自有不同的许可证和使用限制，不能用本仓库的 GPL 许可证替代。

## 安装与构建

在 PowerShell 中执行：

```powershell
git clone https://github.com/010419she-commits/all-in-one-video-studio.git
cd all-in-one-video-studio
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\video-stack\requirements.txt
```

告诉程序你的 ComfyUI 和 Python 位置。以下仅为示例，请换成自己的路径：

```powershell
$env:VIDEO_STUDIO_COMFYUI = "D:\Tools\ComfyUI"
$env:VIDEO_STUDIO_PYTHON = (Resolve-Path .\.venv\Scripts\python.exe).Path
powershell -ExecutionPolicy Bypass -File .\VideoStudio\build.ps1
```

环境变量只对当前 PowerShell 会话生效。前端启动后端时会继承这些值。

| 环境变量 | 用途 | 默认值 |
| --- | --- | --- |
| `VIDEO_STUDIO_COMFYUI` | ComfyUI 根目录 | 后端同级 `ComfyUI`（需自行安装/配置） |
| `VIDEO_STUDIO_PYTHON` | 启动后端和首帧提取的 Python | 后端为当前解释器；前端为 PATH 中的 `python` |
| `VIDEO_STUDIO_BACKEND` | `video-stack` 目录 | 前端程序上一级的 `video-stack` |
| `VIDEO_STUDIO_VOICE_OUTPUT` | 配音输出目录 | `%LOCALAPPDATA%\VideoStudio\voice` |
| `VIDEO_STUDIO_EDGE_TTS` | 可选 `edge-tts` 可执行文件 | 在 PATH 中查找 |
| `VIDEO_STUDIO_STRATA_ROOT` | 可选 Strata 根目录 | 不配置即不使用 Strata |
| `VIDEO_STUDIO_WECHAT_WORKSPACE` | 需要使用持久视频风格的显式工作区 | 不配置即不自动套用 |
| `VIDEO_STUDIO_KIMI_CLI`、`VIDEO_STUDIO_QWEN_CLI`、`VIDEO_STUDIO_CODEX_CLI` | 三类 Agent CLI 的可执行文件或命令名 | 分别在 PATH 中查找 `kimi`、`qwen`、`codex` |

首次准备模型工作流前，先正常启动 ComfyUI，再在另一个终端刷新本机节点信息：

```powershell
.\.venv\Scripts\python.exe .\video-stack\update_object_info.py
```

该命令只访问本机回环 ComfyUI `/object_info`，生成被 Git 排除的 `video-stack/object_info.json`。之后启动：

```powershell
.\VideoStudio\VideoStudio.exe
```

也可用 `VideoStudio/install-shortcuts.ps1` 创建桌面和开始菜单快捷方式。Agent 接入只在你明确操作时修改对应 Agent 的 `video-studio` MCP 配置项。

配音功能需要额外安装 `edge-tts`，并确保其可执行文件在 PATH 中，或设置 `VIDEO_STUDIO_EDGE_TTS`。量化/探测工具还需要按你的 GPU 环境单独安装 PyTorch 和 safetensors，不属于核心依赖。

## 测试

先按上文生成 `video-stack/object_info.json`；工作流结构测试需要这份本机节点快照。它只来自回环 ComfyUI，不要求加载模型或执行 GPU 推理。

核心测试使用临时目录、mock 和本机回环临时端口，不启动真实 Agent 推理、付费 API 或 GPU 生成：

```powershell
Push-Location .\video-stack
..\.venv\Scripts\python.exe -X utf8 -m unittest discover -p "test_*.py"
Pop-Location
powershell -ExecutionPolicy Bypass -File .\VideoStudio\build.ps1
powershell -ExecutionPolicy Bypass -File .\VideoStudio\qa\run-offline-tests.ps1
.\VideoStudio\VideoStudio.exe --visual-check
```

离线 QA 运行器覆盖连接、时长、进度、交接、计算路径、参考模式和控件生命周期，产物写入 `VideoStudio/qa/bin/`。视觉检查报告位于 `VideoStudio/qa/hybrid/visual-check.txt`；这些生成物都不会提交到 Git。

## 数据与安全

- 设置、加密密钥、草稿和任务状态保存在 `%LOCALAPPDATA%\VideoStudio`，不属于仓库。
- 仓库不保存 API Key。若误提交过密钥，应立即吊销并重新生成，而不是只删除文件。
- “检查连接”只读取模型列表；提示词优化、Agent CLI 和视频生成都需要明确的用户操作。
- 工作流、模型文件和输出可能包含你的创作内容，发布前请自行检查。

## 验证范围与许可证

离线单元/契约测试、前端构建和离屏视觉检查可重复执行。模型实际出片效果、速度和显存占用依赖你的 ComfyUI、节点、模型与硬件，不能由这些离线测试保证。

本项目自有源码按 **GNU GPL v3** 发布。分发修改版或二进制时，需要同时遵守 GPL 的源码提供义务。第三方依赖、外部程序和模型保留各自条款，详见 `THIRD-PARTY.md`。
