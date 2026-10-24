"""Offline warm-handoff controller regressions; all process/HTTP/GPU actions mocked."""
import copy
from email.message import Message
import io
import json
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

import gpu_panel as gpu


class WarmHandoffControllerTests(unittest.TestCase):
    def setUp(self):
        self.saved_state = copy.deepcopy(gpu._state)
        self.saved_cache = gpu._comfy_probe_cache
        gpu._state.update(phase="idle", busy=False, tasks=[], outputs=[], log=[])
        gpu._comfy_probe_cache = (0.0, None)
        self.process = SimpleNamespace(net_connections=lambda **kw: [SimpleNamespace(
            status=gpu.handoff.psutil.CONN_LISTEN,
            laddr=SimpleNamespace(port=gpu.handoff.COMFY_PORT))])
        self.patches = [
            patch.object(gpu.handoff, "comfy_procs", return_value=[]),
            patch.object(gpu.handoff, "port_open", return_value=False),
            patch.object(gpu.handoff, "strata_procs", return_value=[]),
            patch.object(gpu.handoff, "strata_services", return_value=[]),
            patch.object(gpu.handoff, "strata_healthy", return_value=False),
            patch.object(gpu.handoff, "gpu_memory", return_value=(12000, 32768)),
            patch.object(gpu.handoff, "stop_strata", side_effect=AssertionError("UNEXPECTED_REAL_STOP")),
            patch.object(gpu.handoff, "start_comfy", side_effect=AssertionError("UNEXPECTED_REAL_START")),
            patch.object(gpu.handoff, "start_strata", side_effect=AssertionError("UNEXPECTED_REAL_START")),
            patch.object(gpu.handoff, "stop_all_comfy", side_effect=AssertionError("UNEXPECTED_REAL_STOP")),
            patch.object(gpu.handoff, "wait_vram_released", side_effect=AssertionError("UNEXPECTED_REAL_GPU_WAIT")),
            patch.object(gpu, "comfy_api", side_effect=AssertionError("UNEXPECTED_REAL_HTTP")),
            patch.object(gpu._http, "open", side_effect=AssertionError("UNEXPECTED_REAL_HTTP")),
            patch.object(gpu.handoff._http, "open", side_effect=AssertionError("UNEXPECTED_REAL_HTTP")),
            patch.object(gpu, "note"),  # No production log writes, including expected refusal tests.
        ]
        self.mocks = [item.start() for item in self.patches]
        self.comfy, self.port = self.mocks[:2]
        self.strata, self.services = self.mocks[2:4]
        self.stop, self.start = self.mocks[6:8]
        self.api = self.mocks[11]
        self.plan = {"name": "fixture.api.json", "workflow_path": "fixture.api.json",
                     "title": "offline fixture", "sha256": "a" * 64,
                     "graph": {}, "video_nodes": []}

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        gpu._state.clear()
        gpu._state.update(self.saved_state)
        gpu._comfy_probe_cache = self.saved_cache

    def runtime(self, running=True, ready=True):
        return {"comfy_running": running, "warm_handoff_ready": ready,
                "comfy_idle": ready, "accept_detail": "离线夹具：空闲" if ready else "离线夹具：暂不可用"}

    def live_fixture(self, queue=None, stats=None):
        self.comfy.return_value = [self.process]
        self.port.side_effect = lambda port: port == gpu.handoff.COMFY_PORT
        if queue is None:
            queue = {"queue_running": [], "queue_pending": []}
        if stats is None:
            stats = {"system": {}, "devices": []}
        self.api.side_effect = lambda path, payload=None, timeout=15: {
            "/system_stats": stats, "/queue": queue}[path]

    def post(self, body):
        handler = gpu.Handler.__new__(gpu.Handler)
        handler.path = "/handoff"
        encoded = json.dumps(body).encode("utf-8")
        handler.headers = Message()
        for key, value in (("Host", "127.0.0.1:%d" % gpu.PORT),
                           ("Content-Type", "application/json"),
                           ("Content-Length", str(len(encoded)))):
            handler.headers[key] = value
        handler.rfile = io.BytesIO(encoded)
        handler.connection = SimpleNamespace(settimeout=lambda timeout: None)
        handler._send = Mock()
        handler._error = Mock()
        gpu.Handler.do_POST(handler)  # No server, listener, or socket is created.
        return handler

    def good_check(self):
        return {"ok": True, "detail": "offline preflight", "workflow_count": 1, "plans": [copy.deepcopy(self.plan)]}

    def test_cold_absence_is_accepted_without_http(self):
        runtime = gpu.comfy_runtime_state(force=True)
        self.assertFalse(runtime["comfy_running"])
        self.assertFalse(runtime["warm_handoff_ready"])
        self.assertTrue(gpu._batch_acceptance(runtime, False)["can_accept_batch"])
        self.api.assert_not_called()

    def test_managed_healthy_empty_queue_is_warm_ready(self):
        self.live_fixture()
        runtime = gpu.comfy_runtime_state(force=True)
        self.assertTrue(runtime["comfy_running"] and runtime["warm_handoff_ready"] and runtime["comfy_idle"])
        self.assertEqual(self.api.call_args_list, [call("/system_stats", timeout=2), call("/queue", timeout=2)])

    def test_external_listener_is_never_reused(self):
        self.port.return_value = True
        runtime = gpu.comfy_runtime_state(force=True)
        self.assertTrue(runtime["comfy_running"])
        self.assertFalse(gpu._batch_acceptance(runtime, False)["can_accept_batch"])
        self.api.assert_not_called()

    def test_matching_process_must_own_the_listener(self):
        self.live_fixture()
        self.process.net_connections = lambda **kw: []
        self.assertFalse(gpu.comfy_runtime_state(force=True)["warm_handoff_ready"])
        self.api.assert_not_called()

    def test_owned_process_without_open_port_is_not_ready(self):
        self.comfy.return_value = [self.process]
        self.assertFalse(gpu.comfy_runtime_state(force=True)["warm_handoff_ready"])
        self.api.assert_not_called()

    def test_invalid_identity_schema_blocks_before_queue(self):
        self.live_fixture(stats={"system": {}, "devices": "invalid"})
        self.assertFalse(gpu.comfy_runtime_state(force=True)["warm_handoff_ready"])
        self.assertEqual(self.api.call_args_list, [call("/system_stats", timeout=2)])

    def test_unknown_or_malformed_queue_is_not_idle(self):
        for queue in ({}, {"queue_running": [], "queue_pending": None},
                      {"queue_running": {}, "queue_pending": []}, []):
            with self.subTest(shape=type(queue).__name__):
                self.live_fixture(queue=queue)
                runtime = gpu.comfy_runtime_state(force=True)
                self.assertFalse(runtime["comfy_idle"] or runtime["warm_handoff_ready"])

    def test_running_and_pending_work_both_block_warm_reuse(self):
        for running, pending in ((["offline-job"], []), ([], ["offline-job"])):
            with self.subTest(running=bool(running)):
                self.live_fixture(queue={"queue_running": running, "queue_pending": pending})
                self.assertFalse(gpu.comfy_runtime_state(force=True)["warm_handoff_ready"])

    def test_http_failure_is_safe_and_fail_closed(self):
        self.live_fixture()
        self.api.side_effect = RuntimeError("OFFLINE_BODY_MUST_NOT_BE_EXPOSED")
        runtime = gpu.comfy_runtime_state(force=True)
        self.assertFalse(gpu._batch_acceptance(runtime, False)["can_accept_batch"])
        self.assertNotIn("OFFLINE_BODY", runtime["accept_detail"])

    def test_cached_status_and_forced_probe_are_distinct(self):
        with patch.object(gpu, "_probe_comfy_runtime", return_value=self.runtime()) as probe:
            gpu.comfy_runtime_state()
            gpu.comfy_runtime_state()
            self.assertEqual(probe.call_count, 1)
            gpu.comfy_runtime_state(force=True)
            self.assertEqual(probe.call_count, 2)

    def test_status_acceptance_is_independent_of_prepared_graph(self):
        with patch.object(gpu, "comfy_runtime_state", return_value=self.runtime()), \
             patch.object(gpu, "preflight", return_value={"ok": False, "detail": "not prepared", "workflow_count": 0}):
            value = gpu.status()
        self.assertTrue(value["supports_warm_handoff"] and value["can_accept_batch"])
        self.assertTrue(value["warm_handoff_ready"] and value["comfy_idle"])
        self.assertFalse(value["can_handoff"])

    def test_controller_busy_blocks_admission_without_mutating_history(self):
        gpu._state.update(busy=True, phase="batch_done", tasks=[{"phase": "done", "batch_id": "old"}], outputs=["old.mp4"])
        saved = copy.deepcopy(gpu._state)
        with patch.object(gpu, "comfy_runtime_state", return_value=self.runtime()), \
             patch.object(gpu, "preflight", return_value=self.good_check()):
            value = gpu.status()
        self.assertFalse(value["can_accept_batch"] or value["can_handoff"])
        self.assertEqual(gpu._state, saved)

    def test_warm_post_preserves_hash_preflight_and_freezes_mode(self):
        body = {"workflow_paths": [self.plan["workflow_path"]],
                "workflow_sha256": {self.plan["workflow_path"]: self.plan["sha256"]},
                "workflow_titles": {self.plan["workflow_path"]: "offline title"}}
        with patch.object(gpu, "preflight", return_value=self.good_check()) as check, \
             patch.object(gpu, "comfy_runtime_state", return_value=self.runtime()) as probe, \
             patch.object(gpu, "spawn", return_value=True) as spawn, \
             patch.object(gpu, "do_handoff") as worker:
            handler = self.post(body)
            handler._error.assert_not_called()
            handler._send.assert_called_once()
            check.assert_called_once_with(force=True, workflow_path=None,
                                          workflow_paths=body["workflow_paths"], expected_hashes=body["workflow_sha256"])
            probe.assert_called_once_with(force=True)
            spawn.call_args.args[0]()  # Invokes only the mocked worker.
            self.assertTrue(worker.call_args.kwargs["warm"])
            self.assertEqual(worker.call_args.args[0][0]["sha256"], self.plan["sha256"])

    def test_post_rejects_new_busy_queue_before_spawn(self):
        self.live_fixture(queue={"queue_running": ["offline-external-job"], "queue_pending": []})
        gpu._comfy_probe_cache = (gpu.time.monotonic(), self.runtime())  # stale ready snapshot must not authorize POST
        with patch.object(gpu, "preflight", return_value=self.good_check()), patch.object(gpu, "spawn") as spawn:
            handler = self.post({"workflow_paths": [self.plan["workflow_path"]]})
        self.assertEqual(handler._error.call_args.args[0], 409)
        spawn.assert_not_called()

    def test_failed_graph_preflight_never_reaches_runtime_or_worker(self):
        with patch.object(gpu, "preflight", return_value={"ok": False, "detail": "offline hash mismatch"}), \
             patch.object(gpu, "comfy_runtime_state") as probe, patch.object(gpu, "spawn") as spawn:
            handler = self.post({"workflow_paths": [self.plan["workflow_path"]]})
        self.assertEqual(handler._error.call_args.args[0], 409)
        probe.assert_not_called()
        spawn.assert_not_called()

    def test_warm_worker_reuses_without_lifecycle_or_memory_wait(self):
        with patch.object(gpu, "comfy_runtime_state", return_value=self.runtime()) as probe, \
             patch.object(gpu, "run_batch") as run:
            gpu.do_handoff([self.plan], warm=True)
        self.assertEqual(probe.call_args_list, [call(force=True), call(force=True)])
        run.assert_called_once_with([self.plan])
        self.stop.assert_not_called()
        self.start.assert_not_called()
        self.mocks[10].assert_not_called()

    def test_warm_worker_stops_only_strata_without_whole_card_wait(self):
        active = [True]
        self.strata.side_effect = lambda: ["offline-strata"] if active[0] else []
        self.stop.side_effect = lambda **kw: active.__setitem__(0, False)
        with patch.object(gpu, "comfy_runtime_state", return_value=self.runtime()), patch.object(gpu, "run_batch") as run:
            gpu.do_handoff([self.plan], warm=True)
        self.stop.assert_called_once_with(wait_memory=False)
        self.start.assert_not_called()
        self.mocks[10].assert_not_called()
        run.assert_called_once_with([self.plan])

    def test_warm_worker_refuses_remaining_strata(self):
        self.strata.return_value = ["offline-strata"]
        self.stop.side_effect = None
        with patch.object(gpu, "comfy_runtime_state", return_value=self.runtime()), patch.object(gpu, "run_batch") as run:
            gpu.do_handoff([self.plan], warm=True)
        run.assert_not_called()
        self.start.assert_not_called()
        self.assertEqual(gpu._state["phase"], "failed")
        self.assertFalse(gpu._state["busy"])

    def test_warm_worker_rechecks_queue_before_submit(self):
        with patch.object(gpu, "comfy_runtime_state", side_effect=[self.runtime(), self.runtime(ready=False)]), \
             patch.object(gpu, "run_batch") as run:
            gpu.do_handoff([self.plan], warm=True)
        run.assert_not_called()
        self.start.assert_not_called()
        self.assertEqual(gpu._state["phase"], "failed")

    def test_missing_warm_target_does_not_silently_cold_start(self):
        with patch.object(gpu, "comfy_runtime_state", return_value=self.runtime(running=False, ready=False)), \
             patch.object(gpu, "run_batch") as run:
            gpu.do_handoff([self.plan], warm=True)
        self.stop.assert_not_called()
        self.start.assert_not_called()
        run.assert_not_called()

    def test_cold_worker_preserves_original_lifecycle(self):
        order = []
        self.stop.side_effect = lambda: order.append("stop_strata")
        self.start.side_effect = lambda **kwargs: order.append("start_comfy")
        with patch.object(gpu, "comfy_runtime_state", return_value=self.runtime(running=False, ready=False)), \
             patch.object(gpu, "run_batch", side_effect=lambda plans: order.append("run_batch")):
            gpu.do_handoff([self.plan])
        self.assertEqual(order, ["stop_strata", "start_comfy", "run_batch"])
        self.stop.assert_called_once_with()

    def test_cold_worker_refuses_unexpected_existing_engine(self):
        with patch.object(gpu, "comfy_runtime_state", return_value=self.runtime()), patch.object(gpu, "run_batch") as run:
            gpu.do_handoff([self.plan], warm=False)
        self.stop.assert_not_called()
        self.start.assert_not_called()
        run.assert_not_called()

    def test_spawn_atomically_accepts_only_one_caller(self):
        real_thread = threading.Thread
        barrier = threading.Barrier(2)
        outcomes = []
        worker_starts = []
        class InertWorker:
            def __init__(self, target, daemon):
                self.target = target
            def start(self):
                worker_starts.append(True)  # Never execute a GPU worker.
        def caller():
            barrier.wait(timeout=2)
            outcomes.append(gpu.spawn(lambda: None))
        with patch.object(gpu.threading, "Thread", InertWorker):
            callers = [real_thread(target=caller) for _ in range(2)]
            for thread in callers:
                thread.start()
            for thread in callers:
                thread.join(timeout=3)
        self.assertEqual(sorted(outcomes), [False, True])
        self.assertEqual(worker_starts, [True])


if __name__ == "__main__":
    unittest.main(verbosity=2)
