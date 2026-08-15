from __future__ import annotations

import unittest
from pathlib import Path

from graph_engine.config import Settings
from graph_engine.tracer import _request_resolution_warning, trace_view


class FakeDB:
    def __init__(self) -> None:
        self.neighbor_reads = 0

    def execute_read(self, query: str, **parameters):
        if "MATCH (f:CodeFile" in query:
            return [{"id": "sample-web:View.java", "path": "View.java", "routes": ["/tasks"], "classes": ["ExampleTaskView"], "score": 4}]
        if "MATCH (seed:CodeFile" in query:
            return [{"element_id": "1", "labels": ["Page"], "properties": {"id": "sample-web:View.java", "route_path": "/tasks", "repo_name": "sample-web"}}]
        self.neighbor_reads += 1
        if self.neighbor_reads > 1:
            return []
        return [
            {
                "source": "1", "source_labels": ["Page"],
                "source_properties": {"id": "sample-web:View.java", "route_path": "/tasks", "repo_name": "sample-web"},
                "target": "2", "target_labels": ["UIAction"],
                "target_properties": {"id": "sample-web:action", "name": "approve", "event": "click", "repo_name": "sample-web"},
                "relationship_id": "r1", "relationship_type": "HAS_ACTION", "relationship_properties": {},
            },
            {
                "source": "2", "source_labels": ["UIAction"],
                "source_properties": {"id": "sample-web:action", "name": "approve", "event": "click", "repo_name": "sample-web"},
                "target": "3", "target_labels": ["BackendRoute"],
                "target_properties": {"id": "api:route", "method": "POST", "route_path": "/approve", "repo_name": "api"},
                "relationship_id": "r2", "relationship_type": "CALLS",
                "relationship_properties": {"condition": "approved"},
            },
        ]


class TracerTests(unittest.TestCase):
    def test_partial_request_resolution_is_reported_with_bounded_details(self) -> None:
        nodes = {
            str(index): {
                "id": str(index),
                "label": f"GET /api/items/{index}",
                "labels": ["APIEndpoint"],
            }
            for index in range(5)
        }
        links = [{"source": "0", "target": "route", "type": "TARGETS_ROUTE"}]

        warning = _request_resolution_warning(nodes, links)

        self.assertEqual(
            warning,
            "4 of 5 API requests do not resolve to an ingested backend route: "
            "GET /api/items/1; GET /api/items/2; GET /api/items/3; +1 more.",
        )

    def test_request_resolution_warning_is_absent_when_all_requests_resolve(self) -> None:
        nodes = {
            "request": {
                "id": "request", "label": "GET /api/items", "labels": ["APIEndpoint"],
            }
        }
        links = [{"source": "request", "target": "route", "type": "TARGETS_ROUTE"}]

        self.assertIsNone(_request_resolution_warning(nodes, links))

    def test_trace_groups_lanes_and_marks_alternatives(self) -> None:
        settings = Settings(Path("/tmp"), "sample-web", "bolt://unused", "neo4j", "x", None, 1, frozenset())
        trace = trace_view(FakeDB(), "ExampleTaskView", settings)

        self.assertTrue(trace["found"])
        self.assertEqual(trace["stats"]["alternatives"], 1)
        self.assertIn("Vaadin UI", [lane["name"] for lane in trace["lanes"]])
        self.assertIn("Backend", [lane["name"] for lane in trace["lanes"]])
        self.assertIn("flowchart LR", trace["mermaid"])


if __name__ == "__main__":
    unittest.main()
