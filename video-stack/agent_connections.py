"""Register the same local video MCP in the three installed agents.

No model/API credentials are read into returned data. Import and status are
read-only; installation changes only this module's named MCP entry.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import fnmatch
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time
import tomllib
import uuid

import studio_config

BASE = Path(__file__).resolve().parent
PYTHON = studio_config.backend_python()
SERVER = BASE / "video_mcp.py"
SERVER_NAME = "video-studio"
HOME = Path.home()
CONFIGS = {
    "codex": ("Codex", Path(os.environ.get("CODEX_HOME", str(HOME / ".codex"))) / "config.toml"),
    "qwen": ("Qwen Agent", HOME / ".qwen" / "settings.json"),
    "kimi": ("Kimi Code", Path(os.environ.get("KIMI_CODE_HOME", str(HOME / ".kimi-code"))) / "mcp.json"),
}
_LOCK = threading.Lock()


def _load(path: Path):
    raw = path.read_bytes() if path.exists() else b""
    source = raw.decode("utf-8-sig")
    return raw, (tomllib.loads(source) if path.suffix == ".toml" else json.loads(source)) if source.strip() else {}


def _entry(agent: str):
    common = {"command": str(PYTHON), "args": ["-X", "utf8", str(SERVER)], "cwd": str(BASE)}
    if agent == "codex":
        common.update(startup_timeout_sec=30, tool_timeout_sec=60)
    elif agent == "qwen":
        common.update(timeout=60000)
    else:
        common.update(startupTimeoutMs=30000, toolTimeoutMs=60000)
    return common


def _key(agent: str):
    return "mcp_servers" if agent == "codex" else "mcpServers"


def _same_target(entry):
    if not isinstance(entry, dict):
        return False
    expected = _entry("codex")
    return all(entry.get(k) == expected[k] for k in ("command", "args", "cwd"))


def _enabled(agent: str, data, entry):
    if entry.get("enabled") is False or entry.get("disabled") is True:
        return False
    if agent == "qwen":
        rules = data.get("mcp", {})
        allowed = rules.get("allowed")
        excluded = rules.get("excluded", [])
        if allowed is not None and not any(fnmatch.fnmatchcase(SERVER_NAME, p) for p in allowed):
            return False
        if any(fnmatch.fnmatchcase(SERVER_NAME, p) for p in excluded):
            return False
    return True


def status():
    """Public, secret-free status for the native studio (no process launches)."""
    result = []
    for agent, (name, path) in CONFIGS.items():
        item = {"id": agent, "name": name, "configured": False, "detail": "尚未接入", "config_path": str(path)}
        try:
            _, data = _load(path)
            entry = data.get(_key(agent), {}).get(SERVER_NAME)
            if _same_target(entry):
                item["configured"] = _enabled(agent, data, entry)
                item["detail"] = "已注册；新会话可加载视频工具" if item["configured"] else "已注册，但当前 Agent 设置禁用此服务"
            elif entry is not None:
                item["detail"] = "同名服务已存在，启动路径不同；保留原配置"
            if item["configured"] and not SERVER.is_file():
                item["detail"] = "已注册，正在准备视频工具服务"
            item["server_ready"] = SERVER.is_file() and PYTHON.is_file()
        except (OSError, ValueError, TypeError):
            item["detail"] = "配置无法读取；未修改原文件"
        result.append(item)
    return result


def _install_one(agent: str):
    if agent not in CONFIGS:
        raise ValueError("不支持的 Agent")
    _, path = CONFIGS[agent]
    raw, data = _load(path)
    key = _key(agent)
    old_entry = data.get(key, {}).get(SERVER_NAME)
    if old_entry is not None:
        if not _same_target(old_entry):
            raise ValueError("同名视频服务已有不同配置；不会覆盖")
        return False
    updated = copy.deepcopy(data)
    updated.setdefault(key, {})[SERVER_NAME] = _entry(agent)
    if agent == "codex":
        # Append just our table, keeping every existing TOML byte/comment intact.
        suffix = "\n\n[mcp_servers.video-studio]\n" + "\n".join(
            f"{k} = {json.dumps(v, ensure_ascii=False)}" for k, v in _entry(agent).items()
        ) + "\n"
        content = raw + suffix.encode("utf-8")
        parsed = tomllib.loads(content.decode("utf-8-sig"))
    else:
        content = (json.dumps(updated, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        parsed = json.loads(content)
    if parsed != updated:
        raise ValueError("配置完整性检查未通过；未写入")
    # Detect changes by another application between reading and writing.
    if (path.read_bytes() if path.exists() else b"") != raw:
        raise RuntimeError("配置正在被其他应用更新，请稍后重试")
    path.parent.mkdir(parents=True, exist_ok=True)
    backup_dir = path.parent / "backups" / "video-studio"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    if path.exists():
        shutil.copy2(path, backup_dir / f"{path.name}.{stamp}.bak")
    temp = path.with_name(path.name + ".video-studio-" + uuid.uuid4().hex + ".tmp")
    try:
        temp.write_bytes(content)
        if (path.read_bytes() if path.exists() else b"") != raw:
            raise RuntimeError("配置正在被其他应用更新，请稍后重试")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    return True


def install(agent_id="all"):
    """Explicit user action only. Add local video MCP; retain all other settings."""
    choices = list(CONFIGS) if agent_id == "all" else [agent_id]
    with _LOCK:
        for agent in choices:
            _install_one(agent)
    return status()


def verify(agent_id="all", status_tool="video_status"):
    """Handshake/list/read-only status from each actual saved MCP entry.

    Never runs an agent inference request or starts a GPU model. Does not prove
    an already-open agent conversation has reloaded its tool list.
    """
    choices = list(CONFIGS) if agent_id == "all" else [agent_id]
    results = []
    for agent in choices:
        if agent not in CONFIGS:
            raise ValueError("不支持的 Agent")
        path = CONFIGS[agent][1]
        _, data = _load(path)
        entry = data.get(_key(agent), {}).get(SERVER_NAME, {})
        if not _same_target(entry) or not _enabled(agent, data, entry):
            results.append({"id": agent, "ok": False, "error": "视频服务未注册或已禁用"})
            continue
        proc = None
        try:
            env = dict(os.environ)
            env.update(entry.get("env", {}))
            proc = subprocess.Popen([entry["command"], *entry["args"]], cwd=entry["cwd"], env=env,
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, encoding="utf-8", bufsize=1,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            incoming = queue.Queue()
            def read_output():
                try:
                    for line in proc.stdout:
                        incoming.put(line)
                finally:
                    incoming.put(None)
            threading.Thread(target=read_output, daemon=True).start()
            def drain_errors():
                for _ in proc.stderr:
                    pass
            threading.Thread(target=drain_errors, daemon=True).start()
            def send(method, params, ident=None):
                msg = {"jsonrpc": "2.0", "method": method, "params": params}
                if ident is not None:
                    msg["id"] = ident
                proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
                proc.stdin.flush()
                if ident is None:
                    return None
                deadline = time.monotonic() + 30
                while True:
                    left = deadline - time.monotonic()
                    if left <= 0:
                        raise TimeoutError("MCP 请求超时")
                    line = incoming.get(timeout=left)
                    if line is None:
                        raise RuntimeError("MCP 进程提前退出")
                    response = json.loads(line)
                    if response.get("id") == ident:
                        if "error" in response:
                            raise RuntimeError("MCP 返回协议错误")
                        return response["result"]
            initialized = send("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                              "clientInfo": {"name": "video-studio-registration-check", "version": "1.0"}}, 1)
            send("notifications/initialized", {})
            listed = send("tools/list", {}, 2)
            names = [t["name"] for t in listed.get("tools", [])]
            if status_tool not in names:
                raise RuntimeError("MCP 缺少预期的只读状态工具")
            state = send("tools/call", {"name": status_tool, "arguments": {}}, 3)
            if state.get("isError"):
                raise RuntimeError("MCP 状态工具返回错误")
            results.append({"id": agent, "ok": True, "server": initialized.get("serverInfo"),
                            "tool_count": len(names), "tools": names, "status_ok": True,
                            "detail": "配置、协议握手和只读状态调用通过；已有会话需重新加载"})
        except Exception as ex:
            # Do not echo subprocess output or configuration values (can contain secrets).
            results.append({"id": agent, "ok": False, "error": type(ex).__name__})
        finally:
            if proc:
                try:
                    proc.stdin.close()
                    proc.wait(timeout=4)
                except Exception:
                    proc.terminate()
                    try:
                        proc.wait(timeout=4)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", choices=["all", *CONFIGS])
    parser.add_argument("--verify", choices=["all", *CONFIGS])
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--status-tool", default="video_status")
    opts = parser.parse_args()
    result = install(opts.install) if opts.install else verify(opts.verify, opts.status_tool) if opts.verify else status()
    print(json.dumps(result, ensure_ascii=False, indent=2))