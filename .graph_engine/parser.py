"""Fast, dependency-free structural parser and graph ingester."""

from __future__ import annotations

import ast
import hashlib
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .config import SETTINGS, Settings
from .db import GraphDB
from .stitcher import normalize_url


SOURCE_EXTENSIONS = frozenset({".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".java"})
MAX_FILE_BYTES = 2_000_000


@dataclass(slots=True)
class Symbol:
    id: str
    name: str
    line: int
    signature: str = ""


@dataclass(slots=True)
class APIRequest:
    id: str
    method: str
    url: str
    normalized_url: str
    line: int


@dataclass(slots=True)
class Route:
    id: str
    method: str
    route_path: str
    normalized_url: str
    line: int
    handler_id: str | None = None


@dataclass(slots=True)
class ParsedFile:
    id: str
    path: str
    extension: str
    content_hash: str
    imports: list[str] = field(default_factory=list)
    classes: list[Symbol] = field(default_factory=list)
    functions: list[Symbol] = field(default_factory=list)
    requests: list[APIRequest] = field(default_factory=list)
    routes: list[Route] = field(default_factory=list)
    route_path: str | None = None
    frameworks: list[str] = field(default_factory=list)
    workflow_refs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ScanResult:
    files: list[ParsedFile]
    duration_seconds: float
    skipped_files: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def symbol_count(self) -> int:
        return sum(len(item.classes) + len(item.functions) for item in self.files)


def _entity_id(repo_name: str, rel_path: str, name: str) -> str:
    return f"{repo_name}:{rel_path}::{name}"


def _literal_string(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant):
                parts.append(str(value.value))
            else:
                parts.append("{param}")
        return "".join(parts)
    return None


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = [arg.arg for arg in node.args.posonlyargs + node.args.args]
    if node.args.vararg:
        args.append(f"*{node.args.vararg.arg}")
    args.extend(arg.arg for arg in node.args.kwonlyargs)
    if node.args.kwarg:
        args.append(f"**{node.args.kwarg.arg}")
    return f"{node.name}({', '.join(args)})"


class _PythonVisitor(ast.NodeVisitor):
    def __init__(self, repo_name: str, rel_path: str) -> None:
        self.repo_name = repo_name
        self.rel_path = rel_path
        self.imports: list[str] = []
        self.classes: list[Symbol] = []
        self.functions: list[Symbol] = []
        self.requests: list[APIRequest] = []
        self.routes: list[Route] = []
        self.scope: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        self.imports.extend(alias.name for alias in node.names)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        base = "." * node.level + (node.module or "")
        self.imports.extend(f"{base}.{alias.name}".strip(".") for alias in node.names)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qualified = ".".join([*self.scope, node.name])
        bases = [ast.unparse(base) for base in node.bases]
        self.classes.append(
            Symbol(
                _entity_id(self.repo_name, self.rel_path, qualified),
                qualified,
                node.lineno,
                f"class {node.name}({', '.join(bases)})" if bases else f"class {node.name}",
            )
        )
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        qualified = ".".join([*self.scope, node.name])
        function_id = _entity_id(self.repo_name, self.rel_path, qualified)
        self.functions.append(Symbol(function_id, qualified, node.lineno, _signature(node)))
        self._extract_decorated_routes(node, function_id)
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def _extract_decorated_routes(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef, function_id: str
    ) -> None:
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            name = _call_name(decorator.func).lower()
            receiver = name.rsplit(".", 1)[0] if "." in name else ""
            if receiver.rsplit(".", 1)[-1] not in {"app", "router", "server", "blueprint", "bp"}:
                continue
            path = _literal_string(decorator.args[0]) if decorator.args else None
            if not path:
                continue
            method = name.rsplit(".", 1)[-1].upper()
            methods = [method] if method in {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"} else []
            if method in {"ROUTE", "API_ROUTE"}:
                for keyword in decorator.keywords:
                    if keyword.arg == "methods" and isinstance(keyword.value, (ast.List, ast.Tuple)):
                        methods = [
                            value.upper()
                            for item in keyword.value.elts
                            if (value := _literal_string(item))
                        ]
                methods = methods or ["GET"]
            for route_method in methods:
                self._add_route(route_method, path, node.lineno, function_id)

    def visit_Call(self, node: ast.Call) -> None:
        name = _call_name(node.func).lower()
        method = name.rsplit(".", 1)[-1].upper()
        request_clients = ("requests.", "httpx.", "client.", "session.")
        if method in {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"} and any(
            marker in name for marker in request_clients
        ):
            url = _literal_string(node.args[0]) if node.args else None
            if url:
                self._add_request(method, url, node.lineno)
        self.generic_visit(node)

    def _add_request(self, method: str, url: str, line: int) -> None:
        key = f"{method}:{url}:{line}"
        self.requests.append(
            APIRequest(
                _entity_id(self.repo_name, self.rel_path, f"request:{key}"),
                method,
                url,
                normalize_url(url),
                line,
            )
        )

    def _add_route(self, method: str, path: str, line: int, handler_id: str | None) -> None:
        normalized = normalize_url(path)
        self.routes.append(
            Route(
                f"{self.repo_name}:{method}:{normalized}",
                method,
                path,
                normalized,
                line,
                handler_id,
            )
        )


IMPORT_RE = re.compile(
    r"(?:^|\n)\s*(?:import\s+(?:[^'\"\n]+?\s+from\s+)?|require\s*\(\s*)['\"]([^'\"]+)['\"]",
    re.MULTILINE,
)
JS_CLASS_RE = re.compile(r"\bclass\s+([A-Za-z_$][\w$]*)(?:\s+extends\s+([^\s{]+))?")
JS_FUNCTION_RE = re.compile(
    r"(?:\b(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(([^)]*)\)|"
    r"\b(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(([^)]*)\)\s*=>)"
)
FETCH_RE = re.compile(r"\bfetch\s*\(\s*([`'\"])(.*?)\1\s*(?:,\s*\{(.*?)\})?", re.DOTALL)
AXIOS_METHOD_RE = re.compile(
    r"\baxios\s*\.\s*(get|post|put|patch|delete|options|head)\s*\(\s*([`'\"])(.*?)\2",
    re.IGNORECASE | re.DOTALL,
)
AXIOS_CONFIG_RE = re.compile(r"\baxios\s*\(\s*\{(.*?)\}\s*\)", re.DOTALL)
JS_ROUTE_RE = re.compile(
    r"\b(?:app|router|server)\s*\.\s*(get|post|put|patch|delete|options|head)\s*\(\s*([`'\"])(.*?)\2\s*(?:,\s*([A-Za-z_$][\w$]*))?",
    re.IGNORECASE | re.DOTALL,
)
GO_FUNCTION_RE = re.compile(r"(?m)^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)\s*\(([^)]*)\)")
GO_STRUCT_RE = re.compile(r"(?m)^\s*type\s+([A-Za-z_]\w*)\s+struct\s*\{")
GO_IMPORT_RE = re.compile(r"(?m)^\s*import\s+(?:\w+\s+)?\"([^\"]+)\"")
GO_IMPORT_BLOCK_RE = re.compile(r"(?ms)^\s*import\s*\((.*?)\)")
GO_ROUTE_RE = re.compile(
    r"\b(HandleFunc|Handle|GET|POST|PUT|PATCH|DELETE)\s*\(\s*\"([^\"]+)\"(?:\s*,\s*([A-Za-z_]\w*))?"
)
JAVA_IMPORT_RE = re.compile(r"(?m)^\s*import\s+(?:static\s+)?([\w.*]+)\s*;")
JAVA_TYPE_RE = re.compile(
    r"(?m)^\s*(?:public\s+|protected\s+|private\s+|abstract\s+|final\s+|sealed\s+|static\s+)*"
    r"(class|interface|record|enum)\s+([A-Za-z_$][\w$]*)([^\n{]*)"
)
JAVA_METHOD_RE = re.compile(
    r"(?m)^\s*(?:public|protected|private)\s+"
    r"(?:(?:static|final|abstract|synchronized|native|default)\s+)*"
    r"(?:[\w$@.?<>\[\],]+\s+)+([A-Za-z_$][\w$]*)\s*\(([^)]*)\)"
)
JAVA_MAPPING_RE = re.compile(
    r"@(GetMapping|PostMapping|PutMapping|PatchMapping|DeleteMapping|RequestMapping)\s*"
    r"(?:\((.*?)\))?",
    re.DOTALL,
)
JAVA_VAADIN_ROUTE_RE = re.compile(r"@Route\s*(?:\((.*?)\))?", re.DOTALL)
JAVA_REST_TEMPLATE_RE = re.compile(
    r"\.(getForObject|getForEntity|postForObject|postForEntity|put|patchForObject|delete)\s*"
    r"\(\s*\"([^\"]+)\"",
    re.DOTALL,
)
JAVA_WEBCLIENT_RE = re.compile(
    r"\.(get|post|put|patch|delete)\s*\(\s*\)\s*\.\s*uri\s*\(\s*\"([^\"]+)\"",
    re.IGNORECASE | re.DOTALL,
)
JAVA_WORKFLOW_PATTERNS = (
    re.compile(r"startProcessInstanceByKey\s*\(\s*\"([^\"]+)\""),
    re.compile(r"@JobWorker\s*\([^)]*\btype\s*=\s*\"([^\"]+)\"", re.DOTALL),
    re.compile(r"@Process\s*\(\s*\"([^\"]+)\""),
)


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _template_url(value: str) -> str:
    return re.sub(r"\$\{[^}]+\}", "{param}", value)


def _parse_javascript(text: str, repo_name: str, rel_path: str) -> tuple[list[str], list[Symbol], list[Symbol], list[APIRequest], list[Route]]:
    imports = sorted(set(IMPORT_RE.findall(text)))
    classes = [
        Symbol(
            _entity_id(repo_name, rel_path, match.group(1)),
            match.group(1),
            _line_number(text, match.start()),
            f"class {match.group(1)}" + (f" extends {match.group(2)}" if match.group(2) else ""),
        )
        for match in JS_CLASS_RE.finditer(text)
    ]
    functions: list[Symbol] = []
    for match in JS_FUNCTION_RE.finditer(text):
        name = match.group(1) or match.group(3)
        args = match.group(2) if match.group(1) else match.group(4)
        functions.append(
            Symbol(
                _entity_id(repo_name, rel_path, name),
                name,
                _line_number(text, match.start()),
                f"{name}({args.strip()})",
            )
        )

    requests: list[APIRequest] = []

    def add_request(method: str, url: str, offset: int) -> None:
        line = _line_number(text, offset)
        clean_url = _template_url(url)
        requests.append(
            APIRequest(
                _entity_id(repo_name, rel_path, f"request:{method}:{clean_url}:{line}"),
                method.upper(),
                clean_url,
                normalize_url(clean_url),
                line,
            )
        )

    for match in FETCH_RE.finditer(text):
        options = match.group(3) or ""
        method_match = re.search(r"\bmethod\s*:\s*['\"]([A-Za-z]+)['\"]", options)
        add_request(method_match.group(1) if method_match else "GET", match.group(2), match.start())
    for match in AXIOS_METHOD_RE.finditer(text):
        add_request(match.group(1), match.group(3), match.start())
    for match in AXIOS_CONFIG_RE.finditer(text):
        config = match.group(1)
        url_match = re.search(r"\burl\s*:\s*([`'\"])(.*?)\1", config, re.DOTALL)
        if url_match:
            method_match = re.search(r"\bmethod\s*:\s*['\"]([A-Za-z]+)['\"]", config)
            add_request(method_match.group(1) if method_match else "GET", url_match.group(2), match.start())

    routes: list[Route] = []
    for match in JS_ROUTE_RE.finditer(text):
        method = match.group(1).upper()
        path = _template_url(match.group(3))
        handler = match.group(4)
        routes.append(
            Route(
                f"{repo_name}:{method}:{normalize_url(path)}",
                method,
                path,
                normalize_url(path),
                _line_number(text, match.start()),
                _entity_id(repo_name, rel_path, handler) if handler else None,
            )
        )
    return imports, classes, functions, requests, routes


def _parse_go(text: str, repo_name: str, rel_path: str) -> tuple[list[str], list[Symbol], list[Symbol], list[APIRequest], list[Route]]:
    imports = set(GO_IMPORT_RE.findall(text))
    for block in GO_IMPORT_BLOCK_RE.findall(text):
        imports.update(re.findall(r'(?:\w+\s+)?"([^"]+)"', block))
    classes = [
        Symbol(_entity_id(repo_name, rel_path, match.group(1)), match.group(1), _line_number(text, match.start()), f"type {match.group(1)} struct")
        for match in GO_STRUCT_RE.finditer(text)
    ]
    functions = [
        Symbol(_entity_id(repo_name, rel_path, match.group(1)), match.group(1), _line_number(text, match.start()), f"{match.group(1)}({match.group(2).strip()})")
        for match in GO_FUNCTION_RE.finditer(text)
    ]
    routes: list[Route] = []
    for match in GO_ROUTE_RE.finditer(text):
        route_call = match.group(1).upper()
        path = match.group(2)
        method = route_call if route_call in {"GET", "POST", "PUT", "PATCH", "DELETE"} else "GET"
        handler = match.group(3)
        routes.append(Route(f"{repo_name}:{method}:{normalize_url(path)}", method, path, normalize_url(path), _line_number(text, match.start()), _entity_id(repo_name, rel_path, handler) if handler else None))
    return sorted(imports), classes, functions, [], routes


def _java_annotation_path(arguments: str | None) -> str:
    if not arguments:
        return "/"
    match = re.search(r'(?:\b(?:value|path)\s*=\s*)?(?:\{\s*)?"([^"]*)"', arguments)
    return match.group(1) if match else "/"


def _join_route(prefix: str, path: str) -> str:
    if path == "/":
        path = ""
    return normalize_url(f"/{prefix.strip('/')}/{path.strip('/')}")


def _java_mapping_methods(annotation: str, arguments: str | None) -> list[str]:
    direct = {
        "GetMapping": "GET",
        "PostMapping": "POST",
        "PutMapping": "PUT",
        "PatchMapping": "PATCH",
        "DeleteMapping": "DELETE",
    }
    if annotation in direct:
        return [direct[annotation]]
    methods = re.findall(r"RequestMethod\.(GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)", arguments or "")
    return methods or ["GET"]


def _next_java_method(text: str, offset: int) -> re.Match[str] | None:
    match = JAVA_METHOD_RE.search(text, offset)
    return match if match and match.start() - offset < 800 else None


def _parse_java(
    text: str, repo_name: str, rel_path: str
) -> tuple[list[str], list[Symbol], list[Symbol], list[APIRequest], list[Route], str | None, list[str], list[str]]:
    imports = sorted(set(JAVA_IMPORT_RE.findall(text)))
    type_matches = list(JAVA_TYPE_RE.finditer(text))
    primary_type = type_matches[0].group(2) if type_matches else Path(rel_path).stem
    classes = [
        Symbol(
            _entity_id(repo_name, rel_path, match.group(2)),
            match.group(2),
            _line_number(text, match.start()),
            f"{match.group(1)} {match.group(2)}{match.group(3).strip()}",
        )
        for match in type_matches
    ]
    functions: list[Symbol] = []
    method_ids: dict[tuple[str, int], str] = {}
    for match in JAVA_METHOD_RE.finditer(text):
        method_name, arguments = match.group(1), match.group(2)
        argument_types = [
            re.sub(r"\s+[A-Za-z_$][\w$]*$", "", item.strip())
            for item in arguments.split(",")
            if item.strip()
        ]
        entity_name = f"{primary_type}.{method_name}({','.join(argument_types)})"
        symbol = Symbol(
            _entity_id(repo_name, rel_path, entity_name),
            f"{primary_type}.{method_name}",
            _line_number(text, match.start()),
            f"{method_name}({arguments.strip()})",
        )
        functions.append(symbol)
        method_ids[(match.start(), match.end())] = symbol.id

    frameworks: list[str] = []
    joined_imports = "\n".join(imports)
    if "org.springframework" in joined_imports or "@SpringBootApplication" in text:
        frameworks.append("spring-boot")
    if "com.vaadin" in joined_imports or "@Route" in text:
        frameworks.append("vaadin")
    if "org.flowable" in joined_imports or "JavaDelegate" in text or "RuntimeService" in text:
        frameworks.append("flowable")

    vaadin_route: str | None = None
    vaadin_match = JAVA_VAADIN_ROUTE_RE.search(text)
    if vaadin_match:
        vaadin_route = normalize_url(_java_annotation_path(vaadin_match.group(1)))

    class_prefix = ""
    if type_matches:
        header = text[max(0, type_matches[0].start() - 1000) : type_matches[0].start()]
        class_mappings = list(JAVA_MAPPING_RE.finditer(header))
        if class_mappings:
            last_mapping = class_mappings[-1]
            class_prefix = _java_annotation_path(last_mapping.group(2))

    is_feign_client = "@FeignClient" in text
    routes: list[Route] = []
    requests: list[APIRequest] = []

    def add_request(method: str, url: str, offset: int) -> None:
        line = _line_number(text, offset)
        normalized = normalize_url(url)
        requests.append(
            APIRequest(
                _entity_id(repo_name, rel_path, f"request:{method}:{normalized}:{line}"),
                method,
                url,
                normalized,
                line,
            )
        )

    for mapping in JAVA_MAPPING_RE.finditer(text):
        if type_matches and mapping.start() < type_matches[0].end():
            continue
        method_match = _next_java_method(text, mapping.end())
        if not method_match:
            continue
        path = _join_route(class_prefix, _java_annotation_path(mapping.group(2)))
        methods = _java_mapping_methods(mapping.group(1), mapping.group(2))
        if is_feign_client:
            for method in methods:
                add_request(method, path, mapping.start())
            continue
        handler_id = method_ids.get((method_match.start(), method_match.end()))
        for method in methods:
            routes.append(
                Route(
                    f"{repo_name}:{method}:{path}",
                    method,
                    path,
                    path,
                    _line_number(text, mapping.start()),
                    handler_id,
                )
            )

    rest_methods = {
        "getForObject": "GET",
        "getForEntity": "GET",
        "postForObject": "POST",
        "postForEntity": "POST",
        "put": "PUT",
        "patchForObject": "PATCH",
        "delete": "DELETE",
    }
    for match in JAVA_REST_TEMPLATE_RE.finditer(text):
        add_request(rest_methods[match.group(1)], match.group(2), match.start())
    for match in JAVA_WEBCLIENT_RE.finditer(text):
        add_request(match.group(1).upper(), match.group(2), match.start())

    workflow_refs = sorted(
        {value for pattern in JAVA_WORKFLOW_PATTERNS for value in pattern.findall(text)}
    )
    if "implements JavaDelegate" in text:
        workflow_refs.append(f"delegate:{primary_type}")
    return imports, classes, functions, requests, routes, vaadin_route, frameworks, sorted(set(workflow_refs))


def infer_page_route(rel_path: str) -> str | None:
    path = Path(rel_path)
    parts = list(path.with_suffix("").parts)
    markers = {"pages", "app", "routes", "views"}
    marker_index = next((index for index, part in enumerate(parts) if part in markers), None)
    if marker_index is None:
        return None
    route_parts = parts[marker_index + 1 :]
    if route_parts and route_parts[-1] in {"index", "page"}:
        route_parts.pop()
    route = "/" + "/".join(route_parts)
    return normalize_url(route or "/")


def parse_file(path: Path, settings: Settings = SETTINGS) -> ParsedFile:
    resolved = path.resolve()
    rel_path = resolved.relative_to(settings.repo_root).as_posix()
    raw = resolved.read_bytes()
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError(f"file exceeds {MAX_FILE_BYTES} bytes")
    text = raw.decode("utf-8", errors="replace")
    extension = resolved.suffix.lower()
    if extension == ".py":
        tree = ast.parse(text, filename=rel_path)
        visitor = _PythonVisitor(settings.repo_name, rel_path)
        visitor.visit(tree)
        imports, classes, functions, requests, routes = (
            sorted(set(visitor.imports)),
            visitor.classes,
            visitor.functions,
            visitor.requests,
            visitor.routes,
        )
    elif extension in {".js", ".jsx", ".ts", ".tsx"}:
        imports, classes, functions, requests, routes = _parse_javascript(text, settings.repo_name, rel_path)
    elif extension == ".go":
        imports, classes, functions, requests, routes = _parse_go(text, settings.repo_name, rel_path)
        route_path = infer_page_route(rel_path)
        frameworks: list[str] = []
        workflow_refs: list[str] = []
    elif extension == ".java":
        imports, classes, functions, requests, routes, route_path, frameworks, workflow_refs = _parse_java(
            text, settings.repo_name, rel_path
        )
    else:
        raise ValueError(f"unsupported source extension: {extension}")
    if extension != ".java":
        route_path = infer_page_route(rel_path)
        frameworks = []
        workflow_refs = []
    return ParsedFile(
        id=f"{settings.repo_name}:{rel_path}",
        path=rel_path,
        extension=extension,
        content_hash=hashlib.sha256(raw).hexdigest(),
        imports=imports,
        classes=classes,
        functions=functions,
        requests=requests,
        routes=routes,
        route_path=route_path,
        frameworks=frameworks,
        workflow_refs=workflow_refs,
    )


def iter_source_files(settings: Settings = SETTINGS) -> list[Path]:
    files: list[Path] = []
    for root, directories, names in os.walk(settings.repo_root):
        directories[:] = sorted(item for item in directories if item not in settings.excludes)
        base = Path(root)
        for name in names:
            path = base / name
            if path.suffix.lower() in SOURCE_EXTENSIONS:
                files.append(path)
    return sorted(files)


def scan_repository(settings: Settings = SETTINGS) -> ScanResult:
    started = time.monotonic()
    files: list[ParsedFile] = []
    errors: list[str] = []
    skipped = 0
    for path in iter_source_files(settings):
        try:
            files.append(parse_file(path, settings))
        except (OSError, SyntaxError, UnicodeError, ValueError) as exc:
            skipped += 1
            errors.append(f"{path}: {exc}")
    return ScanResult(files, time.monotonic() - started, skipped, errors)


CLEAR_REPOSITORY_STRUCTURE = """
MATCH (n {repo_name: $repo_name})
WHERE n:CodeFile OR n:Class OR n:Function OR n:Page OR n:APIEndpoint OR n:BackendRoute
DETACH DELETE n
"""

UPSERT_FILES = """
MERGE (r:Repository {name: $repo_name})
SET r.root_path = $root_path, r.updated_at = datetime()
WITH r
UNWIND $rows AS row
MERGE (f:CodeFile {id: row.id})
SET f.path = row.path, f.extension = row.extension, f.content_hash = row.content_hash,
    f.imports = row.imports, f.frameworks = row.frameworks, f.workflow_refs = row.workflow_refs,
    f.repo_name = $repo_name, f.updated_at = datetime()
MERGE (r)-[:CONTAINS]->(f)
"""

UPSERT_SYMBOLS = """
UNWIND $rows AS row
MATCH (f:CodeFile {id: row.file_id})
MERGE (n:%s {id: row.id})
SET n.name = row.name, n.line = row.line, n.signature = row.signature,
    n.repo_name = $repo_name, n.source_file_id = row.file_id
MERGE (f)-[:DEFINES]->(n)
"""

UPSERT_PAGES = """
UNWIND $rows AS row
MATCH (f:CodeFile {id: row.file_id})
MERGE (p:Page {id: row.id})
SET p.route_path = row.route_path, p.repo_name = $repo_name, p.source_file_id = row.file_id
MERGE (f)-[:CONTAINS]->(p)
"""

UPSERT_REQUESTS = """
UNWIND $rows AS row
MATCH (p:Page {id: row.page_id})
MERGE (a:APIEndpoint {id: row.id})
SET a.method = row.method, a.url = row.url, a.normalized_url = row.normalized_url,
    a.line = row.line, a.repo_name = $repo_name, a.source_file_id = row.file_id
MERGE (p)-[:MAKES_REQUEST]->(a)
"""

UPSERT_ROUTES = """
UNWIND $rows AS row
MATCH (r:Repository {name: $repo_name})
MERGE (b:BackendRoute {id: row.id})
SET b.method = row.method, b.route_path = row.route_path, b.normalized_url = row.normalized_url,
    b.line = row.line, b.repo_name = $repo_name, b.source_file_id = row.file_id
MERGE (r)-[:CONTAINS]->(b)
WITH b, row
OPTIONAL MATCH (fn:Function {id: row.handler_id})
FOREACH (_ IN CASE WHEN fn IS NULL THEN [] ELSE [1] END | MERGE (b)-[:HANDLED_BY]->(fn))
"""


def ingest_files(db: GraphDB, files: list[ParsedFile], settings: Settings = SETTINGS, *, replace_all: bool = False) -> None:
    if replace_all:
        db.execute_write(CLEAR_REPOSITORY_STRUCTURE, repo_name=settings.repo_name)
    if not files:
        db.execute_write(
            "MERGE (r:Repository {name: $repo_name}) SET r.root_path = $root_path, r.updated_at = datetime()",
            repo_name=settings.repo_name,
            root_path=str(settings.repo_root),
        )
        return
    file_rows = [
        {"id": item.id, "path": item.path, "extension": item.extension, "content_hash": item.content_hash,
         "imports": item.imports, "frameworks": item.frameworks, "workflow_refs": item.workflow_refs}
        for item in files
    ]
    db.execute_write(UPSERT_FILES, repo_name=settings.repo_name, root_path=str(settings.repo_root), rows=file_rows)
    for label, attribute in (("Class", "classes"), ("Function", "functions")):
        rows = [dict(asdict(symbol), file_id=item.id) for item in files for symbol in getattr(item, attribute)]
        if rows:
            db.execute_write(UPSERT_SYMBOLS % label, repo_name=settings.repo_name, rows=rows)
    page_rows = [
        {"id": item.id, "file_id": item.id, "route_path": item.route_path or item.path}
        for item in files
        if item.requests or item.route_path
    ]
    if page_rows:
        db.execute_write(UPSERT_PAGES, repo_name=settings.repo_name, rows=page_rows)
    request_rows = [dict(asdict(request), page_id=item.id, file_id=item.id) for item in files for request in item.requests]
    if request_rows:
        db.execute_write(UPSERT_REQUESTS, repo_name=settings.repo_name, rows=request_rows)
    route_rows = [dict(asdict(route), file_id=item.id) for item in files for route in item.routes]
    if route_rows:
        db.execute_write(UPSERT_ROUTES, repo_name=settings.repo_name, rows=route_rows)


def build_graph(db: GraphDB, settings: Settings = SETTINGS) -> ScanResult:
    result = scan_repository(settings)
    db.ensure_schema()
    ingest_files(db, result.files, settings, replace_all=True)
    return result
