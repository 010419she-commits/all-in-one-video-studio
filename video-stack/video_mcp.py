"""Dependency-free stdio MCP adapter to the independent Video Studio service.

Only protocol JSON is written to stdout. Keys are never accepted by MCP.
"""
import errno
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import studio_config
from studio_core import ROOT, PYTHON, StudioError, json_request

VERSION = "2025-03-26"
VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")


def schema(properties=None, required=None):
    result = {"type": "object", "properties": properties or {}, "additionalProperties": False}
    if required:
        result["required"] = required
    return result


def tool(name, description, input_schema=None, readonly=False, destructive=False):
    return {"name": name, "description": description, "inputSchema": input_schema or schema(),
            "annotations": {"readOnlyHint": readonly, "destructiveHint": destructive,
                            "idempotentHint": readonly, "openWorldHint": False}}


TOOLS = [
    tool("video_status", "检查本地视频工作台、GPU 交接状态、模型下载和 Agent 接入。不启动 GPU 模型。", readonly=True),
    tool("video_catalog", "查看 HunyuanVideo 1.5 / LTX 2.3 可用预设、缺少的模型和默认参数。", readonly=True),
    tool("video_prepare", "创建并选择视频工作流，不开始生成。user_prompt 是本次内容，system_prompt 是仅本次风格覆盖；prompt_profile=wechat 使用已保存的微信视频风格。旧 prompt 等价于本次内容。微信工作区自动选微信配置。", {**schema({
        "preset": {"type": "string", "enum": ["hunyuan15", "ltx23", "ltx25", "h3", "hunyuan_t2v", "hunyuan_i2v", "ltx_t2v", "ltx_i2v", "ltx25_t2v", "ltx25_i2v", "h3_t2v", "h3_i2v"]},
        "prompt": {"type": "string", "minLength": 1, "maxLength": 12000},
        "user_prompt": {"type": "string", "minLength": 1, "maxLength": 12000, "description": "本次视频内容，不会覆盖已保存的风格"},
        "system_prompt": {"type": "string", "maxLength": 12000, "description": "仅本次风格覆盖；省略沿用微信配置，空字符串仅本次不用前缀"},
        "prompt_profile": {"type": "string", "enum": ["wechat"], "description": "使用微信独立的持久风格配置"},
        "title": {"type": "string", "maxLength": 120, "description": "分镜或任务标题，最多 120 字"},
        "image_path": {"type": "string", "description": "可选本地首帧图绝对路径；省略则文生视频"},
        "image_paths": {"type": "array", "minItems": 1, "maxItems": 6, "items": {"type": "string", "minLength": 1}},
        "video_path": {"type": "string", "minLength": 1, "description": "参考视频绝对路径；与参考图二选一"},
        "audio_path": {"type": "string", "minLength": 1},
        "instruct_text": {"type": "string", "maxLength": 12000},
        "width": {"type": "integer", "minimum": 256, "maximum": 3840},
        "height": {"type": "integer", "minimum": 256, "maximum": 3840},
        "seconds": {"type": "number", "minimum": 1, "maximum": 30},
        "seed": {"type": "integer", "minimum": 0},
        "negative_prompt": {"type": "string", "maxLength": 4000},
        "upscale": {"type": "boolean"}, "fast": {"type": "boolean"}}, ["preset"]),
        "anyOf": [{"required": ["prompt"]}, {"required": ["user_prompt"]}]}),
    tool("video_import_workflow", "导入本地 ComfyUI API JSON 工作流。复制为独立任务，检查节点与 MP4 输出，不执行。", schema({
        "path": {"type": "string", "description": "本地 API 工作流 JSON 的绝对路径"},
        "title": {"type": "string", "maxLength": 120}}, ["path"])),
    tool("video_start_batch", "按指定顺序将 1 至 12 个已准备任务交接给本地视频控制器生成 MP4。全部任务预检通过才交接；任一生成失败会停止后续任务。仅在用户要求实际生成时调用，先完成提示词/首帧/参数准备。后台独立运行，用状态与输出工具检查结果；微信工作区的 Strata 已永久停用，无需恢复。", {**schema({
        "workflow_path": {"type": "string", "minLength": 1, "description": "兼容单任务；与 workflow_paths 二选一"},
        "workflow_paths": {"type": "array", "minItems": 1, "maxItems": 12, "uniqueItems": True,
                           "items": {"type": "string", "minLength": 1},
                           "description": "按分镜顺序填写 video_prepare 或 video_import_workflow 返回的路径"}}),
        "oneOf": [{"required": ["workflow_path"]}, {"required": ["workflow_paths"]}]}, destructive=True),
    tool("video_restore_strata", "旧工作区兼容操作：停止 ComfyUI 后恢复 Strata。微信工作区的 Strata 已永久停用，此操作不可用。", destructive=True),
    tool("video_outputs", "列出本地 MP4 输出路径、大小、时间；不修改文件。", readonly=True),
]
ROUTES = {"video_status": ("/status", False), "video_catalog": ("/catalog", False),
          "video_prepare": ("/prepare", True), "video_import_workflow": ("/import", True),
          "video_start_batch": ("/handoff", True), "video_restore_strata": ("/restore", True),
          "video_outputs": ("/outputs", False)}


def ensure_service():
    try:
        status = json_request("http://127.0.0.1:8900/status", timeout=20)
    except urllib.error.URLError as exc:
        reason = exc.reason
        if not (isinstance(reason, ConnectionRefusedError) or getattr(reason, "winerror", None) == 10061 or getattr(reason, "errno", None) == errno.ECONNREFUSED):
            raise StudioError("视频工作台暂时不可用")
        subprocess.Popen([str(PYTHON), "-X", "utf8", "-u", str(ROOT / "studio_service.py")], cwd=str(ROOT),
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)
        status = None
        for _ in range(20):
            time.sleep(.2)
            try:
                status = json_request("http://127.0.0.1:8900/status", timeout=20)
                break
            except urllib.error.URLError:
                continue
        if status is None:
            raise StudioError("视频工作台启动中，请稍后重试")
    if status.get("service") != "video-studio" or status.get("api_version") != 1:
        raise StudioError("8900 端口不是 Video Studio 服务")
    return status


def validate_arguments(definition, arguments):
    spec = definition["inputSchema"]
    if not isinstance(arguments, dict) or any(key not in spec["properties"] for key in arguments):
        raise StudioError("工具参数无效")
    for key in spec.get("required", []):
        if key not in arguments:
            raise StudioError("缺少参数：" + key)
    if "oneOf" in spec and sum(all(key in arguments for key in option.get("required", [])) for option in spec["oneOf"]) != 1:
        raise StudioError("请指定单个任务或 1 至 12 个任务列表，二选一")
    if "anyOf" in spec and not any(all(key in arguments for key in option.get("required", [])) for option in spec["anyOf"]):
        raise StudioError("请提供 prompt 或 user_prompt 作为本次视频内容")
    if "prompt" in arguments and "user_prompt" in arguments and arguments["prompt"] != arguments["user_prompt"]:
        raise StudioError("prompt 与 user_prompt 内容不同，请只提交本次内容一次")
    types = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list}
    for key, value in arguments.items():
        field = spec["properties"][key]
        if not isinstance(value, types[field["type"]]) or (field["type"] in ("integer", "number") and isinstance(value, bool)):
            raise StudioError("参数类型无效：" + key)
        if "enum" in field and value not in field["enum"]:
            raise StudioError("参数选项无效：" + key)
        if isinstance(value, str) and not field.get("minLength", 0) <= len(value) <= field.get("maxLength", 32768):
            raise StudioError("参数长度无效：" + key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and not field.get("minimum", -float("inf")) <= value <= field.get("maximum", float("inf")):
            raise StudioError("参数范围无效：" + key)
        if isinstance(value, list):
            if not field.get("minItems", 0) <= len(value) <= field.get("maxItems", 1000):
                raise StudioError("任务列表需包含 1 至 12 项")
            item_spec = field.get("items", {})
            if any(not isinstance(item, str) or not item_spec.get("minLength", 0) <= len(item) <= item_spec.get("maxLength", 32768) for item in value):
                raise StudioError("任务列表中的路径无效")
            if field.get("uniqueItems") and len(set(value)) != len(value):
                raise StudioError("每批任务不能重复")


def _wechat_scope():
    # The bridge sets this only on its own ACP child. An explicit MCP cwd in
    # the global agent config can otherwise hide the originating workspace.
    if os.environ.get("VIDEO_STUDIO_PROMPT_PROFILE") == "wechat":
        return True
    workspace = studio_config.wechat_workspace()
    if workspace is None:
        return False
    try:
        return os.path.normcase(str(Path.cwd().resolve())) == os.path.normcase(str(workspace.resolve()))
    except OSError:
        return False


def call_tool(name, arguments):
    definition = next((item for item in TOOLS if item["name"] == name), None)
    if not definition:
        raise StudioError("工具不存在")
    validate_arguments(definition, arguments)
    wechat_scope = _wechat_scope()
    if name == "video_restore_strata" and wechat_scope:
        raise StudioError("微信工作区的 Strata 已永久停用，不执行恢复操作")
    if name == "video_prepare" and wechat_scope:
        # Only the bridge's dedicated Kimi workspace gets the persisted prefix.
        # Other agents and the native studio keep the legacy single-prompt path.
        arguments = dict(arguments)
        arguments.setdefault("prompt_profile", "wechat")
    status = ensure_service()
    if name == "video_status":
        return status
    route, write = ROUTES[name]
    try:
        return json_request("http://127.0.0.1:8900" + route, arguments if write else None,
                            {"X-Video-Studio": "1"}, timeout=30)
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read(4096)).get("error", "操作未完成")
        except ValueError:
            detail = "操作未完成"
        raise StudioError(str(detail)[:300])
    except (OSError, ValueError):
        raise StudioError("请求未确认，请查看工作台状态；未自动重复提交")


def rpc_error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def handle(request):
    if not isinstance(request, dict) or request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str):
        return rpc_error(None, -32600, "Invalid Request")
    if "id" not in request:
        return None
    request_id = request["id"]
    method, params = request["method"], request.get("params", {})
    if not isinstance(params, dict):
        return rpc_error(request_id, -32602, "Invalid params")
    if method == "initialize":
        requested = params.get("protocolVersion")
        result = {"protocolVersion": requested if requested in VERSIONS else VERSION,
                  "capabilities": {"tools": {"listChanged": False}},
                  "serverInfo": {"name": "video-studio", "version": "1.0.0"},
                  "instructions": "Prepare workflows first; starting a batch stops the local Strata backend. Never submit generation without the user's intent. API keys are configured only in the native Video Studio app."}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        try:
            value = call_tool(params.get("name"), params.get("arguments", {}))
            result = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}], "isError": False}
        except StudioError as exc:
            result = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
        except Exception:
            result = {"content": [{"type": "text", "text": "工具暂时不可用，请检查 Video Studio"}], "isError": True}
    else:
        return rpc_error(request_id, -32601, "Method not found")
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def main():
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    while True:
        line = sys.stdin.readline(4 * 1024 * 1024 + 1)
        if not line:
            break
        if len(line) > 4 * 1024 * 1024:
            answer = rpc_error(None, -32600, "Request too large")
            print(json.dumps(answer), flush=True)
            break
        try:
            request = json.loads(line)
            if isinstance(request, list):
                answer = [r for r in (handle(item) for item in request) if r is not None] if request else rpc_error(None, -32600, "Invalid Request")
                if answer == []:
                    continue
            else:
                answer = handle(request)
        except (ValueError, TypeError):
            answer = rpc_error(None, -32700, "Parse error")
        if answer is not None:
            print(json.dumps(answer, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
