"""Windows integration tests against disposable homes; never use the real Codex home."""
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "skill/codex-chat-transfer/scripts/codex_chat_transfer.ps1"
PWSH = os.environ.get("CCT_TEST_PWSH") or shutil.which("pwsh")


@unittest.skipUnless(os.name == "nt" and PWSH, "Requires Windows and PowerShell 7")
class TransferCliTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="transfer-cli-test-", dir=os.environ.get("CCT_TEST_ROOT")))
        self.home = self.root / "codex"
        self.storage = self.root / "storage"
        self.project = self.root / "project"
        self.project.mkdir()
        self.thread = "22222222-2222-4222-8222-222222222222"
        session_dir = self.home / "sessions/2026/10/08"
        session_dir.mkdir(parents=True)
        self.rollout = session_dir / ("rollout-2026-10-08T10-00-00-" + self.thread + ".jsonl")
        records = [
            {"timestamp": "2026-10-08T10:00:00Z", "type": "session_meta", "payload": {"id": self.thread, "cwd": str(self.project), "source": "cli", "thread_source": "user", "model_provider": "openai"}},
            {"timestamp": "2026-10-08T10:00:01Z", "type": "event_msg", "payload": {"type": "user_message", "message": "Small synthetic transfer fixture"}},
        ]
        self.rollout.write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")
        with closing(sqlite3.connect(self.home / "state_5.sqlite")) as connection, connection:
            connection.execute("CREATE TABLE threads(id TEXT PRIMARY KEY,title TEXT,cwd TEXT,updated_at INTEGER,archived INTEGER,thread_source TEXT)")
            connection.execute("INSERT INTO threads VALUES(?,?,?,?,?,?)", (self.thread, "Synthetic title", str(self.project), 1791453601, 0, "user"))
        (self.home / ".codex-global-state.json").write_text(json.dumps({"local-projects": {}}))
        (self.home / "chat-transfer-settings.json").write_text(json.dumps({"TransferRoot": str(self.storage)}))
        memories = self.home / "memories/nested"
        memories.mkdir(parents=True)
        (memories / "note.md").write_text("Synthetic test memory.\n")

    def call(self, action, *extra, home=None, expect_success=True):
        environment = os.environ.copy()
        environment.pop("CODEX_CHAT_TRANSFER_ROOT", None)
        process = subprocess.run([PWSH, "-NoProfile", "-File", str(SCRIPT), "-Action", action,
                                  "-CodexHome", str(home or self.home), *map(str, extra)],
                                 env=environment, capture_output=True, encoding="utf-8", errors="replace")
        if expect_success:
            self.assertEqual(process.returncode, 0, process.stderr)
            return json.loads(process.stdout)
        self.assertNotEqual(process.returncode, 0)
        return process

    def test_default_export_inspect_and_multiple_package_selection(self):
        exported = self.call("Export", "-ThreadId", self.thread)
        folder = Path(exported["TransferFolder"])
        self.assertEqual(folder.parent, self.storage)
        self.assertEqual(exported["MemoryFileCount"], 1)
        manifest = json.loads((folder / "manifest.json").read_text())
        self.assertEqual(manifest["Memories"]["Files"][0]["Path"], "memories/nested/note.md")
        inspected = self.call("Inspect")
        self.assertEqual(inspected["Chats"][0]["Diff"]["identical"], 1)
        self.call("Export", "-ThreadId", self.thread)
        ambiguous = self.call("Import", "-Apply")
        self.assertEqual(ambiguous["Status"], "selection-required")
        self.assertEqual(len(ambiguous["Packages"]), 2)
        self.assertFalse((self.home / "chat-transfer-receipts").exists())

    def test_empty_storage_is_read_only_and_relative_escape_is_rejected(self):
        result = self.call("Import", "-Apply")
        self.assertEqual(result["Status"], "selection-required")
        self.assertFalse(self.storage.exists())
        self.call("Inspect", "-TransferFolder", "../elsewhere", expect_success=False)

    def test_import_round_trip_into_isolated_home_and_conflicting_memory_quarantine(self):
        exported = self.call("Export", "-ThreadId", self.thread)
        destination = self.root / "destination-codex"
        (destination / "memories/nested").mkdir(parents=True)
        local_note = destination / "memories/nested/note.md"
        local_note.write_text("Keep this local memory.\n")
        result = self.call("Import", "-TransferFolder", exported["TransferFolder"], "-Apply", "-MemoryMode", "Merge", home=destination)
        self.assertEqual(result["MemoryConflicts"], 1)
        self.assertEqual(local_note.read_text(), "Keep this local memory.\n")
        imported = list((destination / "sessions").rglob("*.jsonl"))
        self.assertEqual(len(imported), 1)
        self.assertEqual(imported[0].read_bytes(), self.rollout.read_bytes())
        quarantine = list((destination / "memory-import-conflicts").rglob("note.md"))
        self.assertEqual(len(quarantine), 1)
        self.assertEqual(quarantine[0].read_text(), "Synthetic test memory.\n")

    def test_mapped_import_is_identical_on_repeated_inspection(self):
        exported = self.call("Export", "-ThreadId", self.thread)
        destination = self.root / "mapped-codex"
        destination.mkdir()
        project = self.root / "mapped-project"
        project.mkdir()
        mapping = str(self.project) + "=" + str(project)
        self.call("Import", "-TransferFolder", exported["TransferFolder"], "-Apply", "-PathMap", mapping, home=destination)
        inspected = self.call("Inspect", "-TransferFolder", exported["TransferFolder"], "-PathMap", mapping, home=destination)
        self.assertEqual(inspected["Chats"][0]["Diff"]["identical"], 1)

    def test_verify_skip_ignores_local_memory_changes_but_merge_reports_missing(self):
        exported = self.call("Export", "-ThreadId", self.thread)
        (self.home / "memories/nested/note.md").write_text("A newer local memory.\n")
        self.assertTrue(self.call("Verify")["Verified"])
        self.assertFalse(self.call("Verify", "-MemoryMode", "Merge")["Verified"])
        self.call("Import", "-TransferFolder", exported["TransferFolder"], "-Apply", "-MemoryMode", "Merge")
        verified = self.call("Verify", "-MemoryMode", "Merge")
        self.assertTrue(verified["Verified"])
        self.assertEqual(verified["Memories"][0]["Status"], "quarantined")


if __name__ == "__main__":
    unittest.main()
