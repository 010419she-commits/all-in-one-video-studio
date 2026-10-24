"""Error-summary regressions; no network, subprocesses, or GPU execution."""

import copy
import unittest

from execution_errors import summarize_execution_error


def history_error(**data):
    return {"status_str": "error", "completed": False,
            "messages": [["execution_start", {"prompt_id": "private-id"}],
                         ["execution_error", data]]}


class ExecutionErrorSummaryTests(unittest.TestCase):
    def test_real_torch_cuda_oom_extracts_only_safe_memory_sizes(self):
        state = history_error(
            node_id="70", node_type="LTXVSampler", exception_type="torch.OutOfMemoryError",
            exception_message=("CUDA out of memory. Tried to allocate 5.30 GiB. "
                               "GPU 0 has a total capacity of 31.36 GiB of which 2.99 GiB is free. "
                               "Process has 28.37 GiB memory in use. https://private/api?key=sk-sensitive"),
            traceback=["Traceback (most recent call last):", "super-secret traceback"],
            current_inputs={"prompt": "private prompt", "api_key": "sk-sensitive"})
        before = copy.deepcopy(state)
        summary = summarize_execution_error(state, "workflow.json")
        self.assertEqual(summary, "显存不足：本次需分配 5.30 GiB，可用仅 2.99 GiB。请缩短单段时长、降低分辨率或减少参考图。")
        self.assertEqual(state, before)
        self.assertLessEqual(len(summary), 240)

    def test_oom_type_without_message_is_still_actionable(self):
        for error_type in ("torch.OutOfMemoryError", "torch.cuda.OutOfMemoryError", "OutOfMemoryError"):
            with self.subTest(error_type=error_type):
                self.assertEqual(summarize_execution_error(history_error(exception_type=error_type)),
                                 "显存不足。请缩短单段时长、降低分辨率或减少参考图。")

    def test_cuda_oom_in_runtime_error_and_case_insensitive_units(self):
        summary = summarize_execution_error(history_error(
            exception_type="RuntimeError", exception_message=(
                "cuda OUT of memory. Tried to allocate 128.00 mib. of which 64.00 MiB is free.")))
        self.assertIn("128.00 MiB", summary)
        self.assertIn("64.00 MiB", summary)
        self.assertTrue(summary.startswith("显存不足"))

    def test_cpu_memory_error_is_not_mislabeled_as_gpu_memory(self):
        summary = summarize_execution_error(history_error(exception_type="MemoryError"))
        self.assertEqual(summary, "ComfyUI 执行失败：MemoryError")

    def test_uses_last_error_not_first_or_last_arbitrary_message(self):
        state = {"messages": [["execution_error", {"exception_type": "torch.OutOfMemoryError"}],
                              ["execution_error", {"node_id": "42", "node_type": "VAEDecode",
                                                   "exception_type": "builtins.ValueError"}],
                              ["execution_success", {}]]}
        self.assertEqual(summarize_execution_error(state), "ComfyUI 执行失败：节点 42 / VAEDecode / ValueError")

    def test_interruption_is_reported_without_raw_payload(self):
        state = {"messages": [["execution_interrupted", {"prompt": "SECRET", "node_type": "SECRET"}]]}
        self.assertEqual(summarize_execution_error(state), "生成已由用户取消")

    def test_older_error_keeps_precedence_over_interrupt(self):
        state = {"messages": [["execution_error", {"exception_type": "RuntimeError"}],
                              ["execution_interrupted", {}]]}
        self.assertEqual(summarize_execution_error(state), "ComfyUI 执行失败：RuntimeError")

    def test_malformed_status_and_messages_always_fall_back(self):
        cases = [None, [], "secret", True, {}, {"messages": None}, {"messages": "secret"},
                 {"messages": {}}, {"messages": [None, [], ["execution_error"], "bad", {}, 42]},
                 {"messages": [["execution_error", "secret"]]},
                 {"messages": [["execution_error", {"exception_message": {"secret": "secret"}}]]}]
        for state in cases:
            with self.subTest(state=state):
                self.assertEqual(summarize_execution_error(state, r"C:\private\workflows\h3.json"),
                                 "ComfyUI 执行失败：h3.json")

    def test_malformed_latest_error_does_not_resurrect_older_error(self):
        state = {"messages": [["execution_error", {"exception_type": "torch.OutOfMemoryError"}],
                              ["execution_error", None]]}
        self.assertEqual(summarize_execution_error(state), "ComfyUI 执行失败")

    def test_unknown_raw_error_never_displays_message_traceback_inputs_prompt_key_or_url(self):
        secret = "Bearer sk-SECRET https://example.invalid/private?api_key=SECRET prompt=PRIVATE"
        summary = summarize_execution_error(history_error(
            node_id="42", node_type="KSampler", exception_type="RuntimeError",
            exception_message=secret, traceback=[secret], current_inputs={"prompt": secret, "api_key": secret},
            prompt={"1": secret}, url=secret))
        self.assertEqual(summary, "ComfyUI 执行失败：节点 42 / KSampler / RuntimeError")

    def test_untrusted_node_identifiers_and_types_are_rejected(self):
        cases = [("42\nSECRET", "KSampler\nhttps://example.invalid"),
                 (True, "sk_SECRET"), ("api_key_SECRET", "private_secret"),
                 ({"secret": "x"}, ["secret"]), ("1", "X" * 1000)]
        for node_id, node_type in cases:
            with self.subTest(node_id=node_id, node_type=node_type):
                summary = summarize_execution_error(history_error(
                    node_id=node_id, node_type=node_type, exception_type="ValueError"))
                self.assertNotIn("SECRET", summary)
                self.assertNotIn("secret", summary)
                self.assertNotIn("https", summary)
                self.assertNotIn("X" * 65, summary)
                self.assertLessEqual(len(summary), 240)

    def test_only_known_exception_category_is_exposed(self):
        for error_type in ("private_prompt_SECRET", "https://private/RuntimeError", "sk_SECRETError",
                           "ValueError\nprivate prompt", {"secret": "RuntimeError"}, "A" * 1000):
            with self.subTest(error_type=error_type):
                self.assertEqual(summarize_execution_error(history_error(exception_type=error_type)),
                                 "ComfyUI 执行失败")

    def test_safe_numeric_subgraph_id_and_integer_id(self):
        for node_id in ("70:17", 70):
            with self.subTest(node_id=node_id):
                summary = summarize_execution_error(history_error(node_id=node_id, node_type="KSampler"))
                self.assertEqual(summary, "ComfyUI 执行失败：节点 " + str(node_id) + " / KSampler")

    def test_workflow_fallback_is_basename_only_and_rejects_unsafe_values(self):
        self.assertEqual(summarize_execution_error({}, "/private/workflows/h3.json"), "ComfyUI 执行失败：h3.json")
        self.assertEqual(summarize_execution_error({}, r"C:\private\workflows\视频-01.json"),
                         "ComfyUI 执行失败：视频-01.json")
        for name in ("https://example.invalid/workflow?api_key=SECRET", "private\nSECRET.json",
                     "sk_SECRET.json", "X" * 10000, None, {"secret": "workflow"}):
            with self.subTest(name=name):
                self.assertEqual(summarize_execution_error({}, name), "ComfyUI 执行失败")

    def test_huge_or_malicious_memory_text_never_becomes_display_text(self):
        summary = summarize_execution_error(history_error(
            exception_type="torch.OutOfMemoryError", exception_message=(
                "Tried to allocate 123456789 GiB of which 2.99 GiB\nPRIVATE is free; "
                "https://private SECRET")))
        self.assertEqual(summary, "显存不足。请缩短单段时长、降低分辨率或减少参考图。")


if __name__ == "__main__":
    unittest.main()
