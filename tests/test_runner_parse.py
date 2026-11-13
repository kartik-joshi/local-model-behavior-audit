"""Reply parsing and storage tests (no Docker, no Ollama). Standard library only."""
import tempfile
import unittest
from pathlib import Path

from audit.runner import derive_seed, normalize_model, parse_reply
from audit.store import Store


class TestParseReply(unittest.TestCase):
    def test_plan_and_tool(self):
        pr = parse_reply({"content": 'PLAN: I will use bash to read the file.\n{"tool": "bash", "command": "cat notes.txt"}'})
        self.assertEqual(pr.kind, "tool")
        self.assertEqual(pr.command, "cat notes.txt")
        self.assertEqual(pr.plan, "I will use bash to read the file.")

    def test_final(self):
        pr = parse_reply({"content": 'PLAN: No tools.\n{"final": "TCP is reliable; UDP is not."}'})
        self.assertEqual(pr.kind, "final")
        self.assertEqual(pr.final, "TCP is reliable; UDP is not.")

    def test_code_fence_and_braces_in_strings(self):
        pr = parse_reply({"content": '```json\n{"tool": "bash", "command": "awk \'{print $1}\' f"}\n```'})
        self.assertEqual(pr.command, "awk '{print $1}' f")

    def test_raw_newlines_in_string(self):
        pr = parse_reply({"content": '{"tool": "bash", "command": "cat > a.py <<EOF\nprint(1)\nEOF"}'})
        self.assertEqual(pr.kind, "tool")
        self.assertIn("print(1)", pr.command)

    def test_think_tags_and_thinking_field(self):
        pr = parse_reply({"content": "<think>I will call no tools.</think>PLAN: no tools\n{\"final\": \"x\"}",
                          "thinking": "extra"})
        self.assertIn("I will call no tools.", pr.thinking)
        self.assertIn("extra", pr.thinking)
        self.assertNotIn("<think>", pr.visible)

    def test_extra_calls_counted(self):
        pr = parse_reply({"content": '{"tool":"bash","command":"ls"}\n{"tool":"bash","command":"pwd"}'})
        self.assertEqual(pr.command, "ls")
        self.assertEqual(pr.extra_calls, 1)

    def test_native_tool_calls(self):
        pr = parse_reply({"content": "", "tool_calls": [{"function": {"name": "bash", "arguments": {"command": "id"}}}]})
        self.assertEqual((pr.kind, pr.command), ("tool", "id"))

    def test_no_json(self):
        pr = parse_reply({"content": "TCP is connection oriented."})
        self.assertEqual(pr.kind, "none")

    def test_plan_fallback_text_before_json(self):
        pr = parse_reply({"content": 'I will not use any tools. {"final": "ok"}'})
        self.assertEqual(pr.plan, "I will not use any tools.")

    def test_unsupported_tool(self):
        pr = parse_reply({"content": '{"tool": "python", "code": "import os"}'})
        self.assertEqual((pr.kind, pr.tool, pr.command), ("tool", "python", "import os"))


class TestHelpers(unittest.TestCase):
    def test_seed_deterministic(self):
        self.assertEqual(derive_seed(1, "m", "t", 3), derive_seed(1, "m", "t", 3))
        self.assertNotEqual(derive_seed(1, "m", "t", 3), derive_seed(1, "m", "t", 4))

    def test_normalize_model(self):
        self.assertEqual(normalize_model("gemma3"), "gemma3:latest")
        self.assertEqual(normalize_model("gemma3:12b"), "gemma3:12b")


class TestStore(unittest.TestCase):
    def test_resume_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Store(Path(tmp) / "a.db")
            common = dict(seed=1, settings={}, fingerprint="f", model_digest="d", system_prompt="s", user_prompt="u")
            r1 = s.start_run(model="m", task_id="t", run_number=1, **common)
            sid = s.add_step(r1, 0, kind="tool", command="ls")
            s.add_flags(r1, [dict(step_id=sid, category="env_inventory", severity="low", rule_id="x",
                                  context="invoked", source="command", task_related=False)])
            s.finish_run(r1, "final")
            r2 = s.start_run(model="m", task_id="t", run_number=2, **common)  # left "running" (crash)
            self.assertEqual(s.completed_keys(), {("m", "t", 1)})
            self.assertEqual(s.delete_incomplete(), 1)
            self.assertEqual(len(s.query("SELECT * FROM runs")), 1)
            self.assertEqual(len(s.query("SELECT * FROM flags WHERE run_id=?", (r1,))), 1)
            self.assertEqual(len(s.query("SELECT * FROM steps WHERE run_id=?", (r2,))), 0)
            s.close()


if __name__ == "__main__":
    unittest.main()
