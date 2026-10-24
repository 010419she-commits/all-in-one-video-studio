"""Offline role-separation regression. No HTTP, handoff, models or GPU work."""
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import studio_core as core
import video_mcp as mcp
import video_prompt_profiles as profiles
import workflow_builder as builder


class BackendPromptContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="video-prompt-contract-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.profile_path = self.root / "wechat-profile.json"
        self.store = profiles.VideoPromptStore(self.profile_path)
        self.app = core.Studio(self.root / "app")
        for p in [
            patch.object(core, "VIDEO_PROMPT_PROFILE_PATH", self.profile_path),
            patch.object(core, "json_request", side_effect=AssertionError("no HTTP requests")),
            patch.object(core.subprocess, "Popen", side_effect=AssertionError("no process launch")),
        ]:
            p.start()
            self.addCleanup(p.stop)

    def prepared(self, body):
        with patch.object(builder, "build_workflow", return_value={"fixture": True}) as build, \
             patch.object(self.app, "_save_graph", return_value={"ok": True}) as save:
            self.app.prepare(body)
        return build, save

    def test_legacy_prompt_does_not_read_or_apply_wechat_style(self):
        self.store.set_system_prompt("STYLE_FIXTURE")
        with patch.object(profiles, "compile_video_prompt", side_effect=AssertionError("legacy path must not compile")):
            build, save = self.prepared({"preset": "h3_t2v", "prompt": "  USER_FIXTURE  "})
        self.assertEqual(build.call_args.args[:2], ("h3_t2v", "USER_FIXTURE"))
        self.assertEqual(save.call_args.args[1], "USER_FIXTURE")
        self.assertEqual(save.call_args.kwargs, {})

    def test_unknown_profile_is_rejected_even_with_only_legacy_prompt(self):
        with patch.object(builder, "build_workflow") as build:
            with self.assertRaises(core.StudioError):
                self.app.prepare({"preset": "h3_t2v", "prompt": "content", "prompt_profile": "typo"})
        build.assert_not_called()

    def test_wechat_profile_is_composed_once_and_fields_are_preserved(self):
        profile = self.store.set_system_prompt(" STYLE_FIXTURE\n")
        user = "  USER_FIXTURE\nsecond line  "
        expected = profiles.compile_video_prompt(user, store=self.store)
        build, save = self.prepared({"preset": "h3_t2v", "user_prompt": user, "prompt_profile": "wechat"})
        self.assertEqual(build.call_args.args[1], expected["prompt"])
        self.assertEqual(build.call_args.args[1].count("STYLE_FIXTURE"), 1)
        parts = save.call_args.kwargs["prompt_parts"]
        self.assertEqual(parts, expected)
        self.assertEqual(parts["profile_version"], profile["version"])
        self.assertEqual(parts["user_prompt"], user)
        self.assertEqual(save.call_args.args[1], user.strip())
        self.assertEqual(self.store.get()["system_prompt"], " STYLE_FIXTURE\n")
        self.assertNotIn("user_prompt", json.loads(self.profile_path.read_text(encoding="utf-8")))

    def test_explicit_style_is_per_request_and_never_overwrites_saved_style(self):
        self.store.set_system_prompt("PERSISTED_STYLE")
        build, save = self.prepared({"preset": "h3_t2v", "user_prompt": "USER_FIXTURE",
                                     "system_prompt": "REQUEST_STYLE", "prompt_profile": "wechat"})
        self.assertIn("REQUEST_STYLE", build.call_args.args[1])
        self.assertNotIn("PERSISTED_STYLE", build.call_args.args[1])
        self.assertIsNone(save.call_args.kwargs["prompt_parts"]["profile_version"])
        self.assertEqual(self.store.get()["system_prompt"], "PERSISTED_STYLE")

    def test_explicit_empty_style_bypasses_profile_for_one_request(self):
        self.store.set_system_prompt("PERSISTED_STYLE")
        build, save = self.prepared({"preset": "ltx25_t2v", "user_prompt": "USER_FIXTURE",
                                     "system_prompt": "", "prompt_profile": "wechat"})
        self.assertEqual(build.call_args.args[1], "USER_FIXTURE")
        self.assertEqual(save.call_args.kwargs["prompt_parts"]["system_prompt"], "")
        self.assertEqual(self.store.get()["system_prompt"], "PERSISTED_STYLE")

    def test_non_wechat_user_field_has_no_implicit_global_profile(self):
        self.store.set_system_prompt("PERSISTED_STYLE")
        # If backend accidentally passes None, the shared compiler would load
        # this nonempty default profile and the regression would be visible.
        with patch.object(profiles, "DEFAULT_PROFILE_PATH", self.profile_path):
            for body in [
                {"preset": "h3_t2v", "user_prompt": "USER_FIXTURE"},
                {"preset": "h3_t2v", "user_prompt": "USER_FIXTURE", "system_prompt": None},
            ]:
                build, save = self.prepared(body)
                self.assertEqual(build.call_args.args[1], "USER_FIXTURE")
                self.assertEqual(save.call_args.kwargs["prompt_parts"]["system_prompt"], "")

    def test_matching_legacy_and_user_fields_compile_once_conflicts_are_rejected(self):
        self.store.set_system_prompt("STYLE_FIXTURE")
        build, _ = self.prepared({"preset": "h3_t2v", "prompt": "USER_FIXTURE",
                                  "user_prompt": "USER_FIXTURE", "prompt_profile": "wechat"})
        self.assertEqual(build.call_args.args[1].count("STYLE_FIXTURE"), 1)
        with patch.object(builder, "build_workflow") as build:
            with self.assertRaises(core.StudioError):
                self.app.prepare({"preset": "h3_t2v", "prompt": "LEGACY", "user_prompt": "DIFFERENT"})
            build.assert_not_called()

    def test_combined_length_is_rejected_without_truncation_or_build(self):
        self.store.set_system_prompt("S" * 100)
        with patch.object(builder, "build_workflow") as build:
            with self.assertRaises(core.StudioError):
                self.app.prepare({"preset": "h3_t2v", "user_prompt": "U" * 11950, "prompt_profile": "wechat"})
            build.assert_not_called()

    def test_tts_and_lipsync_prepare_keep_legacy_text(self):
        self.store.set_system_prompt("STYLE_FIXTURE")
        with patch.object(profiles, "compile_video_prompt", side_effect=AssertionError("tools must not compile video style")):
            for preset in ["cosyvoice_tts", "cosyvoice_lipsync", "musetalk_lipsync"]:
                build, save = self.prepared({"preset": preset, "prompt": "SPOKEN_FIXTURE",
                                             "system_prompt": "IGNORED_STYLE", "prompt_profile": "wechat"})
                self.assertEqual(build.call_args.args[1], "SPOKEN_FIXTURE")
                self.assertEqual(save.call_args.kwargs, {})

    def test_prompt_parts_are_persisted_with_the_task_and_returned(self):
        original = self.store.set_system_prompt("STYLE_FIXTURE")
        graph = {"1": {"class_type": "SaveVideo", "inputs": {"filename_prefix": "fixture"}}}
        (self.root / "object_info.json").write_text(json.dumps({"SaveVideo": {}}), encoding="utf-8")
        gpu_stub = types.SimpleNamespace(_mp4_output=lambda node: True, _image_output=lambda node: False)
        with patch.object(core, "ROOT", self.root), patch.object(core, "WORKFLOWS", self.root / "workflows"), \
             patch.dict(sys.modules, {"gpu_panel": gpu_stub}), \
             patch.object(self.app, "_check", return_value={"ok": True, "detail": "fixture"}), \
             patch.object(builder, "build_workflow", return_value=graph):
            result = self.app.prepare({"preset": "h3_t2v", "user_prompt": "USER_FIXTURE", "prompt_profile": "wechat"})
        self.assertEqual(result["prompt_parts"], result["task"]["prompt_parts"])
        saved = json.loads(self.app.state_path.read_text(encoding="utf-8"))["tasks"][-1]
        self.assertEqual(saved["prompt_parts"], result["prompt_parts"])
        self.store.set_system_prompt("LATER_STYLE")
        restored = core.Studio(self.root / "app")
        self.assertEqual(restored.tasks[-1]["prompt_parts"]["system_prompt"], "STYLE_FIXTURE")
        self.assertEqual(restored.tasks[-1]["prompt_parts"]["profile_version"], original["version"])
        self.assertEqual(restored.tasks[-1]["title"], "USER_FIXTURE")


class MCPVideoPromptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.definition = next(item for item in mcp.TOOLS if item["name"] == "video_prepare")

    def setUp(self):
        environment = patch.dict(mcp.os.environ, {"VIDEO_STUDIO_PROMPT_PROFILE": "", "VIDEO_STUDIO_WECHAT_WORKSPACE": ""})
        environment.start()
        self.addCleanup(environment.stop)

    def test_new_presets_and_real_parameters_validate(self):
        for preset in ["h3", "h3_t2v", "h3_i2v", "ltx25", "ltx25_t2v", "ltx25_i2v"]:
            mcp.validate_arguments(self.definition, {
                "preset": preset, "user_prompt": "USER_FIXTURE", "system_prompt": "STYLE_FIXTURE",
                "fast": True, "image_paths": ["C:\\fixture1.png", "C:\\fixture2.png"],
                "audio_path": "C:\\fixture.wav", "instruct_text": "fixture", "width": 3840,
            })

    def test_one_content_field_required_both_equal_allowed_and_conflict_denied(self):
        with self.assertRaises(mcp.StudioError):
            mcp.validate_arguments(self.definition, {"preset": "h3_t2v", "system_prompt": "STYLE_ONLY"})
        mcp.validate_arguments(self.definition, {"preset": "h3_t2v", "prompt": "USER", "user_prompt": "USER"})
        with self.assertRaises(mcp.StudioError):
            mcp.validate_arguments(self.definition, {"preset": "h3_t2v", "prompt": "OLD", "user_prompt": "DIFFERENT"})

    def test_wechat_workspace_auto_tags_profile_without_mutating_caller(self):
        args = {"preset": "h3_t2v", "user_prompt": "USER_FIXTURE"}
        with tempfile.TemporaryDirectory(prefix="video-wechat-workspace-") as directory, \
             patch.dict(mcp.os.environ, {"VIDEO_STUDIO_WECHAT_WORKSPACE": directory}), \
             patch.object(mcp.Path, "cwd", return_value=Path(directory)), \
             patch.object(mcp, "ensure_service", return_value={"service": "video-studio"}), \
             patch.object(mcp, "json_request", return_value={"ok": True}) as request:
            mcp.call_tool("video_prepare", args)
        sent = request.call_args.args[1]
        self.assertEqual(sent["prompt_profile"], "wechat")
        self.assertEqual(sent["user_prompt"], "USER_FIXTURE")
        self.assertNotIn("prompt_profile", args)

    def test_other_workspace_keeps_legacy_prompt_without_profile(self):
        args = {"preset": "h3_t2v", "prompt": "USER_FIXTURE"}
        with tempfile.TemporaryDirectory(prefix="video-other-workspace-") as directory, \
             tempfile.TemporaryDirectory(prefix="video-wechat-workspace-") as workspace, \
             patch.dict(mcp.os.environ, {"VIDEO_STUDIO_WECHAT_WORKSPACE": workspace}), \
             patch.object(mcp.Path, "cwd", return_value=Path(directory)), \
             patch.object(mcp, "ensure_service", return_value={"service": "video-studio"}), \
             patch.object(mcp, "json_request", return_value={"ok": True}) as request:
            mcp.call_tool("video_prepare", args)
        self.assertEqual(request.call_args.args[1], args)
        self.assertNotIn("prompt_profile", request.call_args.args[1])

    def test_wechat_environment_tags_profile_despite_actual_config_backend_cwd(self):
        args = {"preset": "h3_t2v", "user_prompt": "USER_FIXTURE"}
        backend_cwd = Path(r"C:\fixture-backend")
        with patch.dict(mcp.os.environ, {"VIDEO_STUDIO_PROMPT_PROFILE": "wechat"}), \
             patch.object(mcp.Path, "cwd", return_value=backend_cwd), \
             patch.object(mcp, "ensure_service", return_value={"service": "video-studio"}), \
             patch.object(mcp, "json_request", return_value={"ok": True}) as request:
            mcp.call_tool("video_prepare", args)
        self.assertEqual(request.call_args.args[1]["prompt_profile"], "wechat")
        self.assertNotIn("prompt_profile", args)

    def test_other_environment_does_not_apply_wechat_style_to_backend_cwd(self):
        args = {"preset": "h3_t2v", "prompt": "USER_FIXTURE"}
        with patch.dict(mcp.os.environ, {"VIDEO_STUDIO_PROMPT_PROFILE": "other"}), \
             patch.object(mcp.Path, "cwd", return_value=Path(r"C:\fixture-backend")), \
             patch.object(mcp, "ensure_service", return_value={"service": "video-studio"}), \
             patch.object(mcp, "json_request", return_value={"ok": True}) as request:
            mcp.call_tool("video_prepare", args)
        self.assertEqual(request.call_args.args[1], args)

    def test_wechat_restore_is_refused_before_any_service_or_network_call(self):
        with patch.dict(mcp.os.environ, {"VIDEO_STUDIO_PROMPT_PROFILE": "wechat"}), \
             patch.object(mcp, "ensure_service", side_effect=AssertionError("must not contact service")) as ensure, \
             patch.object(mcp, "json_request", side_effect=AssertionError("must not send HTTP")) as request:
            with self.assertRaises(mcp.StudioError):
                mcp.call_tool("video_restore_strata", {})
        ensure.assert_not_called()
        request.assert_not_called()

    def test_other_workspace_retains_restore_compatibility_without_executing_it(self):
        with patch.object(mcp.Path, "cwd", return_value=Path(r"C:\fixture-other-workspace")), \
             patch.object(mcp, "ensure_service", return_value={"service": "video-studio"}), \
             patch.object(mcp, "json_request", return_value={"ok": True}) as request:
            mcp.call_tool("video_restore_strata", {})
        self.assertTrue(request.call_args.args[0].endswith("/restore"))


if __name__ == "__main__":
    unittest.main()
