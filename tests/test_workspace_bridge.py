import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing

SCRIPTS = Path(__file__).resolve().parents[1] / "skill/codex-chat-transfer/scripts"
sys.path.insert(0, str(SCRIPTS))
import codex_workspace_bridge as bridge
from workspace_index import metadata


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="chat-transfer-test-", dir=os.environ.get("CCT_TEST_ROOT")))
        self.home = self.root / "codex"
        self.home.mkdir()
        self.project = self.root / "源论文项目"
        self.project.mkdir()
        self.dest = self.root / "目标论文项目"
        self.dest.mkdir()
        self.thread = "11111111-1111-4111-8111-111111111111"

    def prepare(self, modern=False, explicit=False):
        self.db = self.home / "state_5.sqlite"
        with closing(sqlite3.connect(self.db)) as connection, connection:
            extras = ",name TEXT,project_id TEXT" if modern else ""
            connection.execute("CREATE TABLE threads(id TEXT PRIMARY KEY,title TEXT,cwd TEXT,updated_at INTEGER,archived INTEGER,thread_source TEXT" + extras + ")")
            connection.execute("INSERT INTO threads(id,title,cwd,updated_at,archived,thread_source) VALUES(?,?,?,?,?,?)", (self.thread, "initial prompt", str(self.project), 1000, 0, "user"))
            if modern:
                connection.execute("UPDATE threads SET name=? WHERE id=?", ("主工作对话", self.thread))
                connection.execute("CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT,metadata TEXT,position INTEGER,created_at_ms INTEGER,updated_at_ms INTEGER)")
                connection.execute("CREATE TABLE project_roots(project_id TEXT,position INTEGER,path TEXT,PRIMARY KEY(project_id,position))")
                connection.execute("INSERT INTO projects VALUES('native-project','Research','{}',0,0,0)")
                connection.execute("INSERT INTO project_roots VALUES('native-project',0,?)", (str(self.project),))
                if explicit:
                    connection.execute("UPDATE threads SET project_id='native-project'")
        state = {"local-projects": {"legacy-project": {"name": "Research", "rootPaths": [str(self.project)]}}, "thread-project-assignments": {}}
        (self.home / ".codex-global-state.json").write_text(json.dumps(state), encoding="utf-8")
        (self.home / "session_index.jsonl").write_text(json.dumps({"id": self.thread, "thread_name": "主工作对话"}, ensure_ascii=False) + "\n", encoding="utf-8")

    def snapshot(self):
        return bridge.snapshot(self.home, [self.thread], self.root / "workspace.json")

    def test_legacy_title_and_implicit_project(self):
        self.prepare()
        result = self.snapshot()
        self.assertEqual(result["Threads"][0]["Title"], "主工作对话")
        self.assertEqual(result["Threads"][0]["ProjectAssociationBasis"], "unique-root-match")
        self.assertEqual(len(result["Projects"]), 1)
        self.assertTrue(bridge.verify(self.home, result, [])["Verified"])

    def test_modern_title_beats_stale_legacy_index(self):
        self.prepare(modern=True, explicit=True)
        (self.home / "session_index.jsonl").write_text(json.dumps({"id": self.thread, "thread_name": "stale"}) + "\n")
        result = self.snapshot()
        self.assertEqual(result["Threads"][0]["Title"], "主工作对话")
        self.assertEqual(result["Threads"][0]["SourceProjectId"], "native-project")
        self.assertTrue(bridge.verify(self.home, result, [])["Verified"])

    def test_duplicate_native_and_legacy_roots_are_one_project(self):
        self.prepare(modern=True)
        result = self.snapshot()
        self.assertEqual(result["Threads"][0]["SourceProjectId"], "native-project")
        self.assertEqual(len(result["Projects"]), 1)

    def test_explicit_projectless_is_preserved(self):
        self.prepare(modern=True)
        path = self.home / ".codex-global-state.json"
        state = json.loads(path.read_text())
        state["projectless-thread-ids"] = [self.thread]
        path.write_text(json.dumps(state))
        self.assertIsNone(self.snapshot()["Threads"][0]["SourceProjectId"])

    def test_missing_index_still_returns_title_metadata(self):
        (self.home / "session_index.jsonl").write_text(json.dumps({"id": self.thread, "thread_name": "Saved title"}) + "\n")
        self.assertEqual(metadata(self.home, [self.thread])["Threads"][0]["Title"], "Saved title")

    def test_missing_destination_is_rejected_before_restore(self):
        self.prepare()
        result = self.snapshot()
        missing = self.root / "missing"
        with self.assertRaisesRegex(RuntimeError, "do not exist"):
            bridge.restore(self.home, result, [str(self.project) + "=" + str(missing)], None)

    def test_legacy_restore_maps_paths_and_updates_display_index(self):
        self.prepare()
        result = self.snapshot()
        result["Threads"][0]["Title"] = "New display title"
        maps = [str(self.project) + "=" + str(self.dest)]
        self.assertTrue(bridge.restore(self.home, result, maps, None)["Verified"])
        self.assertTrue(bridge.verify(self.home, result, maps)["Verified"])

    def test_modern_restore_updates_native_name_and_project(self):
        self.prepare(modern=True, explicit=True)
        result = self.snapshot()
        result["Threads"][0]["Title"] = "Restored title"
        maps = [str(self.project) + "=" + str(self.dest)]
        self.assertTrue(bridge.restore(self.home, result, maps, None)["Verified"])
        with closing(sqlite3.connect(self.db)) as connection, connection:
            name, project_id = connection.execute("SELECT name,project_id FROM threads WHERE id=?", (self.thread,)).fetchone()
            self.assertEqual(name, "Restored title")
            self.assertEqual(connection.execute("SELECT path FROM project_roots WHERE project_id=?", (project_id,)).fetchone()[0], str(self.dest))
        self.assertTrue(bridge.verify(self.home, result, maps)["Verified"])


if __name__ == "__main__":
    unittest.main()
