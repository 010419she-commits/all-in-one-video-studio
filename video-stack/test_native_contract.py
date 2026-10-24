"""Native UI / loopback API contract checks; no models or paid requests run."""
import json
from pathlib import Path
import re
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

import studio_core
import studio_service
import workflow_builder


SOURCE = (Path(__file__).resolve().parents[1] / "VideoStudio" / "StudioForm.cs").read_text(encoding="utf-8-sig")


class NativeContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="native-studio-contract-")
        cls.studio = studio_core.Studio(cls.temp.name)
        cls.previous = studio_service.studio
        cls.previous_port = studio_service.PORT
        studio_service.studio = cls.studio
        cls.server = studio_service.ThreadingHTTPServer(("127.0.0.1", 0), studio_service.Handler)
        studio_service.PORT = cls.server.server_address[1]
        cls.url = "http://127.0.0.1:" + str(studio_service.PORT)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)
        studio_service.studio = cls.previous
        studio_service.PORT = cls.previous_port
        cls.temp.cleanup()

    def request(self, route, body=None):
        req = urllib.request.Request(self.url + route,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "X-Video-Studio": "1"})
        try:
            with self.opener.open(req, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

    def test_native_preset_ids_accepted_by_prepare(self):
        line = next(line for line in SOURCE.splitlines() if "preset.Items.AddRange" in line)
        presets = re.findall(r'new Choice\("([^"\n]+)"', line)
        self.assertEqual(len(presets), 11)
        with patch.object(workflow_builder, "build_workflow", return_value={"dummy": {}}) as build, \
             patch.object(self.studio, "_save_graph", side_effect=lambda g, title, preset: {
                 "workflow_path": "mock.api.json", "ready": True, "detail": preset}):
            for preset in presets:
                code, result = self.request("/prepare", {"preset": preset, "prompt": "contract example", "image_path": "mock.png"})
                self.assertEqual(code, 200, (preset, result))
                self.assertEqual(build.call_args.args[0], preset)
                self.assertIn("workflow_path", result)

    def test_catalog_covers_every_native_preset(self):
        line = next(line for line in SOURCE.splitlines() if "preset.Items.AddRange" in line)
        presets = re.findall(r'new Choice\("([^"\n]+)"', line)
        code, data = self.request("/catalog")
        self.assertEqual(code, 200)
        by_id = {item["id"]: item for item in data["catalog"]}
        for preset in presets:
            self.assertIn(preset, by_id)
            self.assertIsInstance(by_id[preset]["ready"], bool)
            self.assertIsInstance(by_id[preset]["detail"], str)

    def test_native_key_remove_and_secret_free_status(self):
        dummy = "contract-placeholder-key-not-a-real-key"
        code, data = self.request("/settings", {"kimi_api_key": dummy})
        self.assertEqual(code, 200)
        self.assertTrue(data["settings"]["key_saved"])
        self.assertNotIn(dummy, self.studio.settings_path.read_text())
        with patch.object(self.studio, "controller", return_value={"connected": True, "busy": False}), \
             patch.object(self.studio, "outputs", return_value=[]):
            code, state = self.request("/status")
        self.assertEqual(code, 200)
        self.assertTrue(state["settings"]["key_saved"])
        self.assertNotIn("key_dpapi", json.dumps(state))
        self.assertNotIn(dummy, json.dumps(state))
        lines = SOURCE.splitlines()
        start = next(i for i, line in enumerate(lines) if "Task RemoveKey()" in line)
        body = "\n".join(lines[start:start + 20])
        field = re.search(r'new\{(\w+)=true\}', body).group(1)
        code, data = self.request("/settings", {field: True})
        self.assertEqual(code, 200)
        self.assertFalse(data["settings"]["key_saved"])

    def test_native_check_connection_route_is_readonly(self):
        dummy = "contract-placeholder-key-not-a-real-key"
        self.studio.save_settings({"kimi_api_key": dummy})
        def models_only(url, body=None, headers=None, timeout=8):
            self.assertIsNone(body, "Connection check must not send a paid inference request")
            self.assertEqual(url, self.studio.config["kimi_base_url"] + "/models")
            self.assertEqual(headers["Authorization"], "Bearer " + dummy)
            return {"data": [{"id": self.studio.config["kimi_model"]}]}
        with patch.object(studio_core, "json_request", side_effect=models_only):
            code, data = self.request("/test-connection", {"agent": "kimi_api"})
        self.assertEqual(code, 200)
        self.assertTrue(data["ok"])
        self.assertTrue(data["model_available"])
        self.assertNotIn(dummy, json.dumps(data))

    def test_agent_status_and_saved_selection_shapes(self):
        for agent in ("kimi_api", "qwen", "codex", "kimi_code"):
            code, data = self.request("/settings", {"agent": agent})
            self.assertEqual(code, 200)
            self.assertEqual(data["settings"]["agent"], agent)
        with patch.object(self.studio, "controller", return_value={"connected": True, "busy": False}), \
             patch.object(self.studio, "outputs", return_value=[]):
            code, data = self.request("/status")
        self.assertEqual(code, 200)
        self.assertEqual(data["service"], "video-studio")
        self.assertEqual({a["id"] for a in data["agents"]}, {"codex", "qwen", "kimi_code"})
        for agent in data["agents"]:
            self.assertIsInstance(agent["configured"], bool)
            self.assertIn("name", agent)
            self.assertIn("detail", agent)


if __name__ == "__main__":
    unittest.main(verbosity=2)