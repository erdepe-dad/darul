from __future__ import annotations

import unittest

from graph_engine.config import SETTINGS
from graph_engine.visualizer import GraphVisualizationService


class FakeDB:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def execute_read(self, query: str, **parameters):
        self.calls.append((query, parameters))
        if "RETURN repo AS name" in query:
            return [{"name": "sample", "nodes": 3}]
        if "UNWIND labels(n)" in query:
            return [{"label": "CodeFile", "count": 2}, {"label": "Function", "count": 1}]
        if "RETURN type(r) AS relationship" in query:
            return [{"relationship": "DEFINES", "count": 1}]
        if "elementId(source)" in query:
            return [
                {
                    "element_id": "rel-1",
                    "source": "node-1",
                    "target": "node-2",
                    "relationship": "DEFINES",
                    "properties": {},
                }
            ]
        if "collect(CASE WHEN r IS NULL" in query:
            return [
                {
                    "element_id": "node-1",
                    "labels": ["CodeFile"],
                    "properties": {"id": "sample:src/app.py", "path": "src/app.py"},
                    "neighbors": [
                        {
                            "relationship": "DEFINES",
                            "direction": "out",
                            "node_id": "node-2",
                            "labels": ["Function"],
                            "label": "run",
                        }
                    ],
                }
            ]
        return [
            {
                "element_id": "node-1",
                "labels": ["CodeFile"],
                "properties": {"id": "sample:src/app.py", "path": "src/app.py"},
                "repository": "sample",
            },
            {
                "element_id": "node-2",
                "labels": ["Function"],
                "properties": {"id": "sample:src/app.py::run", "name": "run"},
                "repository": "sample",
            },
        ]


class VisualizationServiceTests(unittest.TestCase):
    def test_metadata_reports_available_scope(self) -> None:
        service = GraphVisualizationService(FakeDB(), SETTINGS)
        result = service.metadata()
        self.assertEqual(result["repositories"][0]["name"], "sample")
        self.assertEqual(result["relationships"][0]["relationship"], "DEFINES")

    def test_graph_returns_browser_safe_nodes_and_links(self) -> None:
        db = FakeDB()
        service = GraphVisualizationService(db, SETTINGS)
        result = service.graph(
            repository="sample",
            labels=["CodeFile", "Function"],
            relationship_types=["DEFINES"],
            limit=80,
        )
        self.assertEqual([node["label"] for node in result["nodes"]], ["src/app.py", "run"])
        self.assertEqual(result["links"][0]["source"], "node-1")
        node_call = next(parameters for query, parameters in db.calls if "searchable" in query)
        self.assertEqual(node_call["repository"], "sample")

    def test_focus_depth_is_bounded_to_two_hops(self) -> None:
        db = FakeDB()
        service = GraphVisualizationService(db, SETTINGS)
        service.graph(focus="node-1", depth=99)
        focus_query = next(query for query, _ in db.calls if "MATCH (center)" in query)
        self.assertIn("[*0..2]", focus_query)

    def test_node_detail_filters_null_neighbors(self) -> None:
        service = GraphVisualizationService(FakeDB(), SETTINGS)
        result = service.node_detail("node-1")
        self.assertIsNotNone(result)
        self.assertEqual(result["neighbors"][0]["label"], "run")


if __name__ == "__main__":
    unittest.main()
