r"""Independent local GPU handoff controller. Import/start only observes services."""
import importlib.util
import copy
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import uuid
import urllib.request
from urllib.parse import urlsplit, quote
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import handoff
from progress_tracker import ComfyProgressListener, PromptProgress, idle_progress
from execution_errors import summarize_execution_error
from workflow_builder import graph_compute_path, validate_compute_path
from local_http_server import LocalHttpServer

PORT = 8899
ROOT = Path(__file__).resolve().parent
WORKFLOW_DIR = str(ROOT / "workflows")
LOG = str(ROOT / "panel.log")
OBJECT_INFO = ROOT / "object_info.json"
MODEL_ROOT = Path(handoff.COMFY_DIR) / "models"
OUTPUT_ROOT = Path(handoff.COMFY_DIR) / "output"
_lock = threading.Lock()
_preflight_lock = threading.Lock()
_preflight_cache = (0.0, None)
_comfy_probe_lock = threading.Lock()
_comfy_probe_cache = (0.0, None)
_comfy_probe_ttl = 1.5
_state = {"phase": "idle", "detail": "", "busy": False, "outputs": [], "log": [], "tasks": [], "batch_id": "",
          "paused": False, "cancel_requested": False, "progress": idle_progress()}
_progress_tracker = None
_progress_token = ""
_http = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def note(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    with _lock:
        _state["log"] = (_state["log"] + [line])[-200:]
    try:
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def set_phase(phase, detail=""):
    global _progress_tracker, _progress_token
    with _lock:
        _state["phase"], _state["detail"] = phase, str(detail)[:240]
        labels = {"queued": "任务已接收，等待 GPU 交接", "checking_comfy": "正在检查视频引擎",
                  "stopping_strata": "正在释放大模型显存", "starting_comfy": "正在启动视频引擎",
                  "restoring": "正在恢复大模型", "failed": "视频任务失败", "idle": "等待生成任务"}
        if phase in labels:
            _progress_tracker, _progress_token = None, uuid.uuid4().hex
            previous = _state.get("progress", idle_progress())
            active = phase not in ("failed", "idle")
            _state["progress"] = {**idle_progress(), "stage": phase, "label": labels[phase],
                                  "active": active, "indeterminate": active,
                                  "task_count": previous.get("task_count", 0),
                                  "task_index": previous.get("task_index", 0) if phase == "failed" else 0,
                                  "elapsed_seconds": previous.get("elapsed_seconds", 0.0) if phase == "failed" else 0.0}


def _begin_progress(graph, task_index, task_count):
    global _progress_tracker, _progress_token
    token = uuid.uuid4().hex
    def publish(progress):
        with _lock:
            if _progress_token == token:
                _state["progress"] = progress
    tracker = PromptProgress(graph, task_index, task_count, publish=publish)
    with _lock:
        _progress_token, _progress_tracker = token, tracker
        _state["progress"] = tracker.snapshot()
    return tracker


def comfy_api(path, payload=None, timeout=15):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (handoff.COMFY_PORT, path),
                                 data=data, headers={"Content-Type": "application/json"})
    with _http.open(req, timeout=timeout) as resp:
        return json.load(resp)


def _managed_comfy_listener(processes):
    """The verified main.py process must own the target listening socket."""
    for process in processes:
        try:
            connections = getattr(process, "net_connections", None) or process.connections
            for connection in connections(kind="tcp"):
                if connection.status != handoff.psutil.CONN_LISTEN or not connection.laddr:
                    continue
                port = getattr(connection.laddr, "port", None)
                if port is None:
                    port = connection.laddr[1]
                if port == handoff.COMFY_PORT:
                    return True
        except (handoff.psutil.Error, OSError, AttributeError):
            continue
    return False


def _probe_comfy_runtime():
    """Observe ownership, HTTP identity and queue only; never starts/stops/submits."""
    result = {"comfy_running": True, "comfy_idle": False,
              "warm_handoff_ready": False, "accept_detail": "视频引擎状态未知，暂不能提交"}
    try:
        processes = handoff.comfy_procs()
        listening = handoff.port_open(handoff.COMFY_PORT)
        result["comfy_running"] = bool(processes) or listening
        if not result["comfy_running"]:
            result["accept_detail"] = "可准备并交接新任务"
            return result
        if not processes:
            result["accept_detail"] = "视频端口由未受管服务占用，暂不能复用"
            return result
        if not listening:
            result["accept_detail"] = "视频引擎正在启动或尚未就绪"
            return result
        if not _managed_comfy_listener(processes):
            result["accept_detail"] = "无法确认视频端口属于受管引擎，暂不能复用"
            return result
        stats = comfy_api("/system_stats", timeout=2)
        if not (isinstance(stats, dict) and isinstance(stats.get("system"), dict)
                and isinstance(stats.get("devices"), list)):
            result["accept_detail"] = "视频引擎身份响应无效，暂不能复用"
            return result
        queue = comfy_api("/queue", timeout=2)
        if not (isinstance(queue, dict) and isinstance(queue.get("queue_running"), list)
                and isinstance(queue.get("queue_pending"), list)):
            result["accept_detail"] = "无法确认视频队列状态，暂不能提交"
            return result
        if queue["queue_running"] or queue["queue_pending"]:
            result["accept_detail"] = "视频引擎仍有运行或排队任务，请等待完成"
            return result
        result.update(comfy_idle=True, warm_handoff_ready=True,
                      accept_detail="视频引擎已就绪且队列空闲，可继续出片")
        return result
    except Exception:
        # Never expose an HTTP body, external error text, or task content here.
        result["comfy_idle"] = result["warm_handoff_ready"] = False
        result["accept_detail"] = "视频引擎或队列暂时无法确认，请稍后重试"
        return result


def comfy_runtime_state(force=False):
    global _comfy_probe_cache
    with _comfy_probe_lock:
        now = time.monotonic()
        if not force and _comfy_probe_cache[1] is not None and now - _comfy_probe_cache[0] < _comfy_probe_ttl:
            return dict(_comfy_probe_cache[1])
        result = _probe_comfy_runtime()
        _comfy_probe_cache = (time.monotonic(), dict(result))
        return result


def _batch_acceptance(runtime, own_busy):
    return {**runtime,
            "can_accept_batch": bool(not own_busy and
                                     (not runtime["comfy_running"] or runtime["warm_handoff_ready"])),
            "accept_detail": "交接或视频任务正在进行，请等待完成" if own_busy else runtime["accept_detail"]}


def _contained(root, value):
    root = root.resolve()
    path = (root / value).resolve()
    if path == root or root not in path.parents:
        raise ValueError("文件路径超出允许目录")
    return path


def _complete_file(path):
    if not path.is_file() or path.stat().st_size <= 0:
        return False
    lower = path.name.lower()
    if lower.endswith((".part", ".parts", ".partial", ".incomplete", ".tmp", ".download", ".downloading")):
        return False
    return not any(Path(str(path) + suffix).exists() for suffix in
                   (".part", ".parts", ".partial", ".incomplete", ".tmp", ".download", ".downloading"))


def _file_reference(class_name, key, value):
    if not isinstance(value, str):
        return
    low = value.lower()
    media = key.lower() in ("image", "video", "audio", "image_path", "video_path", "audio_path")
    if media and ("load" in class_name.lower() or key.lower().endswith("_path")):
        folder = "input"
        for tag in (" [input]", " [output]", " [temp]"):
            if value.endswith(tag):
                folder, value = tag[2:-1], value[:-len(tag)]
                break
        candidate = _contained(Path(handoff.COMFY_DIR) / folder, value)
        if not _complete_file(candidate):
            raise ValueError("输入素材不存在或未完成：" + candidate.name)
        return
    extensions = (".safetensors", ".gguf", ".ckpt", ".pt", ".pth", ".bin", ".onnx", ".pkl")
    if not low.endswith(extensions):
        if low.endswith((".part", ".parts", ".incomplete", ".partial", ".downloading")):
            raise ValueError("工作流引用了下载临时文件")
        return
    folders = {
        "unet_name": ("diffusion_models", "unet"), "ckpt_name": ("checkpoints",),
        "vae_name": ("vae", "vae_approx"), "clip_name": ("text_encoders", "clip"),
        "clip_name1": ("text_encoders", "clip"), "clip_name2": ("text_encoders", "clip"),
        "clip_name3": ("text_encoders", "clip"), "lora_name": ("loras",),
        "control_net_name": ("controlnet",), "clip_vision_name": ("clip_vision",),
        "upscale_model": ("upscale_models", "latent_upscale_models"),
        "model_name": ("diffusion_models", "unet", "checkpoints", "latent_upscale_models", "upscale_models"),
    }
    search_dirs = folders.get(key)
    if class_name == "CLIPVisionLoader" and key == "clip_name":
        search_dirs = ("clip_vision",)
    if search_dirs:
        candidates = [_contained(MODEL_ROOT / folder, value) for folder in search_dirs]
    else:
        # Unknown loader: resolve a relative model name against local model categories,
        # never against the download staging tree or an arbitrary absolute path.
        candidates = [_contained(directory, value) for directory in MODEL_ROOT.iterdir()
                      if directory.is_dir() and not directory.name.startswith(".")]
    if not any(_complete_file(p) for p in candidates):
        raise ValueError("模型未下载完成或不存在：" + Path(value).name)


def _mp4_output(node):
    kind, inputs = node["class_type"], node.get("inputs", {})
    if kind not in ("SaveVideo", "VHS_VideoCombine"):
        return False
    fmt = inputs.get("format")
    if isinstance(fmt, dict):
        fmt = fmt.get("key") or fmt.get("value") or fmt.get("format")
    fmt = str(fmt or "").lower()
    if kind == "SaveVideo":
        return fmt == "mp4"
    return inputs.get("save_output", True) is True and "mp4" in fmt


def _image_output(node):
    return node["class_type"] == "SaveImage"


def preflight(force=False, workflow_path=None, workflow_paths=None, expected_hashes=None):
    global _preflight_cache
    with _preflight_lock:
        now = time.monotonic()
        scoped = workflow_path is not None or workflow_paths is not None or expected_hashes is not None
        if not scoped and not force and _preflight_cache[1] is not None and now - _preflight_cache[0] < 2:
            return _preflight_cache[1]
        if workflow_paths is not None or workflow_path is not None:
            try:
                if workflow_paths is not None and workflow_path is not None:
                    raise ValueError("不能同时指定单个任务和任务列表")
                selected_paths = workflow_paths if workflow_paths is not None else [workflow_path]
                if not isinstance(selected_paths, list) or not 1 <= len(selected_paths) <= 12:
                    raise ValueError("每批请选择 1 至 12 个任务")
                paths = []
                for value in selected_paths:
                    if not isinstance(value, str) or not value:
                        raise ValueError("任务路径无效")
                    selected = Path(value).resolve()
                    if selected.parent != Path(WORKFLOW_DIR).resolve() or not selected.name.endswith(".api.json") or selected in paths:
                        raise ValueError("任务路径无效或重复")
                    paths.append(selected)
            except (ValueError, TypeError, OSError):
                return {"ok": False, "workflow_count": 0, "detail": "请选择 1 至 12 个不同的工作流目录内任务，且不能同时指定单个任务和列表", "plans": []}
        else:
            paths = sorted(Path(WORKFLOW_DIR).glob("*.api.json"))
        result = {"ok": False, "workflow_count": len(paths), "detail": "", "plans": []}
        try:
            if not paths:
                raise ValueError("尚无 *.api.json 工作流；Strata 将保持运行")
            if len(paths) > 12:
                raise ValueError("每批最多 12 个工作流，请选择本次任务")
            if not OBJECT_INFO.is_file():
                raise ValueError("缺少本地节点清单 object_info.json")
            if importlib.util.find_spec("av") is None:
                raise ValueError("缺少视频核验组件 PyAV，暂不能安全跑批")
            info = json.loads(OBJECT_INFO.read_text(encoding="utf-8-sig"))
            if not isinstance(info, dict):
                raise ValueError("本地节点清单格式无效")
            for path in paths:
                try:
                    raw = path.read_bytes()
                    digest = hashlib.sha256(raw).hexdigest()
                    graph = json.loads(raw.decode("utf-8-sig"))
                except (ValueError, OSError):
                    raise ValueError(path.name + " 不是完整有效的 JSON")
                if expected_hashes is not None and (not isinstance(expected_hashes, dict) or expected_hashes.get(str(path)) != digest):
                    raise ValueError("工作流已更改，请重新准备：" + path.name)
                if not isinstance(graph, dict) or not graph:
                    raise ValueError(path.name + " 不是 API 节点图")
                video_nodes = []
                image_nodes = []
                for node_id, node in graph.items():
                    if not isinstance(node, dict) or not isinstance(node.get("inputs"), dict):
                        raise ValueError(path.name + " 包含无效节点")
                    kind = node.get("class_type")
                    if kind not in info:
                        raise ValueError("本地缺少节点：" + str(kind))
                    required = info[kind].get("input", {}).get("required", {})
                    for field, spec in required.items():
                        if field not in node["inputs"]:
                            # Dynamic inputs may be flattened as field.subfield, but
                            # the selector itself is always required in the API graph.
                            raise ValueError(kind + " 缺少输入：" + field)
                    for key, value in node["inputs"].items():
                        if isinstance(value, list) and len(value) == 2 and isinstance(value[1], int):
                            source = graph.get(str(value[0]))
                            if source is None or value[1] < 0:
                                raise ValueError(kind + " 包含无效节点连线")
                            if isinstance(source, dict) and source.get("class_type") in info:
                                count = len(info[source["class_type"]].get("output", []))
                                if value[1] >= count:
                                    raise ValueError(kind + " 连线端口不存在")
                        else:
                            _file_reference(kind, key, value)
                    if _mp4_output(node):
                        video_nodes.append(str(node_id))
                        prefix = str(node["inputs"].get("filename_prefix", "video/ComfyUI"))
                        _contained(OUTPUT_ROOT, prefix)
                    if _image_output(node):
                        image_nodes.append(str(node_id))
                        prefix = str(node["inputs"].get("filename_prefix", "studio/img/ComfyUI"))
                        _contained(OUTPUT_ROOT, prefix)
                visited, visiting = set(), set()
                def visit(node_id):
                    if node_id in visiting:
                        raise ValueError(path.name + " 存在循环连线")
                    if node_id in visited:
                        return
                    visiting.add(node_id)
                    for value in graph[node_id]["inputs"].values():
                        if isinstance(value, list) and len(value) == 2 and isinstance(value[1], int):
                            visit(str(value[0]))
                    visiting.remove(node_id)
                    visited.add(node_id)
                for node_id in graph:
                    visit(node_id)
                if not video_nodes and not image_nodes:
                    raise ValueError(path.name + " 缺少明确保存 MP4 的 SaveVideo / VHS_VideoCombine 节点或 SaveImage 节点")
                # Freeze the exact checked graph. Another editor can keep preparing
                # later workflow files without changing a batch already accepted.
                result["plans"].append({"name": path.name, "workflow_path": str(path), "sha256": digest,
                                        "title": path.stem, "graph": graph, "video_nodes": video_nodes,
                                        "image_nodes": image_nodes, "compute_path": graph_compute_path(graph)})
            result["ok"] = True
            result["detail"] = "%d 个工作流已就绪；所需模型和素材已存在" % len(paths)
        except (ValueError, OSError, TypeError, KeyError) as exc:
            result["detail"] = str(exc)[:240]
            result["plans"] = []
        if not scoped:
            _preflight_cache = (now, result)
        return result


def validate_image(item):
    """Verify a completed image file inside the output root; returns its path."""
    subfolder = str(item.get("subfolder", ""))
    filename = str(item.get("filename", ""))
    if not filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
        return None
    path = _contained(OUTPUT_ROOT, str(Path(subfolder) / filename))
    if not _complete_file(path):
        raise RuntimeError("图片文件尚未完整写入：" + path.name)
    try:
        with path.open("rb") as stream:
            signature = stream.read(8)
        if not (signature.startswith(b"\x89PNG\r\n\x1a\n") or signature.startswith(b"\xff\xd8") or signature[:4] == b"RIFF"):
            raise ValueError("bad signature")
    except Exception:
        raise RuntimeError("图片核验失败：" + path.name)
    return str(path)


def validate_mp4(item):
    import av
    if not isinstance(item, dict) or item.get("type", "output") != "output":
        return None
    filename = item.get("filename")
    if not isinstance(filename, str) or not filename.lower().endswith(".mp4"):
        return None
    subfolder = item.get("subfolder", "")
    if not isinstance(subfolder, str):
        raise RuntimeError("视频输出子目录无效")
    path = _contained(OUTPUT_ROOT, str(Path(subfolder) / filename))
    if not _complete_file(path):
        raise RuntimeError("视频文件尚未完整写入：" + path.name)
    try:
        with av.open(str(path)) as container:
            if not container.streams.video:
                raise ValueError("no video stream")
            stream = container.streams.video[0]
            duration = float(stream.duration * stream.time_base) if stream.duration is not None else (container.duration or 0) / av.time_base
            if duration <= 0:
                raise ValueError("no video duration")
            frame = next(container.decode(video=0), None)
            if frame is None or frame.width <= 0 or frame.height <= 0:
                raise ValueError("no decodable frame")
    except Exception:
        raise RuntimeError("MP4 视频核验失败：" + path.name)
    return str(path)


def set_task_phase(plan, phase, outputs=None, detail=""):
    path = plan.get("workflow_path", plan["name"])
    with _lock:
        task = next((t for t in _state.get("tasks", []) if t["workflow_path"] == path), None)
        if task is not None:
            task["phase"], task["detail"] = phase, str(detail)[:240]
            if outputs is not None:
                task["outputs"] = list(outputs)


def fail_unfinished_tasks(detail):
    with _lock:
        tasks = _state.get("tasks", [])
        failed = any(t["phase"] == "running" for t in tasks)
        for task in tasks:
            if task["phase"] == "running":
                task["phase"], task["detail"] = "failed", str(detail)[:240]
            elif task["phase"] == "queued":
                task["phase"] = "skipped" if failed else "failed"
                task["detail"] = "前序任务或交接失败，本任务未执行"
                failed = True


class BatchCancelled(Exception):
    pass


def cancel_unfinished_tasks(detail):
    with _lock:
        for task in _state.get("tasks", []):
            if task["phase"] == "running":
                task["phase"], task["detail"] = "cancelled", str(detail)[:240]
            elif task["phase"] == "queued":
                task["phase"], task["detail"] = "skipped", str(detail)[:240]


def _check_control():
    """Pause holds at batch boundaries; cancel raises out of the batch runner."""
    with _lock:
        if _cancel_ready_locked():
            raise BatchCancelled()
        paused = _state.get("paused")
    while paused:
        time.sleep(1)
        with _lock:
            if _cancel_ready_locked():
                raise BatchCancelled()
            paused = _state.get("paused")


def do_pause():
    with _lock:
        if not _state["busy"]:
            raise RuntimeError("当前没有正在运行的任务")
        _state["paused"] = not _state.get("paused", False)
        paused = _state["paused"]
    if paused:
        set_phase("paused", "已暂停：当前成片完成后不再继续下一任务，点继续恢复")
    else:
        set_phase(_state["phase"] if _state["phase"] != "paused" else "generating", "已继续")
    return {"ok": True, "paused": paused}


def do_cancel():
    with _lock:
        if not _state["busy"]:
            raise RuntimeError("当前没有正在运行的任务")
        _state["cancel_requested"] = True
        _state["cancel_requires_confirmation"] = False
        _state["paused"] = False
    try:
        comfy_api("/interrupt", {})
    except (OSError, ValueError):
        pass
    return {"ok": True}


def _cancel_ready_locked():
    """Conditional cancellation cannot release ownership before exact cancel."""
    if not _state.get("cancel_requested"):
        return False
    if not _state.get("cancel_requires_confirmation") or _state.get("cancel_confirmed"):
        return True
    active_prompt = any(task.get("phase") == "running" and task.get("prompt_submission_started")
                        for task in _state.get("tasks", []))
    return not active_prompt


def _cancel_exact_prompt(prompt_id):
    # This installed ComfyUI endpoint checks/interrupts its own running set
    # atomically under its queue mutex. Never fall back to global /interrupt.
    try:
        canonical = str(uuid.UUID(prompt_id))
    except (ValueError, TypeError, AttributeError):
        raise RuntimeError("视频执行编号无效，未发送中断")
    answer = comfy_api("/api/jobs/" + quote(canonical, safe="") + "/cancel", {}, timeout=5)
    return isinstance(answer, dict) and answer.get("cancelled") is True


def _retry_conditional_cancel():
    with _lock:
        if not (_state.get("cancel_requested") and _state.get("cancel_requires_confirmation")) or _state.get("cancel_confirmed"):
            return False
        batch_id = _state.get("batch_id")
        task = next((task for task in _state.get("tasks", []) if task.get("cancel_requested") and task.get("prompt_id")), None)
        prompt_id = task.get("prompt_id") if task else None
    if not prompt_id:
        return False
    try:
        confirmed = _cancel_exact_prompt(prompt_id)
    except (OSError, ValueError, RuntimeError):
        return False
    with _lock:
        # An old response must not change a newer batch's cancellation state.
        if _state.get("batch_id") == batch_id and task in _state.get("tasks", []):
            if confirmed:
                _state["cancel_confirmed"] = True
            return confirmed
    return False


def do_cancel_task(body):
    """Compare ownership and record cancellation atomically, for one-job batches.

    The task center hands off exactly one workflow. Reject a legacy multi-shot
    batch rather than cancelling other shots that happen to share its batch.
    """
    if not isinstance(body, dict) or set(body) != {"batch_id", "workflow_path", "workflow_sha256"}:
        raise RuntimeError("定向取消需要批次、工作流和校验值")
    batch_id, path, digest = body["batch_id"], body["workflow_path"], body["workflow_sha256"]
    if not all(isinstance(value, str) and value for value in (batch_id, path, digest)):
        raise RuntimeError("定向取消身份无效")
    with _lock:
        tasks = _state.get("tasks", [])
        if _state.get("batch_id") != batch_id or len(tasks) != 1:
            raise RuntimeError("批次身份已改变或并非独立任务；未取消其他任务")
        task = tasks[0]
        if (os.path.normcase(os.path.abspath(task.get("workflow_path", ""))) != os.path.normcase(os.path.abspath(path))
                or task.get("sha256") != digest or task.get("batch_id") != batch_id):
            raise RuntimeError("工作流身份不匹配；未取消其他任务")
        if task.get("phase") in ("done", "cancelled", "skipped"):
            return {"ok": True, "phase": task["phase"], "cancel_requested": False}
        task["cancel_requested"] = True
        _state["cancel_requested"] = True
        _state["cancel_requires_confirmation"] = True
        _state["paused"] = False
    confirmed = _retry_conditional_cancel()
    return {"ok": True, "phase": "cancel_requested", "cancel_requested": True,
            "cancel_confirmed": confirmed}


def _assert_no_strata():
    if handoff.strata_procs() or handoff.strata_services() or handoff.port_open(handoff.STRATA_PORT):
        raise RuntimeError("Strata 仍在运行，不能切换或提交视频计算引擎")


def _ensure_compute_path(compute_path):
    """Only called at batch boundaries, before opening/submitting a new prompt."""
    compute_path = validate_compute_path(compute_path)
    runtime = comfy_runtime_state(force=True)
    if not runtime.get("warm_handoff_ready"):
        raise RuntimeError(runtime.get("accept_detail") or "视频引擎队列不空闲，不能切换计算引擎")
    _assert_no_strata()
    current = handoff.comfy_compute_path()
    if current is None:
        raise RuntimeError("无法确认当前视频计算引擎，未重启或提交任务")
    if current == compute_path:
        return False
    # Probe again immediately before stopping: an external ComfyUI job or a
    # Strata restart since admission must prevent switching the managed engine.
    _check_control()
    runtime = comfy_runtime_state(force=True)
    if not runtime.get("warm_handoff_ready"):
        raise RuntimeError(runtime.get("accept_detail") or "视频队列状态已变化，未切换计算引擎")
    _assert_no_strata()
    set_phase("starting_comfy", "正在切换至" + ("高清省显存引擎" if compute_path == "hd" else "快速计算引擎"))
    handoff.stop_all_comfy()
    _check_control()
    _assert_no_strata()
    handoff.start_comfy(compute_path=compute_path)
    runtime = comfy_runtime_state(force=True)
    if not runtime.get("warm_handoff_ready") or handoff.comfy_compute_path() != compute_path:
        raise RuntimeError("视频计算引擎尚未就绪，当前任务未提交")
    return True


def run_batch(plans):
    if not plans:
        raise RuntimeError("没有有效跑批工作流")
    for task_index, plan in enumerate(plans, 1):
        _check_control()
        name = plan["name"]
        set_task_phase(plan, "running", detail="正在确认视频计算引擎")
        _ensure_compute_path(graph_compute_path(plan["graph"]))
        _check_control()
        set_task_phase(plan, "running", detail="正在提交 ComfyUI")
        set_phase("queueing", name)
        tracker = _begin_progress(plan["graph"], task_index, len(plans))
        client_id = uuid.uuid4().hex
        listener = ComfyProgressListener(handoff.COMFY_PORT, client_id, tracker)
        try:
            # Open before submission so even cached/fast nodes cannot race the
            # observer. Events arriving before the response are buffered by ID.
            listener.start()
            # Keep an identity even when the POST response is lost. This local
            # ComfyUI supports caller-provided UUID prompt IDs.
            intended_prompt_id = str(uuid.uuid4())
            with _lock:
                if _state.get("cancel_requested"):
                    raise BatchCancelled()
                active = next((task for task in _state.get("tasks", [])
                               if task.get("workflow_path") == plan.get("workflow_path", plan["name"])), None)
                if active is not None:
                    active.update(prompt_id=intended_prompt_id, prompt_submission_started=True)
            answer = comfy_api("/prompt", {"prompt": plan["graph"], "client_id": client_id,
                                            "prompt_id": intended_prompt_id})
            prompt_id = answer.get("prompt_id")
            if not prompt_id or answer.get("node_errors"):
                raise RuntimeError("ComfyUI 拒绝工作流：" + name)
            with _lock:
                if active is not None:
                    active["prompt_id"] = str(prompt_id)
            _retry_conditional_cancel()
            _check_control()
            tracker.bind(str(prompt_id))
            note("已排队：" + name + "，任务 " + str(prompt_id)[:12])
            set_phase("generating", name + "，等待视频完成")
            set_task_phase(plan, "running", detail="ComfyUI 正在生成，等待 MP4 核验")
            deadline = time.monotonic() + 3600
            while time.monotonic() < deadline:
                _retry_conditional_cancel()
                _check_control()
                if not handoff.comfy_procs() or not handoff.port_open(handoff.COMFY_PORT):
                    raise RuntimeError("ComfyUI 已退出，请检查视频引擎状态")
                try:
                    entry = comfy_api("/history/" + str(prompt_id)).get(prompt_id)
                except (OSError, ValueError):
                    time.sleep(3)
                    continue
                if not entry:
                    time.sleep(3)
                    continue
                state = entry.get("status") or {}
                if state.get("status_str") == "error":
                    raise RuntimeError(summarize_execution_error(state, name))
                if not state.get("completed"):
                    time.sleep(3)
                    continue
                tracker.verifying()
                found = []
                targets = [(node_id, validate_mp4) for node_id in plan["video_nodes"]] + \
                          [(node_id, validate_image) for node_id in plan.get("image_nodes", [])]
                for node_id, validator in targets:
                    out = (entry.get("outputs") or {}).get(node_id) or {}
                    for key in ("images", "gifs", "videos"):
                        for item in out.get(key) or []:
                            path = validator(item)
                            if path and path not in found:
                                found.append(path)
                if not found:
                    raise RuntimeError("任务结束但没有核验通过的产物：" + name)
                with _lock:
                    for path in found:
                        if path not in _state["outputs"]:
                            _state["outputs"].append(path)
                note("已核验 %d 个 MP4：" % len(found) + name)
                set_task_phase(plan, "done", outputs=found, detail="MP4 已核验")
                tracker.finish(True)
                break
            else:
                raise RuntimeError("生成超过一小时，请检查 ComfyUI 与本次任务状态")
        except Exception:
            tracker.finish(False)
            raise
        finally:
            listener.close()
    with _lock:
        count = len(_state["outputs"])
    set_phase("batch_done", "%d 个视频已完成并通过核验" % count)


def do_handoff(plans, warm=False):
    try:
        _check_control()
        if not plans:
            raise RuntimeError("没有有效跑批工作流；Strata 保持运行")
        compute_paths = [graph_compute_path(plan["graph"]) for plan in plans]
        runtime = comfy_runtime_state(force=True)
        if warm:
            if not runtime["warm_handoff_ready"]:
                raise RuntimeError(runtime["accept_detail"])
            set_phase("checking_comfy", "正在复核视频引擎与空闲队列")
            if handoff.strata_procs() or handoff.strata_services():
                _check_control()
                set_phase("stopping_strata", "正在释放 Strata，保留已就绪的视频引擎")
                # ComfyUI may retain model weights: a whole-card <4 GiB wait
                # would block warm reuse even after all Strata processes exit.
                handoff.stop_strata(wait_memory=False)
            if handoff.strata_procs() or handoff.strata_services() or handoff.port_open(handoff.STRATA_PORT):
                raise RuntimeError("Strata 尚未完全退出，已取消继续出片")
            runtime = comfy_runtime_state(force=True)
            if not runtime["warm_handoff_ready"]:
                raise RuntimeError(runtime["accept_detail"])
            if handoff.strata_procs() or handoff.strata_services() or handoff.port_open(handoff.STRATA_PORT):
                raise RuntimeError("Strata 状态已变化，已取消继续出片")
        else:
            if runtime["comfy_running"]:
                raise RuntimeError("视频引擎状态已变化，请检查后重新提交")
            _check_control()
            set_phase("stopping_strata", "正在停止 Strata 并等待显存释放")
            handoff.stop_strata()
            _check_control()
            set_phase("starting_comfy", "正在启动 ComfyUI")
            handoff.start_comfy(compute_path=compute_paths[0])
        run_batch(plans)
    except BatchCancelled:
        note("已按用户请求取消批次")
        cancel_unfinished_tasks("已按用户请求取消")
        set_phase("cancelled", "已取消当前批次；视频引擎保持现状")
    except Exception as exc:
        note("交接失败：" + str(exc)[:200])
        fail_unfinished_tasks(str(exc))
        set_phase("failed", str(exc))
    finally:
        with _lock:
            _state["busy"] = False


def do_restore():
    try:
        set_phase("restoring", "正在恢复 Strata")
        healthy = bool(handoff.strata_services()) and handoff.strata_healthy(force=True)
        if healthy and not handoff.comfy_procs() and not handoff.port_open(handoff.COMFY_PORT):
            set_phase("idle", "Strata 已就绪，可回到 Qwen 验收")
            return
        # Drain both managed stacks before waiting for memory; a stale partial
        # Strata start must not make the ComfyUI memory wait impossible.
        if not healthy and handoff.strata_procs():
            handoff.stop_strata(wait_memory=False)
        handoff.stop_all_comfy(wait_memory=not healthy)
        if handoff.start_strata():
            set_phase("idle", "Strata 已就绪，可回到 Qwen 验收")
    except Exception as exc:
        note("恢复失败：" + str(exc)[:200])
        set_phase("failed", str(exc))
    finally:
        with _lock:
            _state["busy"] = False


def do_unload():
    try:
        set_phase("unloading", "正在卸载视频模型，等待显存释放")
        handoff.stop_all_comfy(wait_memory=True)
        if handoff.comfy_procs() or handoff.port_open(handoff.COMFY_PORT):
            raise RuntimeError("视频引擎尚未完全退出，请稍后重试")
        set_phase("idle", "视频模型已卸载，显存已释放；Strata 保持原状态")
    except Exception as exc:
        note("卸载失败：" + str(exc)[:200])
        set_phase("failed", str(exc))
    finally:
        with _lock:
            _state["busy"] = False


def spawn(fn, clear_outputs=False, plans=None):
    global _progress_tracker, _progress_token
    with _lock:
        if _state["busy"]:
            return False
        _state["busy"] = True
        if clear_outputs:
            _state["outputs"] = []
        if plans is not None:
            _progress_tracker, _progress_token = None, uuid.uuid4().hex
            _state["progress"] = {**idle_progress(), "stage": "queued", "label": "等待 GPU 交接",
                                  "active": True, "indeterminate": True, "task_count": len(plans)}
            _state["batch_id"] = uuid.uuid4().hex
            _state["cancel_requested"] = False
            _state["cancel_requires_confirmation"] = False
            _state["cancel_confirmed"] = False
            _state["paused"] = False
            _state["phase"], _state["detail"] = "queued", "任务已接收，等待 GPU 交接"
            _state["tasks"] = [{"workflow_path": p.get("workflow_path", p["name"]),
                                "title": p.get("title", p["name"]), "sha256": p.get("sha256", ""),
                                "phase": "queued", "outputs": [], "detail": "等待前序任务",
                                "batch_id": _state["batch_id"]} for p in plans]
    try:
        threading.Thread(target=fn, daemon=True).start()
    except Exception:
        fail_unfinished_tasks("后台任务线程未能启动")
        set_phase("failed", "后台任务线程未能启动")
        with _lock:
            _state["busy"] = False
        return False
    return True


def status():
    check = preflight()
    strata = handoff.strata_procs()
    healthy = bool(handoff.strata_services()) and handoff.strata_healthy()
    runtime = comfy_runtime_state()
    comfy = runtime["comfy_running"]
    used, total = handoff.gpu_memory()
    with _lock:
        snapshot = copy.deepcopy(_state)
        tracker = _progress_tracker
    if tracker is not None:
        snapshot["progress"] = tracker.snapshot()
    snapshot["log"] = snapshot["log"][-25:]
    admission = _batch_acceptance(runtime, snapshot["busy"])
    snapshot.update({
        "service": "qwen-gpu-panel", "api_version": 1, "supports_selected_workflow": True, "supports_batch_workflows": True,
        "supports_warm_handoff": True,
        "supports_conditional_cancel": True,
        "supports_compute_paths": True,
        "can_accept_batch": admission["can_accept_batch"],
        "warm_handoff_ready": runtime["warm_handoff_ready"], "comfy_idle": runtime["comfy_idle"],
        "accept_detail": admission["accept_detail"],
        "strata_running": bool(strata), "strata_count": len(strata), "strata_healthy": healthy,
        "comfy_running": comfy, "vram_used_mib": used, "vram_total_mib": total,
        "can_handoff": bool(check["ok"] and admission["can_accept_batch"]),
        "can_restore": bool(not snapshot["busy"] and (not healthy or comfy)),
        "can_unload": bool(not snapshot["busy"] and comfy),
        "preflight_detail": check["detail"], "workflow_count": check["workflow_count"],
    })
    return snapshot



PAGE = """<!doctype html><meta charset=utf-8>
<title>GPU 交接面板</title>
<style>
 body{font:16px/1.6 system-ui,"Microsoft YaHei",sans-serif;margin:0;padding:28px;
      background:#14161a;color:#e8eaed;max-width:900px;margin-inline:auto}
 h1{font-size:20px;margin:0 0 6px}
 .sub{color:#9aa0a6;font-size:13px;margin-bottom:22px}
 .btns{display:flex;gap:16px;flex-wrap:wrap}
 button{flex:1 1 320px;padding:26px 18px;font-size:18px;font-weight:600;border:0;
        border-radius:12px;cursor:pointer;color:#fff}
 button:disabled{opacity:.4;cursor:not-allowed}
 #handoff{background:#b3261e}#restore{background:#1a73e8}#unload{background:#5f6368}
 .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin:24px 0}
 .card{background:#1e2126;border-radius:10px;padding:14px}
 .k{color:#9aa0a6;font-size:12px;text-transform:uppercase;letter-spacing:.05em}
 .v{font-size:22px;font-weight:600;margin-top:4px}
 .phase{background:#1e2126;border-left:4px solid #8ab4f8;padding:12px 16px;border-radius:6px;margin:18px 0}
 pre{background:#0d0f12;padding:14px;border-radius:8px;font-size:12px;overflow-x:auto;
     max-height:340px;white-space:pre-wrap}
 a{color:#8ab4f8}
</style>
<h1>GPU 交接面板</h1>
<div class="sub">32 GB 显存只够一个栈。这个面板不是 Strata 起的，所以 Strata 停了它还在。</div>
<div class="btns">
 <button id="handoff">停 Strata · 交接跑批出 mp4</button>
 <button id="restore">重启 Strata · 回来验收</button>
 <button id="unload">一键卸载视频模型 · 只释放显存</button>
</div>
<div class="phase">状态：<b id="phase"></b> <span id="detail"></span></div>
<div class="grid">
 <div class="card"><div class="k">显存占用</div><div class="v" id="vram"></div></div>
 <div class="card"><div class="k">Strata</div><div class="v" id="strata"></div></div>
 <div class="card"><div class="k">ComfyUI</div><div class="v" id="comfy"></div></div>
 <div class="card"><div class="k">产出</div><div class="v" id="outs"></div></div>
</div>
<div class="sub">生成期间可开 <a href="http://127.0.0.1:8188" target="_blank">ComfyUI 网页</a> 手动调参。mp4 落在 ComfyUI 的 output 目录。</div>
<pre id="log"></pre>
<script>
const $=id=>document.getElementById(id);
$('handoff').onclick=()=>{if(confirm('这会停掉 Strata，Qwen 助手会离线直到你按第二个按钮。继续？'))post('/handoff');};
$('restore').onclick=()=>post('/restore');
$('unload').onclick=()=>{if(confirm('这会停掉 ComfyUI 视频引擎并等待显存释放，不会重启 Strata，也不删除模型文件。继续？'))post('/unload');};
async function post(u){const r=await fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});if(!r.ok)alert(await r.text());poll();}
async function poll(){
 const s=await (await fetch('/status')).json();
 $('phase').textContent=s.phase;$('detail').textContent='— '+(s.detail||s.preflight_detail||'');
 $('vram').textContent=s.vram_used_mib+' / '+s.vram_total_mib+' MiB';
 $('strata').textContent=s.strata_running?('在跑 ('+s.strata_count+')'):'已停';
 $('comfy').textContent=s.comfy_running?'在跑':'未启动';
 $('outs').textContent=s.outputs.length;
 $('log').textContent=s.log.join('\\n');
 $('handoff').disabled=!s.can_handoff;
 $('restore').disabled=!s.can_restore;
 $('unload').disabled=!s.can_unload;
}
setInterval(poll,1500);poll();
</script>"""




class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, body, ctype="application/json", code=200):
        raw = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _error(self, code, message):
        self._send(json.dumps({"error": message}, ensure_ascii=False), code=code)

    def do_GET(self):
        if self.path == "/status":
            try:
                self._send(json.dumps(status(), ensure_ascii=False))
            except Exception:
                self._error(503, "状态暂时不可用，请稍后重试")
        elif self.path in ("/", "/index.html"):
            self._send(PAGE, "text/html")
        else:
            self._error(404, "接口不存在")

    def do_POST(self):
        host = self.headers.get("Host", "").lower()
        allowed_hosts = ("127.0.0.1:%d" % PORT, "localhost:%d" % PORT, "[::1]:%d" % PORT)
        origin = self.headers.get("Origin")
        if host not in allowed_hosts:
            return self._error(403, "仅允许本机访问")
        if origin:
            parsed = urlsplit(origin)
            if parsed.scheme != "http" or parsed.netloc.lower() != host or parsed.path not in ("", "/"):
                return self._error(403, "拒绝跨站控制请求")
        if self.headers.get_content_type() != "application/json":
            return self._error(415, "请求需要 application/json")
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 32768 or self.headers.get("Transfer-Encoding"):
                return self._error(400, "请求长度无效")
            self.connection.settimeout(5)
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(body, dict):
                return self._error(400, "请求正文需要 JSON 对象")
        except (ValueError, OSError):
            return self._error(400, "请求正文无效")
        if self.path == "/handoff":
            with _lock:
                busy = _state["busy"]
            if busy:
                return self._error(409, "交接正在进行")
            if "workflow_paths" in body and not isinstance(body["workflow_paths"], list):
                return self._error(400, "workflow_paths 需要任务路径列表")
            check = preflight(force=True, workflow_path=body.get("workflow_path"),
                              workflow_paths=body.get("workflow_paths"), expected_hashes=body.get("workflow_sha256"))
            if not check["ok"]:
                return self._error(409, check["detail"])
            runtime = comfy_runtime_state(force=True)
            with _lock:
                busy = _state["busy"]
            admission = _batch_acceptance(runtime, busy)
            if not admission["can_accept_batch"]:
                return self._error(409, admission["accept_detail"])
            titles = body.get("workflow_titles", {})
            if not isinstance(titles, dict) or any(not isinstance(v, str) or len(v) > 120 for v in titles.values()):
                return self._error(400, "任务标题无效")
            for plan in check["plans"]:
                plan["title"] = titles.get(plan["workflow_path"], plan["title"])
            if not spawn(lambda: do_handoff(check["plans"], warm=runtime["comfy_running"]),
                         clear_outputs=True, plans=check["plans"]):
                return self._error(409, "交接正在进行")
            with _lock:
                batch_id = _state.get("batch_id", "")
            self._send(json.dumps({"ok": True, "batch_id": batch_id}))
        elif self.path == "/restore":
            if not spawn(do_restore):
                return self._error(409, "交接正在进行，请等待当前操作结束")
            self._send('{"ok":true}')
        elif self.path == "/unload":
            if not spawn(do_unload):
                return self._error(409, "交接正在进行，请等待当前操作结束")
            self._send('{"ok":true}')
        elif self.path == "/pause":
            try:
                self._send(json.dumps(do_pause(), ensure_ascii=False))
            except RuntimeError as exc:
                self._error(409, str(exc)[:200])
        elif self.path == "/cancel":
            try:
                self._send(json.dumps(do_cancel(), ensure_ascii=False))
            except RuntimeError as exc:
                self._error(409, str(exc)[:200])
        elif self.path == "/cancel-task":
            try:
                self._send(json.dumps(do_cancel_task(body), ensure_ascii=False))
            except RuntimeError as exc:
                self._error(409, str(exc)[:200])
        else:
            self._error(404, "接口不存在")


if __name__ == "__main__":
    note("独立面板已启动：http://127.0.0.1:%d；保持现有模型状态" % PORT)
    LocalHttpServer(("127.0.0.1", PORT), Handler).serve_forever()
