from __future__ import annotations

import unittest

from graph_engine.hooks.context_inject import render_context
from graph_engine.stitcher import normalize_url


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
