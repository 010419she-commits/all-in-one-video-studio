"""Fetch the installed node catalog from the local ComfyUI loopback service.

Runs only when the user explicitly starts it; the sole network target is
http://127.0.0.1:8188/object_info. The snapshot is written atomically as
object_info.json next to this file and is required by the offline workflow
checks (validate_graph / preflight).
"""
import json
import os
from pathlib import Path
import sys
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "object_info.json"
URL = "http://127.0.0.1:8188/object_info"
MAX_BYTES = 8 * 1024 * 1024

_http = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def main():
    try:
        with _http.open(URL, timeout=15) as response:
            data = response.read(MAX_BYTES + 1)
    except OSError as exc:
        print("无法连接本机 ComfyUI（127.0.0.1:8188）：%s" % type(exc).__name__)
        return 1
    if len(data) > MAX_BYTES:
        print("object_info 响应超过 8 MB，未写入")
        return 1
    try:
        value = json.loads(data.decode("utf-8-sig"))
    except ValueError:
        print("object_info 响应不是有效 JSON，未写入")
        return 1
    if not isinstance(value, dict) or not value:
        print("object_info 响应不是有效节点清单，未写入")
        return 1
    temporary = TARGET.with_name(TARGET.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, TARGET)
    finally:
        if temporary.exists():
            temporary.unlink()
    print("已更新 %s（%d 个节点）" % (TARGET, len(value)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
