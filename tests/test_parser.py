from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from graph_engine.config import Settings
from graph_engine.parser import parse_file, scan_repository


def settings_for(root: Path) -> Settings:
    return Settings(
        repo_root=root,
        repo_name="sample",
        uri="bolt://unused:7687",
        user="neo4j",
        password="test",
        database=None,
        connection_timeout=0.1,
        excludes=frozenset({"node_modules", ".git", ".graph_engine"}),
    )


class ParserTests(unittest.TestCase):
    def test_python_ast_extracts_symbols_requests_and_routes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "api.py"
            source.write_text(
                """import httpx
from fastapi import APIRouter

router = APIRouter()

class UserService:
    async def load(self, user_id):
        return await httpx.get(f\"/api/users/{user_id}\")

@router.get('/api/users/{user_id}')
async def get_user(user_id):
    return user_id
""",
                encoding="utf-8",
            )
            parsed = parse_file(source, settings_for(root))

        self.assertEqual(parsed.id, "sample:api.py")
        self.assertIn("httpx", parsed.imports)
        self.assertEqual(parsed.classes[0].id, "sample:api.py::UserService")
        self.assertIn("UserService.load", [item.name for item in parsed.functions])
        self.assertEqual(parsed.requests[0].normalized_url, "/api/users/{param}")
        self.assertEqual(parsed.routes[0].id, "sample:GET:/api/users/{param}")
        self.assertEqual(parsed.routes[0].handler_id, "sample:api.py::get_user")

    def test_python_mock_patch_is_not_a_backend_route(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "test_service.py"
            source.write_text(
                """from unittest.mock import patch
@patch('service.client')
def test_client(mock_client):
    pass
""",
                encoding="utf-8",
            )
            parsed = parse_file(source, settings_for(root))

        self.assertEqual(parsed.routes, [])

    def test_javascript_fallback_extracts_fetch_axios_and_express(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "src" / "pages" / "users.tsx"
            source.parent.mkdir(parents=True)
            source.write_text(
                """import axios from 'axios';
export const loadUser = async (id) => fetch(`/api/users/${id}`);
axios.post('/api/users', {name: 'Ada'});
router.get('/api/users/:id', loadUser);
""",
                encoding="utf-8",
            )
            parsed = parse_file(source, settings_for(root))

        self.assertEqual(parsed.route_path, "/users")
        self.assertEqual([request.method for request in parsed.requests], ["GET", "POST"])
        self.assertEqual(parsed.requests[0].normalized_url, "/api/users/{param}")
        self.assertEqual(parsed.routes[0].handler_id, "sample:src/pages/users.tsx::loadUser")

    def test_scan_skips_excluded_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.go").write_text("package main\nfunc main() {}", encoding="utf-8")
            excluded = root / "node_modules"
            excluded.mkdir()
            (excluded / "ignored.js").write_text("function ignored() {}", encoding="utf-8")
            result = scan_repository(settings_for(root))

        self.assertEqual([item.path for item in result.files], ["main.go"])
        self.assertEqual(result.symbol_count, 1)

    def test_go_parser_extracts_block_imports_and_route_methods(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "server.go"
            source.write_text(
                '''package server
import (
    "net/http"
    json "encoding/json"
)
type Server struct {}
func createUser(w http.ResponseWriter, r *http.Request) {}
func routes() { router.POST("/api/users/:id", createUser) }
''',
                encoding="utf-8",
            )
            parsed = parse_file(source, settings_for(root))

        self.assertEqual(parsed.imports, ["encoding/json", "net/http"])
        self.assertEqual(parsed.classes[0].name, "Server")
        self.assertEqual(parsed.routes[0].method, "POST")
        self.assertEqual(parsed.routes[0].normalized_url, "/api/users/{param}")

    def test_java_spring_vaadin_and_flowable_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "src" / "UsersView.java"
            source.parent.mkdir()
            source.write_text(
                '''package dev.rudix.users;
import com.vaadin.flow.router.Route;
import org.flowable.engine.delegate.JavaDelegate;
import org.flowable.engine.RuntimeService;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.client.RestTemplate;

@Route("users")
@RestController
@RequestMapping("/api/users")
public class UsersView implements JavaDelegate {
    @GetMapping("/{id}")
    public String getUser(String id) { return id; }

    @PostMapping
    public void createUser() {}

    public void callRemote(RestTemplate client) {
        client.getForObject("/api/roles/{roleId}", String.class);
    }

    public void start(RuntimeService runtimeService) {
        runtimeService.startProcessInstanceByKey("user-onboarding");
    }
}
''',
                encoding="utf-8",
            )
            parsed = parse_file(source, settings_for(root))

        self.assertEqual(parsed.route_path, "/users")
        self.assertEqual(parsed.frameworks, ["spring-boot", "vaadin", "flowable"])
        self.assertEqual([route.method for route in parsed.routes], ["GET", "POST"])
        self.assertEqual(parsed.routes[0].normalized_url, "/api/users/{param}")
        self.assertEqual(parsed.requests[0].normalized_url, "/api/roles/{param}")
        self.assertIn("user-onboarding", parsed.workflow_refs)
        self.assertIn("delegate:UsersView", parsed.workflow_refs)


if __name__ == "__main__":
    unittest.main()
