"""Progress regressions. All sockets, submissions and GPU operations are mocked."""
import asyncio
import copy
import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import gpu_panel as gpu
from progress_tracker import ComfyProgressListener, PromptProgress, idle_progress


GRAPH = {"1": {"class_type": "UNETLoader"}, "2": {"class_type": "CLIPTextEncode"},
         "3": {"class_type": "KSampler"}, "4": {"class_type": "VAEDecode"},
         "5": {"class_type": "SaveVideo"}}


def event(kind, prompt="owned", **data):
    return {"type": kind, "data": {"prompt_id": prompt, **data}}


class TrackerTests(unittest.TestCase):
    def setUp(self):
        self.now = 10.0
        self.updates = []
        self.tracker = PromptProgress(GRAPH, 2, 3, self.updates.append, clock=lambda: self.now)
        self.tracker.bind("owned")

    def send(self, kind, **data):
        self.tracker.on_event(event(kind, **data))
        return self.tracker.snapshot()

    def test_initial_and_bound_contract(self):
        state = self.tracker.snapshot()
        self.assertEqual(set(state), set(idle_progress()))
        self.assertEqual((state["task_index"], state["task_count"]), (2, 3))
        self.assertTrue(state["active"] and state["indeterminate"])
        self.assertIsNone(state["percent"])

    def test_elapsed_is_real_current_task_time(self):
        self.now += 12.5
        self.assertEqual(self.tracker.snapshot()["elapsed_seconds"], 12.5)
        self.tracker.finish(True)
        self.now += 50
        self.assertEqual(self.tracker.snapshot()["elapsed_seconds"], 12.5)

    def test_progress_is_current_sampler_step_fraction(self):
        state = self.send("progress", node="3", value=3, max=20)
        self.assertEqual((state["stage"], state["value"], state["max"], state["percent"]),
                         ("sampling", 3, 20, 15.0))
        self.assertFalse(state["indeterminate"])
        self.assertEqual(state["label"], "采样步骤 3 / 20")

    def test_last_sampler_step_does_not_claim_video_done(self):
        state = self.send("progress", node="3", value=20, max=20)
        self.assertIsNone(state["percent"])
        self.assertTrue(state["active"] and state["indeterminate"])
        self.assertIn("等待后处理", state["label"])

    def test_rounding_never_reports_100_before_last_step(self):
        state = self.send("progress", node="3", value=99999, max=100000)
        self.assertLess(state["percent"], 100)

    def test_executing_classifies_nodes_without_percentage_guesses(self):
        for node, stage in (("1", "loading"), ("2", "preparing"), ("3", "sampling"),
                            ("4", "decoding"), ("5", "encoding"), ("unknown", "processing")):
            with self.subTest(node=node):
                state = self.send("executing", node=node)
                self.assertEqual(state["stage"], stage)
                self.assertIsNone(state["percent"])
                self.assertTrue(state["indeterminate"])

    def test_success_or_executing_none_only_requests_verification(self):
        for kind, data in (("executing", {"node": None}), ("execution_success", {}),
                           ("executed", {"node": "5"})):
            with self.subTest(kind=kind):
                state = self.send(kind, **data)
                self.assertEqual(state["stage"], "verifying")
                self.assertIsNone(state["percent"])
                self.assertTrue(state["active"])

    def test_only_validated_finish_reports_done_100(self):
        self.tracker.finish(True)
        state = self.tracker.snapshot()
        self.assertEqual((state["stage"], state["percent"]), ("done", 100.0))
        self.assertFalse(state["active"] or state["indeterminate"])
        self.send("progress", node="3", value=1, max=20)
        self.tracker.unavailable()
        self.assertEqual(self.tracker.snapshot(), state)

    def test_error_and_interrupt_are_terminal_without_100(self):
        for kind, stage in (("execution_error", "failed"), ("execution_interrupted", "interrupted")):
            tracker = PromptProgress(GRAPH, 1, 1)
            tracker.bind("owned")
            tracker.on_event(event(kind, exception_message="DO NOT DISPLAY RAW RESPONSE"))
            state = tracker.snapshot()
            self.assertEqual(state["stage"], stage)
            self.assertFalse(state["active"] or state["indeterminate"])
            self.assertIsNone(state["percent"])
            self.assertNotIn("RAW RESPONSE", str(state))
            tracker.on_event(event("progress", node="3", value=19, max=20))
            self.assertEqual(tracker.snapshot(), state)

    def test_failed_mp4_never_reports_100(self):
        self.send("execution_success")
        self.tracker.finish(False)
        state = self.tracker.snapshot()
        self.assertEqual(state["stage"], "failed")
        self.assertIsNone(state["percent"])

    def test_other_prompt_and_missing_prompt_never_change_state(self):
        state = self.tracker.snapshot()
        self.tracker.on_event(event("progress", "foreign", node="3", value=18, max=20))
        self.tracker.on_event({"type": "progress", "data": {"node": "3", "value": 18, "max": 20}})
        self.tracker.on_event({"type": "executing", "data": {"node": None}})
        self.assertEqual(self.tracker.snapshot(), state)

    def test_fast_pre_response_events_are_replayed_only_for_owned_prompt(self):
        tracker = PromptProgress(GRAPH, 1, 1)
        tracker.on_event(event("progress", "foreign", node="3", value=19, max=20))
        tracker.on_event(event("progress", node="3", value=7, max=20))
        tracker.bind("owned")
        self.assertEqual(tracker.snapshot()["value"], 7)
        with self.assertRaises(ValueError):
            tracker.bind("other")

    def test_pre_response_buffer_is_bounded(self):
        tracker = PromptProgress(GRAPH, 1, 1)
        for number in range(1000):
            tracker.on_event(event("progress", node="3", value=number, max=1000))
        self.assertEqual(len(tracker._pending), 256)
        tracker.bind("owned")
        self.assertEqual(tracker.snapshot()["value"], 999)
        self.assertEqual(tracker._pending, [])

    def test_malformed_and_invalid_values_are_ignored(self):
        state = self.tracker.snapshot()
        for value, maximum in ((True, 20), (2, False), ("2", 20), (-1, 20), (21, 20),
                               (2, 0), (2.5, 20), (float("nan"), 20), (2, float("inf"))):
            self.send("progress", node="3", value=value, max=maximum)
            self.assertEqual(self.tracker.snapshot(), state)
        for data in (None, "bad", {}, {"type": "progress", "data": []}):
            self.tracker.on_event(data)
        self.assertEqual(self.tracker.snapshot(), state)

    def test_cache_does_not_convert_node_counts_to_overall_percentage(self):
        state = self.send("execution_cached", nodes=["1", "2", "3", "4", "5"])
        self.assertIsNone(state["percent"])
        self.assertTrue(state["active"])

    def test_unavailable_is_safe_before_and_after_bind(self):
        tracker = PromptProgress(GRAPH, 1, 1)
        tracker.unavailable()
        tracker.bind("owned")
        state = tracker.snapshot()
        self.assertEqual(state["stage"], "unavailable")
        self.assertTrue(state["active"] and state["indeterminate"])
        self.assertIsNone(state["percent"])

    def test_new_task_clears_previous_sampler_state(self):
        self.send("progress", node="3", value=19, max=20)
        tracker = PromptProgress(GRAPH, 3, 3)
        self.assertEqual(tracker.snapshot()["node_id"], "")
        self.assertIsNone(tracker.snapshot()["value"])
        self.assertEqual(tracker.snapshot()["elapsed_seconds"], 0.0)


class ListenerTests(unittest.TestCase):
    def test_listener_is_ready_before_submission_and_closes_without_network(self):
        import aiohttp
        received = threading.Event()
        state = {"opened": False, "closed": False, "url": ""}
        tracker = PromptProgress(GRAPH, 1, 1, lambda value: received.set() if value["value"] == 7 else None)
        class Socket:
            sent = False
            async def __aenter__(self):
                state["opened"] = True
                return self
            async def __aexit__(self, *args):
                state["closed"] = True
            async def receive(self):
                if not self.sent:
                    self.sent = True
                    return SimpleNamespace(type=aiohttp.WSMsgType.TEXT,
                        data=json.dumps(event("progress", node="3", value=7, max=20)))
                await asyncio.sleep(5)
        class Session:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def ws_connect(self, url, **kwargs):
                state["url"] = url
                return Socket()
        listener = ComfyProgressListener(8188, "unique-observer", tracker, session_factory=lambda **kwargs: Session())
        self.assertTrue(listener.start())
        self.assertTrue(state["opened"])
        tracker.bind("owned")
        self.assertTrue(received.wait(2))
        listener.close()
        self.assertTrue(state["closed"])
        self.assertFalse(listener._thread.is_alive())
        self.assertEqual(state["url"], "http://127.0.0.1:8188/ws?clientId=unique-observer")

    def test_listener_failure_degrades_without_leaking_or_raising(self):
        class Session:
            async def __aenter__(self):
                raise OSError("SECRET RESPONSE")
            async def __aexit__(self, *args):
                pass
        tracker = PromptProgress(GRAPH, 1, 1)
        listener = ComfyProgressListener(8188, "unique", tracker, session_factory=lambda **kwargs: Session())
        self.assertFalse(listener.start())
        self.assertFalse(listener._thread.is_alive())
        self.assertEqual(tracker.snapshot()["stage"], "unavailable")
        self.assertNotIn("SECRET", str(tracker.snapshot()))

    def test_thread_start_failure_is_a_nonfatal_telemetry_failure(self):
        tracker = PromptProgress(GRAPH, 1, 1)
        listener = ComfyProgressListener(8188, "unique", tracker)
        with patch.object(threading.Thread, "start", side_effect=RuntimeError("cannot start")):
            self.assertFalse(listener.start())
        listener.close()
        self.assertEqual(tracker.snapshot()["stage"], "unavailable")


class ControllerProgressTests(unittest.TestCase):
    def setUp(self):
        self.old = copy.deepcopy(gpu._state)
        self.old_tracker, self.old_token = gpu._progress_tracker, gpu._progress_token
        gpu._state.update(busy=True, outputs=[], tasks=[])

    def tearDown(self):
        gpu._state.clear()
        gpu._state.update(self.old)
        gpu._progress_tracker, gpu._progress_token = self.old_tracker, self.old_token

    def test_previous_task_callback_cannot_overwrite_new_task(self):
        first = gpu._begin_progress(GRAPH, 1, 2)
        second = gpu._begin_progress(GRAPH, 2, 2)
        first.bind("first")
        first.on_event(event("progress", "first", node="3", value=19, max=20))
        self.assertEqual(gpu._state["progress"]["task_index"], 2)
        second.bind("second")
        second.on_event(event("progress", "second", node="3", value=3, max=20))
        self.assertEqual(gpu._state["progress"]["value"], 3)

    def test_status_elapsed_advances_without_new_events(self):
        tracker = gpu._begin_progress(GRAPH, 1, 1)
        now = [12.0]
        tracker.started, tracker.clock = 10.0, lambda: now[0]
        tracker.bind("owned")
        runtime = {"comfy_running": True, "warm_handoff_ready": False, "comfy_idle": False,
                   "accept_detail": "busy"}
        with patch.object(gpu, "preflight", return_value={"ok": True, "detail": "ok", "workflow_count": 1}), \
             patch.object(gpu.handoff, "strata_procs", return_value=[]), \
             patch.object(gpu.handoff, "strata_services", return_value=[]), \
             patch.object(gpu.handoff, "gpu_memory", return_value=(1, 2)), \
             patch.object(gpu, "comfy_runtime_state", return_value=runtime):
            self.assertEqual(gpu.status()["progress"]["elapsed_seconds"], 2.0)
            now[0] = 70.0
            self.assertEqual(gpu.status()["progress"]["elapsed_seconds"], 60.0)

    def run_fake_batch(self, error=None, reject=False, listener_error=False):
        submitted = []
        order = []
        plan = {"name": "test.api.json", "graph": GRAPH, "video_nodes": ["5"]}
        progress_before_verify = []
        class Listener:
            def __init__(self, port, client_id, tracker):
                self.client_id, self.tracker = client_id, tracker
            def start(self):
                order.append("connected")
                if listener_error:
                    raise RuntimeError("listener start failed")
                return True
            def close(self):
                order.append("closed")
        def api(route, payload=None):
            if route == "/prompt":
                order.append("submitted")
                submitted.append(payload)
                return {"prompt_id": "owned", "node_errors": {} if not reject else {"1": "error"}}
            return {"owned": {"status": {"completed": True},
                              "outputs": {"5": {"videos": [{"filename": "test.mp4"}]}}}}
        def validate(item):
            progress_before_verify.append(copy.deepcopy(gpu._state["progress"]))
            if error:
                raise RuntimeError(error)
            return "verified.mp4"
        with patch.object(gpu, "ComfyProgressListener", Listener), patch.object(gpu, "comfy_api", side_effect=api), \
             patch.object(gpu, "_ensure_compute_path"), \
             patch.object(gpu.handoff, "comfy_procs", return_value=[object()]), \
             patch.object(gpu.handoff, "port_open", return_value=True), \
             patch.object(gpu, "validate_mp4", side_effect=validate), patch.object(gpu, "note"):
            if error or reject or listener_error:
                with self.assertRaises(RuntimeError):
                    gpu.run_batch([plan])
            else:
                gpu.run_batch([plan])
        return submitted, order, progress_before_verify

    def test_listener_connects_before_post_and_done_requires_mp4_verification(self):
        submitted, order, before = self.run_fake_batch()
        self.assertEqual(order, ["connected", "submitted", "closed"])
        self.assertEqual(submitted[0]["prompt"], GRAPH)
        self.assertEqual(len(submitted[0]["client_id"]), 32)
        self.assertEqual(before[0]["stage"], "verifying")
        self.assertIsNone(before[0]["percent"])
        self.assertEqual(gpu._state["progress"]["percent"], 100.0)
        self.assertEqual(gpu._state["outputs"], ["verified.mp4"])

    def test_invalid_mp4_fails_and_cleans_up_listener(self):
        _, order, _ = self.run_fake_batch(error="invalid MP4")
        self.assertEqual(order[-1], "closed")
        self.assertEqual(gpu._state["progress"]["stage"], "failed")
        self.assertIsNone(gpu._state["progress"]["percent"])
        self.assertEqual(gpu._state["outputs"], [])

    def test_rejected_prompt_cleans_up_listener(self):
        _, order, _ = self.run_fake_batch(reject=True)
        self.assertEqual(order[-1], "closed")
        self.assertEqual(gpu._state["progress"]["stage"], "failed")

    def test_failure_before_submit_still_closes_listener(self):
        submitted, order, _ = self.run_fake_batch(listener_error=True)
        self.assertEqual(submitted, [])
        self.assertEqual(order, ["connected", "closed"])

    def test_handoff_and_failure_stages_have_no_invented_fraction(self):
        for stage in ("checking_comfy", "stopping_strata", "starting_comfy"):
            gpu.set_phase(stage)
            self.assertTrue(gpu._state["progress"]["indeterminate"])
            self.assertIsNone(gpu._state["progress"]["percent"])
        gpu.set_phase("failed")
        self.assertFalse(gpu._state["progress"]["active"])
        self.assertIsNone(gpu._state["progress"]["percent"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
