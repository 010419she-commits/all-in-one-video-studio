"""Separate persistent video style from per-request video content.

No model, backend, filesystem write, or network operation occurs on import.
Only an explicit profile update writes the profile; compile never persists the
per-request user prompt and never optimizes either source string.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
from typing import Any
import uuid

DEFAULT_PROFILE_PATH = Path(__file__).resolve().with_name("video-prompt-profile.json")
DEFAULT_MAX_LENGTH = 12000
SYSTEM_LABEL = "【视频风格 / 系统提示词】"
USER_LABEL = "【本次内容 / 用户提示词】"


class VideoPromptError(ValueError):
    """Safe configuration or compilation error suitable for a chat reply."""

    def __init__(self, message: str, code: str = "INVALID_VIDEO_PROMPT") -> None:
        super().__init__(message)
        self.code = code


def _limit(value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise VideoPromptError("视频提示词长度上限必须是正整数。", "INVALID_LIMIT")
    return value


def _check_length(text: str, maximum: int, kind: str = "合成后的视频提示词") -> None:
    if len(text) > maximum:
        raise VideoPromptError(
            f"{kind}共 {len(text)} 个字符，超过 {maximum} 个字符上限；请缩短后重试，原文未被截断。",
            "PROMPT_TOO_LONG",
        )


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp." + uuid.uuid4().hex)
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


class VideoPromptStore:
    _registry_lock = threading.Lock()
    _path_locks: dict[str, threading.RLock] = {}

    def __init__(self, path: str | Path, max_prompt_length: int = DEFAULT_MAX_LENGTH):
        self.path = Path(path)
        self.max_prompt_length = _limit(max_prompt_length)
        key = os.path.normcase(os.path.realpath(self.path))
        with self._registry_lock:
            self.lock = self._path_locks.setdefault(key, threading.RLock())

    def _load(self) -> dict:
        # The last complete state remains recoverable if a write was interrupted.
        for path in (self.path, self.path.with_suffix(self.path.suffix + ".bak")):
            try:
                value = json.loads(path.read_text(encoding="utf-8-sig"))
                if isinstance(value, dict):
                    return value
            except (OSError, ValueError):
                continue
        return {}

    def get(self) -> dict[str, Any]:
        """Read style metadata only; a missing profile is empty and is not saved."""
        with self.lock:
            value = self._load()
            system = value.get("system_prompt", "")
            version = value.get("version", 0)
            updated = value.get("updated_at")
            if not isinstance(system, str):
                raise VideoPromptError("已保存的视频系统提示词格式无效，请重新设置。", "INVALID_PROFILE")
            if not isinstance(version, int) or isinstance(version, bool) or version < 0:
                raise VideoPromptError("已保存的视频提示词版本格式无效，请重新设置。", "INVALID_PROFILE")
            _check_length(system, self.max_prompt_length, "视频系统提示词")
            return {"system_prompt": system, "version": version,
                    "updated_at": updated if isinstance(updated, str) else None, "schema_version": 1}

    def set_system_prompt(self, text: str) -> dict[str, Any]:
        """Atomically save only the persistent style and increment its version."""
        if not isinstance(text, str):
            raise VideoPromptError("视频系统提示词必须是文本。", "INVALID_SYSTEM_PROMPT")
        _check_length(text, self.max_prompt_length, "视频系统提示词")
        with self.lock:
            old = self._load()
            previous = self.get()
            new = dict(old)
            new.update(
                system_prompt=text, version=previous["version"] + 1,
                updated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), schema_version=1,
            )
            _atomic_json(self.path.with_suffix(self.path.suffix + ".bak"), old)
            _atomic_json(self.path, new)
            return self.get()


def compile_video_prompt(
    user_prompt: str,
    system_prompt: str | None = None,
    *,
    store: VideoPromptStore | None = None,
    max_length: int = DEFAULT_MAX_LENGTH,
) -> dict[str, Any]:
    """Compose one prompt while preserving both original source fields exactly.

    None reads the saved style. An explicit empty string bypasses the saved style
    for this request. profile_version is None when an explicit override is used.
    The caller should pass the two source fields, never feed `prompt` back as a
    source field for a second compilation.
    """
    maximum = _limit(max_length)
    if not isinstance(user_prompt, str) or not user_prompt.strip():
        raise VideoPromptError("请提供本次视频的用户提示词。", "EMPTY_USER_PROMPT")
    if system_prompt is not None and not isinstance(system_prompt, str):
        raise VideoPromptError("视频系统提示词必须是文本或不提供。", "INVALID_SYSTEM_PROMPT")
    if store is not None and not isinstance(store, VideoPromptStore):
        raise VideoPromptError("视频提示词配置来源无效。", "INVALID_STORE")
    version = None
    if system_prompt is None:
        profile = (store or VideoPromptStore(DEFAULT_PROFILE_PATH)).get()
        system_prompt = profile["system_prompt"]
        version = profile["version"]
    if system_prompt:
        prompt = f"{SYSTEM_LABEL}\n{system_prompt}\n\n{USER_LABEL}\n{user_prompt}"
    else:
        prompt = user_prompt
    _check_length(prompt, maximum)
    return {"system_prompt": system_prompt, "user_prompt": user_prompt,
            "prompt": prompt, "profile_version": version}
