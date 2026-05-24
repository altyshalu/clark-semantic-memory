import os
import tempfile
import unittest

from clark_semantic_memory import ClarkSemanticMemory


class ClarkSemanticMemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "clark.sqlite3")
        self.memory = ClarkSemanticMemory(db_path=self.db_path)

    def tearDown(self):
        self.memory.close()
        self.tmpdir.cleanup()

    def test_persists_project_tool_rule_entries(self):
        self.memory.remember_project(
            "A Zone prepares startups before investor matching.",
            project="a-zone",
            canonical_key="project:a-zone:mission",
        )
        self.memory.remember_tool(
            "Hermes is the runtime behind Jessica.",
            tool_name="Hermes",
            canonical_key="tool:hermes:runtime",
        )
        self.memory.remember_rule(
            "Never overwrite user-owned files without approval.",
            rule_name="file-safety",
            canonical_key="rule:file-safety",
        )
        entries = self.memory.list_entries()
        self.assertEqual(len(entries), 3)
        self.assertEqual({entry["kind"] for entry in entries}, {"project", "tool", "rule"})

    def test_query_returns_semantic_runtime_knowledge(self):
        self.memory.remember_tool(
            "Hermes is the runtime behind Jessica.",
            tool_name="Hermes",
            canonical_key="tool:hermes:runtime",
        )
        result = self.memory.query("What runtime powers Jessica?")
        self.assertTrue(result["results"])
        self.assertEqual(result["results"][0]["canonical_key"], "tool:hermes:runtime")

    def test_context_includes_rules(self):
        self.memory.remember_rule(
            "Never overwrite user-owned files without approval.",
            rule_name="file-safety",
            canonical_key="rule:file-safety",
        )
        context = self.memory.session_context()
        self.assertIn("[Rules]", context)
        self.assertIn("Never overwrite", context)

    def test_upsert_by_canonical_key(self):
        self.memory.remember_project(
            "Clark memory stores durable project truths.",
            project="clark",
            canonical_key="project:clark:mission",
        )
        self.memory.remember_project(
            "Clark memory stores durable project truths with reinforcement.",
            project="clark",
            canonical_key="project:clark:mission",
        )
        entries = self.memory.list_entries(kind="project")
        self.assertEqual(len(entries), 1)
        self.assertIn("reinforcement", entries[0]["statement"])


if __name__ == "__main__":
    unittest.main()
