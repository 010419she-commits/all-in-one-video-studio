"""Passive progress for an already-running legacy controller.

Only a fixed loopback GET and a randomly named websocket are used.  This module
never submits, interrupts, starts or stops a model.  New controllers provide
their own progress and do not use this compatibility observer.
"""
import atexit
import copy
import hashlib
import json
import math
from pathlib import Path
import threading
import time
import urllib.request
import uuid

from progress_tracker import ComfyProgressListener, PromptProgress, idle_progress


def _queue():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    # Redirects must never take local telemetry to an external address.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener.add_handler(NoRedirect())
    with opener.open("http://127.0.0.1:8188/queue", timeout=2) as response:
        raw = response.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError("queue response too large")
    return json.loads(raw)


def _fingerprint(graph):
    # ComfyUI validates numeric widget values against node schemas and can
    # convert an integer input into an equal float (e.g. fps=24 -> 24.0).
    # Normalize exactly equivalent JSON numbers, retaining every node, class
    # and input value. In particular, bools remain distinct from 0/1.
    def numbers(value):
        if isinstance(value, float) and math.isfinite(value) and value.is_integer():
            return int(value)
        if isinstance(value, dict):
            return {key: numbers(item) for key, item in value.items()}
        if isinstance(value, list):
            return [numbers(item) for item in value]
        return value
    return hashlib.sha256(json.dumps(numbers(graph), sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()


def _context(control):
    tasks = control.get("tasks")
    tasks = [t for t in tasks if isinstance(t, dict)] if isinstance(tasks, list) else []
    running = next(((n + 1, task) for n, task in enumerate(tasks)
                    if task.get("phase") == "running"), None)
    index, task = running or (0, {})
    return {"key": (str(control.get("batch_id", "")), str(task.get("workflow_path", "")),
                    str(task.get("sha256", "")), index),
            "index": index, "count": len(tasks),
            "path": task.get("workflow_path"), "sha256": task.get("sha256")}


def _fallback(control):
    result = idle_progress()
    busy = control.get("busy") is True
    phase = control.get("phase")
    stage, label = {
        "queued": ("queued", "任务已接收，等待显卡交接"),
        "checking_comfy": ("checking", "正在检查视频引擎"),
        "stopping_strata": ("stopping_strata", "正在释放显卡资源"),
        "starting_comfy": ("starting_comfy", "正在启动视频引擎"),
        "queueing": ("queueing", "正在提交视频任务"),
        "generating": ("loading", "视频引擎正在生成，等待步骤进度"),
        "restoring": ("restoring", "正在恢复本地大模型"),
        "failed": ("failed", "视频任务失败，请查看任务状态"),
        "offline": ("offline", "正在连接视频控制服务"),
    }.get(phase, ("processing", "正在处理视频任务") if busy else ("idle", "等待生成任务"))
    context = _context(control)
    result.update(active=busy, stage=stage, label=label, indeterminate=busy,
                  task_index=context["index"], task_count=context["count"], elapsed_seconds=None)
    # Only the controller's MP4 verification, never websocket completion, can
    # claim that the video is complete.
    if phase == "batch_done" and control.get("outputs"):
        result.update(active=False, stage="done", label="MP4 已核验", percent=100.0,
                      indeterminate=False)
    return result


class PassiveProgressObserver:
    def __init__(self, enabled=True, queue_reader=None, listener_factory=None,
                 clock=time.monotonic):
        self.enabled = enabled
        self._queue_reader = queue_reader or _queue
        self._listener_factory = listener_factory or ComfyProgressListener
        self._clock = clock
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._closed = False
        self._thread = None
        self._wanted = False
        self._last_seen = 0.0
        self._context = None
        self._tracker = None
        self._tracker_key = None
        self._prompt_id = None
        self._listener = None
        self._expected_key = None
        self._expected_fingerprint = None
        if enabled:
            atexit.register(self.close)

    def snapshot(self, control):
        # A native controller has exact prompt ownership, including private
        # client events. Prefer it without creating any passive connection.
        if isinstance(control.get("progress"), dict):
            self._deactivate()
            return copy.deepcopy(control["progress"])
        result = _fallback(control)
        wanted = (self.enabled and control.get("connected") is True and
                  control.get("busy") is True and
                  control.get("phase") in ("generating", "queueing"))
        context = _context(control)
        with self._lock:
            self._wanted = wanted and not self._closed
            self._last_seen = self._clock()
            self._context = context
            if self._wanted:
                if self._thread is None or not self._thread.is_alive():
                    self._thread = threading.Thread(target=self._run, name="studio-progress", daemon=True)
                    self._thread.start()
                if self._tracker is not None and self._tracker_key == context["key"]:
                    result = self._tracker.snapshot()
                    if result["stage"] in ("queued", "queueing") and control.get("phase") == "generating":
                        result.update(stage="loading", label="视频引擎正在生成，等待步骤进度")
                    # We attached midway through a legacy task. Its original
                    # start time is unavailable; observer uptime is not elapsed
                    # generation time.
                    result["elapsed_seconds"] = None
            else:
                self._wake.set()
        return result

    def _deactivate(self):
        with self._lock:
            self._wanted = False
            self._wake.set()

    def _detach(self):
        with self._lock:
            listener, self._listener = self._listener, None
            self._tracker = None
            self._tracker_key = None
            self._prompt_id = None
        if listener is not None:
            listener.close()

    def _expected(self, context):
        if context["key"] == self._expected_key:
            return self._expected_fingerprint
        self._expected_key, self._expected_fingerprint = context["key"], None
        path, digest = context["path"], context["sha256"]
        if not isinstance(path, str) or not isinstance(digest, str) or len(digest) != 64:
            return None
        source = Path(path)
        if not source.is_absolute() or source.stat().st_size > 8 * 1024 * 1024:
            return None
        raw = source.read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest:
            return None
        graph = json.loads(raw)
        if not isinstance(graph, dict):
            return None
        self._expected_fingerprint = _fingerprint(graph)
        return self._expected_fingerprint

    def _refresh(self, context):
        """One read-only observation; also independently testable offline."""
        expected = self._expected(context)
        if expected is None:
            return
        queue = self._queue_reader()
        running = queue.get("queue_running") if isinstance(queue, dict) else None
        if not isinstance(running, list) or len(running) != 1:
            if running == []:
                with self._lock:
                    if self._tracker is not None and self._tracker_key == context["key"]:
                        self._tracker.verifying()
            return
        entry = running[0]
        if not isinstance(entry, (list, tuple)) or len(entry) < 3:
            return
        prompt_id, graph = entry[1:3]
        if not isinstance(prompt_id, str) or not prompt_id or not isinstance(graph, dict):
            return
        if _fingerprint(graph) != expected:
            return
        with self._lock:
            if self._prompt_id == prompt_id and self._tracker_key == context["key"]:
                return
        self._detach()
        # Retain node classes only. Prompt text, keys and workflow inputs never
        # enter the event tracker or a public progress label.
        nodes = {str(k): {"class_type": str(v.get("class_type", ""))}
                 for k, v in graph.items() if isinstance(v, dict)}
        tracker = PromptProgress(nodes, context["index"], context["count"], clock=self._clock)
        tracker.bind(prompt_id)
        listener = self._listener_factory(8188, "studio-observer-" + uuid.uuid4().hex, tracker)
        with self._lock:
            if self._closed:
                return
            self._tracker = tracker
            self._tracker_key = context["key"]
            self._prompt_id = prompt_id
            self._listener = listener
        listener.start(timeout=3)

    def _run(self):
        try:
            while True:
                with self._lock:
                    wanted = self._wanted and not self._closed and self._clock() - self._last_seen < 30
                    context = self._context
                if not wanted:
                    return
                try:
                    self._refresh(context)
                except Exception:
                    # Telemetry failure cannot fail generation or expose a
                    # response/error body through the native app.
                    with self._lock:
                        if self._tracker is not None:
                            self._tracker.unavailable()
                # Status polling must not multiply queue traffic. Wake early
                # only when deactivated/closed.
                self._wake.clear()
                self._wake.wait(2)
        finally:
            self._detach()

    def close(self):
        atexit.unregister(self.close)
        with self._lock:
            self._closed = True
            self._wanted = False
            thread = self._thread
            self._wake.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=6)
        if thread is None or not thread.is_alive():
            self._detach()
