from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from graph_engine.config import Settings
from graph_engine.sync import Change, git_changes, sync_changes


class FakeDB:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def execute_write(self, query: str, **parameters):
        self.calls.append((query, parameters))
        if "RETURN count(st) AS stitched" in query:
            return [{"stitched": 0}]
        return []


def settings_for(root: Path) -> Settings:
    return Settings(root, "repo", "bolt://unused", "neo4j", "x", None, 1.0, frozenset())


class SyncTests(unittest.TestCase):
    @patch("graph_engine.sync.subprocess.run")
    def test_git_changes_handles_renames(self, run) -> None:
        run.return_value.stdout = "M\tsrc/app.py\nD\told.py\nR100\tbefore.ts\tafter.ts\n"
        changes = git_changes(settings_for(Path.cwd()))
        self.assertEqual(changes[2], Change("R", "after.ts", "before.ts"))

    def test_sync_replaces_only_changed_subtrees(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text("def current():\n    return 1\n", encoding="utf-8")
            db = FakeDB()
            result = sync_changes(
                db,
                settings_for(root),
                changes=[Change("M", "app.py"), Change("D", "removed.ts")],
            )

        self.assertEqual(result.added_or_modified, ["app.py"])
        self.assertEqual(result.deleted, ["removed.ts"])
        deleted_ids = [parameters.get("file_id") for _, parameters in db.calls if "file_id" in parameters]
        self.assertEqual(deleted_ids, ["repo:app.py", "repo:removed.ts"])


if __name__ == "__main__":
    unittest.main()
