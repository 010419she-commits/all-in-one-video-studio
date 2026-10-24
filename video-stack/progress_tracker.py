"""Read-only ComfyUI progress events. History and MP4 checks decide completion."""
import asyncio
import copy
import json
import math
import threading
import time


def idle_progress():
    return {"active": False, "stage": "idle", "label": "等待生成任务", "percent": None,
            "indeterminate": False, "value": None, "max": None, "task_index": 0,
            "task_count": 0, "node_id": "", "elapsed_seconds": 0.0}


class PromptProgress:
    """One task, with strict prompt ownership and bounded pre-response buffering."""
    def __init__(self, graph, task_index, task_count, publish=None, clock=time.monotonic):
        self.graph = graph
        self.publish = publish
        self.clock = clock
        self.started = clock()
        self._lock = threading.RLock()
        self._prompt_id = None
        self._pending = []
        self._terminal = False
        self._unavailable = False
        self._state = {**idle_progress(), "active": True, "stage": "queueing",
                       "label": "正在提交视频任务", "indeterminate": True,
                       "task_index": task_index, "task_count": task_count}

    def snapshot(self):
        with self._lock:
            result = copy.deepcopy(self._state)
            if result["active"]:
                result["elapsed_seconds"] = round(max(0, self.clock() - self.started), 1)
            return result

    def _update(self, stage, label, active=True, node_id="", value=None, maximum=None, percent=None):
        self._state.update(active=active, stage=stage, label=label, node_id=str(node_id),
                           value=value, max=maximum, percent=percent,
                           indeterminate=bool(active and percent is None),
                           elapsed_seconds=round(max(0, self.clock() - self.started), 1))
        if self.publish is not None:
            self.publish(self.snapshot())

    def bind(self, prompt_id):
        if not isinstance(prompt_id, str) or not prompt_id:
            raise ValueError("prompt_id must be a nonempty string")
        with self._lock:
            if self._prompt_id is not None and prompt_id != self._prompt_id:
                raise ValueError("progress task already owns a different prompt")
            self._prompt_id = prompt_id
            self._update("queued", "已提交，等待视频引擎")
            pending, self._pending = self._pending, []
            for event in pending:
                self.on_event(event)
            if self._unavailable and not self._terminal:
                self._update("unavailable", "步骤进度暂不可用，继续等待视频核验")

    def _node_stage(self, node_id):
        node = self.graph.get(str(node_id)) or {}
        kind = str(node.get("class_type", "")).lower()
        if "sampler" in kind or "sampling" in kind:
            return "sampling", "正在采样，等待步骤进度"
        if "decode" in kind:
            return "decoding", "正在解码视频画面"
        if "savevideo" in kind or "videocombine" in kind:
            return "encoding", "正在编码并保存视频"
        if "textencode" in kind or "conditioning" in kind:
            return "preparing", "正在处理提示词"
        if "loader" in kind or kind.startswith("load"):
            return "loading", "正在加载模型或输入素材"
        return "processing", "正在处理视频节点"

    def on_event(self, event):
        if not isinstance(event, dict) or not isinstance(event.get("data"), dict):
            return
        data = event["data"]
        prompt_id = data.get("prompt_id")
        if not isinstance(prompt_id, str) or not prompt_id:
            return
        with self._lock:
            if self._terminal:
                return
            if self._prompt_id is None:
                self._pending = (self._pending + [event])[-256:]
                return
            if prompt_id != self._prompt_id:
                return
            kind = event.get("type")
            node = data.get("node")
            if kind == "execution_start":
                self._update("loading", "正在加载模型或处理输入")
            elif kind == "executing":
                if node is None:
                    self._update("verifying", "生成结束，正在核验 MP4")
                else:
                    stage, label = self._node_stage(node)
                    self._update(stage, label, node_id=node)
            elif kind == "progress":
                value, maximum = data.get("value"), data.get("max")
                if any(isinstance(v, bool) or not isinstance(v, (int, float)) or
                       not math.isfinite(v) or int(v) != v for v in (value, maximum)):
                    return
                value, maximum = int(value), int(maximum)
                if maximum <= 0 or value < 0 or value > maximum or node is None:
                    return
                stage, _ = self._node_stage(node)
                sampler = stage == "sampling"
                label = ("采样步骤 %d / %d" if sampler else "当前节点进度 %d / %d") % (value, maximum)
                # A completed sampler is not a completed video. Avoid a false
                # 100% while decoding, encoding or MP4 verification remains.
                percent = min(99.9, round(value * 100.0 / maximum, 1)) if value < maximum else None
                if value == maximum:
                    label += "，等待后处理"
                self._update(stage, label, node_id=node, value=value, maximum=maximum, percent=percent)
            elif kind == "execution_cached":
                if self._state["stage"] in ("queued", "queueing", "loading"):
                    self._update("loading", "正在复用缓存并处理视频任务")
            elif kind == "executed":
                stage, label = self._node_stage(node)
                self._update("verifying" if stage == "encoding" else "processing",
                             "视频已写入，正在核验 MP4" if stage == "encoding" else "节点完成，等待后续处理",
                             node_id=node or "")
            elif kind == "execution_success":
                self._update("verifying", "生成结束，正在核验 MP4")
            elif kind in ("execution_error", "execution_interrupted"):
                self._terminal = True
                self._update("interrupted" if kind.endswith("interrupted") else "failed",
                             "视频生成已中断" if kind.endswith("interrupted") else "视频生成失败",
                             active=False, node_id=node or "")

    def unavailable(self):
        with self._lock:
            self._unavailable = True
            if not self._terminal:
                self._update("unavailable", "步骤进度暂不可用，继续等待视频核验")

    def verifying(self):
        with self._lock:
            if not self._terminal:
                self._update("verifying", "正在核验 MP4 文件")

    def finish(self, success):
        with self._lock:
            self._terminal = True
            self._pending = []
            self._update("done" if success else "failed", "MP4 已核验" if success else "视频生成失败",
                         active=False, percent=100.0 if success else None)


class ComfyProgressListener:
    """An isolated, non-reconnecting observer; it never submits or cancels work."""
    def __init__(self, port, client_id, progress, session_factory=None):
        self.port, self.client_id, self.progress = port, client_id, progress
        self._session_factory = session_factory
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        self.connected = False

    def start(self, timeout=4):
        self._thread = threading.Thread(target=self._run, name="comfy-progress", daemon=True)
        try:
            self._thread.start()
        except Exception:
            self._thread = None
            self.progress.unavailable()
            return False
        self._ready.wait(timeout)
        if not self.connected:
            self.close()
            self.progress.unavailable()
        return self.connected

    def _run(self):
        try:
            asyncio.run(self._listen())
        except Exception:
            # Telemetry must never fail the generation or leak response bodies.
            if not self._stop.is_set():
                self.progress.unavailable()
        finally:
            self.connected = False
            self._ready.set()

    async def _listen(self):
        import aiohttp  # Already part of the local ComfyUI environment.
        factory = self._session_factory or aiohttp.ClientSession
        async with factory(trust_env=False) as session:
            url = "http://127.0.0.1:%d/ws?clientId=%s" % (self.port, self.client_id)
            connection = session.ws_connect(url, heartbeat=20, max_msg_size=1024 * 1024,
                                            timeout=aiohttp.ClientWSTimeout(ws_close=1))
            async with await asyncio.wait_for(connection, timeout=3) as ws:
                self.connected = True
                self._ready.set()
                while not self._stop.is_set():
                    try:
                        message = await asyncio.wait_for(ws.receive(), timeout=0.5)
                    except asyncio.TimeoutError:
                        continue
                    if message.type == aiohttp.WSMsgType.TEXT:
                        try:
                            self.progress.on_event(json.loads(message.data))
                        except (ValueError, TypeError):
                            continue
                    elif message.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                        if not self._stop.is_set():
                            self.progress.unavailable()
                        break

    def close(self):
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=4)
