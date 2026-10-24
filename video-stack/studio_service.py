"""Video Studio loopback HTTP service, independent of GPU model processes."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from urllib.parse import urlsplit
from studio_core import Studio, StudioError, VOICES
from local_http_server import LocalHttpServer

PORT = 8900
studio = Studio()


class Handler(BaseHTTPRequestHandler):
    server_version = "VideoStudio/1"
    def log_message(self, *args):
        pass

    def send_json(self, value, status=200):
        raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def allowed(self, write=False):
        host = self.headers.get("Host", "").lower()
        if host not in ("127.0.0.1:%d" % PORT, "localhost:%d" % PORT):
            return False
        origin = self.headers.get("Origin")
        if origin:
            parsed = urlsplit(origin)
            if parsed.scheme != "http" or parsed.netloc.lower() != host or parsed.path not in ("", "/"):
                return False
        return not write or self.headers.get("X-Video-Studio") == "1"

    def do_GET(self):
        if not self.allowed():
            return self.send_json({"error": "仅允许本机应用访问"}, 403)
        try:
            if self.path == "/status":
                return self.send_json(studio.status())
            if self.path == "/catalog":
                return self.send_json({"catalog": studio.catalog()})
            if self.path == "/outputs":
                return self.send_json({"outputs": studio.outputs()})
            if self.path == "/image-outputs":
                return self.send_json({"outputs": studio.image_outputs()})
            if self.path == "/voices":
                return self.send_json({"voices": VOICES})
            return self.send_json({"error": "接口不存在"}, 404)
        except Exception:
            return self.send_json({"error": "状态暂时不可用，请稍后重试"}, 503)

    def do_POST(self):
        if not self.allowed(True):
            return self.send_json({"error": "拒绝跨站或未授权的控制请求"}, 403)
        if self.headers.get_content_type() != "application/json":
            return self.send_json({"error": "需要 JSON 请求"}, 415)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 4 * 1024 * 1024 or self.headers.get("Transfer-Encoding"):
                raise ValueError()
            self.connection.settimeout(10)
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(body, dict):
                raise ValueError()
        except (ValueError, OSError):
            return self.send_json({"error": "请求正文无效"}, 400)
        routes = {"/settings": studio.save_settings, "/improve": studio.improve,
                  "/test-connection": studio.test_connection,
                  "/prepare": studio.prepare, "/import": studio.import_workflow, "/dramake": studio.dramake,
                  "/select": studio.select, "/handoff": studio.handoff,
                  "/restore": lambda _: studio.restore(),
                  "/unload": lambda _: studio.unload(),
                  "/pause": lambda _: studio.pause(), "/cancel": lambda _: studio.cancel(),
                  "/cancel-task": studio.cancel_task,
                  "/prepare-image": studio.prepare_image, "/tts": studio.tts}
        if self.path not in routes:
            return self.send_json({"error": "接口不存在"}, 404)
        try:
            return self.send_json(routes[self.path](body))
        except StudioError as exc:
            return self.send_json({"error": str(exc)[:300]}, 409)
        except Exception:
            # Never echo request bodies, API keys or remote errors.
            return self.send_json({"error": "操作未完成，请检查输入或稍后重试"}, 500)


if __name__ == "__main__":
    LocalHttpServer(("127.0.0.1", PORT), Handler).serve_forever()
