from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from graph_engine.config import Settings
from graph_engine.db import SCHEMA_QUERIES, DatabaseConnectionError, GraphDB


class DatabaseConfigurationTests(unittest.TestCase):
    def test_schema_indexes_cross_repository_class_lookups(self) -> None:
        self.assertTrue(
            any(
                "Class" in query and "qualified_name" in query
                for query in SCHEMA_QUERIES
            )
        )

    def test_missing_password_fails_before_driver_connection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(
                Path(directory),
                "repo",
                "bolt://127.0.0.1:7687",
                "neo4j",
                "",
                None,
                1.0,
                frozenset(),
            )
            with self.assertRaisesRegex(DatabaseConnectionError, "GRAPH_DB_PASS"):
                GraphDB(settings).connect()


if __name__ == "__main__":
    unittest.main()
