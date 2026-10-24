"""Scoped cancellation races with fixture state only. No GPU or live service."""
import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

import gpu_panel as gpu
import studio_core as core


class ConditionalCancelTests(unittest.TestCase):
    def setUp(self):
        self.saved_state = copy.deepcopy(gpu._state)
        self.addCleanup(self.restore_state)
        self.prompt_id = str(uuid.uuid4())
        self.task = {"workflow_path": r"E:\fixture\workflow.api.json", "sha256": "a" * 64,
            "batch_id": "fixture-batch", "phase": "running", "outputs": [],
            "prompt_id": self.prompt_id, "prompt_submission_started": True}
        gpu._state.clear()
        gpu._state.update(busy=True, batch_id="fixture-batch", tasks=[self.task], outputs=[],
            phase="generating", cancel_requested=False, cancel_requires_confirmation=False,
            cancel_confirmed=False, paused=False, log=[])
        self.patches = [
            patch.object(gpu, "comfy_api", return_value={"cancelled": True}),
            patch.object(gpu, "note"),
            patch.object(gpu.handoff, "stop_strata", side_effect=AssertionError("no GPU lifecycle")),
            patch.object(gpu.handoff, "start_strata", side_effect=AssertionError("never restore Strata")),
            patch.object(gpu.handoff, "start_comfy", side_effect=AssertionError("no model starts")),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def restore_state(self):
        gpu._state.clear()
        gpu._state.update(self.saved_state)

    def request(self, **changes):
        body = {"batch_id": "fixture-batch", "workflow_path": self.task["workflow_path"],
                "workflow_sha256": "a" * 64}
        body.update(changes)
        return body

    def test_exact_identity_uses_atomic_prompt_endpoint_only(self):
        result = gpu.do_cancel_task(self.request())
        self.assertTrue(result["cancel_confirmed"])
        self.assertTrue(self.task["cancel_requested"])
        gpu.comfy_api.assert_called_once_with("/api/jobs/" + self.prompt_id + "/cancel", {}, timeout=5)
        with self.assertRaises(gpu.BatchCancelled):
            gpu._check_control()

    def test_changed_batch_path_or_hash_cannot_cancel_anything(self):
        for changes in [{"batch_id": "new-batch"}, {"workflow_path": r"E:\fixture\other.api.json"},
                        {"workflow_sha256": "b" * 64}]:
            with self.assertRaises(RuntimeError):
                gpu.do_cancel_task(self.request(**changes))
        self.assertFalse(gpu._state["cancel_requested"])
        gpu.comfy_api.assert_not_called()

    def test_multi_shot_batch_is_not_globally_cancelled_for_one_shot(self):
        gpu._state["tasks"].append({**self.task, "workflow_path": r"E:\fixture\other.api.json"})
        with self.assertRaises(RuntimeError):
            gpu.do_cancel_task(self.request())
        gpu.comfy_api.assert_not_called()
        self.assertFalse(gpu._state["cancel_requested"])

    def test_late_cancel_does_not_claim_already_done_task_was_cancelled(self):
        self.task["phase"] = "done"
        result = gpu.do_cancel_task(self.request())
        self.assertEqual(result["phase"], "done")
        self.assertFalse(result["cancel_requested"])
        gpu.comfy_api.assert_not_called()

    def test_pre_submission_cancel_stops_without_creating_prompt(self):
        self.task.update(phase="queued", prompt_submission_started=False)
        self.task.pop("prompt_id")
        result = gpu.do_cancel_task(self.request())
        self.assertFalse(result["cancel_confirmed"])
        gpu.comfy_api.assert_not_called()
        with self.assertRaises(gpu.BatchCancelled):
            gpu._check_control()

    def test_unconfirmed_cancel_keeps_controller_ownership_until_confirmed(self):
        gpu.comfy_api.return_value = {"cancelled": False}
        gpu.do_cancel_task(self.request())
        gpu._check_control()
        self.assertTrue(gpu._state["busy"])
        gpu.comfy_api.return_value = {"cancelled": True}
        self.assertTrue(gpu._retry_conditional_cancel())
        with self.assertRaises(gpu.BatchCancelled):
            gpu._check_control()

    def test_old_cancel_response_cannot_mark_new_batch_cancelled(self):
        def response_after_new_batch(*args, **kwargs):
            with gpu._lock:
                gpu._state.update(batch_id="new-batch", cancel_requested=False, cancel_confirmed=False,
                    tasks=[{**self.task, "batch_id": "new-batch", "prompt_id": str(uuid.uuid4())}])
            return {"cancelled": True}
        gpu.comfy_api.side_effect = response_after_new_batch
        gpu.do_cancel_task(self.request())
        self.assertEqual(gpu._state["batch_id"], "new-batch")
        self.assertFalse(gpu._state["cancel_requested"])
        self.assertFalse(gpu._state["cancel_confirmed"])
        self.assertIn(self.prompt_id, gpu.comfy_api.call_args.args[0])

    def test_cancel_during_prompt_post_retries_exact_id_and_releases_batch(self):
        self.task.update(phase="queued", prompt_submission_started=False)
        self.task.pop("prompt_id")
        plan = {"name": "fixture", "workflow_path": self.task["workflow_path"],
                "sha256": self.task["sha256"], "graph": {"1": {"class_type": "SaveVideo", "inputs": {}}},
                "video_nodes": ["1"]}
        cancel_calls = []
        def fake_api(path, payload=None, **kwargs):
            if path == "/prompt":
                # Simulate request sent but not yet accepted: first exact cancel
                # is a no-op; the runner must retry after the response arrives.
                self.assertEqual(self.task["prompt_id"], payload["prompt_id"])
                gpu.do_cancel_task(self.request())
                return {"prompt_id": payload["prompt_id"]}
            if path.startswith("/api/jobs/") and path.endswith("/cancel"):
                cancel_calls.append(path)
                return {"cancelled": len(cancel_calls) > 1}
            raise AssertionError("unexpected ComfyUI request: " + path)
        listener = Mock()
        with patch.object(gpu, "comfy_api", side_effect=fake_api), \
             patch.object(gpu, "comfy_runtime_state", return_value={"warm_handoff_ready": True, "comfy_running": True}), \
             patch.object(gpu.handoff, "strata_procs", return_value=[]), \
             patch.object(gpu.handoff, "strata_services", return_value=[]), \
             patch.object(gpu.handoff, "port_open", return_value=False), \
             patch.object(gpu, "_ensure_compute_path"), \
             patch.object(gpu, "_begin_progress", return_value=Mock()), \
             patch.object(gpu, "ComfyProgressListener", return_value=listener):
            gpu.do_handoff([plan], warm=True)
        self.assertEqual(len(cancel_calls), 2)
        self.assertEqual(cancel_calls[0], cancel_calls[1])
        self.assertEqual(self.task["phase"], "cancelled")
        self.assertEqual(gpu._state["phase"], "cancelled")
        self.assertFalse(gpu._state["busy"])
        listener.close.assert_called_once()


class StudioConditionalCancelTests(unittest.TestCase):
    def test_forwarding_has_no_status_check_use_race_and_no_global_cancel(self):
        with tempfile.TemporaryDirectory() as directory:
            app = core.Studio(directory)
            body = {"batch_id": "batch", "workflow_path": r"E:\fixture\file.api.json", "workflow_sha256": "a" * 64}
            with patch.object(app, "controller", side_effect=AssertionError("no stale status check")), \
                 patch.object(app, "_control_write", return_value={"ok": True}) as write:
                app.cancel_task(body)
            write.assert_called_once_with("/cancel-task", body)

    def test_exact_cancelled_controller_task_and_prompt_id_are_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            app = core.Studio(directory)
            task = {"workflow_path": r"E:\fixture\file.api.json", "sha256": "a" * 64,
                    "batch_id": "batch", "phase": "running", "outputs": []}
            app.tasks = [task]
            remote = {**task, "phase": "cancelled", "prompt_id": str(uuid.uuid4()), "cancel_requested": True}
            control = {"connected": True, "busy": False, "comfy_running": False, "tasks": [remote]}
            with patch.object(app, "controller", return_value=control), \
                 patch.object(app, "catalog", return_value=[]), patch.object(app, "outputs", return_value=[]), \
                 patch.object(app, "image_outputs", return_value=[]), \
                 patch.dict(sys.modules, {"agent_connections": SimpleNamespace(status=lambda: [])}):
                app.status()
            saved = json.loads(app.state_path.read_text(encoding="utf-8"))["tasks"][0]
            self.assertEqual(saved["phase"], "cancelled")
            self.assertEqual(saved["prompt_id"], remote["prompt_id"])


if __name__ == "__main__":
    unittest.main()
