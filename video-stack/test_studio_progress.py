"""Legacy progress compatibility tests: all queue/websocket I/O is fake."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import studio_core
import studio_progress as progress


class Listener:
    instances = []

    def __init__(self, port, client_id, tracker):
        self.port, self.client_id, self.tracker = port, client_id, tracker
        self.closed = False
        self.__class__.instances.append(self)

    def start(self, timeout=3):
        self.timeout = timeout
        return True

    def close(self):
        self.closed = True


class PassiveProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="studio-progress-test-")
        self.graph = {"1": {"class_type": "KSampler", "inputs": {"secret": "never publish"}},
                      "2": {"class_type": "SaveVideo", "inputs": {}}}
        self.path = Path(self.temp.name) / "shot.json"
        self.path.write_text(json.dumps(self.graph), encoding="utf-8")
        self.control = {"connected": True, "busy": True, "phase": "generating", "batch_id": "batch-one",
                        "tasks": [{"phase": "running", "workflow_path": str(self.path),
                                   "sha256": hashlib.sha256(self.path.read_bytes()).hexdigest()}]}
        self.queue = {"queue_running": [[1, "prompt-one", self.graph, {}, []]], "queue_pending": []}
        Listener.instances = []
        self.observer = progress.PassiveProgressObserver(enabled=False, queue_reader=lambda: self.queue,
                                                         listener_factory=Listener)

    def tearDown(self):
        self.observer.close()
        self.temp.cleanup()

    def refresh(self):
        self.observer._refresh(progress._context(self.control))

    def test_refresh_only_binds_verified_matching_graph(self):
        self.refresh()
        self.assertEqual(self.observer._prompt_id, "prompt-one")
        self.assertEqual(len(Listener.instances), 1)
        listener = Listener.instances[0]
        self.assertEqual(listener.port, 8188)
        self.assertTrue(listener.client_id.startswith("studio-observer-"))
        self.assertNotIn("never publish", json.dumps(listener.tracker.graph))
        self.assertEqual(listener.tracker.snapshot()["task_index"], 1)

    def test_other_prompt_events_cannot_change_progress(self):
        self.refresh()
        tracker = self.observer._tracker
        before = tracker.snapshot()
        tracker.on_event({"type": "progress", "data": {"prompt_id": "other", "node": "1", "value": 3, "max": 4}})
        self.assertEqual(tracker.snapshot()["percent"], before["percent"])
        tracker.on_event({"type": "progress", "data": {"prompt_id": "prompt-one", "node": "1", "value": 3, "max": 4}})
        self.assertEqual(tracker.snapshot()["percent"], 75)
        self.assertEqual(tracker.snapshot()["stage"], "sampling")

    def test_websocket_success_never_claims_video_done(self):
        self.refresh()
        self.observer._tracker.on_event({"type": "execution_success", "data": {"prompt_id": "prompt-one"}})
        result = self.observer._tracker.snapshot()
        self.assertEqual(result["stage"], "verifying")
        self.assertIsNone(result["percent"])
        self.assertTrue(result["active"])

    def test_done_requires_controller_verified_outputs(self):
        result = self.observer.snapshot({**self.control, "phase": "batch_done", "busy": False,
                                        "outputs": ["verified.mp4"]})
        self.assertEqual(result["percent"], 100)
        self.assertFalse(result["active"])
        self.assertIsNone(self.observer.snapshot({"phase": "batch_done"})["percent"])

    def test_disabled_custom_directory_never_starts_network(self):
        with patch.object(threading.Thread, "start", side_effect=AssertionError("thread started")):
            app = studio_core.Studio(self.temp.name)
            result = app.progress_observer.snapshot(self.control)
        self.assertTrue(result["indeterminate"])
        self.assertIsNone(result["elapsed_seconds"])

    def test_native_controller_progress_always_wins_without_observer_connection(self):
        native = {"active": True, "stage": "sampling", "percent": 42, "elapsed_seconds": 90}
        self.observer.enabled = True
        with patch.object(threading.Thread, "start", side_effect=AssertionError("thread started")):
            result = self.observer.snapshot({**self.control, "progress": native})
        self.assertEqual(result, native)
        self.assertIsNot(result, native)

    def test_graph_mismatch_is_not_observed(self):
        self.queue["queue_running"][0][2] = {"1": {"class_type": "Other"}}
        self.refresh()
        self.assertEqual(Listener.instances, [])

    def test_comfy_equal_integer_float_normalization_keeps_match(self):
        disk = {"1": {"class_type": "Sampler", "inputs": {"shift": 7, "fps": 24, "nested": [1, 2.5]}}}
        queue = {"1": {"class_type": "Sampler", "inputs": {"shift": 7.0, "fps": 24.0, "nested": [1.0, 2.5]}}}
        self.assertEqual(progress._fingerprint(disk), progress._fingerprint(queue))

    def test_numeric_normalization_does_not_erase_real_graph_changes(self):
        original = {"1": {"class_type": "Sampler", "inputs": {"value": 1}}}
        variants = [
            {"1": {"class_type": "Other", "inputs": {"value": 1}}},
            {"1": {"class_type": "Sampler", "inputs": {"value": 1.5}}},
            {"1": {"class_type": "Sampler", "inputs": {"value": True}}},
            {"1": {"class_type": "Sampler", "inputs": {"value": "1"}}},
            {"1": {"class_type": "Sampler", "inputs": {"other": 1}}},
            {"2": {"class_type": "Sampler", "inputs": {"value": 1}}},
        ]
        for changed in variants:
            with self.subTest(changed=changed):
                self.assertNotEqual(progress._fingerprint(original), progress._fingerprint(changed))

    def test_nonfinite_queue_values_fail_closed(self):
        for value in [float("nan"), float("inf"), -float("inf")]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                progress._fingerprint({"1": {"inputs": {"value": value}}})

    def test_file_sha_mismatch_is_not_observed(self):
        self.control["tasks"][0]["sha256"] = "0" * 64
        self.refresh()
        self.assertEqual(Listener.instances, [])

    def test_ambiguous_or_malformed_queue_is_not_observed(self):
        for running in [None, {}, [[1]], self.queue["queue_running"] * 2]:
            with self.subTest(running=running):
                self.queue = {"queue_running": running}
                self.refresh()
        self.assertEqual(Listener.instances, [])

    def test_same_prompt_reuses_listener(self):
        self.refresh()
        self.refresh()
        self.assertEqual(len(Listener.instances), 1)

    def test_next_prompt_closes_previous_listener(self):
        self.refresh()
        previous = Listener.instances[0]
        self.queue["queue_running"][0][1] = "prompt-two"
        self.refresh()
        self.assertTrue(previous.closed)
        self.assertEqual(self.observer._prompt_id, "prompt-two")

    def test_queue_empty_is_verifying_not_complete(self):
        self.refresh()
        self.queue["queue_running"] = []
        self.refresh()
        result = self.observer._tracker.snapshot()
        self.assertEqual(result["stage"], "verifying")
        self.assertIsNone(result["percent"])

    def test_attached_task_does_not_invent_elapsed_time(self):
        self.refresh()
        self.observer.enabled = True
        # Treat the worker as alive, with no real thread or sockets started.
        class Alive:
            def is_alive(self):
                return True
        self.observer._thread = Alive()
        result = self.observer.snapshot(self.control)
        self.assertIsNone(result["elapsed_seconds"])
        self.observer._thread = None

    def test_stage_fallbacks_are_safe_fixed_messages(self):
        for phase, stage in [("starting_comfy", "starting_comfy"), ("stopping_strata", "stopping_strata"),
                             ("restoring", "restoring"), ("queueing", "queueing"), ("failed", "failed")]:
            result = self.observer.snapshot({**self.control, "phase": phase, "detail": "SECRET ERROR BODY"})
            self.assertEqual(result["stage"], stage)
            self.assertNotIn("SECRET", json.dumps(result))

    def test_controller_network_failure_returns_safe_stage(self):
        self.observer.enabled = True
        self.observer._queue_reader = lambda: (_ for _ in ()).throw(ValueError("SECRET RESPONSE"))
        result = self.observer.snapshot(self.control)
        self.assertIsNone(result["percent"])
        self.assertNotIn("SECRET", json.dumps(result))
        self.observer.close()
        self.assertFalse(self.observer._thread.is_alive())

    def test_core_status_injects_fallback_and_preserves_native(self):
        class Observer:
            calls = []
            def snapshot(self, control):
                self.calls.append(copy.deepcopy(control))
                return {"stage": "sampling", "percent": 22}
        observer = Observer()
        app = studio_core.Studio(self.temp.name, progress_observer=observer)
        for native in [False, True]:
            control = {"connected": False, "busy": False, "phase": "offline"}
            if native:
                control["progress"] = {"stage": "sampling", "percent": 44}
            with patch.object(app, "controller", return_value=control), \
                 patch.object(app, "catalog", return_value=[]), \
                 patch.object(app, "outputs", return_value=[]):
                result = app.status()
            self.assertEqual(result["controller"]["progress"]["percent"], 44 if native else 22)
        self.assertEqual(len(observer.calls), 2)


if __name__ == "__main__":
    unittest.main()
