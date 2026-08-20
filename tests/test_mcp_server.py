from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from graph_engine.mcp_server import (
    SERVER_INSTRUCTIONS,
    SEARCH_QUERY,
    list_repositories,
    search_graph,
)


class FakeDB:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, dict]] = []

    def __enter__(self) -> "FakeDB":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute_read(self, query: str, **parameters: object) -> list[dict]:
        self.calls.append((query, parameters))
        return self.rows


class MCPToolTests(unittest.TestCase):
    def test_repository_list_is_compact_markdown(self) -> None:
        db = FakeDB([{"name": "checkout", "root_path": "/srv/checkout", "files": 42}])
        with patch("graph_engine.mcp_server.GraphDB", return_value=db):
            rendered = list_repositories()

        self.assertIn("checkout (42 files) - /srv/checkout", rendered)

    def test_search_is_scoped_and_bounded(self) -> None:
        db = FakeDB(
            [
                {
                    "id": "checkout:src/cart.py::load_cart",
                    "labels": ["Function"],
                    "name": "load_cart",
                    "source_file_id": "checkout:src/cart.py",
                    "line": 12,
                }
            ]
        )
        with patch("graph_engine.mcp_server.GraphDB", return_value=db):
            rendered = search_graph("Cart", repository="checkout", limit=500)

        self.assertIn("Function: load_cart - checkout:src/cart.py:12", rendered)
        query, parameters = db.calls[0]
        self.assertEqual(query, SEARCH_QUERY)
        self.assertEqual(parameters["repo_name"], "checkout")
        self.assertEqual(parameters["term"], "cart")
        self.assertEqual(parameters["limit"], 50)

    def test_server_instructions_require_graph_first_retrieval(self) -> None:
        self.assertIn("Use Darul before broad filesystem search", SERVER_INSTRUCTIONS)
        self.assertIn("Read source only", SERVER_INSTRUCTIONS)


class MCPProtocolTests(unittest.TestCase):
    def test_stdio_server_initializes_and_lists_read_only_tools(self) -> None:
        async def exercise_server() -> None:
            root = Path(__file__).resolve().parent.parent
            parameters = StdioServerParameters(
                command=sys.executable,
                args=["-m", "graph_engine.mcp_server"],
                cwd=str(root),
            )
            async with stdio_client(parameters) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    initialized = await session.initialize()
                    tools = (await session.list_tools()).tools

            self.assertEqual(initialized.serverInfo.name, "darul")
            self.assertEqual(
                {tool.name for tool in tools},
                {
                    "darul_repositories",
                    "darul_context",
                    "darul_search",
                    "darul_inspect_page",
                    "darul_trace",
                },
            )
            self.assertTrue(all(tool.annotations.readOnlyHint for tool in tools))
            self.assertTrue(all(tool.annotations.destructiveHint is False for tool in tools))

        anyio.run(exercise_server)


if __name__ == "__main__":
    unittest.main()
