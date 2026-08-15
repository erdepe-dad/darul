from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from graph_engine.config import Settings
from graph_engine.parser import ingest_files, parse_file


class FakeDB:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def execute_write(self, query: str, **parameters):
        self.calls.append((query, parameters))
        return []


class IngestionTests(unittest.TestCase):
    def test_ingestion_batches_globally_scoped_entities(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "service.py"
            source.write_text(
                """from fastapi import FastAPI
app = FastAPI()
@app.get('/health')
def health():
    return {'ok': True}
""",
                encoding="utf-8",
            )
            settings = Settings(root, "repo-a", "bolt://unused", "neo4j", "x", None, 1.0, frozenset())
            parsed = parse_file(source, settings)
            db = FakeDB()
            ingest_files(db, [parsed], settings, replace_all=True)

        all_rows = [row for _, parameters in db.calls for row in parameters.get("rows", [])]
        ids = [row["id"] for row in all_rows if "id" in row]
        self.assertTrue(ids)
        self.assertTrue(all(item.startswith("repo-a:") for item in ids))
        self.assertTrue(any("BackendRoute" in query for query, _ in db.calls))

        clear_query, _ = db.calls[0]
        self.assertNotIn("n:CodeFile", clear_query)
        prune_query, prune_parameters = db.calls[1]
        self.assertIn("MATCH (f:CodeFile", prune_query)
        self.assertEqual(prune_parameters["file_ids"], ["repo-a:service.py"])

    def test_workflow_starts_are_persisted_before_a_process_is_available(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "FlowService.java"
            source.write_text(
                '''import org.flowable.engine.RuntimeService;
public class FlowService {
    public void start(RuntimeService runtimeService) {
        runtimeService.startProcessInstanceByKey("late-process");
    }
}
''',
                encoding="utf-8",
            )
            settings = Settings(root, "repo-a", "bolt://unused", "neo4j", "x", None, 1.0, frozenset())
            parsed = parse_file(source, settings)
            db = FakeDB()

            ingest_files(db, [parsed], settings)

        start_call = next(
            (query, parameters)
            for query, parameters in db.calls
            if "MERGE (start:WorkflowStart" in query
        )
        self.assertEqual(start_call[1]["rows"][0]["process_key"], "late-process")
        self.assertTrue(start_call[1]["rows"][0]["id"].startswith("repo-a:"))
        self.assertTrue(
            any(
                "MATCH (source:Function)-[:DECLARES_START]->(start:WorkflowStart)" in query
                for query, _ in db.calls
            )
        )


if __name__ == "__main__":
    unittest.main()
