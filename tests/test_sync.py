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
        self.read_rows: list[dict] = []

    def execute_write(self, query: str, **parameters):
        self.calls.append((query, parameters))
        if "RETURN count(st) AS stitched" in query:
            return [{"stitched": 0}]
        return []

    def execute_read(self, query: str, **parameters):
        self.calls.append((query, parameters))
        return self.read_rows


def settings_for(root: Path, excludes: frozenset[str] = frozenset()) -> Settings:
    return Settings(root, "repo", "bolt://unused", "neo4j", "x", None, 1.0, excludes)


class SyncTests(unittest.TestCase):
    @patch("graph_engine.sync.subprocess.run")
    def test_git_changes_handles_renames(self, run) -> None:
        run.return_value.stdout = (
            "M\tsrc/app.py\nD\told.py\nR100\tbefore.ts\tafter.ts\nC100\tsource.ts\tcopy.ts\n"
        )
        changes = git_changes(settings_for(Path.cwd()))
        self.assertEqual(changes[2], Change("R", "after.ts", "before.ts"))
        self.assertEqual(changes[3], Change("C", "copy.ts", "source.ts"))

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
        deleted_file_ids = [
            parameters.get("file_id")
            for query, parameters in db.calls
            if "DETACH DELETE f" in query
        ]
        self.assertEqual(deleted_file_ids, ["repo:removed.ts"])
        self.assertEqual(
            sum("managed_by = 'callsite'" in query for query, _ in db.calls),
            1,
        )
        self.assertEqual(
            sum("old:IMPORTS" in query for query, _ in db.calls),
            1,
        )

    def test_sync_skips_excluded_directories_and_removes_stale_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            excluded = root / ".graph_engine" / "parser.py"
            excluded.parent.mkdir()
            excluded.write_text("def internal():\n    return 1\n", encoding="utf-8")
            db = FakeDB()
            result = sync_changes(
                db,
                settings_for(root, frozenset({".graph_engine"})),
                changes=[Change("M", ".graph_engine/parser.py")],
            )

        self.assertEqual(result.added_or_modified, [])
        self.assertEqual(result.skipped, [".graph_engine/parser.py"])
        deleted_file_ids = [
            parameters.get("file_id")
            for query, parameters in db.calls
            if "DETACH DELETE f" in query
        ]
        self.assertEqual(deleted_file_ids, ["repo:.graph_engine/parser.py"])

    def test_sync_copy_keeps_original_file_node(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "copy.py").write_text("def copied():\n    return 1\n", encoding="utf-8")
            db = FakeDB()
            result = sync_changes(
                db,
                settings_for(root),
                changes=[Change("C", "copy.py", "original.py")],
            )

        self.assertEqual(result.added_or_modified, ["copy.py"])
        self.assertEqual(result.deleted, [])
        deleted_file_ids = [
            parameters.get("file_id")
            for query, parameters in db.calls
            if "DETACH DELETE f" in query
        ]
        self.assertEqual(deleted_file_ids, [])

    def test_sync_non_source_changes_skip_graph_reconciliation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = FakeDB()
            result = sync_changes(
                db,
                settings_for(Path(directory)),
                changes=[Change("M", "README.md")],
            )

        self.assertEqual(result.skipped, ["README.md"])
        self.assertEqual(db.calls, [])

    def test_sync_scopes_reconciliation_to_changed_and_referencing_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "RenamedService.java"
            source.write_text(
                "package sample;\npublic class RenamedService {}\n",
                encoding="utf-8",
            )
            db = FakeDB()
            db.read_rows = [
                {
                    "name": "OldService",
                    "qualified_name": "sample.OldService",
                    "aliases": ["oldService"],
                }
            ]
            sync_changes(
                db,
                settings_for(root),
                changes=[Change("M", source.name)],
            )

        scoped_parameters = [
            parameters
            for _, parameters in db.calls
            if "class_identifiers" in parameters
        ]
        self.assertEqual(len(scoped_parameters), 3)
        for parameters in scoped_parameters:
            self.assertEqual(parameters["file_ids"], ["repo:RenamedService.java"])
            self.assertEqual(
                parameters["class_identifiers"],
                [
                    "OldService",
                    "RenamedService",
                    "oldService",
                    "renamedService",
                    "sample.OldService",
                    "sample.RenamedService",
                ],
            )


if __name__ == "__main__":
    unittest.main()
