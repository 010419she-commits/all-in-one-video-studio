"""Central local path configuration for the video backend.

Importing never starts a process, opens a network connection, or touches the
GPU. Values come from environment variables or the documented defaults below;
missing optional values stay unset instead of guessing machine locations.

VIDEO_STUDIO_COMFYUI          ComfyUI checkout root (unset until configured)
VIDEO_STUDIO_PYTHON           Python for backend services (default: current)
VIDEO_STUDIO_STRATA_ROOT      Optional local Strata checkout root
VIDEO_STUDIO_EDGE_TTS         Optional edge-tts executable (PATH lookup allowed)
VIDEO_STUDIO_VOICE_OUTPUT     Voice-over output directory
                              (default: LOCALAPPDATA/VideoStudio/voice)
VIDEO_STUDIO_WECHAT_WORKSPACE Explicit WeChat workspace directory (optional)
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parent


def _path(name):
    value = os.environ.get(name, "").strip()
    return Path(value).expanduser() if value else None


def comfyui_root():
    return _path("VIDEO_STUDIO_COMFYUI")


def backend_python():
    return _path("VIDEO_STUDIO_PYTHON") or Path(sys.executable)


def strata_root():
    return _path("VIDEO_STUDIO_STRATA_ROOT")


def edge_tts():
    configured = _path("VIDEO_STUDIO_EDGE_TTS")
    if configured is not None:
        return str(configured)
    return shutil.which("edge-tts")


def voice_output_dir():
    configured = _path("VIDEO_STUDIO_VOICE_OUTPUT")
    if configured is not None:
        return configured
    base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    return base / "VideoStudio" / "voice"


def wechat_workspace():
    return _path("VIDEO_STUDIO_WECHAT_WORKSPACE")


def agent_cli(name):
    configured = os.environ.get("VIDEO_STUDIO_" + name.upper() + "_CLI", "").strip()
    if configured:
        return configured
    return shutil.which(name)
