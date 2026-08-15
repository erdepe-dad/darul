from __future__ import annotations

import unittest

from graph_engine.hooks.context_inject import render_context
from graph_engine.stitcher import normalize_url, stitch_endpoints


class StitchDB:
    def __init__(self) -> None:
        self.query = ""
        self.parameters: dict = {}

    def execute_write(self, query: str, **parameters):
        self.query = query
        self.parameters = parameters
        return [{"stitched": 3}]


class NormalizationTests(unittest.TestCase):
    def test_parameter_dialects_normalize_equally(self) -> None:
        values = [
            "/api/v1/users/${userId}",
            "/api/v1/users/:id",
            "/api/v1/users/{user_id}",
            "/api/v1/users/[id]",
        ]
        self.assertEqual({normalize_url(value) for value in values}, {"/api/v1/users/{param}"})

    def test_absolute_urls_drop_origin_query_and_trailing_slash(self) -> None:
        self.assertEqual(normalize_url("https://service.test/api/users/?active=1"), "/api/users")

    def test_absolute_urls_with_a_leading_slash_drop_the_origin(self) -> None:
        self.assertEqual(
            normalize_url("/http://localhost:40000/api/users?active=1"),
            "/api/users",
        )

    def test_stitching_revisits_requests_from_all_repositories(self) -> None:
        db = StitchDB()

        stitched = stitch_endpoints(db)

        self.assertEqual(stitched, 3)
        self.assertIn("MATCH (a:APIEndpoint)", db.query)
        self.assertNotIn("repo_name", db.query)
        self.assertEqual(db.parameters, {})


class ContextRenderingTests(unittest.TestCase):
    def test_decision_lineage_is_delineated(self) -> None:
        rendered = render_context(
            [
                {
                    "active_decision": "Use Redis",
                    "rationale": "Lower latency",
                    "affected_files": ["src/session.ts"],
                    "superseded_decision": "Use Postgres",
                }
            ],
            [],
        )
        self.assertTrue(rendered.startswith("--- GRAPH KNOWLEDGE CONTEXT ---"))
        self.assertIn("Supersedes: Use Postgres", rendered)
        self.assertTrue(rendered.endswith("--------------------------------"))

    def test_unavailable_database_still_returns_context(self) -> None:
        rendered = render_context([], [], "connection refused")
        self.assertIn("No active decisions recorded", rendered)
        self.assertIn("Graph unavailable: connection refused", rendered)


if __name__ == "__main__":
    unittest.main()
