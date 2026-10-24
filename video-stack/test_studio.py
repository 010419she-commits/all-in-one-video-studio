"""Security/contract tests. No live GPU transitions and no paid API requests."""
import base64
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch, Mock
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
import studio_core as core
import studio_service as service
import video_mcp as mcp
import gpu_panel


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="video-studio-test-")
        self.app = core.Studio(self.temp.name)
    def tearDown(self):
        self.temp.cleanup()
    def test_dpapi_roundtrip_and_no_plaintext(self):
        key = "sk-only-a-dummy-key-not-real-12345"
        with patch.object(core, "json_request", side_effect=AssertionError("unexpected network")):
            result = self.app.save_settings({"kimi_api_key": key})
        raw = self.app.settings_path.read_text(encoding="utf-8")
        self.assertNotIn(key, raw)
        self.assertNotIn(key, json.dumps(result))
        self.assertNotIn("key_dpapi", self.app.settings())
        restored = core.Studio(self.temp.name)
        actual = core.dpapi(base64.b64decode(restored.config["key_dpapi"]), decrypt=True).decode()
        self.assertEqual(actual, key)
        self.assertTrue(restored.settings()["key_saved"])
    def test_empty_key_keeps_saved(self):
        self.app.save_settings({"kimi_api_key": "sk-dummy-testing-456"})
        self.app.save_settings({"kimi_api_key": ""})
        self.assertTrue(self.app.settings()["key_saved"])
    def test_remove_key(self):
        self.app.save_settings({"kimi_api_key": "sk-dummy-testing-456"})
        self.app.save_settings({"remove_key": True})
        self.assertFalse(self.app.settings()["key_saved"])
    def test_untrusted_endpoint_rejected_before_change(self):
        for endpoint in ("https://evil.test/v1", "http://api.moonshot.cn/v1", "https://api.moonshot.cn.evil.test/v1", "https://api.moonshot.cn@evil.test/v1"):
            with self.assertRaises(core.StudioError):
                self.app.save_settings({"kimi_base_url": endpoint, "kimi_api_key": "sk-dummy-testing-456"})
        self.assertFalse(self.app.settings_path.exists())
    def test_key_newline_rejected(self):
        with self.assertRaises(core.StudioError):
            self.app.save_settings({"kimi_api_key": "secret\r\nX: injected"})
    def test_connection_is_get_and_key_never_returned(self):
        key = "sk-dummy-testing-456"
        self.app.save_settings({"kimi_api_key": key})
        with patch.object(core, "json_request", return_value={"data": [{"id": "kimi-k2.6"}]}) as request:
            result = self.app.test_connection({"agent": "kimi_api"})
        self.assertTrue(result["model_available"])
        self.assertTrue(request.call_args.args[0].endswith("/models"))
        self.assertNotIn("body", request.call_args.kwargs)
        self.assertNotIn(key, json.dumps(result))
    def test_remote_error_body_not_echoed(self):
        self.app.save_settings({"kimi_api_key": "sk-dummy-testing-456"})
        failure = urllib.error.HTTPError("https://api.moonshot.cn/v1/models", 401, "secret echoed by remote", {}, io.BytesIO(b"sk-dummy-testing-456"))
        with patch.object(core, "json_request", side_effect=failure):
            with self.assertRaises(core.StudioError) as caught:
                self.app.test_connection({"agent": "kimi_api"})
        self.assertNotIn("dummy", str(caught.exception))
    def test_no_implicit_external_agent_or_cloud_request(self):
        with patch.object(core, "json_request", side_effect=AssertionError("unexpected network")), \
             patch.object(core.subprocess, "Popen", side_effect=AssertionError("no process launch")), \
             patch.object(core.subprocess, "run", side_effect=AssertionError("no process run")), \
             patch.object(core.studio_config, "agent_cli", return_value=None):
            for agent in ("codex", "kimi_code", "kimi_api"):
                with self.assertRaises(core.StudioError):
                    self.app.improve({"agent": agent, "prompt": "a quiet scene"})
    def test_no_handoff_without_prepared_task(self):
        with patch.object(core, "json_request", side_effect=AssertionError("unexpected network")):
            with self.assertRaises(core.StudioError):
                self.app.handoff()
    def test_handoff_tampered_file_rejected(self):
        path = Path(self.temp.name) / "test.api.json"
        path.write_text("{}")
        self.app.tasks = [{"workflow_path": str(path), "sha256": "wrong"}]
        self.app.selected = str(path)
        with patch.object(core, "WORKFLOWS", Path(self.temp.name)), patch.object(core, "json_request", side_effect=AssertionError("unexpected network")):
            with self.assertRaises(core.StudioError):
                self.app.handoff()


class SelectedWorkflowTests(unittest.TestCase):
    def test_clip_vision_loader_resolves_vision_folder(self):
        with tempfile.TemporaryDirectory(prefix="vision-folder-test-") as directory:
            root = Path(directory)
            (root / "clip_vision").mkdir()
            (root / "clip_vision" / "vision.safetensors").write_bytes(b"model-fixture")
            with patch.object(gpu_panel, "MODEL_ROOT", root):
                gpu_panel._file_reference("CLIPVisionLoader", "clip_name", "vision.safetensors")

    def test_selected_preflight_ignores_other_invalid_jobs(self):
        with tempfile.TemporaryDirectory(prefix="video-selected-test-") as directory:
            root = Path(directory)
            info = {"Source": {"input": {"required": {}}, "output": ["VIDEO"]},
                    "SaveVideo": {"input": {"required": {"video": ["VIDEO"], "format": [["mp4"]]}}, "output": []}}
            (root / "info.json").write_text(json.dumps(info))
            graph = {"1": {"class_type": "Source", "inputs": {}}, "2": {"class_type": "SaveVideo", "inputs": {"video": ["1", 0], "format": "mp4"}}}
            path = root / "good.api.json"
            path.write_text(json.dumps(graph))
            (root / "bad.api.json").write_text("{}")
            with patch.object(gpu_panel, "WORKFLOW_DIR", str(root)), patch.object(gpu_panel, "OBJECT_INFO", root / "info.json"):
                all_jobs = gpu_panel.preflight(force=True)
                selected = gpu_panel.preflight(force=True, workflow_path=str(path))
                escaped = gpu_panel.preflight(force=True, workflow_path=str(root.parent / "outside.api.json"))
            self.assertFalse(all_jobs["ok"])
            self.assertTrue(selected["ok"], selected["detail"])
            self.assertEqual(len(selected["plans"]), 1)
            self.assertFalse(escaped["ok"])


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.mock = Mock()
        self.mock.status.return_value = {"service": "video-studio", "api_version": 1}
        self.mock.save_settings.return_value = {"ok": True}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), service.Handler)
        self.port = self.server.server_address[1]
        self.patches = [patch.object(service, "studio", self.mock), patch.object(service, "PORT", self.port)]
        for item in self.patches:
            item.start()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        for item in reversed(self.patches):
            item.stop()
    def request(self, headers=None, data=b"{}", route="/settings"):
        request = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, route), data=data,
                                         headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with core.HTTP.open(request) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)
    def test_write_requires_custom_header(self):
        self.assertEqual(self.request()[0], 403)
        self.mock.save_settings.assert_not_called()
    def test_cross_origin_rejected(self):
        self.assertEqual(self.request({"X-Video-Studio": "1", "Origin": "https://evil.test"})[0], 403)
        self.mock.save_settings.assert_not_called()
    def test_rebound_host_rejected(self):
        self.assertEqual(self.request({"X-Video-Studio": "1", "Host": "evil.test"})[0], 403)
    def test_native_write_accepted(self):
        self.assertEqual(self.request({"X-Video-Studio": "1"})[0], 200)
        self.mock.save_settings.assert_called_once_with({})
    def test_error_exception_is_not_reflected(self):
        self.mock.save_settings.side_effect = Exception("dummy-secret-should-not-leak")
        code, value = self.request({"X-Video-Studio": "1"})
        self.assertEqual(code, 500)
        self.assertNotIn("dummy", json.dumps(value))
    def test_get_origin_rejected(self):
        self.assertEqual(self.request({"Origin": "https://evil.test"}, data=None, route="/status")[0], 403)


class McpTests(unittest.TestCase):
    def test_initialize_and_list_no_external_actions(self):
        with patch.object(mcp, "ensure_service", side_effect=AssertionError("unexpected start")):
            result = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}})
            listing = mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual(result["result"]["serverInfo"]["name"], "video-studio")
        self.assertEqual(len(listing["result"]["tools"]), 7)
    def test_start_requires_explicit_path(self):
        with patch.object(mcp, "ensure_service", side_effect=AssertionError("unexpected start")):
            answer = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "video_start_batch", "arguments": {}}})
        self.assertTrue(answer["result"]["isError"])
    def test_status_is_readonly(self):
        with patch.object(mcp, "ensure_service", return_value={"service": "video-studio"}), patch.object(mcp, "json_request", side_effect=AssertionError("unexpected write")):
            result = mcp.call_tool("video_status", {})
        self.assertEqual(result["service"], "video-studio")
    def test_no_notifications_response(self):
        self.assertIsNone(mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
    def test_unknown_method(self):
        answer = mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "arbitrary/command"})
        self.assertEqual(answer["error"]["code"], -32601)


if __name__ == "__main__":
    unittest.main(verbosity=2)