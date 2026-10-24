"""Compute engine selection regressions; all service/process/HTTP actions mocked."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

import gpu_panel as gpu
import handoff
import studio_core as core
import workflow_builder as builder


def graph(path=None):
    output = {"class_type": "SaveVideo", "inputs": {"format": "mp4"}}
    if path is not None:
        output["_meta"] = {builder.COMPUTE_PATH_META: path}
    return {"1": output}


class ComputeGraphTests(unittest.TestCase):
    def test_default_and_invalid_values(self):
        self.assertEqual(builder.DEFAULT_COMPUTE_PATH, "hd")
        self.assertEqual(builder.graph_compute_path(graph()), "hd")
        for invalid in (None, "", "turbo", "HD", True, 1, [], {}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                builder.build_workflow("h3_t2v", "test", compute_path=invalid)
            with self.subTest(metadata=invalid), self.assertRaises(ValueError):
                builder.graph_compute_path({"1": {"_meta": {builder.COMPUTE_PATH_META: invalid}}})
        conflict = {**graph("fast"), "2": graph("hd")["1"]}
        with self.assertRaisesRegex(ValueError, "多个计算引擎"):
            builder.graph_compute_path(conflict)

    def test_h3_both_engines_have_identical_eight_step_sampling_and_dimensions(self):
        validate = builder.validate_graph
        with patch.object(builder, "_complete_model", return_value=True), \
             patch.object(builder, "_copy_image", return_value="studio/fixture.png"), \
             patch.object(builder.uuid, "uuid4", return_value=SimpleNamespace(hex="a" * 32)), \
             patch.object(builder.time, "strftime", return_value="20261023"), \
             patch.object(builder, "validate_graph", side_effect=lambda g: validate(g, check_files=False)):
            graphs = [builder.build_workflow("h3_i2v", "a quiet scene", image_paths=["a.png", "b.png"],
                      width=1280, height=704, seconds=15, seed=38, fast=True, compute_path=p)
                      for p in ("fast", "hd")]
        for candidate, path in zip(graphs, ("fast", "hd")):
            self.assertEqual(builder.graph_compute_path(candidate), path)
            self.assertEqual(candidate["11"]["inputs"]["steps"], 8)
            self.assertEqual(candidate["6"]["inputs"]["width"], 1280)
            self.assertEqual(candidate["6"]["inputs"]["height"], 704)
            self.assertEqual(candidate["6"]["inputs"]["length"], 362)
            self.assertEqual(candidate["92"]["_meta"][builder.COMPUTE_PATH_META], path)
            candidate["92"].pop("_meta")
        self.assertEqual(*graphs)

    def test_prepare_forwards_compute_path_and_rejects_explicit_invalid_values(self):
        with tempfile.TemporaryDirectory() as data:
            app = core.Studio(data)
            with patch.object(builder, "build_workflow", return_value=graph("fast")) as build, \
                 patch.object(app, "_save_graph", return_value={"ok": True}):
                app.prepare({"preset": "h3_t2v", "prompt": "test", "compute_path": "fast"})
                self.assertEqual(build.call_args.kwargs["compute_path"], "fast")
                for value in (None, "", "bad", True):
                    with self.subTest(value=value), self.assertRaises(core.StudioError):
                        app.prepare({"preset": "h3_t2v", "prompt": "test", "compute_path": value})
                self.assertEqual(build.call_count, 1)

    def test_launch_mapping_and_no_sampler_flags_changed(self):
        fast, hd = handoff.comfy_command("fast"), handoff.comfy_command("hd")
        self.assertEqual(fast[:-1], hd[:-1])
        self.assertEqual(fast[-1], "--disable-triton-backend")
        self.assertEqual(hd[-1], "--enable-triton-backend")
        self.assertEqual(handoff.comfy_command(), hd)
        with patch.object(handoff, "comfy_procs") as processes, \
             patch.object(handoff, "_launch", side_effect=AssertionError("must not launch")):
            with self.assertRaises(ValueError):
                handoff.start_comfy("unknown")
            processes.assert_not_called()

    def test_effective_process_flags_fail_closed_for_unknown_or_mixed(self):
        def process(*flags):
            return SimpleNamespace(cmdline=lambda: ["python", "main.py", *flags])
        self.assertEqual(handoff.comfy_compute_path([process()]), "fast")
        self.assertEqual(handoff.comfy_compute_path([process("--enable-triton-backend")]), "hd")
        self.assertEqual(handoff.comfy_compute_path([process("--enable-triton-backend", "--disable-triton-backend")]), "fast")
        self.assertIsNone(handoff.comfy_compute_path([]))
        self.assertIsNone(handoff.comfy_compute_path([object()]))
        self.assertIsNone(handoff.comfy_compute_path([process(), process("--enable-triton-backend")]))

    def test_old_controller_cannot_silently_ignore_new_engine_metadata(self):
        with tempfile.TemporaryDirectory() as data:
            root = Path(data)
            path = root / "fixture.api.json"
            path.write_text(json.dumps(graph("hd")), encoding="utf-8")
            app = core.Studio(root / "state")
            app.tasks = [{"workflow_path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                          "title": "fixture", "phase": "prepared"}]
            control = {"connected": True, "busy": False, "comfy_running": False,
                       "supports_selected_workflow": True, "supports_batch_workflows": True}
            check = {"ok": True, "plans": [{"graph": graph("hd")}], "detail": "ok"}
            with patch.object(core, "WORKFLOWS", root), patch.object(app, "_check", return_value=check), \
                 patch.object(app, "controller", return_value=control), \
                 patch.object(app, "_control_write", side_effect=AssertionError("must not hand off")) as write:
                with self.assertRaisesRegex(core.StudioError, "计算引擎选择"):
                    app.handoff({"workflow_paths": [str(path)]})
                write.assert_not_called()


class EngineBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.old_state = copy.deepcopy(gpu._state)
        gpu._state.update(busy=True, paused=False, cancel_requested=False, tasks=[], outputs=[])
        self.current = "hd"
        self.events = []
        self.active = False
        self.ready = {"comfy_running": True, "warm_handoff_ready": True,
                      "comfy_idle": True, "accept_detail": "idle fixture"}
        self.patches = [
            patch.object(gpu, "comfy_runtime_state", return_value=self.ready),
            patch.object(handoff, "comfy_compute_path", side_effect=lambda: self.current),
            patch.object(handoff, "strata_procs", return_value=[]),
            patch.object(handoff, "strata_services", return_value=[]),
            patch.object(handoff, "port_open", side_effect=lambda port: port == handoff.COMFY_PORT),
            patch.object(handoff, "comfy_procs", return_value=[object()]),
            patch.object(handoff, "stop_all_comfy", side_effect=self.stop),
            patch.object(handoff, "start_comfy", side_effect=self.start),
            patch.object(handoff, "stop_strata"),
            patch.object(gpu, "comfy_api", side_effect=AssertionError("unexpected HTTP")),
            patch.object(gpu, "note"),
            patch.object(gpu, "set_phase"),
        ]
        self.mocks = [item.start() for item in self.patches]

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        gpu._state.clear()
        gpu._state.update(self.old_state)

    def stop(self):
        self.assertFalse(self.active, "must never stop after prompt submission/before completion")
        self.events.append("stop")

    def start(self, compute_path="hd"):
        self.assertFalse(self.active)
        self.current = compute_path
        self.events.append("start:" + compute_path)

    def test_same_path_reuses_and_legacy_defaults_to_hd(self):
        self.assertFalse(gpu._ensure_compute_path(builder.graph_compute_path(graph())))
        self.mocks[6].assert_not_called()
        self.mocks[7].assert_not_called()

    def test_different_path_restarts_only_after_idle_probes(self):
        self.assertTrue(gpu._ensure_compute_path("fast"))
        self.assertEqual(self.events, ["stop", "start:fast"])
        self.assertEqual(self.mocks[0].call_args_list, [call(force=True)] * 3)

    def test_busy_or_unknown_queue_refuses_before_stop_or_submit(self):
        for runtimes in ([{**self.ready, "warm_handoff_ready": False}],
                         [self.ready, {**self.ready, "warm_handoff_ready": False}]):
            self.mocks[0].side_effect = runtimes
            with self.assertRaises(RuntimeError):
                gpu._ensure_compute_path("fast")
        self.mocks[6].assert_not_called()
        self.mocks[7].assert_not_called()
        self.mocks[9].assert_not_called()

    def test_strata_or_unknown_engine_refuses_switch(self):
        self.mocks[2].return_value = ["strata fixture"]
        with self.assertRaisesRegex(RuntimeError, "Strata"):
            gpu._ensure_compute_path("fast")
        self.mocks[2].return_value = []
        self.mocks[1].side_effect = lambda: None
        with self.assertRaisesRegex(RuntimeError, "无法确认"):
            gpu._ensure_compute_path("fast")
        self.mocks[6].assert_not_called()
        self.mocks[7].assert_not_called()

    def test_mixed_batch_switches_between_completed_prompts_only(self):
        plans = [{"name": "fixture%d" % index, "graph": graph(path), "video_nodes": ["1"]}
                 for index, path in enumerate(("hd", "hd", "fast", "hd"))]
        submitted = []
        def api(route, payload=None):
            if route == "/prompt":
                self.assertFalse(self.active)
                expected = builder.graph_compute_path(payload["prompt"])
                self.assertEqual(self.current, expected)
                self.active = True
                submitted.append(expected)
                self.events.append("submit:" + expected)
                return {"prompt_id": "fixture"}
            self.assertEqual(route, "/history/fixture")
            self.active = False
            self.events.append("completed")
            return {"fixture": {"status": {"completed": True}, "outputs": {
                "1": {"videos": [{"filename": "fixture.mp4"}]}}}}
        self.mocks[9].side_effect = api
        with patch.object(gpu, "ComfyProgressListener"), patch.object(gpu, "_begin_progress", return_value=Mock()), \
             patch.object(gpu, "validate_mp4", return_value="fixture.mp4"):
            gpu.run_batch(plans)
        self.assertEqual(submitted, ["hd", "hd", "fast", "hd"])
        self.assertEqual(self.events, ["submit:hd", "completed", "submit:hd", "completed",
                                      "stop", "start:fast", "submit:fast", "completed",
                                      "stop", "start:hd", "submit:hd", "completed"])

    def test_cold_start_uses_first_plan_not_default(self):
        self.mocks[0].return_value = {"comfy_running": False}
        plans = [{"name": "first", "graph": graph("fast"), "video_nodes": ["1"]},
                 {"name": "second", "graph": graph("hd"), "video_nodes": ["1"]}]
        with patch.object(gpu, "run_batch") as run:
            gpu.do_handoff(plans)
        self.mocks[7].assert_called_once_with(compute_path="fast")
        run.assert_called_once_with(plans)

    def test_bad_metadata_rejected_before_any_lifecycle_action(self):
        with patch.object(gpu, "run_batch") as run:
            gpu.do_handoff([{"name": "bad", "graph": graph("bad")}])
        self.mocks[7].assert_not_called()
        self.mocks[8].assert_not_called()
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
