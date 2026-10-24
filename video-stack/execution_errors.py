"""Bounded, privacy-safe summaries of ComfyUI history failures.

ComfyUI error records can contain the complete graph, prompts and credentials.
Only fixed labels, strictly validated identifiers and extracted memory sizes may
cross into a user-facing error. Never stringify a record or a traceback here.
"""

import ntpath
import re


_NODE_ID = re.compile(r"[0-9]{1,12}(?::[0-9]{1,12}){0,3}\Z")
_NODE_TYPE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z")
_EXCEPTION_NAME = re.compile(r"(?:[A-Za-z_][A-Za-z0-9_]*\.){0,6}([A-Za-z_][A-Za-z0-9_]{0,63})\Z")
_WORKFLOW_NAME = re.compile(r"[A-Za-z0-9_\u3400-\u9fff][A-Za-z0-9_\u3400-\u9fff .()\-]{0,95}\Z")
_SENSITIVE_NAME = re.compile(r"(?:secret|password|passwd|bearer|token|apikey|api_key|sk_)", re.I)
_CUDA_OOM = re.compile(r"\bCUDA\s+out\s+of\s+memory\b", re.I)
_MEMORY_SIZE = r"([0-9]{1,5}(?:\.[0-9]{1,3})?)\s+(GiB|MiB|KiB)\b"
_ALLOCATION = re.compile(r"\bTried\s+to\s+allocate\s+" + _MEMORY_SIZE, re.I)
_FREE = re.compile(r"\bof\s+which\s+" + _MEMORY_SIZE + r"\s+is\s+free\b", re.I)
_UNITS = {"gib": "GiB", "mib": "MiB", "kib": "KiB"}
_OOM_TYPES = {"torch.OutOfMemoryError", "torch.cuda.OutOfMemoryError", "OutOfMemoryError"}
_EXCEPTION_CATEGORIES = {
    "RuntimeError", "ValueError", "TypeError", "KeyError", "IndexError",
    "AttributeError", "AssertionError", "FileNotFoundError", "PermissionError",
    "OSError", "IOError", "ImportError", "ModuleNotFoundError",
    "NotImplementedError", "OverflowError", "ZeroDivisionError", "TimeoutError",
    "ConnectionError", "MemoryError", "OutOfMemoryError", "Exception", "Error",
}


def _fallback(workflow_name):
    name = ""
    if isinstance(workflow_name, str) and len(workflow_name) <= 4096:
        # ntpath also handles Windows paths when tests run on another OS.
        candidate = ntpath.basename(workflow_name.replace("/", "\\"))
        if _WORKFLOW_NAME.fullmatch(candidate) and not _SENSITIVE_NAME.search(candidate):
            name = candidate
    return "ComfyUI 执行失败" + ("：" + name if name else "")


def _memory_size(pattern, message):
    match = pattern.search(message)
    if match:
        return match.group(1) + " " + _UNITS[match.group(2).lower()]
    return ""


def _safe_field(value, pattern):
    if isinstance(value, str) and pattern.fullmatch(value) and not _SENSITIVE_NAME.search(value):
        return value
    return ""


def summarize_execution_error(status, workflow_name=""):
    """Return a Chinese summary (at most 240 characters) without raw errors.

    ``status`` is the history entry's ``status`` object, not the full history
    entry. The final execution_error takes precedence over earlier errors.
    Malformed records fall back safely; interruption is reported when there is
    no execution_error. This function neither changes state nor retries work.
    """
    fallback = _fallback(workflow_name)
    if not isinstance(status, dict):
        return fallback
    messages = status.get("messages")
    if not isinstance(messages, (list, tuple)):
        return fallback

    error = None
    interrupted = False
    found_error = False
    for item in reversed(messages):
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        kind = item[0]
        if kind == "execution_error":
            found_error = True
            error = item[1] if isinstance(item[1], dict) else None
            break
        if kind == "execution_interrupted":
            interrupted = True
    if not found_error:
        return "生成已由用户取消" if interrupted else fallback
    if error is None:
        return fallback

    raw_type = error.get("exception_type")
    message = error.get("exception_message")
    # Do not coerce arbitrary payloads to strings. Message text is used only
    # for classification and numeric extraction, never as display content.
    message = message if isinstance(message, str) else ""
    if (isinstance(raw_type, str) and raw_type in _OOM_TYPES) or _CUDA_OOM.search(message):
        allocated = _memory_size(_ALLOCATION, message)
        free = _memory_size(_FREE, message)
        details = []
        if allocated:
            details.append("本次需分配 " + allocated)
        if free:
            details.append("可用仅 " + free)
        return ("显存不足" + ("：" + "，".join(details) if details else "")
                + "。请缩短单段时长、降低分辨率或减少参考图。")

    details = []
    node_id = error.get("node_id")
    if isinstance(node_id, int) and not isinstance(node_id, bool) and 0 <= node_id <= 999999999999:
        node_id = str(node_id)
    node_id = _safe_field(node_id, _NODE_ID)
    node_type = _safe_field(error.get("node_type"), _NODE_TYPE)
    if node_id:
        details.append("节点 " + node_id)
    if node_type:
        details.append(node_type)
    # Canonical categories, rather than arbitrary class names, prevent error
    # metadata being used to smuggle prompts or credentials into the summary.
    if isinstance(raw_type, str) and len(raw_type) <= 256:
        match = _EXCEPTION_NAME.fullmatch(raw_type)
        if match and match.group(1) in _EXCEPTION_CATEGORIES:
            details.append(match.group(1))
    result = "ComfyUI 执行失败：" + " / ".join(details) if details else fallback
    return result[:240]
