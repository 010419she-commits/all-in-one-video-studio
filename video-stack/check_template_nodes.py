r"""Check every node class a bundled template uses against the live object_info.

Catches the case where a template names a class this ComfyUI build does not
register, which otherwise only shows up as a red node in the web UI.
"""

import json
import os
import sys

import studio_config

TPL_DIR = str(studio_config.backend_python().parent.parent /
              "Lib" / "site-packages" / "comfyui_workflow_templates_json" / "templates")
HERE = os.path.dirname(os.path.abspath(__file__))

info = json.load(open(os.path.join(HERE, "object_info.json"), encoding="utf-8"))

NOTE_TYPES = {"Note", "MarkdownNote"}


def collect(node, out):
    out.append((node.get("id"), node.get("type"), node.get("widgets_values")))


for name in sys.argv[1:]:
    path = os.path.join(TPL_DIR, name if name.endswith(".json") else name + ".json")
    wf = json.load(open(path, encoding="utf-8"))
    nodes = list(wf.get("nodes") or [])
    subgraphs = (wf.get("definitions") or {}).get("subgraphs") or []
    print("== %s  (%d top nodes, %d subgraphs)" % (os.path.basename(path), len(nodes), len(subgraphs)))
    for sg in subgraphs:
        print("   subgraph %r (%d nodes)" % (sg.get("name"), len(sg.get("nodes") or [])))
        nodes.extend(sg.get("nodes") or [])
    missing = []
    for node_id, cls, widgets in [(n[0], n[1], n[2]) for n in nodes]:
        if cls not in info:
            missing.append((node_id, cls))
        spec = info.get(cls, {}).get("input", {})
        print("  %-40s widgets=%-3s required=%-3s optional=%s"
              % (cls, "-" if widgets is None else len(widgets),
                 len(spec.get("required") or {}), len(spec.get("optional") or {})))
    real_missing = [(i, c) for i, c in missing if c not in NOTE_TYPES]
    if real_missing:
        print("  MISSING CLASSES: %s" % ", ".join("%s(%s)" % (c, i) for i, c in real_missing))
    else:
        print("  all real node classes present (Note/MarkdownNote are frontend-only comments)")
