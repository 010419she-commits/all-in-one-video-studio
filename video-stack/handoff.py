r"""Local, narrowly scoped Strata/ComfyUI lifecycle helpers. No action on import."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import urllib.request

import psutil

import studio_config

ROOT = Path(__file__).resolve().parent
STRATA_ROOT = studio_config.strata_root()
STRATA_CONFIG = STRATA_ROOT / "strata-iq3_s.json" if STRATA_ROOT is not None else None
STRATA_SCRIPT = STRATA_ROOT / "serve" / "server.py" if STRATA_ROOT is not None else None
STRATA_PORT = 8080
STRATA_MODEL = "qwen3.8-flash-next-iq3_s"
STRATA_LAUNCH = [str(STRATA_ROOT / ".venv/Scripts/python.exe"), str(STRATA_SCRIPT),
                 "--engine", "strata", "--config", str(STRATA_CONFIG), "--port", str(STRATA_PORT)] if STRATA_ROOT is not None else []
COMFY_DIR = str(studio_config.comfyui_root() or ROOT.parent / "ComfyUI")
COMFY_PY = str(studio_config.backend_python())
COMFY_PORT = 8188
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DETACHED = NO_WINDOW | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
_http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_gpu_lock = threading.Lock()
_gpu_cache = (0.0, (-1, -1))
_health_lock = threading.Lock()
_health_cache = (0.0, False)


def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def norm(path, cwd=None):
    return os.path.normcase(os.path.realpath(os.path.join(cwd or "", str(path))))


def arg(args, flag):
    for i, value in enumerate(args):
        if value == flag and i + 1 < len(args):
            return args[i + 1]
        if value.startswith(flag + "="):
            return value.split("=", 1)[1]
    return None


def _python_script(proc, script):
    try:
        if proc.name().lower() not in ("python.exe", "pythonw.exe", "python", "pythonw"):
            return False
        args = proc.cmdline()
        cwd = proc.cwd()
        return any(norm(token, cwd) == norm(script) for token in args[1:] if token.endswith(".py"))
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return False


def strata_services():
    if STRATA_SCRIPT is None:
        return []
    found = []
    for proc in psutil.process_iter():
        if not _python_script(proc, STRATA_SCRIPT):
            continue
        try:
            args, cwd = proc.cmdline(), proc.cwd()
            if (arg(args, "--engine") == "strata" and arg(args, "--port") == str(STRATA_PORT)
                    and norm(arg(args, "--config") or "", cwd) == norm(STRATA_CONFIG)):
                found.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return found


def strata_process_objects():
    """Only the configured service or exact configured engine/vision binaries and model arguments."""
    services = strata_services()
    hits = {p.pid: p for p in services}
    if STRATA_CONFIG is None:
        return list(hits.values())
    try:
        cfg = json.loads(STRATA_CONFIG.read_text(encoding="utf-8-sig"))
        engine_exe = norm(cfg["exe"], cfg.get("cwd", str(STRATA_ROOT)))
        engine_args = cfg.get("args", [])
        expected_pack, expected_native = arg(engine_args, "--pack"), arg(engine_args, "--native")
        vision = cfg.get("vision") or {}
        vision_exe = norm(vision.get("exe", ""), str(STRATA_ROOT))
    except (OSError, ValueError, KeyError):
        return list(hits.values())
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = (proc.info["name"] or "").lower()
            if name not in ("strata.exe", "strata-vision.exe"):
                continue
            args, exe, cwd = proc.cmdline(), norm(proc.exe()), proc.cwd()
            engine_match = (name == "strata.exe" and exe == engine_exe and expected_pack and expected_native
                            and norm(arg(args, "--pack") or "", cwd) == norm(expected_pack, str(STRATA_ROOT))
                            and norm(arg(args, "--native") or "", cwd) == norm(expected_native, str(STRATA_ROOT)))
            vision_match = (name == "strata-vision.exe" and exe == vision_exe and vision.get("model") and vision.get("mmproj")
                            and norm(arg(args, "--model") or "", cwd) == norm(vision["model"], str(STRATA_ROOT))
                            and norm(arg(args, "--mmproj") or "", cwd) == norm(vision["mmproj"], str(STRATA_ROOT)))
            if engine_match or vision_match:
                hits[proc.pid] = proc
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            pass
    return list(hits.values())


def strata_procs():
    hits = []
    for proc in strata_process_objects():
        try:
            hits.append((proc.pid, proc.name()))
        except psutil.NoSuchProcess:
            pass
    return hits


def comfy_procs():
    return [p for p in psutil.process_iter() if _python_script(p, Path(COMFY_DIR) / "main.py")
            and _comfy_port_matches(p)]


def _comfy_port_matches(proc):
    try:
        return arg(proc.cmdline(), "--port") in (None, str(COMFY_PORT))
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def gpu_memory(force=False):
    global _gpu_cache
    with _gpu_lock:
        now = time.monotonic()
        if not force and now - _gpu_cache[0] < 2:
            return _gpu_cache[1]
        try:
            result = subprocess.run(
                ["nvidia-smi", "--id=0", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=8, creationflags=NO_WINDOW, check=True)
            used, total = [int(x.strip()) for x in result.stdout.strip().splitlines()[0].split(",")]
            value = (used, total)
        except (OSError, ValueError, subprocess.SubprocessError, IndexError):
            value = (-1, -1)
        _gpu_cache = (now, value)
        return value


def fb_used_mib():
    return gpu_memory()[0]


def fb_total_mib():
    return gpu_memory()[1]


def port_open(port):
    with socket.socket() as sock:
        sock.settimeout(.7)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def wait_port_closed(port, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not port_open(port):
            return True
        time.sleep(.5)
    return False


def strata_healthy(force=False):
    global _health_cache
    with _health_lock:
        now = time.monotonic()
        if not force and now - _health_cache[0] < 2:
            return _health_cache[1]
        healthy = False
        try:
            with _http.open("http://127.0.0.1:%d/health" % STRATA_PORT, timeout=2) as response:
                data = json.load(response)
            healthy = (data.get("service") == "strata" and data.get("model") == STRATA_MODEL
                       and data.get("status") == "ok" and data.get("loaded") is True)
        except (OSError, ValueError):
            pass
        _health_cache = (now, healthy)
        return healthy


def wait_vram_released(timeout=180, maximum_used_mib=4000):
    """Fail closed; desktop graphics need not drop to zero. Require two readings."""
    deadline, stable = time.monotonic() + timeout, 0
    while time.monotonic() < deadline:
        used, _ = gpu_memory(force=True)
        stable = stable + 1 if 0 <= used < maximum_used_mib else 0
        if stable >= 2:
            return
        time.sleep(2)
    raise RuntimeError("显存未释放到安全范围，已阻止启动另一模型")


def _kill_scoped(roots):
    """Snapshot owned descendants, retain psutil identity checks against PID reuse."""
    targets = {}
    for root in roots:
        try:
            for child in root.children(recursive=True):
                targets[child.pid] = child
            targets[root.pid] = root
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    for root in roots:
        try:
            root.kill()  # Stop service owner before any engine can be respawned.
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(list(targets.values()), timeout=5)
    for proc in alive:
        try:
            proc.kill()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(alive, timeout=30)
    if alive:
        raise RuntimeError("目标服务进程未退出，已停止交接")


def kill_tree(pid):
    """Compatibility entry point: refuse a PID outside the two managed services."""
    permitted = strata_services() + comfy_procs()
    match = next((p for p in permitted if p.pid == pid), None)
    if match is None:
        raise RuntimeError("拒绝停止不属于目标服务的进程")
    _kill_scoped([match])


def stop_strata(wait_memory=True):
    services = strata_services()
    if services:
        _kill_scoped(services)
    leftovers = strata_process_objects()
    if leftovers:
        _kill_scoped(leftovers)
    deadline = time.monotonic() + 30
    while strata_procs() and time.monotonic() < deadline:
        time.sleep(.5)
    if strata_procs() or not wait_port_closed(STRATA_PORT, 30):
        raise RuntimeError("Strata 尚未完全停止，已取消交接")
    if wait_memory:
        wait_vram_released()
    log("Strata 进程已停止")


def stop_all_comfy(wait_memory=True):
    targets = comfy_procs()
    if targets:
        _kill_scoped(targets)
    if comfy_procs() or not wait_port_closed(COMFY_PORT, 45):
        raise RuntimeError("ComfyUI 尚未停止，已取消恢复")
    if wait_memory:
        wait_vram_released()


def _launch(args, cwd, logfile):
    with open(logfile, "ab") as logf:
        return subprocess.Popen(args, cwd=cwd, stdin=subprocess.DEVNULL, stdout=logf, stderr=logf,
                                creationflags=DETACHED, close_fds=True)


def start_strata():
    if STRATA_ROOT is None:
        raise RuntimeError("未配置 VIDEO_STUDIO_STRATA_ROOT，Strata 不可用；ComfyUI 主流程不受影响")
    if strata_services() and strata_healthy(force=True):
        log("Strata 已就绪，无需重启")
        return True
    if strata_procs():
        stop_strata()
    elif port_open(STRATA_PORT):
        raise RuntimeError("8080 被其他服务占用，未启动 Strata")
    else:
        wait_vram_released()
    proc = _launch(STRATA_LAUNCH, str(STRATA_ROOT), str(STRATA_ROOT / "strata-iq3_s.restart.log"))
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        if strata_healthy(force=True):
            log("Strata 模型已加载，可回到 Qwen 验收")
            return True
        if proc.poll() is not None and not strata_services():
            raise RuntimeError("Strata 启动失败，请查看 restart.log")
        time.sleep(2)
    raise RuntimeError("Strata 加载超时，可再次点击恢复")


def comfy_compute_path(processes=None):
    """Infer the effective Triton option from verified managed process argv."""
    paths = set()
    for proc in comfy_procs() if processes is None else processes:
        try:
            command = proc.cmdline()
            if not isinstance(command, (list, tuple)) or not command:
                return None
            paths.add("hd" if "--enable-triton-backend" in command and
                      "--disable-triton-backend" not in command else "fast")
        except (psutil.Error, OSError, AttributeError, TypeError):
            return None
    return next(iter(paths)) if len(paths) == 1 else None


def comfy_command(compute_path="hd"):
    from workflow_builder import validate_compute_path
    compute_path = validate_compute_path(compute_path)
    command = [COMFY_PY, "-u", str(Path(COMFY_DIR) / "main.py"), "--listen", "127.0.0.1",
               "--port", str(COMFY_PORT), "--fast", "autotune", "cublas_ops"]
    command.append("--enable-triton-backend" if compute_path == "hd" else "--disable-triton-backend")
    return command


def start_comfy(compute_path="hd"):
    command = comfy_command(compute_path)
    if comfy_procs() or port_open(COMFY_PORT):
        raise RuntimeError("ComfyUI 已在运行，请先恢复或完成当前任务")
    wait_vram_released()
    proc = _launch(command,
                   COMFY_DIR, os.path.join(COMFY_DIR, "comfyui.handoff.log"))
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        if port_open(COMFY_PORT):
            try:
                with _http.open("http://127.0.0.1:%d/system_stats" % COMFY_PORT, timeout=3) as response:
                    data = json.load(response)
                if isinstance(data.get("system"), dict) and isinstance(data.get("devices"), list):
                    return proc
            except (OSError, ValueError):
                pass
        if proc.poll() is not None and not comfy_procs():
            raise RuntimeError("ComfyUI 启动失败，请查看 handoff.log")
        time.sleep(2)
    raise RuntimeError("ComfyUI 启动超时，可点击恢复 Strata")


def stop_comfy(proc=None):
    stop_all_comfy()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--restore", action="store_true")
    args = ap.parse_args()
    if args.restore:
        if not (strata_services() and strata_healthy(force=True) and not comfy_procs()):
            stop_all_comfy()
        return 0 if start_strata() else 1
    print("请使用独立 GPU 面板 http://127.0.0.1:8899；跑批前会检查工作流与模型文件。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
