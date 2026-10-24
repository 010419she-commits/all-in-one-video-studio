"""Batch handoff regressions. No live processes, GPU or paid requests are used."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import gpu_panel as gpu
import studio_core as core
import video_mcp as mcp
import workflow_builder


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="video-batch-test-")
        self.root = Path(self.temp.name)
        self.info = self.root / "info.json"
        self.info.write_text(json.dumps({
            "Source": {"input": {"required": {}}, "output": ["VIDEO"]},
            "SaveVideo": {"input": {"required": {"video": ["VIDEO"], "format": [["mp4"]]}}, "output": []},
        }))
        self.graph = {"1": {"class_type": "Source", "inputs": {}},
                      "2": {"class_type": "SaveVideo", "inputs": {"video": ["1", 0], "format": "mp4"}}}
        self.app = core.Studio(self.root / "state")
        self.paths = []
        for n in range(3):
            path = self.root / ("shot%d.api.json" % n)
            path.write_text(json.dumps(self.graph), encoding="utf-8")
            self.paths.append(str(path.resolve()))
            self.app.tasks.append({"id": str(n), "workflow_path": str(path.resolve()),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "title": "镜头 %d" % n,
                "phase": "prepared", "outputs": []})
        self.app.selected = self.paths[0]
        self.control = {"connected": True, "busy": False, "comfy_running": False,
                        "supports_selected_workflow": True, "supports_batch_workflows": True}
        self.saved_state = copy.deepcopy(gpu._state)
        self.patches = [
            patch.object(core, "WORKFLOWS", self.root),
            patch.object(gpu, "WORKFLOW_DIR", str(self.root)),
            patch.object(gpu, "OBJECT_INFO", self.info),
            patch.object(core, "json_request", side_effect=AssertionError("Unexpected network")),
            patch.object(gpu, "comfy_api", side_effect=AssertionError("Unexpected ComfyUI request")),
            patch.object(gpu, "comfy_runtime_state", side_effect=AssertionError("Unexpected live runtime probe")),
            patch.object(core.subprocess, "Popen", side_effect=AssertionError("Unexpected process start")),
            patch.object(gpu.handoff, "stop_strata", side_effect=AssertionError("Unexpected GPU stop")),
            patch.object(gpu.handoff, "start_comfy", side_effect=AssertionError("Unexpected GPU start")),
            patch.dict(sys.modules, {"agent_connections": SimpleNamespace(status=lambda: [])}),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        gpu._state.clear()
        gpu._state.update(self.saved_state)
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def submit(self, body):
        with patch.object(self.app, "controller", return_value=self.control), \
             patch.object(self.app, "_control_write", return_value={"ok": True, "batch_id": "batch-one"}) as write:
            result = self.app.handoff(body)
        return result, write

    def studio_status(self, control):
        with patch.object(self.app, "controller", return_value=dict(control)), \
             patch.object(self.app, "catalog", return_value=[]), \
             patch.object(self.app, "outputs", return_value=[]):
            return self.app.status()

    def warm_control(self):
        return {**self.control, "comfy_running": True, "supports_warm_handoff": True,
                "can_accept_batch": True, "warm_handoff_ready": True, "comfy_idle": True}

    def assert_control_rejected(self, control):
        self.assertFalse(self.studio_status(control)["controller"]["can_handoff"])
        with patch.object(self.app, "controller", return_value=control), \
             patch.object(self.app, "_control_write", side_effect=AssertionError("Handoff attempted")):
            with self.assertRaises(core.StudioError):
                self.app.handoff({"workflow_paths": self.paths})
        self.assertEqual([t["phase"] for t in self.app.tasks], ["prepared"] * 3)
        self.assertEqual(self.app.selected, self.paths[0])

    def test_idle_managed_warm_controller_accepts_batch(self):
        self.control = self.warm_control()
        self.assertTrue(self.studio_status(self.control)["controller"]["can_handoff"])
        order = [self.paths[2], self.paths[0]]
        result, write = self.submit({"workflow_paths": order})
        self.assertEqual(result["workflow_paths"], order)
        self.assertEqual(write.call_args.args[0], "/handoff")
        self.assertEqual(write.call_args.args[1]["workflow_paths"], order)
        self.assertEqual(set(write.call_args.args[1]["workflow_sha256"]), set(order))
        self.assertEqual([t["phase"] for t in self.app.tasks], ["queued", "prepared", "queued"])

    def test_warm_requires_every_capability_as_exact_boolean_true(self):
        for field in ("supports_warm_handoff", "can_accept_batch", "warm_handoff_ready", "comfy_idle"):
            for value in (False, None, "true", 1, "missing"):
                control = self.warm_control()
                if value == "missing":
                    del control[field]
                else:
                    control[field] = value
                with self.subTest(field=field, value=value):
                    self.assert_control_rejected(control)

    def test_busy_and_controller_reported_nonempty_queues_reject_warm(self):
        # Queue inspection belongs to the controller. Studio must honor each
        # of its explicit refusal flags without attempting a cold fallback.
        cases = [
            {"busy": True},
            {"queue_running": [[1, "running"]], "can_accept_batch": False, "comfy_idle": False},
            {"queue_pending": [[2, "pending"]], "can_accept_batch": False, "comfy_idle": False},
            {"warm_handoff_ready": False, "accept_detail": "ComfyUI 健康检查未通过"},
        ]
        for flags in cases:
            with self.subTest(flags=flags):
                self.assert_control_rejected({**self.warm_control(), **flags})

    def test_legacy_running_comfy_never_falls_back_to_cold(self):
        self.assert_control_rejected({**self.control, "comfy_running": True})

    def test_cold_legacy_and_explicit_acceptance_remain_compatible(self):
        for extra in ({}, {"can_accept_batch": True}):
            control = {**self.control, **extra}
            with self.subTest(extra=extra):
                self.assertTrue(self.studio_status(control)["controller"]["can_handoff"])
                with patch.object(self.app, "controller", return_value=control), \
                     patch.object(self.app, "_control_write", return_value={"ok": True}) as write:
                    self.app.handoff({"workflow_paths": [self.paths[0]]})
                write.assert_called_once()

    def test_cold_controller_explicit_refusal_is_not_ignored(self):
        for value in (False, None, "true", 1):
            with self.subTest(value=value):
                self.assert_control_rejected({**self.control, "can_accept_batch": value})

    def test_unknown_connection_busy_or_comfy_state_cannot_accept(self):
        for initial in (self.control, self.warm_control()):
            for field, values in (("connected", (False, None, "true", 1)),
                                  ("busy", (None, "false", 0)),
                                  ("comfy_running", (None, "false", 0))):
                for value in (*values, "missing"):
                    control = {**initial}
                    if value == "missing":
                        del control[field]
                    else:
                        control[field] = value
                    with self.subTest(warm=initial["comfy_running"], field=field, value=value):
                        self.assert_control_rejected(control)

    def test_warm_status_preserves_selected_preflight_and_workflow_capabilities(self):
        control = self.warm_control()
        with patch.object(self.app, "_check", return_value={"ok": False, "detail": "模型缺失"}):
            state = self.studio_status(control)
        self.assertFalse(state["controller"]["can_handoff"])
        self.assertEqual(state["controller"]["preflight_detail"], "模型缺失")
        for field in ("supports_selected_workflow", "supports_batch_workflows"):
            with self.subTest(field=field):
                self.assert_control_rejected({**control, field: False})

    def test_refusal_detail_is_bounded_and_not_coerced_from_other_types(self):
        detail = " queue\n\tbusy " + "x" * 300
        control = {**self.warm_control(), "can_accept_batch": False, "accept_detail": detail}
        with patch.object(self.app, "controller", return_value=control), \
             patch.object(self.app, "_control_write", side_effect=AssertionError("Handoff attempted")):
            with self.assertRaises(core.StudioError) as caught:
                self.app.handoff({"workflow_paths": self.paths})
        self.assertEqual(str(caught.exception), " ".join(detail.split())[:240])
        for value in ({"unexpected": "detail"}, ["unexpected"], None):
            allowed, reason = self.app._handoff_acceptance({**control, "accept_detail": value})
            self.assertFalse(allowed)
            self.assertNotIn("unexpected", reason)

    def test_order_hashes_titles_and_persistence(self):
        order = [self.paths[2], self.paths[0]]
        result, write = self.submit({"workflow_paths": order})
        payload = write.call_args.args[1]
        self.assertEqual(payload["workflow_paths"], order)
        self.assertEqual(result["workflow_paths"], order)
        self.assertEqual(payload["workflow_titles"][order[0]], "镜头 2")
        self.assertEqual(payload["workflow_sha256"][order[0]], self.app.tasks[2]["sha256"])
        self.assertEqual([t["phase"] for t in self.app.tasks], ["queued", "prepared", "queued"])
        restored = core.Studio(self.app.data)
        self.assertEqual(restored.tasks[2]["batch_id"], "batch-one")

    def test_legacy_single_path_and_selected_fallback(self):
        for body in ({"workflow_path": self.paths[1]}, {}):
            result, write = self.submit(body)
            self.assertEqual(len(write.call_args.args[1]["workflow_paths"]), 1)
            self.assertEqual(result["workflow_path"], self.paths[1])

    def test_invalid_batches_never_contact_controller(self):
        invalid = [{"workflow_paths": []}, {"workflow_paths": self.paths * 5},
                   {"workflow_paths": self.paths[0]}, {"workflow_paths": [None]},
                   {"workflow_paths": [self.paths[0], self.paths[0]]},
                   {"workflow_path": self.paths[0], "workflow_paths": self.paths},
                   {"workflow_paths": [str(self.root / "unregistered.api.json")]}]
        with patch.object(self.app, "controller", side_effect=AssertionError("Controller reached")), \
             patch.object(self.app, "_control_write", side_effect=AssertionError("Handoff attempted")):
            for body in invalid:
                with self.subTest(body=body), self.assertRaises(core.StudioError):
                    self.app.handoff(body)

    def test_second_file_tamper_blocks_entire_batch(self):
        Path(self.paths[1]).write_text("{}")
        with patch.object(self.app, "_control_write", side_effect=AssertionError("Handoff attempted")):
            with self.assertRaisesRegex(core.StudioError, "已更改"):
                self.app.handoff({"workflow_paths": self.paths})
        self.assertEqual([t["phase"] for t in self.app.tasks], ["prepared"] * 3)

    def test_second_preflight_failure_blocks_entire_batch(self):
        with patch.object(self.app, "_check", side_effect=[{"ok": True}, {"ok": False, "detail": "missing model"}]), \
             patch.object(self.app, "_control_write", side_effect=AssertionError("Handoff attempted")):
            with self.assertRaisesRegex(core.StudioError, "missing model"):
                self.app.handoff({"workflow_paths": self.paths})

    def test_controller_missing_batch_support_rejects_before_write(self):
        self.control["supports_batch_workflows"] = False
        with patch.object(self.app, "controller", return_value=self.control), \
             patch.object(self.app, "_control_write", side_effect=AssertionError("Handoff attempted")):
            with self.assertRaisesRegex(core.StudioError, "重新加载"):
                self.app.handoff({"workflow_paths": self.paths})

    def test_controller_rechecks_hash_and_keeps_order(self):
        order = [self.paths[2], self.paths[0]]
        hashes = {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in order}
        good = gpu.preflight(force=True, workflow_paths=order, expected_hashes=hashes)
        self.assertTrue(good["ok"], good["detail"])
        self.assertEqual([p["workflow_path"] for p in good["plans"]], order)
        Path(order[1]).write_text(json.dumps({**self.graph, "3": self.graph["1"]}))
        bad = gpu.preflight(force=True, workflow_paths=order, expected_hashes=hashes)
        self.assertFalse(bad["ok"])
        self.assertIn("已更改", bad["detail"])
        self.assertEqual(bad["plans"], [])
        self.assertEqual(len(good["plans"][1]["graph"]), 2, "Accepted plan must remain frozen")

    def test_controller_rejects_unscoped_oversize_batch(self):
        for n in range(15):
            (self.root / ("extra%d.api.json" % n)).write_text(json.dumps(self.graph))
        self.assertFalse(gpu.preflight(force=True)["ok"])

    def test_prepare_title_passed_and_bad_title_does_not_build(self):
        with patch.object(workflow_builder, "build_workflow", return_value=self.graph) as build, \
             patch.object(self.app, "_save_graph", return_value={"ok": True}) as save:
            self.app.prepare({"prompt": "海边", "title": "开场", "preset": "hunyuan15"})
            self.assertEqual(save.call_args.args[1], "开场")
            for title in (None, ["a"], "x" * 121):
                with self.assertRaises(core.StudioError):
                    self.app.prepare({"prompt": "海边", "title": title})
            self.assertEqual(build.call_count, 1)

    def test_prepare_registers_task_without_controller_or_background_handoff(self):
        (self.root / "object_info.json").write_bytes(self.info.read_bytes())
        with patch.object(core, "ROOT", self.root), \
             patch.object(workflow_builder, "build_workflow", return_value=self.graph), \
             patch.object(self.app, "controller", side_effect=AssertionError("Controller reached")), \
             patch.object(self.app, "_control_write", side_effect=AssertionError("Handoff attempted")), \
             patch.object(self.app, "handoff", side_effect=AssertionError("Background handoff attempted")):
            result = self.app.prepare({"prompt": "海边", "title": "开场", "preset": "hunyuan15"})
        self.assertTrue(result["ready"], result["detail"])
        self.assertEqual(len(self.app.tasks), 4)
        self.assertEqual(self.app.tasks[-1]["phase"], "prepared")
        self.assertTrue(Path(result["workflow_path"]).is_file())
        self.assertEqual(core.Studio(self.app.data).tasks[-1]["phase"], "prepared")

    def test_status_uses_each_task_and_keeps_outputs_on_restore(self):
        self.submit({"workflow_paths": self.paths})
        remote = []
        for task, phase, outputs in zip(self.app.tasks, ["done", "running", "queued"], [["a.mp4"], [], []]):
            remote.append({**task, "phase": phase, "outputs": outputs, "detail": phase})
        control = {**self.control, "phase": "generating", "outputs": ["a.mp4"], "tasks": remote}
        with patch.object(self.app, "controller", return_value=control), patch.object(self.app, "outputs", return_value=[]):
            state = self.app.status()
            self.assertEqual([t["phase"] for t in state["tasks"]], ["done", "running", "queued"])
            self.assertEqual([t["outputs"] for t in state["tasks"]], [["a.mp4"], [], []])
            control["phase"] = "restoring"
            state = self.app.status()
        self.assertEqual(state["tasks"][0]["phase"], "done")
        self.assertEqual(core.Studio(self.app.data).tasks[0]["outputs"], ["a.mp4"])

    def test_status_ignores_old_batch_or_mismatched_graph(self):
        self.submit({"workflow_paths": self.paths})
        remote = [{**self.app.tasks[0], "phase": "done", "batch_id": "older"},
                  {**self.app.tasks[1], "phase": "done", "sha256": "different"}]
        with patch.object(self.app, "controller", return_value={**self.control, "tasks": remote}), \
             patch.object(self.app, "outputs", return_value=[]):
            self.assertEqual([t["phase"] for t in self.app.status()["tasks"]], ["queued"] * 3)

    def test_mid_batch_failure_preserves_first_and_skips_last(self):
        plans = gpu.preflight(force=True, workflow_paths=self.paths)["plans"]
        gpu._state.update({"busy": True, "outputs": [], "tasks": [
            {"workflow_path": p["workflow_path"], "phase": "queued", "outputs": []} for p in plans]})
        submitted = []
        def api(route, payload=None):
            if route == "/prompt":
                submitted.append(payload)
                return {"prompt_id": str(len(submitted))}
            if route == "/history/1":
                return {"1": {"status": {"completed": True}, "outputs": {"2": {"videos": [{"filename": "a.mp4"}]}}}}
            return {"2": {"status": {"status_str": "error"}}}
        with patch.object(gpu.handoff, "stop_strata") as stop, patch.object(gpu.handoff, "start_comfy") as start, \
             patch.object(gpu, "_ensure_compute_path"), \
             patch.object(gpu, "comfy_runtime_state", return_value={"comfy_running": False}), \
             patch.object(gpu.handoff, "comfy_procs", return_value=[{}]), patch.object(gpu.handoff, "port_open", return_value=True), \
             patch.object(gpu, "comfy_api", side_effect=api), patch.object(gpu, "validate_mp4", return_value="a.mp4"), \
             patch.object(gpu, "ComfyProgressListener"), \
             patch.object(gpu, "note"):
            gpu.do_handoff(plans)
        stop.assert_called_once()
        start.assert_called_once()
        self.assertEqual(len(submitted), 2)
        self.assertEqual([t["phase"] for t in gpu._state["tasks"]], ["done", "failed", "skipped"])
        self.assertEqual(gpu._state["tasks"][0]["outputs"], ["a.mp4"])
        self.assertFalse(gpu._state["busy"])

    def test_gpu_handoff_failure_does_not_mark_any_task_done(self):
        plans = gpu.preflight(force=True, workflow_paths=self.paths)["plans"]
        gpu._state.update({"busy": True, "tasks": [
            {"workflow_path": p["workflow_path"], "phase": "queued", "outputs": []} for p in plans]})
        with patch.object(gpu, "comfy_runtime_state", return_value={"comfy_running": False}), \
             patch.object(gpu.handoff, "stop_strata", side_effect=RuntimeError("GPU memory not released")), patch.object(gpu, "note"):
            gpu.do_handoff(plans)
        self.assertEqual([t["phase"] for t in gpu._state["tasks"]], ["failed", "skipped", "skipped"])

    def test_mcp_batch_schema_checks_before_service(self):
        definition = next(t for t in mcp.TOOLS if t["name"] == "video_start_batch")
        mcp.validate_arguments(definition, {"workflow_paths": self.paths})
        mcp.validate_arguments(definition, {"workflow_path": self.paths[0]})
        for args in ({}, {"workflow_paths": []}, {"workflow_paths": self.paths * 5},
                     {"workflow_paths": ["a", "a"]}, {"workflow_paths": [12]},
                     {"workflow_paths": ["a"], "workflow_path": "a"}):
            with patch.object(mcp, "ensure_service", side_effect=AssertionError("Unexpected service access")):
                with self.subTest(args=args), self.assertRaises(core.StudioError):
                    mcp.call_tool("video_start_batch", args)


if __name__ == "__main__":
    unittest.main(verbosity=2)
