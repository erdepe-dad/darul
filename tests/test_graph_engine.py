from __future__ import annotations

import unittest

from graph_engine.hooks.context_inject import HOTSPOTS_QUERY, render_context
from graph_engine.stitcher import (
    configure_service,
    normalize_url,
    service_path_prefix,
    stitch_endpoints,
)


class StitchDB:
    def __init__(self) -> None:
        self.writes: list[tuple[str, dict]] = []
        self.mapped_requests: list[dict] = []

    def execute_write(self, query: str, **parameters):
        self.writes.append((query, parameters))
        if "UNWIND $rows AS row" in query:
            return [{"stitched": 2}]
        if "RETURN properties(s) AS service" in query:
            return [{"service": parameters}]
        if "DELETE st" in query:
            return []
        return [{"stitched": 3}]

    def execute_read(self, query: str, **parameters):
        return self.mapped_requests


class NormalizationTests(unittest.TestCase):
    def test_parameter_dialects_normalize_equally(self) -> None:
        values = [
            "/api/v1/users/${userId}",
            "/api/v1/users/:id",
            "/api/v1/users/{user_id}",
            "/api/v1/users/[id]",
        ]
        self.assertEqual({normalize_url(value) for value in values}, {"/api/v1/users/{param}"})

    def test_nextjs_optional_catchall_normalizes_without_a_trailing_bracket(self) -> None:
        self.assertEqual(normalize_url("/api/admin/[[...path]]"), "/api/admin/{param}")

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
        exact_query, parameters = db.writes[0]
        self.assertIn("MATCH (a:APIEndpoint)", exact_query)
        self.assertNotIn("MATCH (a:APIEndpoint {repo_name:", exact_query)
        self.assertIn("b.route_path CONTAINS '[...'", exact_query)
        self.assertIn("b.repo_name = a.repo_name", exact_query)
        self.assertIn("trust_status = 'SUGGESTED'", exact_query)
        self.assertEqual(parameters, {})

    def test_stitching_applies_persisted_service_path_prefixes(self) -> None:
        db = StitchDB()
        db.mapped_requests = [
            {
                "request_id": "ui:request:1",
                "method": "POST",
                "normalized_url": "/login",
                "system": "BACKEND_API_URL",
                "path_prefix": "/api",
                "target_repo": "admin-rest",
            }
        ]

        stitched = stitch_endpoints(db)

        self.assertEqual(stitched, 5)
        mapped_query, parameters = db.writes[-1]
        self.assertIn("configured-prefix", mapped_query)
        self.assertIn("trust_status = 'SUGGESTED'", mapped_query)
        self.assertEqual(parameters["rows"][0]["normalized_url"], "/api/login")
        self.assertEqual(parameters["rows"][0]["target_repo"], "admin-rest")

    def test_service_base_url_path_is_persisted_for_future_stitching(self) -> None:
        db = StitchDB()

        service = configure_service(
            db,
            "BACKEND_API_URL",
            "http://127.0.0.1:10000/api/",
            repo_name="sample-web",
            target_repo="sample-api",
        )

        self.assertEqual(service["path_prefix"], "/api")
        self.assertEqual(service["target_repo"], "sample-api")
        self.assertEqual(service_path_prefix("http://service.test"), "")


class ContextRenderingTests(unittest.TestCase):
    def test_agent_hotspots_require_validated_routes(self) -> None:
        self.assertIn("route.trust_status = 'VALIDATED'", HOTSPOTS_QUERY)

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
