"""Offline tests: no Comfy service, CUDA, process launch, or network request."""
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

import workflow_builder as builder
import video_mcp as mcp


class RecipeTests(unittest.TestCase):
    def test_all_sixteen_recipes_match_installed_node_schema(self):
        for model, recipe in [("hunyuan", builder._hunyuan), ("ltx", builder._ltx), ("ltx25", builder._ltx25), ("h3", builder._h3)]:
            for image in [None, "studio/first.png"]:
                for upscale in [False, True]:
                    with self.subTest(model=model, image=bool(image), upscale=upscale):
                        graph = recipe("Camera moves slowly.", "", image, None, 768, 512, 121, 123, upscale, "studio/test")
                        self.assertTrue(builder.validate_graph(graph, check_files=False))

    def test_video_reference_recipes_match_installed_node_schema(self):
        for model, recipe in [("hunyuan", builder._hunyuan), ("ltx", builder._ltx), ("ltx25", builder._ltx25), ("h3", builder._h3)]:
            for upscale in [False, True]:
                with self.subTest(model=model, upscale=upscale):
                    graph = recipe("Camera moves slowly.", "", None, "studio/ref.mp4", 768, 512, 121, 123, upscale, "studio/test")
                    self.assertTrue(builder.validate_graph(graph, check_files=False))
                    kinds = [node["class_type"] for node in graph.values()]
                    self.assertIn("LoadVideo", kinds)
                    self.assertIn("ImageFromBatch", kinds)
        graph = builder._ltx("test", "", None, "studio/ref.mp4", 768, 512, 121, 0, False, "studio/test")
        kinds = [node["class_type"] for node in graph.values()]
        self.assertIn("GetICLoRAParameters", kinds)
        self.assertIn("LTXVAddGuide", kinds)

    def test_explicit_mp4_dynamic_codec(self):
        graph = builder._ltx("test", "", None, None, 768, 512, 121, 0, False, "studio/test")
        self.assertEqual(graph["92"]["inputs"]["format"], "mp4")
        self.assertEqual(graph["92"]["inputs"]["format.codec"], "h264")
        graph["92"]["inputs"].pop("format.codec")
        with self.assertRaisesRegex(ValueError, "format.codec"):
            builder.validate_graph(graph, check_files=False)

    def test_invalid_port_is_rejected(self):
        graph = builder._ltx("test", "", None, None, 768, 512, 121, 0, False, "studio/test")
        graph["91"]["inputs"]["images"] = ["90", 99]
        with self.assertRaises(ValueError):
            builder.validate_graph(graph, check_files=False)

    def test_wrong_link_type_is_rejected(self):
        graph = builder._ltx("test", "", None, None, 768, 512, 121, 0, False, "studio/test")
        graph["91"]["inputs"]["images"] = ["1", 0]
        with self.assertRaisesRegex(ValueError, "类型"):
            builder.validate_graph(graph, check_files=False)

    def test_cycle_is_rejected(self):
        graph = builder._ltx("test", "", None, None, 768, 512, 121, 0, False, "studio/test")
        graph["4"]["inputs"]["model"] = ["4", 0]
        with self.assertRaisesRegex(ValueError, "循环"):
            builder.validate_graph(graph, check_files=False)

    def test_output_traversal_is_rejected(self):
        for prefix in ["studio/../escape", "E:/outside", "studio/%date:yyyy%", "studio/..\\escape"]:
            with self.subTest(prefix=prefix):
                graph = builder._ltx("test", "", None, None, 768, 512, 121, 0, False, prefix)
                with self.assertRaises(ValueError):
                    builder.validate_graph(graph, check_files=False)

    def test_t2v_cannot_silently_drop_image(self):
        with self.assertRaisesRegex(ValueError, "无需首帧"):
            builder.build_workflow("ltx_t2v", "test", image_path="C:/test.png")

    def test_i2v_requires_image(self):
        with self.assertRaisesRegex(ValueError, "首帧"):
            builder.build_workflow("ltx_i2v", "test")

    def test_missing_weights_rejected_before_image_copy(self):
        with patch.object(builder, "_complete_model", return_value=False), patch.object(builder, "_copy_image") as copy:
            with self.assertRaisesRegex(ValueError, "完整下载"):
                builder.build_workflow("hunyuan_i2v", "test", image_path="C:/test.png")
            copy.assert_not_called()

    def test_invalid_parameters(self):
        for kwargs in [{"prompt": " "}, {"seconds": 0}, {"seconds": 31}, {"seconds": True}, {"seconds": float("nan")},
                       {"seed": -1}, {"seed": True}, {"width": 768, "height": 432},
                       {"width": 20000}, {"upscale": "false"}, {"model_id": "unknown"}]:
            args = {"model_id": "ltx23", "prompt": "test", **kwargs}
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                builder.build_workflow(**args)

    def test_frame_alignment(self):
        with patch.object(builder, "_complete_model", return_value=True):
            for preset, node, temporal in [("ltx_t2v", "8", 8), ("hunyuan_t2v", "9", 4)]:
                graph = builder.build_workflow(preset, "test", seconds=3.7)
                self.assertEqual((graph[node]["inputs"]["length"] - 1) % temporal, 0)

    def test_long_t2v_and_i2v_graphs_match_installed_schema(self):
        # Exercise the public builder while faking assets, not the node schemas.
        # These are frame-accurate recipe checks, not successful generation runs.
        validate = builder.validate_graph
        for seconds, expected_frames in [(15, 361), (20, 481), (30, 721)]:
            for preset in ["hunyuan_t2v", "hunyuan_i2v", "ltx_t2v", "ltx_i2v"]:
                for upscale in [False, True]:
                    with self.subTest(seconds=seconds, preset=preset, upscale=upscale), \
                         patch.object(builder, "_complete_model", return_value=True), \
                         patch.object(builder, "_copy_image", return_value="studio/fake-first.png") as copy, \
                         patch.object(builder, "validate_graph", side_effect=lambda g: validate(g, check_files=False)) as validation:
                        graph = builder.build_workflow(
                            preset, "A continuous camera movement.", seconds=seconds, upscale=upscale,
                            image_path="unused-local-first-frame.png" if preset.endswith("i2v") else None)
                        validation.assert_called_once_with(graph)
                        if preset.endswith("i2v"):
                            copy.assert_called_once_with("unused-local-first-frame.png")
                        else:
                            copy.assert_not_called()
                        is_ltx = preset.startswith("ltx")
                        frames = graph["8" if is_ltx else "9"]["inputs"]["length"]
                        self.assertEqual(frames, expected_frames)
                        self.assertEqual((frames - 1) % (8 if is_ltx else 4), 0)
                        self.assertEqual(graph["91"]["inputs"]["fps"], 24)
                        if is_ltx:
                            self.assertEqual(graph["9"]["inputs"]["frames_number"], frames)
                            self.assertEqual(graph["9"]["inputs"]["frame_rate"], 24.0)
                            self.assertEqual(graph["91"]["inputs"]["audio"], ["80", 0])

    def test_duration_choices_and_legacy_ten_second_recipe(self):
        self.assertEqual(builder.DURATION_CHOICES, (3, 4, 5, 6, 8, 10, 12, 15, 20, 30))
        with patch.object(builder, "_complete_model", return_value=True):
            video = [row for row in builder.catalog() if row["family"] != "tools"]
            self.assertEqual({row["id"] for row in video},
                             {"hunyuan_t2v", "hunyuan_i2v", "ltx_t2v", "ltx_i2v",
                              "ltx25_t2v", "ltx25_i2v", "h3_t2v", "h3_i2v"})
            for row in video:
                self.assertEqual(row["seconds_min"], 1)
                self.assertEqual(row["seconds_max"], 30)
                self.assertEqual(row["seconds_choices"], list(builder.DURATION_CHOICES))
            tools = [row for row in builder.catalog() if row["family"] == "tools"]
            self.assertEqual({row["id"] for row in tools}, {"musetalk_lipsync", "cosyvoice_tts", "cosyvoice_lipsync"})
            for row in tools:
                self.assertTrue(row["requires_audio"])
            for preset, node, frames in [("hunyuan_t2v", "9", 241), ("ltx_t2v", "8", 241),
                                         ("ltx25_t2v", "8", 241), ("h3_t2v", "6", 243)]:
                graph = builder.build_workflow(preset, "test", seconds=10)
                self.assertEqual(graph[node]["inputs"]["length"], frames)

    def test_all_eleven_catalog_entries_and_partial_pack(self):
        with patch.object(builder, "_complete_model", side_effect=lambda key: key != "hv_t2v"):
            items = {row["id"]: row for row in builder.catalog()}
            self.assertEqual(set(items), {"hunyuan_t2v", "hunyuan_i2v", "ltx_t2v", "ltx_i2v",
                                          "ltx25_t2v", "ltx25_i2v", "h3_t2v", "h3_i2v",
                                          "musetalk_lipsync", "cosyvoice_tts", "cosyvoice_lipsync"})
            self.assertFalse(items["hunyuan_t2v"]["ready"])
            self.assertTrue(items["hunyuan_i2v"]["ready"])
            self.assertFalse(items["ltx_t2v"]["runtime_validated"])


class McpDurationTests(unittest.TestCase):
    def test_prepare_schema_and_validator_accept_thirty_offline(self):
        definition = next(tool for tool in mcp.TOOLS if tool["name"] == "video_prepare")
        self.assertEqual(definition["inputSchema"]["properties"]["seconds"]["maximum"], 30)
        with patch.object(mcp, "ensure_service", side_effect=AssertionError("no service calls")) as service, \
             patch.object(mcp, "json_request", side_effect=AssertionError("no HTTP calls")) as request, \
             patch.object(mcp.subprocess, "Popen", side_effect=AssertionError("no process launch")) as process:
            for seconds in [10, 12, 15, 20, 30]:
                with self.subTest(seconds=seconds):
                    mcp.validate_arguments(definition, {"preset": "ltx_t2v", "prompt": "test", "seconds": seconds})
            for seconds in [31, True, float("nan"), float("inf")]:
                with self.subTest(invalid_seconds=seconds), self.assertRaises(mcp.StudioError):
                    mcp.validate_arguments(definition, {"preset": "ltx_t2v", "prompt": "test", "seconds": seconds})
            service.assert_not_called()
            request.assert_not_called()
            process.assert_not_called()


class FileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _weight(self):
        path = self.root.joinpath(*builder.FILES["ltx"])
        path.parent.mkdir(parents=True)
        header = json.dumps({"x": {"dtype": "F32", "shape": [1], "data_offsets": [0, 4]}}).encode()
        path.write_bytes(struct.pack("<Q", len(header)) + header + b"\0" * 4)
        return path

    def test_complete_safetensors_header(self):
        self._weight()
        with patch.object(builder, "MODEL_ROOT", self.root):
            self.assertTrue(builder._complete_model("ltx"))

    def test_truncated_model_rejected(self):
        path = self._weight()
        path.write_bytes(path.read_bytes()[:-2])
        with patch.object(builder, "MODEL_ROOT", self.root):
            self.assertFalse(builder._complete_model("ltx"))

    def test_sidecar_beats_cached_complete(self):
        path = self._weight()
        with patch.object(builder, "MODEL_ROOT", self.root):
            self.assertTrue(builder._complete_model("ltx"))
            Path(str(path) + ".parts").write_text("{}")
            self.assertFalse(builder._complete_model("ltx"))

    def test_staging_does_not_count_as_final_model(self):
        staged = self.root / ".hf_dl" / builder.FILES["ltx"][1]
        staged.parent.mkdir()
        staged.write_bytes(b"preallocated bytes")
        with patch.object(builder, "MODEL_ROOT", self.root):
            self.assertFalse(builder._complete_model("ltx"))

    def test_image_is_copied_without_touching_original(self):
        source = self.root / "source.jpg"
        Image.new("RGB", (40, 20), "red").save(source)
        original = source.read_bytes()
        inputs = self.root / "input"
        with patch.object(builder, "INPUT_ROOT", inputs):
            ref = builder._copy_image(source)
        self.assertTrue(ref.startswith("studio/"))
        self.assertEqual(source.read_bytes(), original)
        with Image.open(inputs / ref) as image:
            self.assertEqual(image.size, (40, 20))
            self.assertEqual(image.format, "PNG")

    def test_bad_image_rejected(self):
        source = self.root / "bad.png"
        source.write_text("not an image")
        with self.assertRaisesRegex(ValueError, "无法解码"):
            builder._copy_image(source)

    def test_video_is_copied_with_extension(self):
        source = self.root / "ref.mp4"
        source.write_bytes(b"\0" * 64)
        inputs = self.root / "input"
        with patch.object(builder, "INPUT_ROOT", inputs):
            ref = builder._copy_video(source)
        self.assertTrue(ref.startswith("studio/") and ref.endswith(".mp4"))
        self.assertEqual((inputs / ref).read_bytes(), b"\0" * 64)

    def test_bad_video_extension_rejected(self):
        source = self.root / "ref.avi"
        source.write_bytes(b"\0" * 64)
        with self.assertRaisesRegex(ValueError, "MP4"):
            builder._copy_video(source)

    def test_image_and_video_are_mutually_exclusive(self):
        with self.assertRaisesRegex(ValueError, "二选一"):
            builder.build_workflow("ltx_i2v", "test", image_path="C:/a.png", video_path="C:/b.mp4")

    def test_t2v_with_video_uses_i2v_weights(self):
        with patch.object(builder, "_complete_model", return_value=True), \
             patch.object(builder, "_copy_video", return_value="studio/ref.mp4"), \
             patch.object(builder, "validate_graph", return_value=True):
            graph = builder.build_workflow("hunyuan_t2v", "test", video_path="C:/ref.mp4")
            self.assertEqual(graph["1"]["inputs"]["unet_name"], builder.FILES["hv_i2v"][1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
