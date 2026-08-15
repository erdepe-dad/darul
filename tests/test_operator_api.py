from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from graph_engine.config import Settings
from graph_engine.operator_api import OperatorService, RepositoryRegistry


class FakeDB:
    def execute_read(self, query: str, **parameters):
        if "MATCH (r:Repository)" in query:
            return [{"name": "sample-web", "root_path": "/srv/sample-web", "updated_at": None, "files": 80}]
        if "MATCH (a:APIEndpoint)" in query and "TARGETS_ROUTE" in query:
            return [{"name": "sample-web", "requests": 10, "resolved": 4}]
        if "MATCH (s:ExternalSystem)" in query:
            return [{"repo_name": "sample-web", "key": "BACKEND_API_URL"}]
        if "NOT (a)-[:TARGETS_ROUTE]" in query:
            return [{"method": "GET", "normalized_url": "/api/users"}]
        return []

    def healthcheck(self):
        return {"ok": 1}


class OperatorServiceTests(unittest.TestCase):
    def test_overview_calculates_unresolved_and_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "darul"
            root.mkdir()
            (root / ".git").mkdir()
            settings = Settings(root, "darul", "bolt://unused", "neo4j", "x", None, 1, frozenset())
            registry = RepositoryRegistry({"sample-web": root}, root.parent)
            result = OperatorService(FakeDB(), settings, registry).overview()

        self.assertEqual(result["repositories"][0]["unresolved"], 6)
        self.assertEqual(result["repositories"][0]["coverage"], 40.0)
        self.assertTrue(result["repositories"][0]["operable"])

    def test_unresolved_limit_is_bounded(self) -> None:
        service = OperatorService(FakeDB())
        result = service.unresolved("sample-web", 10_000)
        self.assertEqual(result["limit"], 500)

    def test_registry_rejects_paths_and_missing_repositories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry = RepositoryRegistry({}, Path(directory))
            with self.assertRaisesRegex(ValueError, "invalid"):
                registry.resolve("../secret")
            with self.assertRaisesRegex(ValueError, "not available"):
                registry.resolve("sample-web")


if __name__ == "__main__":
    unittest.main()
