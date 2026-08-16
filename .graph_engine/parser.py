"""Fast, dependency-free structural parser and graph ingester."""

from __future__ import annotations

import ast
import bisect
import concurrent.futures
import functools
import hashlib
import io
import itertools
import os
import posixpath
import re
import time
import tokenize
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .config import SETTINGS, Settings
from .db import GraphDB
from .stitcher import normalize_url


SOURCE_EXTENSIONS = frozenset({".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".java", ".bpmn"})
EVIDENCE_EXTENSIONS = frozenset({
    ".properties", ".yml", ".yaml", ".toml", ".gradle", ".conf", ".prisma", ".sql",
})
EVIDENCE_FILE_NAMES = frozenset({
    "pom.xml", "build.gradle.kts", "package.json", "requirements.txt", "pyproject.toml",
    "go.mod", "compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml",
})
SYSTEM_EVIDENCE_HINTS = (
    "http://", "https://", "jdbc:", "postgres", "psycopg", "asyncpg", "pg_trgm", "jsonb",
    "mysql", "mariadb", "oracle",
    "sqlserver", "mongodb", "neo4j", "redis", "@cache", "cachemanager", "kafka",
    "rabbit", "jms", "pulsar", "elastic", "opensearch", "amazons3", "awssdk", "s3client", "minio",
    "javamailsender", "jakarta.mail", "javax.mail", "spring.mail", "smtp", "sendgrid", "mailgun", "sesclient", "whatsapp", "twilio", "stripe",
    "midtrans", "xendit", "paypal", "adyen", "doku", "espay", "keycloak", "flowable",
)
SYSTEM_TECH_HINTS = {
    "postgresql": ("postgres", "jdbc:postgresql", "psycopg", "asyncpg", "pg_trgm", "jsonb"),
    "mysql": ("mysql",),
    "mariadb": ("mariadb",),
    "oracle": ("oracle", "ojdbc"),
    "sqlserver": ("sqlserver", "mssql"),
    "mongodb": ("mongodb", "mongoclient"),
    "neo4j": ("neo4j", "graphdatabase.driver"),
    "h2": ("jdbc:h2", "h2database"),
    "spring-cache": ("@cache", "cachemanager"),
    "kafka": ("kafka",),
    "rabbitmq": ("rabbit", "amqp"),
    "jms": ("jms", "activemq"),
    "pulsar": ("pulsar",),
    "elasticsearch": ("elastic", "resthighlevelclient"),
    "opensearch": ("opensearch",),
    "s3": ("s3client", "amazons3", "awssdk.services.s3"),
    "minio": ("minio",),
    "smtp": ("javamailsender", "jakarta.mail", "javax.mail", "spring.mail", "smtp://"),
    "sendgrid": ("sendgrid",),
    "mailgun": ("mailgun",),
    "amazon-ses": ("sesclient", "amazonsimpleemailservice", "awssdk.services.ses"),
    "whatsapp": ("whatsapp", "graph.facebook.com", "wa.me/"),
    "twilio": ("twilio",),
    "stripe": ("stripe",),
    "midtrans": ("midtrans",),
    "xendit": ("xendit",),
    "paypal": ("paypal",),
    "adyen": ("adyen",),
    "doku": ("doku",),
    "espay": ("espay",),
    "keycloak": ("keycloak",),
    "flowable": ("flowable", "runtimeservice"),
}
CONFIG_ASSIGNMENT_RE = re.compile(
    r"(?m)^[ \t]*([A-Za-z_][A-Za-z0-9_.-]{1,120})[ \t]*[:=][ \t]*([^\r\n#;]*?)[ \t]*(?:[#;].*)?$"
)
MAX_FILE_BYTES = 2_000_000
PARALLEL_SCAN_MIN_FILES = 64
JSON_API_PREFIX_RE = re.compile(
    r"^\s*(?:crnk|katharsis)\.(?:pathPrefix|path-prefix)\s*=\s*([^#\r\n]+)",
    re.MULTILINE | re.IGNORECASE,
)


@dataclass(slots=True)
class Symbol:
    id: str
    name: str
    line: int
    signature: str = ""
    qualified_name: str = ""
    aliases: list[str] = field(default_factory=list)


@dataclass(slots=True)
class APIRequest:
    id: str
    method: str
    url: str
    normalized_url: str
    line: int
    source_function_id: str = ""
    system: str = ""


@dataclass(slots=True)
class Route:
    id: str
    method: str
    route_path: str
    normalized_url: str
    line: int
    handler_id: str | None = None


@dataclass(slots=True)
class WorkflowProcess:
    id: str
    process_key: str
    name: str


@dataclass(slots=True)
class WorkflowStep:
    id: str
    process_id: str
    step_key: str
    name: str
    kind: str
    bindings: list[str] = field(default_factory=list)
    called_process: str = ""


@dataclass(slots=True)
class WorkflowFlow:
    id: str
    process_id: str
    source_id: str
    target_id: str
    name: str = ""
    condition: str = ""
    is_default: bool = False


@dataclass(slots=True)
class UIAction:
    id: str
    name: str
    event: str
    line: int
    handler_id: str
    effects: list[str] = field(default_factory=list)


@dataclass(slots=True)
class FunctionCall:
    id: str
    source_id: str
    target_type: str
    target_method: str
    line: int
    condition: str = ""


@dataclass(slots=True)
class ProcessStart:
    id: str
    source_id: str
    process_key: str
    line: int


@dataclass(slots=True)
class MessageUse:
    id: str
    source_id: str
    broker: str
    channel: str
    direction: str
    line: int


@dataclass(slots=True)
class MessageBinding:
    id: str
    broker: str
    source_channel: str
    target_channel: str
    line: int


@dataclass(slots=True)
class SystemDependency:
    id: str
    name: str
    kind: str
    technology: str
    role: str
    evidence: str
    line: int
    scope: str = "runtime"
    config_key: str = ""


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
    workflow_processes: list[WorkflowProcess] = field(default_factory=list)
    workflow_steps: list[WorkflowStep] = field(default_factory=list)
    workflow_flows: list[WorkflowFlow] = field(default_factory=list)
    ui_actions: list[UIAction] = field(default_factory=list)
    function_calls: list[FunctionCall] = field(default_factory=list)
    process_starts: list[ProcessStart] = field(default_factory=list)
    message_uses: list[MessageUse] = field(default_factory=list)
    message_bindings: list[MessageBinding] = field(default_factory=list)
    system_dependencies: list[SystemDependency] = field(default_factory=list)

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


def _safe_identifier(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-") or "unknown"


def _request_system_name(url: str) -> str:
    value = url.strip().strip("`'\"")
    if "://" in value:
        return (urlsplit(value).hostname or "").lower()
    match = re.match(r"(?:\$\{([^}:]+)(?::[^}]*)?}|\$?([A-Z][A-Z0-9_]{2,}))", value)
    return (match.group(1) or match.group(2)) if match else ""


def _safe_request_url(url: str) -> str:
    value = url.strip().strip("`'\"")
    if "://" not in value:
        return value.split("?", 1)[0].split("#", 1)[0]
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    try:
        port = parsed.port
    except ValueError:
        port = None
    authority = f"{host}:{port}" if port else host
    return f"{parsed.scheme}://{authority}{parsed.path or '/'}"


def _evidence_scope(rel_path: str) -> str:
    parts = {part.lower() for part in Path(rel_path).parts}
    return "test" if parts.intersection({"test", "tests", "testing", "fixtures"}) else "runtime"


def _parse_system_dependencies(
    text: str, repo_name: str, rel_path: str, extension: str, lower_text: str | None = None,
) -> list[SystemDependency]:
    """Extract evidence-backed surrounding systems without retaining secret values."""
    dependencies: list[SystemDependency] = []
    seen: set[tuple[str, str, str]] = set()
    scope = _evidence_scope(rel_path)

    def add(kind: str, technology: str, role: str, match: re.Match[str], name: str = "") -> None:
        key = (kind, technology, role)
        if key in seen:
            return
        seen.add(key)
        line = _line_number(text, match.start())
        line_start = text.rfind("\n", 0, match.start()) + 1
        line_end = text.find("\n", match.end())
        line_text = text[line_start:line_end if line_end >= 0 else len(text)].strip()
        config_key = ""
        assignment = re.match(r"([A-Za-z_][A-Za-z0-9_.-]{1,120})\s*[:=]", line_text)
        if assignment:
            config_key = assignment.group(1)
        marker = config_key or match.group(0)[:80]
        if kind == "http-service":
            marker = config_key or "literal URL host"
        evidence = f"{marker} declares {technology} {role}"
        identity = name or technology
        dependencies.append(
            SystemDependency(
                _entity_id(
                    repo_name, rel_path,
                    f"system:{_safe_identifier(kind)}:{_safe_identifier(technology)}:{_safe_identifier(role)}",
                ),
                identity,
                kind,
                technology,
                role,
                evidence,
                line,
                scope,
                config_key,
            )
        )

    assignments: dict[str, tuple[str, re.Match[str]]] = {}
    for assignment_match in CONFIG_ASSIGNMENT_RE.finditer(text):
        key = re.sub(r"[_.-]+", ".", assignment_match.group(1).lower()).strip(".")
        assignments[key] = (assignment_match.group(2).strip().strip("'\""), assignment_match)

    smtp_host_keys = {
        "spring.mail.host", "mail.smtp.host", "smtp.host", "mail.host",
    }
    for key, (raw_host, host_match) in assignments.items():
        if key not in smtp_host_keys and not key.endswith(".smtp.host"):
            continue
        host = raw_host.lower().rstrip(".")
        if "://" in host:
            host = (urlsplit(host).hostname or "").lower().rstrip(".")
        if not host or not re.fullmatch(r"[a-z0-9.-]+", host):
            continue
        port_key = f"{key.rsplit('.', 1)[0]}.port"
        raw_port = assignments.get(port_key, ("", host_match))[0]
        port = int(raw_port) if raw_port.isdigit() and 0 < int(raw_port) <= 65535 else None
        identity = f"{host}:{port}" if port else host
        add("email", "smtp", "email", host_match, identity)

    patterns = (
        (
            "database", "postgresql", "database",
            r"jdbc:postgresql|postgres(?:ql)?-jdbc|org\.postgresql|\bpostgresql\b|"
            r"\bpsycopg(?:2)?\b|\basyncpg\b|\bpg_trgm\b|\btsvector\b|\bjsonb\b",
        ),
        ("database", "mysql", "database", r"jdbc:mysql|mysql-connector|com\.mysql\.cj|com\.mysql\.jdbc"),
        ("database", "mariadb", "database", r"jdbc:mariadb|mariadb-java-client|org\.mariadb"),
        ("database", "oracle", "database", r"jdbc:oracle|\bojdbc\d*\b|oracle\.jdbc"),
        ("database", "sqlserver", "database", r"jdbc:sqlserver|mssql-jdbc|com\.microsoft\.sqlserver"),
        ("database", "mongodb", "database", r"mongodb(?:\+srv)?://|spring\.data\.mongodb|MongoClient|mongodb-driver"),
        ("database", "neo4j", "database", r"neo4j(?:\+s|\+ssc)?://|GraphDatabase\.driver|spring\.data\.neo4j"),
        ("database", "h2", "database", r"jdbc:h2|com\.h2database"),
        ("cache", "spring-cache", "cache", r"@Cacheable|@CachePut|@CacheEvict|CacheManager"),
        ("messaging", "kafka", "pubsub", r"KafkaTemplate|KafkaListener|spring\.kafka|kafka-clients"),
        ("messaging", "rabbitmq", "queue", r"RabbitTemplate|RabbitListener|spring\.rabbitmq|amqp-client"),
        ("messaging", "jms", "queue", r"JmsTemplate|JmsListener|jakarta\.jms|javax\.jms|activemq"),
        ("messaging", "pulsar", "pubsub", r"PulsarTemplate|PulsarListener|spring\.pulsar"),
        ("search", "elasticsearch", "search", r"ElasticsearchClient|RestHighLevelClient|spring\.elasticsearch|elasticsearch-java"),
        ("search", "opensearch", "search", r"OpenSearchClient|opensearch-java|spring\.opensearch"),
        ("object-storage", "s3", "storage", r"S3Client|AmazonS3|aws-sdk-s3|software\.amazon\.awssdk\.services\.s3"),
        ("object-storage", "minio", "storage", r"MinioClient|io\.minio|\bminio\b"),
        ("email", "smtp", "email", r"JavaMailSender|jakarta\.mail|javax\.mail|spring\.mail|smtp://"),
        ("email", "sendgrid", "email", r"SendGrid|api\.sendgrid\.com|sendgrid-java"),
        ("email", "mailgun", "email", r"MailgunClient|api\.mailgun\.net|mailgun-java"),
        ("email", "amazon-ses", "email", r"SesClient|AmazonSimpleEmailService|software\.amazon\.awssdk\.services\.ses"),
        ("communications", "whatsapp", "messaging", r"WhatsAppClient|WHATSAPP_(?:API_)?URL|graph\.facebook\.com/[^\s\"']*/messages|api\.whatsapp\.com|wa\.me/"),
        ("communications", "twilio", "messaging", r"Twilio\.init|api\.twilio\.com|twilio-java|twilio-node"),
        ("payment", "stripe", "payment-gateway", r"StripeClient|Stripe\.apiKey|api\.stripe\.com|stripe-java|\"stripe\"\s*:"),
        ("payment", "midtrans", "payment-gateway", r"Midtrans|api\.midtrans\.com|app\.sandbox\.midtrans\.com|midtrans-client"),
        ("payment", "xendit", "payment-gateway", r"Xendit|api\.xendit\.co|xendit-java|xendit-node"),
        ("payment", "paypal", "payment-gateway", r"PayPalHttpClient|api-m\.paypal\.com|paypalhttp|paypal-checkout"),
        ("payment", "adyen", "payment-gateway", r"Adyen|checkout-live\.adyenpayments\.com|adyen-java-api-library"),
        ("payment", "doku", "payment-gateway", r"DokuClient|api\.doku\.com|sandbox\.doku\.com"),
        ("payment", "espay", "payment-gateway", r"Espay|api\.espay\.id|sandbox-api\.espay\.id"),
        ("identity", "keycloak", "identity", r"Keycloak|keycloak-spring|keycloak\.auth-server-url"),
        ("workflow", "flowable", "workflow", r"org\.flowable|flowable-spring|RuntimeService"),
    )
    lower_text = lower_text if lower_text is not None else text.lower()
    if any(marker in lower_text for marker in SYSTEM_EVIDENCE_HINTS):
        for kind, technology, role, pattern in patterns:
            technology_hints = SYSTEM_TECH_HINTS.get(technology, ())
            if technology_hints and not any(hint in lower_text for hint in technology_hints):
                continue
            if match := re.search(pattern, text, re.IGNORECASE):
                add(kind, technology, role, match)

    redis_roles = (
        ("cache", "cache", r"spring\.cache[^\n=]*[:=]\s*redis|RedisCacheManager"),
        ("cache", "session", r"spring\.session[^\n=]*[:=]\s*redis|RedisIndexedSessionRepository"),
        ("messaging", "pubsub", r"RedisListener|convertAndSend\s*\("),
        ("messaging", "queue", r"\.(?:rpush|lpush|lpop|rpop|blpop|brpop)\s*\("),
        (
            "cache", "datastore",
            r"StringRedisTemplate|spring\.(?:data\.)?redis|"
            r"\bRedis\.from_url\s*\(|\bfrom\s+redis\s+import\s+Redis\b|"
            r"opsFor(?:Value|Hash|List|Set|ZSet|Geo|HyperLogLog)|"
            r"bound(?:Value|Hash|List|Set|ZSet)Ops",
        ),
    )
    if "redis" in lower_text or "@cache" in lower_text:
        for kind, role, pattern in redis_roles:
            if match := re.search(pattern, text, re.IGNORECASE):
                add(kind, "redis", role, match)

    if extension in SOURCE_EXTENSIONS or extension in {".properties", ".yml", ".yaml", ".conf"}:
        ignored_hosts = {
            "www.w3.org", "maven.apache.org", "www.apache.org", "schemas.xmlsoap.org",
            "schema.org",
        }
        for match in re.finditer(r"https?://[^\s\"'<>]+", text):
            parsed_url = urlsplit(match.group(0))
            host = (parsed_url.hostname or "").lower().rstrip(".")
            if not host or host in ignored_hosts or host.endswith(".example.com"):
                continue
            try:
                port = parsed_url.port
            except ValueError:
                port = None
            identity = f"{host}:{port}" if port else host
            add("http-service", identity, "external-http", match, identity)

    return dependencies


@functools.lru_cache(maxsize=64)
def _discover_json_api_prefixes(
    repo_root: str, excludes: tuple[str, ...], override: str,
) -> tuple[str, ...]:
    """Find server-side CRNK/Katharsis prefixes without treating client DTOs as routes."""
    prefixes = {
        normalize_url(item)
        for item in override.split(",")
        if item.strip()
    }
    excluded = set(excludes)
    for directory, names, files in os.walk(repo_root):
        names[:] = [name for name in names if name not in excluded]
        for filename in files:
            if not filename.endswith(".properties"):
                continue
            path = Path(directory) / filename
            try:
                if path.stat().st_size > 256_000:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            prefixes.update(
                normalize_url(match.group(1).strip())
                for match in JSON_API_PREFIX_RE.finditer(text)
                if match.group(1).strip()
            )
    return tuple(sorted(prefixes))


def _json_api_prefixes(settings: Settings) -> tuple[str, ...]:
    return _discover_json_api_prefixes(
        str(settings.repo_root),
        tuple(sorted(settings.excludes)),
        os.getenv("GRAPH_ENGINE_JSON_API_PREFIXES", "").strip(),
    )


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
        safe_url = _safe_request_url(url)
        key = f"{method}:{safe_url}:{line}"
        self.requests.append(
            APIRequest(
                _entity_id(self.repo_name, self.rel_path, f"request:{key}"),
                method,
                safe_url,
                normalize_url(safe_url),
                line,
                system=_request_system_name(url),
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
JS_DEFAULT_IMPORT_RE = re.compile(
    r"(?:^|\n)\s*import\s+([A-Za-z_$][\w$]*)\s+from\s+['\"]([^'\"]+)['\"]",
    re.MULTILINE,
)
JS_NAMED_IMPORT_RE = re.compile(
    r"(?:^|\n)\s*import\s*\{([^}]+)\}\s*from\s*['\"]([^'\"]+)['\"]",
    re.MULTILINE | re.DOTALL,
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
JS_HTTP_CLIENT_RE = re.compile(
    r"\b([A-Za-z_$][\w$]*)\s*\.\s*"
    r"(get|post|put|patch|delete|options|head)\s*"
    r"(?:<.{0,600}?>\s*)?\(\s*([`'\"])(/.*?)\3",
    re.IGNORECASE | re.DOTALL,
)
NEXT_ROUTE_FUNCTION_RE = re.compile(
    r"\bexport\s+(?:async\s+)?function\s+(GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)\s*\(",
    re.IGNORECASE,
)
NEXT_ROUTE_CONST_RE = re.compile(
    r"\bexport\s+const\s+(GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)\s*=",
    re.IGNORECASE,
)
NEXT_ROUTE_DESTRUCTURE_RE = re.compile(r"\bexport\s+const\s*\{([^}]+)\}\s*=", re.DOTALL)
NEXT_ROUTE_EXPORT_RE = re.compile(r"\bexport\s*\{([^}]+)\}", re.DOTALL)
NEXT_ROUTE_FILE_NAMES = frozenset({"route.js", "route.jsx", "route.ts", "route.tsx"})
GO_FUNCTION_RE = re.compile(r"(?m)^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)\s*\(([^)]*)\)")
GO_STRUCT_RE = re.compile(r"(?m)^\s*type\s+([A-Za-z_]\w*)\s+struct\s*\{")
GO_IMPORT_RE = re.compile(r"(?m)^\s*import\s+(?:\w+\s+)?\"([^\"]+)\"")
GO_IMPORT_BLOCK_RE = re.compile(r"(?ms)^\s*import\s*\((.*?)\)")
GO_ROUTE_RE = re.compile(
    r"\b([A-Za-z_]\w*)\s*\.\s*(HandleFunc|Handle|GET|POST|PUT|PATCH|DELETE)"
    r"\s*\(\s*\"([^\"]*)\"(?:\s*,\s*([A-Za-z_]\w*))?"
)
GO_GROUP_RE = re.compile(
    r"(?m)\b([A-Za-z_]\w*)\s*:=\s*([A-Za-z_]\w*)\.Group\(\s*\"([^\"]*)\""
)
GO_REGISTER_ROUTES_RE = re.compile(
    r"\b([A-Za-z_]\w*)\.RegisterRoutes\(\s*([A-Za-z_]\w*)\b"
)
GO_HTTP_REQUEST_WITH_CONTEXT_RE = re.compile(
    r"\bhttp\.NewRequestWithContext\(\s*[^,\n]+,\s*([^,\n]+),\s*([^,\n]+),"
)
GO_HTTP_REQUEST_RE = re.compile(
    r"\bhttp\.NewRequest\(\s*([^,\n]+),\s*([^,\n]+),"
)
JAVA_IMPORT_RE = re.compile(r"(?m)^\s*import\s+(?:static\s+)?([\w.*]+)\s*;")
JAVA_COMMENT_TOKEN_RE = re.compile(
    r'"(?:\\.|[^"\\])*(?:"|\Z)'
    r"|'(?:\\.|[^'\\])*(?:'|\Z)"
    r"|`(?:\\.|[^`\\])*(?:`|\Z)"
    r"|//[^\r\n]*"
    r"|/\*.*?(?:\*/|\Z)",
    re.DOTALL,
)
NON_NEWLINE_RE = re.compile(r"[^\r\n]")
JAVA_TYPE_RE = re.compile(
    r"(?m)^\s*(?:public\s+|protected\s+|private\s+|abstract\s+|final\s+|sealed\s+|static\s+)*"
    r"(class|interface|record|enum)\s+([A-Za-z_$][\w$]*)([^\n{]*)"
)
JAVA_METHOD_RE = re.compile(
    r"(?m)^\s*(?!(?:new|return|throw)\b)(?:(?:public|protected|private)\s+)?"
    r"(?:(?:static|final|abstract|synchronized|native|default)\s+)*"
    r"(?:[\w$@.?<>\[\],]+\s+)+([A-Za-z_$][\w$]*)\s*\(([^)]*)\)"
)
JAVA_EXPLICIT_METHOD_RE = re.compile(
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
JAVA_SPRING_VIEW_RE = re.compile(r"@SpringView\s*\((.*?)\)", re.DOTALL)
JAVA_JSON_API_RESOURCE_RE = re.compile(r"@JsonApiResource\s*\([^)]*\btype\s*=\s*\"([^\"]+)\"", re.DOTALL)
JAVA_STRING_CONSTANT_RE = re.compile(
    r"(?m)\b(?:public\s+|private\s+|protected\s+)?(?:static\s+)?(?:final\s+)?String\s+"
    r"([A-Za-z_$][\w$]*)\s*=\s*\"([^\"]*)\"\s*;"
)
JAVA_STRING_ASSIGNMENT_RE = re.compile(
    r"\bString\s+([A-Za-z_$][\w$]*)\s*=\s*(.*?);", re.DOTALL
)
JAVA_REST_TEMPLATE_RE = re.compile(
    r"\b([A-Za-z_$][\w$]*)\s*\.(getForObject|getForEntity|postForObject|postForEntity|put|patchForObject|delete)\s*"
    r"\(\s*\"([^\"]+)\"",
    re.DOTALL,
)
JAVA_REST_TEMPLATE_VARIABLE_RE = re.compile(
    r"\b([A-Za-z_$][\w$]*)\s*\."
    r"(getForObject|getForEntity|postForObject|postForEntity|put|patchForObject|delete)\s*"
    r"\(\s*([A-Za-z_$][\w$]*)\b",
    re.DOTALL,
)
JAVA_REST_TEMPLATE_FACTORY_RE = re.compile(
    r"\brest(?:List)?Template\s*\(\s*\)\s*\.\s*"
    r"(getForObject|getForEntity|postForObject|postForEntity|put|patchForObject|delete)\s*"
    r"\(\s*([A-Za-z_$][\w$]*|\"[^\"]+\")",
    re.DOTALL,
)
JAVA_EXCHANGE_RE = re.compile(
    r"(?:\b[A-Za-z_$][\w$]*|new\s+RestTemplate\s*\([^)]*\))\s*\.exchange\s*\(\s*"
    r"([A-Za-z_$][\w$]*|\"[^\"]+\")\s*,\s*HttpMethod\.(GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)",
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
JAVA_LISTENER_RE = re.compile(
    r"\b([A-Za-z_$][\w$]*)\s*\.\s*add(Click|Selection|ValueChange|ItemClick|Attach|Detach)Listener\s*\([^;]*?->\s*\{",
    re.DOTALL,
)
JAVA_UI_EFFECT_RE = re.compile(
    r"\b([A-Za-z_$][\w$]*)\s*\.\s*"
    r"(setEnabled|setVisible|setReadOnly|setValue|setItems|setContent|clear|select|deselectAll|focus)"
    r"\s*\((.*?)\)\s*;",
    re.DOTALL,
)
JAVA_QUALIFIED_CALL_RE = re.compile(
    r"(?:\bnew\s+([A-Za-z_$][\w$]*)\s*\([^)]*\)|\b([A-Za-z_$][\w$]*))\s*\.\s*([A-Za-z_$][\w$]*)\s*\("
)
JAVA_LOCAL_CALL_RE = re.compile(r"(?<![.\w])([A-Za-z_$][\w$]*)\s*\(")
JAVA_IF_RE = re.compile(r"\bif\s*\(([^;{}]*?)\)\s*\{", re.DOTALL)
JAVA_VARIABLE_TYPE_RE = re.compile(
    r'\b([A-Z][A-Za-z0-9_$]*(?:<[^;=()]+>)?)\s+([A-Za-z_$][\w$]*)\s*(?=[=;,)\"])'
)
JAVA_PROCESS_START_RE = re.compile(
    r"\b(?:startProcessInstanceByKey(?:AndTenantId)?|startProcess)\s*\(\s*"
    r'("(?:\\.|[^"\\])*"|(?:[A-Za-z_$][\w$]*\.)*[A-Za-z_$][\w$]*(?:\s*\(\s*\))?)'
)
JAVA_STRING_FIELD_ASSIGNMENT_RE = re.compile(
    r"\b(?:this\.)?([A-Za-z_$][\w$]*)\s*=\s*\"([^\"]+)\"\s*;"
)
JAVA_VALUE_FIELD_RE = re.compile(
    r'@Value\s*\(\s*"([^"]+)"\s*\)\s*'
    r'(?:private\s+|protected\s+|public\s+)?(?:static\s+)?(?:final\s+)?String\s+([A-Za-z_$][\w$]*)',
    re.DOTALL,
)
JAVA_MESSAGE_LISTENER_RE = re.compile(
    r"@(KafkaListener|RabbitListener|JmsListener|RedisListener|StreamListener)\s*\((.*?)\)",
    re.DOTALL,
)
JAVA_MESSAGE_PUBLISH_RE = re.compile(
    r"\b([A-Za-z_$][\w$]*)\s*\.\s*(send|convertAndSend)\s*\((.*?)\)\s*;",
    re.DOTALL,
)
JAVA_RABBIT_BINDING_RE = re.compile(
    r"BindingBuilder\s*\.\s*bind\s*\(([A-Za-z_$][\w$]*(?:\(\))?)\)\s*"
    r"\.\s*to\s*\(([A-Za-z_$][\w$]*(?:\(\))?)\)\s*"
    r"\.\s*with\s*\(([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)?)\)",
    re.DOTALL,
)


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _matching_brace(text: str, opening: int) -> int:
    depth = 0
    quote = ""
    escaped = False
    index = opening
    while index < len(text):
        char = text[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
        elif char in {'"', "'"}:
            quote = char
        elif text.startswith("//", index):
            newline = text.find("\n", index + 2)
            index = len(text) if newline < 0 else newline
        elif text.startswith("/*", index):
            closing = text.find("*/", index + 2)
            index = len(text) if closing < 0 else closing + 1
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return len(text)


def _brace_pairs(text: str) -> dict[int, int]:
    pairs: dict[int, int] = {}
    stack: list[int] = []
    tokens = re.compile(
        r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[{}]',
        re.DOTALL,
    )
    for match in tokens.finditer(text):
        token = match.group(0)
        if token == "{":
            stack.append(match.start())
        elif token == "}" and stack:
            pairs[stack.pop()] = match.start()
    return pairs


def _java_condition_at(text: str, offset: int, method_start: int) -> str:
    conditions: list[tuple[int, str]] = []
    for match in re.finditer(r"\bif\s*\(([^;{}]*?)\)\s*\{", text[method_start:offset], re.DOTALL):
        absolute_open = method_start + match.end() - 1
        if _matching_brace(text, absolute_open) >= offset:
            condition = re.sub(r"\s+", " ", match.group(1)).strip()
            conditions.append((absolute_open, condition[:240]))
    return conditions[-1][1] if conditions else ""


def _java_system_hint(expression: str, text: str) -> str:
    constants = dict(JAVA_STRING_CONSTANT_RE.findall(text))
    direct = re.search(r"\b([A-Z][A-Z0-9_]*(?:URL|URL_KEY|_URL_KEY))\b", expression)
    if direct:
        return direct.group(1)
    config_call = re.search(
        r"\bgetConfig(?:Value)?\s*\(\s*(?:[A-Za-z_$][\w$]*\.)?"
        r"(?:([A-Z][A-Z0-9_]*)|\"([^\"]+)\")\s*\)",
        expression,
    )
    if config_call:
        key = config_call.group(1) or config_call.group(2)
        return constants.get(key, key)
    receiver = re.match(
        r"\s*(?:this\.)?([A-Za-z_$][\w$]*)"
        r"(?:\.[A-Za-z_$][\w$]*\s*\([^)]*\))*\s*\+",
        expression,
    )
    if not receiver:
        return ""
    variable = receiver.group(1)
    assignment = re.search(
        rf"\b{re.escape(variable)}\s*=\s*[^;]*?getConfig(?:Value)?\s*\(\s*"
        rf"(?:[A-Za-z_$][\w$]*\.)?(?:([A-Z][A-Z0-9_]*)|\"([^\"]+)\")\s*\)",
        text,
        re.DOTALL,
    )
    if assignment:
        key = assignment.group(1) or assignment.group(2)
        return constants.get(key, key)
    # An arbitrary receiver name is not a service identity. Keep it unresolved.
    return ""


def _template_url(value: str) -> str:
    # Query-builder expressions do not change the route path. Nested template
    # literals are often truncated by the lightweight call regex at their first backtick.
    value = re.sub(r"\$\{[^}]*(?:query|suffix)[^}]*\}", "", value, flags=re.IGNORECASE)
    dynamic_start = value.find("${")
    if dynamic_start >= 0:
        remainder = value[dynamic_start + 2:]
        if "?" in remainder and re.match(r"(?:q|query|suffix)\b", remainder.strip(), re.IGNORECASE):
            value = value[:dynamic_start]
    return re.sub(r"\$\{[^}]+\}", "{param}", value)


@functools.lru_cache(maxsize=64)
def _discover_javascript_http_clients(
    repo_root: str, excludes: tuple[str, ...],
) -> tuple[tuple[tuple[str, str, str, str], ...], tuple[tuple[str, str], ...]]:
    """Return configured Axios modules and Next.js rewrite prefixes."""
    root = Path(repo_root)
    excluded = set(excludes)
    rewrite_rules: list[tuple[str, str]] = []
    client_rows: list[tuple[str, str, str, str]] = []
    candidates: list[Path] = []
    for directory, names, files in os.walk(root):
        names[:] = [name for name in names if name not in excluded]
        base = Path(directory)
        for filename in files:
            lower_name = filename.lower()
            if lower_name.startswith("next.config.") or (
                Path(filename).suffix.lower() in {".js", ".jsx", ".ts", ".tsx"}
                and Path(filename).stem.lower() in {"api", "axios", "client", "http", "httpclient"}
            ):
                candidates.append(base / filename)
    candidates.sort(key=lambda path: (not path.name.lower().startswith("next.config."), path.as_posix()))
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if path.name.lower().startswith("next.config."):
            env_match = re.search(r"process\.env\.([A-Z][A-Z0-9_]*)", text)
            for source_match in re.finditer(r"\bsource\s*:\s*['\"]([^'\"]+)['\"]", text):
                prefix = re.sub(r"/:\w+\*$", "", source_match.group(1)).rstrip("/") or "/"
                if env_match:
                    rewrite_rules.append((normalize_url(prefix), env_match.group(1)))
            continue
        if "axios.create" not in text:
            continue
        rel_path = path.relative_to(root).as_posix()
        canonical = rel_path.rsplit(".", 1)[0]
        for match in re.finditer(
            r"\b(?:export\s+)?const\s+([A-Za-z_$][\w$]*)\s*=\s*axios\.create\s*\(\s*\{(.*?)\}\s*\)",
            text,
            re.DOTALL,
        ):
            base_match = re.search(r"\bbaseURL\s*:\s*['\"]([^'\"]+)['\"]", match.group(2))
            if not base_match:
                continue
            raw_base_url = base_match.group(1)
            base_url = normalize_url(raw_base_url)
            system = next(
                (name for prefix, name in rewrite_rules if base_url == prefix or base_url.startswith(prefix + "/")),
                _request_system_name(raw_base_url),
            )
            client_rows.append((canonical, match.group(1), base_url, system))
    return tuple(client_rows), tuple(rewrite_rules)


def _javascript_import_path(rel_path: str, module: str) -> str:
    if module.startswith("@/"):
        return "src/" + module[2:].lstrip("/")
    if module.startswith("."):
        return posixpath.normpath(posixpath.join(posixpath.dirname(rel_path), module))
    return module


def _nextjs_route_path(rel_path: str) -> str | None:
    parts = Path(rel_path).parts
    if not parts or parts[-1].lower() not in NEXT_ROUTE_FILE_NAMES:
        return None
    app_index = next(
        (index for index in range(len(parts) - 1) if parts[index:index + 2] == ("src", "app")),
        None,
    )
    if app_index is None:
        return None
    segments = [
        segment
        for segment in parts[app_index + 2:-1]
        if not (segment.startswith("(") and segment.endswith(")"))
        and not segment.startswith("@")
    ]
    return "/" + "/".join(segments) if segments else "/"


def _nextjs_routes(
    text: str, repo_name: str, rel_path: str, functions: list[Symbol],
) -> list[Route]:
    route_path = _nextjs_route_path(rel_path)
    if route_path is None:
        return []
    function_names = {function.name for function in functions}
    methods: dict[str, tuple[int, str | None]] = {}
    for match in NEXT_ROUTE_FUNCTION_RE.finditer(text):
        method = match.group(1).upper()
        handler_id = _entity_id(repo_name, rel_path, method) if method in function_names else None
        methods.setdefault(method, (match.start(), handler_id))
    for match in NEXT_ROUTE_CONST_RE.finditer(text):
        method = match.group(1).upper()
        handler_id = _entity_id(repo_name, rel_path, method) if method in function_names else None
        methods.setdefault(method, (match.start(), handler_id))
    for export_re in (NEXT_ROUTE_DESTRUCTURE_RE, NEXT_ROUTE_EXPORT_RE):
        for match in export_re.finditer(text):
            for item in match.group(1).split(","):
                method = re.split(r"\s+as\s+", item.strip(), flags=re.IGNORECASE)[-1].strip().upper()
                if method in {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"}:
                    methods.setdefault(method, (match.start(), None))
    return [
        Route(
            f"{repo_name}:{method}:{normalize_url(route_path)}",
            method,
            route_path,
            normalize_url(route_path),
            _line_number(text, offset),
            handler_id,
        )
        for method, (offset, handler_id) in sorted(methods.items(), key=lambda item: item[1][0])
    ]


def _parse_javascript(
    text: str, repo_name: str, rel_path: str, settings: Settings,
) -> tuple[list[str], list[Symbol], list[Symbol], list[APIRequest], list[Route]]:
    imports = sorted(set(IMPORT_RE.findall(text)))
    client_rows, rewrite_rules = _discover_javascript_http_clients(
        str(settings.repo_root), tuple(sorted(settings.excludes)),
    )
    clients_by_path = {
        canonical: (variable, base_url, system)
        for canonical, variable, base_url, system in client_rows
    }
    resolved_clients: dict[str, tuple[str, str]] = {}
    current_module = rel_path.rsplit(".", 1)[0]
    if current_module in clients_by_path:
        variable, base_url, system = clients_by_path[current_module]
        resolved_clients[variable] = (base_url, system)
    for import_match in JS_DEFAULT_IMPORT_RE.finditer(text):
        canonical = _javascript_import_path(rel_path, import_match.group(2))
        if canonical in clients_by_path:
            _, base_url, system = clients_by_path[canonical]
            resolved_clients[import_match.group(1)] = (base_url, system)
    for import_match in JS_NAMED_IMPORT_RE.finditer(text):
        canonical = _javascript_import_path(rel_path, import_match.group(2))
        client = clients_by_path.get(canonical)
        if client is None:
            continue
        exported_name, base_url, system = client
        for item in import_match.group(1).split(","):
            names = re.split(r"\s+as\s+", item.strip())
            if names and names[0] == exported_name:
                resolved_clients[names[-1]] = (base_url, system)
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

    def add_request(method: str, url: str, offset: int, system: str = "") -> None:
        line = _line_number(text, offset)
        clean_url = _safe_request_url(_template_url(url))
        request_system = system or _request_system_name(url)
        if not request_system and clean_url.startswith("/"):
            request_system = next(
                (
                    name
                    for prefix, name in rewrite_rules
                    if clean_url == prefix or clean_url.startswith(prefix.rstrip("/") + "/")
                ),
                "",
            )
        requests.append(
            APIRequest(
                _entity_id(repo_name, rel_path, f"request:{method}:{clean_url}:{line}"),
                method.upper(),
                clean_url,
                normalize_url(clean_url),
                line,
                system=request_system,
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
    for match in JS_HTTP_CLIENT_RE.finditer(text):
        receiver = match.group(1)
        path = match.group(4)
        client = resolved_clients.get(receiver)
        if client is None:
            if receiver not in {"api", "apiClient", "httpClient"} or not path.startswith("/api"):
                continue
            add_request(match.group(2), path, match.start())
            continue
        base_url, system = client
        full_url = normalize_url(f"{base_url.rstrip('/')}/{path.lstrip('/')}")
        add_request(match.group(2), full_url, match.start(), system)

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
    routes.extend(_nextjs_routes(text, repo_name, rel_path, functions))
    return imports, classes, functions, requests, routes


def _go_group_paths(text: str) -> dict[str, str]:
    groups: dict[str, str] = {}
    pending = list(GO_GROUP_RE.finditer(text))
    while pending:
        changed = False
        remaining: list[re.Match[str]] = []
        for match in pending:
            child, parent, path = match.groups()
            if parent in groups:
                groups[child] = _join_route(groups[parent], path)
                changed = True
            elif not any(item.group(1) == parent for item in pending):
                groups[child] = normalize_url(path)
                changed = True
            else:
                remaining.append(match)
        if not changed:
            for match in remaining:
                groups[match.group(1)] = normalize_url(match.group(3))
            break
        pending = remaining
    return groups


def _go_method(expression: str) -> str:
    value = expression.strip()
    match = re.fullmatch(r"(?:http\.)?Method(GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)", value, re.IGNORECASE)
    if match:
        return match.group(1).upper()
    if literal := re.fullmatch(r'"(GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)"', value, re.IGNORECASE):
        return literal.group(1).upper()
    return "DYNAMIC"


def _go_symbolic_url(expression: str) -> str:
    value = expression.strip()
    if literal := re.fullmatch(r'"([^"\n]*)"', value):
        return literal.group(1)
    if sprintf := re.match(r'fmt\.Sprintf\(\s*"([^"\n]*)"\s*(?:,\s*(.*))?\)$', value):
        arguments = iter(
            part.strip() for part in (sprintf.group(2) or "").split(",")
        )

        def replace_format(_: re.Match[str]) -> str:
            argument = next(arguments, "")
            if re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+", argument):
                return f"${{{argument}}}"
            return "{param}"

        return re.sub(r"%[-+#0-9.]*[a-zA-Z]", replace_format, sprintf.group(1))
    parts = [part.strip() for part in re.split(r"\s*\+\s*", value)]
    if len(parts) > 1:
        rendered: list[str] = []
        for part in parts:
            if literal := re.fullmatch(r'"([^"\n]*)"', part):
                rendered.append(literal.group(1))
            else:
                rendered.append(f"${{{part}}}")
        return "".join(rendered)
    return f"${{{value}}}"


def _go_requests(
    text: str, repo_name: str, rel_path: str, functions: list[Symbol],
) -> list[APIRequest]:
    helper_urls: dict[str, list[str]] = {}
    function_matches = list(GO_FUNCTION_RE.finditer(text))
    for index, match in enumerate(function_matches):
        end = function_matches[index + 1].start() if index + 1 < len(function_matches) else len(text)
        literals = re.findall(r'\breturn\s+"(https?://[^"\n]+)"', text[match.start():end])
        if literals:
            helper_urls[match.group(1)] = list(dict.fromkeys(literals))

    assignments: dict[str, list[tuple[int, str]]] = {}
    for match in re.finditer(
        r"(?ms)\b([A-Za-z_]\w*)\s*:=\s*(fmt\.Sprintf\(.*?\))", text,
    ):
        assignments.setdefault(match.group(1), []).append((match.start(), match.group(2).strip()))
    for match in re.finditer(r"(?m)\b([A-Za-z_]\w*)\s*:=\s*([^\n]+)", text):
        if match.group(2).lstrip().startswith("fmt.Sprintf("):
            continue
        assignments.setdefault(match.group(1), []).append((match.start(), match.group(2).strip()))

    function_offsets = [(match.start(), symbol.id) for match, symbol in zip(function_matches, functions)]

    def source_function(offset: int) -> str:
        index = bisect.bisect_right([item[0] for item in function_offsets], offset) - 1
        return function_offsets[index][1] if index >= 0 else ""

    def resolve(expression: str, offset: int) -> list[str]:
        value = expression.strip()
        if call := re.fullmatch(r"(?:[A-Za-z_]\w*\.)?([A-Za-z_]\w*)\(\)", value):
            if call.group(1) in helper_urls:
                return helper_urls[call.group(1)]
        if re.fullmatch(r"[A-Za-z_]\w*", value):
            candidates = [item for item in assignments.get(value, []) if item[0] < offset]
            if candidates:
                assignment_offset, assignment = max(candidates, key=lambda item: item[0])
                return resolve(assignment, assignment_offset)
        return [_go_symbolic_url(value)]

    requests: list[APIRequest] = []
    seen: set[tuple[str, str, int]] = set()
    matches = [*GO_HTTP_REQUEST_WITH_CONTEXT_RE.finditer(text), *GO_HTTP_REQUEST_RE.finditer(text)]
    for match in sorted(matches, key=lambda item: item.start()):
        method = _go_method(match.group(1))
        for url in resolve(match.group(2), match.start()):
            safe_url = _safe_request_url(url)
            line = _line_number(text, match.start())
            key = (method, safe_url, line)
            if key in seen:
                continue
            seen.add(key)
            requests.append(
                APIRequest(
                    _entity_id(repo_name, rel_path, f"request:{method}:{safe_url}:{line}"),
                    method,
                    safe_url,
                    normalize_url(safe_url),
                    line,
                    source_function(match.start()),
                    _request_system_name(url),
                )
            )
    return requests


def _parse_go(text: str, repo_name: str, rel_path: str) -> tuple[list[str], list[Symbol], list[Symbol], list[APIRequest], list[Route]]:
    text = _mask_evidence_comments(text, ".go")
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
    requests = _go_requests(text, repo_name, rel_path, functions)
    groups = _go_group_paths(text)
    routes: list[Route] = []
    for match in GO_ROUTE_RE.finditer(text):
        receiver = match.group(1)
        route_call = match.group(2).upper()
        path = _join_route(groups.get(receiver, ""), match.group(3))
        method = route_call if route_call in {"GET", "POST", "PUT", "PATCH", "DELETE"} else "GET"
        handler = match.group(4)
        routes.append(Route(f"{repo_name}:{method}:{normalize_url(path)}", method, path, normalize_url(path), _line_number(text, match.start()), _entity_id(repo_name, rel_path, handler) if handler else None))
    if rel_path.endswith("_test.go"):
        requests, routes = [], []
    return sorted(imports), classes, functions, requests, routes


def _java_annotation_path(arguments: str | None) -> str:
    if not arguments:
        return "/"
    match = re.search(r'(?:\b(?:value|path)\s*=\s*)?(?:\{\s*)?"([^"]*)"', arguments)
    if not match:
        return "/"
    value = match.group(1)
    return re.sub(
        r"\$\{[^}:]+(?::([^}]*))?}",
        lambda item: item.group(1) if item.group(1) is not None else "{param}",
        value,
    )


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


def _spring_property_default(value: str) -> str:
    match = re.fullmatch(r"\$\{[^}:]+(?::([^}]*))?}", value.strip())
    if not match:
        return value.strip()
    return match.group(1) or value.strip()


def _split_java_arguments(arguments: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    quote = ""
    escaped = False
    for index, char in enumerate(arguments):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
        elif char in {'"', "'"}:
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}" and depth:
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(arguments[start:index].strip())
            start = index + 1
    parts.append(arguments[start:].strip())
    return [part for part in parts if part]


def _next_java_method(text: str, offset: int) -> re.Match[str] | None:
    match = JAVA_METHOD_RE.search(text, offset)
    return match if match and match.start() - offset < 800 else None


def _java_expression_url(expression: str) -> str | None:
    tokens = re.findall(r'"(?:\\.|[^"\\])*"|[^+]+', expression)
    first_path = next(
        (index for index, token in enumerate(tokens) if token.strip().startswith('"') and "/" in token),
        None,
    )
    if first_path is None:
        return None
    parts: list[str] = []
    for token in tokens[first_path:]:
        token = token.strip()
        if not token:
            continue
        if token.startswith('"') and token.endswith('"'):
            parts.append(token[1:-1])
        else:
            parts.append("{param}")
    path = "".join(parts)
    query_index = path.find("?")
    if query_index >= 0:
        path = path[:query_index]
    return path if path.startswith("/") else "/" + path


def _mask_java_comments(text: str) -> str:
    """Replace Java comments with spaces while preserving strings, offsets, and newlines."""
    def mask_token(match: re.Match[str]) -> str:
        token = match.group(0)
        if token.startswith(('"', "'", "`")):
            return token
        return NON_NEWLINE_RE.sub(" ", token)

    return JAVA_COMMENT_TOKEN_RE.sub(mask_token, text)


def _mask_python_comments(text: str) -> str:
    lines = [list(line) for line in text.splitlines(keepends=True)]
    try:
        tokens = tokenize.generate_tokens(io.StringIO(text).readline)
        for token in tokens:
            if token.type != tokenize.COMMENT:
                continue
            row, start = token.start
            _, end = token.end
            for index in range(start, min(end, len(lines[row - 1]))):
                if lines[row - 1][index] not in "\r\n":
                    lines[row - 1][index] = " "
    except (IndentationError, tokenize.TokenError):
        return text
    return "".join("".join(line) for line in lines)


def _mask_evidence_comments(text: str, extension: str) -> str:
    if extension == ".py":
        return _mask_python_comments(text)
    if extension in {".java", ".js", ".jsx", ".ts", ".tsx", ".go", ".prisma"}:
        return _mask_java_comments(text)
    if extension == ".sql":
        masked = re.sub(
            r"/\*.*?(?:\*/|\Z)",
            lambda match: NON_NEWLINE_RE.sub(" ", match.group(0)),
            text,
            flags=re.DOTALL,
        )
        return re.sub(
            r"(?m)--[^\r\n]*$",
            lambda match: " " * len(match.group(0)),
            masked,
        )
    masked = re.sub(
        r"<!--.*?-->",
        lambda match: NON_NEWLINE_RE.sub(" ", match.group(0)),
        text,
        flags=re.DOTALL,
    )
    return re.sub(
        r"(?m)^(\s*)[#;].*$",
        lambda match: match.group(1) + " " * (len(match.group(0)) - len(match.group(1))),
        masked,
    )


def _spring_view_route(text: str) -> str | None:
    match = JAVA_SPRING_VIEW_RE.search(text)
    if not match:
        return None
    arguments = match.group(1)
    literal = re.search(r'\bname\s*=\s*"([^"]*)"', arguments)
    if literal:
        return normalize_url("/" + literal.group(1).lstrip("/"))
    reference = re.search(r"\bname\s*=\s*(?:[A-Za-z_$][\w$]*\.)?([A-Za-z_$][\w$]*)", arguments)
    if reference:
        constants = dict(JAVA_STRING_CONSTANT_RE.findall(text))
        if reference.group(1) in constants:
            return normalize_url("/" + constants[reference.group(1)].lstrip("/"))
    return None


def _java_url_helpers(text: str) -> dict[str, tuple[str, str]]:
    helpers: dict[str, tuple[str, str]] = {}
    pattern = re.compile(
        r"\bString\s+([A-Za-z_$][\w$]*(?:Url|URL))\s*\([^)]*\)\s*\{"
        r".*?\breturn\s+(.*?);",
        re.DOTALL,
    )
    for match in pattern.finditer(text):
        expression = match.group(2)
        if url := _java_expression_url(expression):
            helpers[match.group(1)] = (url, _java_system_hint(expression, text))
    return helpers


def _java_builder_path(text: str, offset: int) -> str:
    method_starts = [match.start() for match in JAVA_METHOD_RE.finditer(text) if match.start() < offset]
    start = method_starts[-1] if method_starts else max(0, offset - 1500)
    segment = text[start:offset]
    parts: list[str] = []
    for match in re.finditer(r"\.path\s*\((.*?)\)", segment, re.DOTALL):
        if path := _java_expression_url(match.group(1)):
            parts.append(path)
    return "".join(part.rstrip("/") for part in parts)


def _parse_java(
    text: str, repo_name: str, rel_path: str, json_api_prefixes: tuple[str, ...] = ()
) -> tuple[
    list[str], list[Symbol], list[Symbol], list[APIRequest], list[Route], str | None,
    list[str], list[str], list[UIAction], list[FunctionCall], list[ProcessStart],
    list[MessageUse], list[MessageBinding],
]:
    imports = sorted(set(JAVA_IMPORT_RE.findall(text)))
    comment_sensitive_markers = (
        "startProcess", "@JobWorker", "@Process", "JpaRestController", "FlowController",
    )
    comment_free_text = (
        _mask_java_comments(text)
        if any(marker in text for marker in comment_sensitive_markers)
        else text
    )
    newline_offsets = [match.start() for match in re.finditer("\n", text)]

    def line_at(offset: int) -> int:
        return bisect.bisect_right(newline_offsets, offset) + 1
    behavioral_file = any(
        marker in text
        for marker in (
            "@SpringView", "@Route", "@RestController", "@Controller", "@Service", "@Component",
            "@Repository", "RuntimeService", "JavaDelegate", "addClickListener",
            "addSelectionListener", "addValueChangeListener", "restTemplate", "WebClient",
            "@KafkaListener", "@RabbitListener", "@JmsListener", "@RedisListener",
            "KafkaTemplate", "RabbitTemplate", "RedisTemplate", "JmsTemplate",
        )
    )
    ui_behavioral_file = any(
        marker in text
        for marker in (
            "@SpringView", "@Route", "addClickListener", "addSelectionListener",
            "addValueChangeListener", "addItemClickListener",
        )
    )
    brace_pairs = _brace_pairs(text) if behavioral_file else {}
    type_matches = list(JAVA_TYPE_RE.finditer(text))
    primary_type = type_matches[0].group(2) if type_matches else Path(rel_path).stem
    package_match = re.search(r"(?m)^\s*package\s+([\w.]+)\s*;", text)
    package_name = package_match.group(1) if package_match else ""
    classes = [
        Symbol(
            _entity_id(repo_name, rel_path, match.group(2)),
            match.group(2),
            line_at(match.start()),
            f"{match.group(1)} {match.group(2)}{match.group(3).strip()}",
            f"{package_name}.{match.group(2)}" if package_name else match.group(2),
            [match.group(2), match.group(2)[:1].lower() + match.group(2)[1:]],
        )
        for match in type_matches
    ]
    functions: list[Symbol] = []
    method_ids: dict[tuple[str, int], str] = {}
    method_regions: list[tuple[int, int, str, str]] = []
    method_matches = list((JAVA_METHOD_RE if ui_behavioral_file else JAVA_EXPLICIT_METHOD_RE).finditer(text))
    for match in method_matches:
        method_name, arguments = match.group(1), match.group(2)
        if method_name in {"if", "for", "while", "switch", "catch", "try", "synchronized"}:
            continue
        argument_types = [
            re.sub(r"\s+[A-Za-z_$][\w$]*$", "", item.strip())
            for item in arguments.split(",")
            if item.strip()
        ]
        entity_name = f"{primary_type}.{method_name}({','.join(argument_types)})"
        symbol = Symbol(
            _entity_id(repo_name, rel_path, entity_name),
            f"{primary_type}.{method_name}",
            line_at(match.start()),
            f"{method_name}({arguments.strip()})",
        )
        functions.append(symbol)
        method_ids[(match.start(), match.end())] = symbol.id
        opening = text.find("{", match.end(), min(len(text), match.end() + 500))
        closing = brace_pairs.get(opening, match.end()) if opening >= 0 else match.end()
        method_regions.append((match.start(), closing, symbol.id, method_name))

    method_starts = [item[0] for item in method_regions]

    def source_method(offset: int) -> tuple[str, int]:
        index = bisect.bisect_right(method_starts, offset) - 1
        if index < 0 or offset > method_regions[index][1]:
            return "", 0
        region = method_regions[index]
        return region[2], region[0]

    condition_regions: list[tuple[int, int, str]] = []
    for match in JAVA_IF_RE.finditer(text) if behavioral_file else ():
        opening = match.end() - 1
        closing = brace_pairs.get(opening)
        if closing is None:
            continue
        condition = re.sub(r"\s+", " ", match.group(1)).strip()[:500]
        condition_regions.append((opening, closing, condition))
        else_match = re.match(r"\s*else\s*\{", text[closing + 1:])
        if else_match:
            else_opening = closing + 1 + else_match.end() - 1
            if else_closing := brace_pairs.get(else_opening):
                condition_regions.append(
                    (else_opening, else_closing, f"NOT ({condition})"[:500])
                )

    def condition_at(offset: int, method_start: int) -> str:
        candidates = [item for item in condition_regions if method_start <= item[0] <= offset <= item[1]]
        return candidates[-1][2] if candidates else ""

    frameworks: list[str] = []
    joined_imports = "\n".join(imports)
    if "org.springframework" in joined_imports or "@SpringBootApplication" in text:
        frameworks.append("spring-boot")
    if "com.vaadin" in joined_imports or "@Route" in text or "@SpringView" in text:
        frameworks.append("vaadin")
    if "org.flowable" in joined_imports or "JavaDelegate" in text or "RuntimeService" in text:
        frameworks.append("flowable")

    vaadin_route: str | None = None
    vaadin_match = JAVA_VAADIN_ROUTE_RE.search(text)
    if vaadin_match:
        vaadin_route = normalize_url(_java_annotation_path(vaadin_match.group(1)))
    if vaadin_route is None:
        vaadin_route = _spring_view_route(text)

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

    def add_request(method: str, url: str, offset: int, system: str = "") -> None:
        line = line_at(offset)
        safe_url = _safe_request_url(url)
        normalized = normalize_url(safe_url)
        source_function_id, _ = source_method(offset)
        requests.append(
            APIRequest(
                _entity_id(repo_name, rel_path, f"request:{method}:{normalized}:{line}"),
                method,
                safe_url,
                normalized,
                line,
                source_function_id,
                system or _request_system_name(url),
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
                    line_at(mapping.start()),
                    handler_id,
                )
            )

    json_api_match = JAVA_JSON_API_RESOURCE_RE.search(text)
    if json_api_match:
        frameworks.append("crnk-jsonapi")
        for prefix in json_api_prefixes:
            resource_path = normalize_url(f"{prefix}/{json_api_match.group(1)}")
            for method, suffix in (
                ("GET", ""), ("POST", ""), ("GET", "/{param}"), ("PUT", "/{param}"),
                ("PATCH", "/{param}"), ("DELETE", "/{param}"),
            ):
                route_path = normalize_url(resource_path + suffix)
                routes.append(
                    Route(
                        f"{repo_name}:{method}:{route_path}", method, route_path, route_path,
                        line_at(json_api_match.start()), None,
                    )
                )

    inheritance_text = comment_free_text
    qualified_base = r"(?:[A-Za-z_$][\w$]*\.)*"
    inherited_jpa_rest = re.search(
        rf"\bextends\s+{qualified_base}(?:FilterableJpaRestController|JpaRestController)\b",
        inheritance_text,
    )
    if inherited_jpa_rest and class_prefix:
        frameworks.append("spring-jpa-rest")
        for method, suffix in (
            ("GET", ""), ("POST", ""), ("GET", "/{param}"), ("PUT", "/{param}"),
            ("PATCH", "/{param}"), ("DELETE", "/{param}"),
        ):
            route_path = normalize_url(class_prefix + suffix)
            routes.append(
                Route(
                    f"{repo_name}:{method}:{route_path}", method, route_path, route_path,
                    line_at(inherited_jpa_rest.start()), None,
                )
            )

    inherited_read_only = re.search(
        rf"\bextends\s+{qualified_base}ReadOnlyJpaRestController\b",
        inheritance_text,
    )
    if inherited_read_only and class_prefix:
        frameworks.append("spring-read-only-rest")
        for suffix in ("", "/{param}"):
            route_path = normalize_url(class_prefix + suffix)
            routes.append(
                Route(
                    f"{repo_name}:GET:{route_path}", "GET", route_path, route_path,
                    line_at(inherited_read_only.start()), None,
                )
            )

    inherited_flow = re.search(
        rf"\bextends\s+{qualified_base}FlowController\b",
        inheritance_text,
    )
    if inherited_flow and class_prefix:
        frameworks.append("flowable-rest")
        for method, suffix in (
            ("GET", ""), ("GET", "/{param}"), ("POST", ""),
            ("POST", "/submit"), ("POST", "/update"),
        ):
            route_path = normalize_url(class_prefix + suffix)
            routes.append(
                Route(
                    f"{repo_name}:{method}:{route_path}", method, route_path, route_path,
                    line_at(inherited_flow.start()), None,
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
    if "RestTemplate" in text or "restTemplate" in text:
        request_text = _mask_java_comments(text)
        url_helpers = _java_url_helpers(request_text)
        service_base = url_helpers.get("getServiceUrl", (None, ""))[0]
        rest_template_names = set(
            re.findall(r"\bRestTemplate\s+([A-Za-z_$][\w$]*)", request_text)
        )
        for match in JAVA_REST_TEMPLATE_RE.finditer(request_text):
            receiver, operation, url = match.groups()
            if receiver in rest_template_names:
                add_request(rest_methods[operation], url, match.start())
        assignments: dict[str, list[tuple[int, str, str]]] = {}
        for assignment in JAVA_STRING_ASSIGNMENT_RE.finditer(request_text):
            expression = assignment.group(2)
            if url := _java_expression_url(expression):
                system = _java_system_hint(expression, request_text)
                helper_call = re.match(r"\s*([A-Za-z_$][\w$]*)\s*\(\s*\)\s*\+", expression)
                if helper_call and helper_call.group(1) in url_helpers:
                    helper_url, helper_system = url_helpers[helper_call.group(1)]
                    url = helper_url.rstrip("/") + url
                    system = helper_system or system
                assignments.setdefault(assignment.group(1), []).append(
                    (assignment.start(), url, system)
                )
        for match in JAVA_REST_TEMPLATE_VARIABLE_RE.finditer(request_text):
            receiver, operation, url_argument = match.groups()
            if receiver not in rest_template_names:
                continue
            candidates = [item for item in assignments.get(url_argument, []) if item[0] < match.start()]
            if candidates:
                add_request(
                    rest_methods[operation], candidates[-1][1], match.start(), candidates[-1][2]
                )
        for match in JAVA_REST_TEMPLATE_FACTORY_RE.finditer(request_text):
            operation, url_argument = match.groups()
            if url_argument.startswith('"'):
                add_request(rest_methods[operation], url_argument.strip('"'), match.start())
                continue
            candidates = [item for item in assignments.get(url_argument, []) if item[0] < match.start()]
            if candidates:
                add_request(rest_methods[operation], candidates[-1][1], match.start(), candidates[-1][2])
        for match in JAVA_EXCHANGE_RE.finditer(request_text):
            url_argument, method = match.groups()
            system = ""
            if url_argument.startswith('"'):
                url = url_argument.strip('"')
            else:
                candidates = [item for item in assignments.get(url_argument, []) if item[0] < match.start()]
                url = candidates[-1][1] if candidates else None
                system = candidates[-1][2] if candidates else ""
            if url:
                if service_base and url.startswith("/") and "getServiceUrl" in request_text[max(0, match.start() - 1800):match.start()]:
                    url = service_base.rstrip("/") + url
                add_request(method, url, match.start(), system)
        if service_base:
            service_system = url_helpers["getServiceUrl"][1]
            for match in re.finditer(r"\b(findMany|findOne)\s*\(", request_text):
                path = _java_builder_path(request_text, match.start())
                add_request("GET", service_base.rstrip("/") + path, match.start(), service_system)
            for operation, method in (("postForObject", "POST"), ("postForEntity", "POST"), ("delete", "DELETE")):
                for match in re.finditer(rf"\b[A-Za-z_$][\w$]*\s*\.{operation}\s*\(", request_text):
                    path = _java_builder_path(request_text, match.start())
                    add_request(method, service_base.rstrip("/") + path, match.start(), service_system)
    if "WebClient" in text:
        for match in JAVA_WEBCLIENT_RE.finditer(text):
            add_request(match.group(1).upper(), match.group(2), match.start())

    workflow_refs = sorted(
        {value for pattern in JAVA_WORKFLOW_PATTERNS for value in pattern.findall(comment_free_text)}
    ) if any(marker in comment_free_text for marker in ("startProcess", "@JobWorker", "@Process")) else []
    if "implements JavaDelegate" in text:
        workflow_refs.append(f"delegate:{primary_type}")

    ui_actions: list[UIAction] = []
    action_regions: list[tuple[int, int, str]] = []
    for listener in JAVA_LISTENER_RE.finditer(text) if behavioral_file else ():
        handler_id, method_start = source_method(listener.start())
        if not handler_id:
            continue
        component, event_name = listener.group(1), listener.group(2)
        action_id = _entity_id(
            repo_name, rel_path,
            f"action:{component}:{event_name.lower()}:{line_at(listener.start())}",
        )
        opening = listener.end() - 1
        closing = brace_pairs.get(opening, len(text))
        effects: list[str] = []
        action_body = _mask_java_comments(text[opening + 1:closing])
        for effect in JAVA_UI_EFFECT_RE.finditer(action_body):
            absolute_offset = opening + 1 + effect.start()
            arguments = re.sub(r"\s+", " ", effect.group(3)).strip()
            description = f"{effect.group(1)}.{effect.group(2)}({arguments})"
            if condition := condition_at(absolute_offset, method_start):
                description = f"if {condition}: {description}"
            if description not in effects:
                effects.append(description[:500])
        ui_actions.append(
            UIAction(
                action_id, component.replace("_", " "), event_name.lower(),
                line_at(listener.start()), handler_id, effects[:12],
            )
        )
        action_regions.append((opening, closing, action_id))

    imported_types = {item.rsplit(".", 1)[-1]: item for item in imports if not item.endswith(".*")} if behavioral_file else {}
    variable_types = {
        variable: imported_types.get(type_name, type_name)
        for type_name, variable in re.findall(
            r'\b([A-Z][A-Za-z0-9_$]*(?:<[^;=()]+>)?)\s+([A-Za-z_$][\w$]*)\s*(?=[=;,)"])',
            text if behavioral_file else "",
        )
        if not type_name.startswith(("String", "List", "Map", "Set", "Optional"))
    }
    function_calls: list[FunctionCall] = []
    seen_calls: set[tuple[str, str, str, int]] = set()

    def call_source(offset: int) -> tuple[str, int]:
        actions = [item for item in action_regions if item[0] <= offset <= item[1]]
        if actions:
            handler_id, method_start = source_method(offset)
            return actions[-1][2], method_start
        return source_method(offset)

    def add_call(offset: int, target_type: str, target_method: str) -> None:
        source_id, method_start = call_source(offset)
        if not source_id or target_method in {"getClass", "toString", "equals", "hashCode"}:
            return
        primary_qualified = classes[0].qualified_name if classes else primary_type
        behavioral_markers = (".service.", ".controller.", ".springview.", ".repo.", ".repository.", ".client.", ".listener.")
        if "." in target_type and target_type != primary_qualified and not any(marker in target_type for marker in behavioral_markers):
            return
        line = line_at(offset)
        key = (source_id, target_type, target_method, line)
        if key in seen_calls:
            return
        seen_calls.add(key)
        function_calls.append(
            FunctionCall(
                _entity_id(repo_name, rel_path, f"call:{line}:{target_type}.{target_method}"),
                source_id, target_type, target_method, line,
                condition_at(offset, method_start),
            )
        )

    for call in JAVA_QUALIFIED_CALL_RE.finditer(text) if behavioral_file else ():
        constructed_type, receiver, target_method = call.groups()
        target_type = imported_types.get(constructed_type, constructed_type) if constructed_type else variable_types.get(receiver or "", "")
        if receiver == "this":
            target_type = classes[0].qualified_name if classes else primary_type
        if target_type:
            add_call(call.start(), target_type, target_method)

    local_methods = {region[3] for region in method_regions}
    declaration_ranges = sorted(method_ids)
    declaration_starts = [item[0] for item in declaration_ranges]
    for call in JAVA_LOCAL_CALL_RE.finditer(text) if behavioral_file else ():
        declaration_index = bisect.bisect_right(declaration_starts, call.start()) - 1
        is_declaration = declaration_index >= 0 and call.start() <= declaration_ranges[declaration_index][1]
        if call.group(1) in local_methods and not is_declaration:
            add_call(call.start(), classes[0].qualified_name if classes else primary_type, call.group(1))

    has_process_start = "startProcess" in comment_free_text
    local_constants = dict(JAVA_STRING_CONSTANT_RE.findall(comment_free_text)) if has_process_start else {}
    assigned_strings: dict[str, set[str]] = {}
    if has_process_start:
        for name, value in JAVA_STRING_FIELD_ASSIGNMENT_RE.findall(comment_free_text):
            assigned_strings.setdefault(name, set()).add(value)

    process_key_returns: set[str] = set()
    if has_process_start:
        for start, closing, _, method_name in method_regions:
            if method_name != "getProcessDefinitionKey":
                continue
            for returned in re.findall(r"\breturn\s+([^;]+);", comment_free_text[start:closing]):
                expression = returned.strip().removeprefix("this.")
                if expression.startswith('"') and expression.endswith('"'):
                    process_key_returns.add(expression[1:-1])
                elif expression in local_constants:
                    process_key_returns.add(local_constants[expression])
                else:
                    process_key_returns.update(assigned_strings.get(expression, ()))

    def resolve_process_keys(expression: str) -> set[str]:
        expression = expression.strip()
        if expression.startswith('"') and expression.endswith('"'):
            return {expression[1:-1]}
        unqualified = expression.removeprefix("this.")
        if unqualified in local_constants:
            return {local_constants[unqualified]}
        if "." in unqualified:
            owner, constant = unqualified.rsplit(".", 1)
            if owner in {primary_type, "this"} and constant in local_constants:
                return {local_constants[constant]}
        if unqualified == "getProcessDefinitionKey()":
            return process_key_returns
        return assigned_strings.get(unqualified, set())

    process_starts: list[ProcessStart] = []
    seen_starts: set[tuple[str, str, int]] = set()
    for match in JAVA_PROCESS_START_RE.finditer(comment_free_text) if has_process_start else ():
        source_id, _ = source_method(match.start())
        line = line_at(match.start())
        for process_key in resolve_process_keys(match.group(1)):
            key = (source_id, process_key, line)
            if not source_id or key in seen_starts:
                continue
            seen_starts.add(key)
            workflow_refs.append(process_key)
            process_starts.append(
                ProcessStart(
                    _entity_id(repo_name, rel_path, f"process-start:{process_key}:{line}"),
                    source_id, process_key, line,
                )
            )

    has_messaging = any(
        marker in text
        for marker in (
            "KafkaListener", "RabbitListener", "JmsListener", "RedisListener", "StreamListener",
            "KafkaTemplate", "RabbitTemplate", "RedisTemplate", "JmsTemplate", "BindingBuilder",
        )
    )
    message_constants = dict(JAVA_STRING_CONSTANT_RE.findall(text)) if has_messaging else {}
    message_fields = {
        name: _spring_property_default(value)
        for value, name in JAVA_VALUE_FIELD_RE.findall(text)
    } if has_messaging else {}

    def resolve_message_channel(expression: str) -> str:
        value = expression.strip().removeprefix("this.")
        if value.endswith("()"):
            value = value[:-2]
        if value.startswith('"') and value.endswith('"'):
            return _spring_property_default(value[1:-1])
        if value in message_fields:
            return message_fields[value]
        if value in message_constants:
            return message_constants[value]
        if "." in value:
            _, constant = value.rsplit(".", 1)
            if constant in message_constants:
                return message_constants[constant]
        return ""

    message_uses: list[MessageUse] = []
    seen_message_uses: set[tuple[str, str, str, str, int]] = set()

    def add_message_use(source_id: str, broker: str, channel: str, direction: str, offset: int) -> None:
        if not source_id or not channel:
            return
        line = line_at(offset)
        key = (source_id, broker, channel, direction, line)
        if key in seen_message_uses:
            return
        seen_message_uses.add(key)
        message_uses.append(
            MessageUse(
                _entity_id(repo_name, rel_path, f"message:{direction}:{broker}:{channel}:{line}"),
                source_id, broker, channel, direction, line,
            )
        )

    listener_keys = {
        "KafkaListener": ("kafka", ("topics", "value")),
        "RabbitListener": ("rabbitmq", ("queues", "value")),
        "JmsListener": ("jms", ("destination", "value")),
        "RedisListener": ("redis", ("channels", "value")),
        "StreamListener": ("spring-stream", ("target", "value")),
    }
    for listener in JAVA_MESSAGE_LISTENER_RE.finditer(text) if has_messaging else ():
        annotation, arguments = listener.groups()
        method_match = _next_java_method(text, listener.end())
        source_id = method_ids.get((method_match.start(), method_match.end())) if method_match else ""
        broker, keys = listener_keys[annotation]
        expression = ""
        for key_name in keys:
            named = re.search(
                rf"\b{key_name}\s*=\s*(\{{.*?\}}|\"[^\"]+\"|[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)?)",
                arguments,
                re.DOTALL,
            )
            if named:
                expression = named.group(1)
                break
        if not expression:
            expression = _split_java_arguments(arguments)[0] if arguments.strip() else ""
        candidates = _split_java_arguments(expression[1:-1]) if expression.startswith("{") else [expression]
        for candidate in candidates:
            add_message_use(source_id or "", broker, resolve_message_channel(candidate), "consume", listener.start())

    for publish in JAVA_MESSAGE_PUBLISH_RE.finditer(text) if has_messaging else ():
        receiver, operation, arguments = publish.groups()
        receiver_type = variable_types.get(receiver, "")
        broker = next(
            (name for marker, name in (
                ("KafkaTemplate", "kafka"), ("RabbitTemplate", "rabbitmq"),
                ("RedisTemplate", "redis"), ("JmsTemplate", "jms"),
            ) if marker in receiver_type),
            "",
        )
        if not broker or (operation == "send" and broker != "kafka"):
            continue
        arguments_list = _split_java_arguments(arguments)
        channel_index = 1 if broker == "rabbitmq" and len(arguments_list) >= 3 else 0
        channel = resolve_message_channel(arguments_list[channel_index]) if arguments_list else ""
        source_id, _ = source_method(publish.start())
        add_message_use(source_id, broker, channel, "publish", publish.start())

    message_bindings: list[MessageBinding] = []
    for binding in JAVA_RABBIT_BINDING_RE.finditer(text) if has_messaging else ():
        queue_expression, _, routing_expression = binding.groups()
        source_channel = resolve_message_channel(routing_expression)
        target_channel = resolve_message_channel(queue_expression)
        if source_channel and target_channel:
            message_bindings.append(
                MessageBinding(
                    _entity_id(repo_name, rel_path, f"message-binding:rabbitmq:{source_channel}:{target_channel}"),
                    "rabbitmq", source_channel, target_channel, line_at(binding.start()),
                )
            )
    return (
        imports, classes, functions, requests, routes, vaadin_route, frameworks,
        sorted(set(workflow_refs)), ui_actions, function_calls, process_starts,
        message_uses, message_bindings,
    )


def _local_name(value: str) -> str:
    return value.rsplit("}", 1)[-1]


def _workflow_binding(value: str) -> str:
    value = value.strip()
    expression = re.match(r"^[#$]\{\s*([A-Za-z_$][\w$]*)", value)
    return expression.group(1) if expression else value


def _parse_bpmn(
    raw: bytes, repo_name: str, rel_path: str
) -> tuple[list[WorkflowProcess], list[WorkflowStep], list[WorkflowFlow]]:
    root = ET.fromstring(raw)
    processes: list[WorkflowProcess] = []
    steps: list[WorkflowStep] = []
    flows: list[WorkflowFlow] = []
    step_tags = {
        "startEvent", "endEvent", "userTask", "serviceTask", "scriptTask", "manualTask",
        "businessRuleTask", "receiveTask", "sendTask", "callActivity", "exclusiveGateway",
        "parallelGateway", "inclusiveGateway", "eventBasedGateway", "intermediateCatchEvent",
        "intermediateThrowEvent", "subProcess",
    }
    for process_element in (item for item in root.iter() if _local_name(item.tag) == "process"):
        process_key = process_element.attrib.get("id", Path(rel_path).stem)
        process_id = _entity_id(repo_name, rel_path, f"process:{process_key}")
        processes.append(WorkflowProcess(process_id, process_key, process_element.attrib.get("name", process_key)))
        step_ids: dict[str, str] = {}
        default_flows = {
            element.attrib["default"]
            for element in process_element.iter()
            if element.attrib.get("default")
        }
        for element in process_element.iter():
            kind = _local_name(element.tag)
            step_key = element.attrib.get("id")
            if kind not in step_tags or not step_key:
                continue
            step_id = _entity_id(repo_name, rel_path, f"process:{process_key}:step:{step_key}")
            step_ids[step_key] = step_id
            bindings: set[str] = set()
            called_process = element.attrib.get("calledElement", "") if kind == "callActivity" else ""
            for nested in element.iter():
                for attribute, value in nested.attrib.items():
                    if _local_name(attribute) in {"class", "delegateExpression", "expression"} and value:
                        bindings.add(_workflow_binding(value))
            steps.append(
                WorkflowStep(
                    step_id, process_id, step_key, element.attrib.get("name", step_key), kind,
                    sorted(bindings), called_process,
                )
            )
        for element in process_element.iter():
            if _local_name(element.tag) != "sequenceFlow":
                continue
            source_id = step_ids.get(element.attrib.get("sourceRef", ""))
            target_id = step_ids.get(element.attrib.get("targetRef", ""))
            if source_id and target_id:
                flow_key = element.attrib.get("id", f"{source_id}->{target_id}")
                condition_element = next(
                    (child for child in element if _local_name(child.tag) == "conditionExpression"),
                    None,
                )
                condition = re.sub(
                    r"\s+", " ", (condition_element.text or "").strip()
                )[:500] if condition_element is not None else ""
                flows.append(
                    WorkflowFlow(
                        _entity_id(repo_name, rel_path, f"flow:{flow_key}"), process_id,
                        source_id, target_id, element.attrib.get("name", ""), condition,
                        flow_key in default_flows,
                    )
                )
    return processes, steps, flows


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
    resolved = path if path.is_absolute() else path.absolute()
    rel_path = resolved.relative_to(settings.repo_root).as_posix()
    raw = resolved.read_bytes()
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError(f"file exceeds {MAX_FILE_BYTES} bytes")
    text = raw.decode("utf-8", errors="replace")
    extension = ".bpmn20.xml" if resolved.name.lower().endswith(".bpmn20.xml") else resolved.suffix.lower()
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
        imports, classes, functions, requests, routes = _parse_javascript(
            text, settings.repo_name, rel_path, settings,
        )
    elif extension == ".go":
        imports, classes, functions, requests, routes = _parse_go(text, settings.repo_name, rel_path)
        route_path = infer_page_route(rel_path)
        frameworks: list[str] = []
        workflow_refs: list[str] = []
    elif extension == ".java":
        (
            imports, classes, functions, requests, routes, route_path, frameworks,
            workflow_refs, ui_actions, function_calls, process_starts,
            message_uses, message_bindings,
        ) = _parse_java(
            text, settings.repo_name, rel_path, _json_api_prefixes(settings)
        )
    elif extension in {".bpmn", ".bpmn20.xml"}:
        workflow_processes, workflow_steps, workflow_flows = _parse_bpmn(raw, settings.repo_name, rel_path)
        imports, classes, functions, requests, routes = [], [], [], [], []
        route_path, frameworks, workflow_refs = None, ["flowable"], []
    elif extension in EVIDENCE_EXTENSIONS or resolved.name.lower() in EVIDENCE_FILE_NAMES:
        imports, classes, functions, requests, routes = [], [], [], [], []
        route_path, frameworks, workflow_refs = None, [], []
    else:
        raise ValueError(f"unsupported source extension: {extension}")
    if extension not in {".java", ".bpmn", ".bpmn20.xml"} and not (
        extension in EVIDENCE_EXTENSIONS or resolved.name.lower() in EVIDENCE_FILE_NAMES
    ):
        is_nextjs_route = (
            extension in {".js", ".jsx", ".ts", ".tsx"}
            and _nextjs_route_path(rel_path) is not None
        )
        route_path = None if is_nextjs_route else infer_page_route(rel_path)
        frameworks = ["nextjs"] if is_nextjs_route else []
        workflow_refs = []
    if extension not in {".bpmn", ".bpmn20.xml"}:
        workflow_processes, workflow_steps, workflow_flows = [], [], []
    if extension != ".java":
        ui_actions, function_calls, process_starts = [], [], []
        message_uses, message_bindings = [], []
    system_dependencies = []
    lower_text = text.lower()
    if any(marker in lower_text for marker in SYSTEM_EVIDENCE_HINTS):
        evidence_text = _mask_evidence_comments(text, extension)
        system_dependencies = _parse_system_dependencies(
            evidence_text, settings.repo_name, rel_path, extension, evidence_text.lower(),
        )
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
        workflow_processes=workflow_processes,
        workflow_steps=workflow_steps,
        workflow_flows=workflow_flows,
        ui_actions=ui_actions,
        function_calls=function_calls,
        process_starts=process_starts,
        message_uses=message_uses,
        message_bindings=message_bindings,
        system_dependencies=system_dependencies,
    )


def is_supported_file(path: Path) -> bool:
    name = path.name.lower()
    return (
        path.suffix.lower() in SOURCE_EXTENSIONS
        or path.suffix.lower() in EVIDENCE_EXTENSIONS
        or name in EVIDENCE_FILE_NAMES
        or name.endswith(".bpmn20.xml")
    )


def iter_source_files(settings: Settings = SETTINGS) -> list[Path]:
    files: list[Path] = []
    for root, directories, names in os.walk(settings.repo_root):
        directories[:] = sorted(item for item in directories if item not in settings.excludes)
        base = Path(root)
        for name in names:
            path = base / name
            if is_supported_file(path):
                files.append(path)
    return sorted(files)


def _parse_file_worker(path: Path, settings: Settings) -> tuple[ParsedFile | None, str | None]:
    try:
        return parse_file(path, settings), None
    except (OSError, SyntaxError, UnicodeError, ValueError) as exc:
        return None, f"{path}: {exc}"


def _scan_worker_count(file_count: int) -> int:
    configured = os.getenv("GRAPH_ENGINE_WORKERS", "").strip()
    if configured:
        try:
            requested = int(configured)
        except ValueError as exc:
            raise ValueError("GRAPH_ENGINE_WORKERS must be a positive integer") from exc
        if requested < 1:
            raise ValueError("GRAPH_ENGINE_WORKERS must be a positive integer")
        return max(1, min(requested, file_count))
    if file_count < PARALLEL_SCAN_MIN_FILES:
        return 1
    return max(1, min(4, os.cpu_count() or 1, file_count))


def _go_import_aliases(text: str) -> dict[str, str]:
    aliases: dict[str, str] = {}
    blocks = GO_IMPORT_BLOCK_RE.findall(text)
    entries = [
        *re.findall(r'(?m)^\s*import\s+(?:([A-Za-z_]\w*)\s+)?"([^"]+)"', text),
    ]
    for block in blocks:
        entries.extend(re.findall(r'(?m)^\s*(?:([A-Za-z_]\w*)\s+)?"([^"]+)"', block))
    for alias, import_path in entries:
        name = alias or import_path.rstrip("/").rsplit("/", 1)[-1]
        if name not in {"_", "."}:
            aliases[name] = import_path
    return aliases


def _compose_go_repository_routes(files: list[ParsedFile], settings: Settings) -> None:
    """Apply runtime Gin group prefixes passed across package RegisterRoutes calls."""
    go_mod = settings.repo_root / "go.mod"
    try:
        module_match = re.search(
            r"(?m)^\s*module\s+(\S+)", go_mod.read_text(encoding="utf-8", errors="replace")
        )
    except OSError:
        module_match = None
    if not module_match:
        return
    module = module_match.group(1).rstrip("/")
    package_prefixes: dict[str, set[str]] = {}
    source_texts: dict[str, str] = {}
    for parsed in files:
        if parsed.extension != ".go" or parsed.path.endswith("_test.go"):
            continue
        path = settings.repo_root / parsed.path
        try:
            text = _mask_evidence_comments(path.read_text(encoding="utf-8", errors="replace"), ".go")
        except OSError:
            continue
        source_texts[parsed.path] = text
        groups = _go_group_paths(text)
        aliases = _go_import_aliases(text)
        for match in GO_REGISTER_ROUTES_RE.finditer(text):
            import_path = aliases.get(match.group(1), "")
            prefix = groups.get(match.group(2))
            if not prefix or not import_path.startswith(f"{module}/"):
                continue
            package_path = import_path[len(module) + 1:].strip("/")
            package_prefixes.setdefault(package_path, set()).add(prefix)

    function_defs: dict[tuple[str, str], tuple[str, int, int, str, str, dict[str, str]]] = {}
    for rel_path, text in source_texts.items():
        package_path = posixpath.dirname(rel_path)
        matches = list(GO_FUNCTION_RE.finditer(text))
        for index, match in enumerate(matches):
            router = re.search(r"\b([A-Za-z_]\w*)\s+\*gin\.RouterGroup\b", match.group(2))
            if not router:
                continue
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            segment = text[match.start():end]
            function_defs[(package_path, match.group(1))] = (
                rel_path,
                _line_number(text, match.start()),
                _line_number(text, end),
                router.group(1),
                segment,
                _go_group_paths(segment),
            )

    function_prefixes: dict[tuple[str, str], set[str]] = {
        key: {""} for key in function_defs if key[1] == "RegisterRoutes"
    }
    changed = True
    while changed:
        changed = False
        for key, prefixes in list(function_prefixes.items()):
            definition = function_defs.get(key)
            if not definition:
                continue
            _, _, _, router, segment, groups = definition
            for call in re.finditer(r"\b([A-Za-z_]\w*)\s*\(\s*([A-Za-z_]\w*)\b", segment):
                target = (key[0], call.group(1))
                if target == key or target not in function_defs:
                    continue
                argument = call.group(2)
                if argument == router:
                    local_prefix = ""
                elif argument in groups:
                    local_prefix = groups[argument]
                else:
                    continue
                target_prefixes = function_prefixes.setdefault(target, set())
                before = len(target_prefixes)
                target_prefixes.update(_join_route(prefix, local_prefix) for prefix in prefixes)
                changed = changed or len(target_prefixes) != before

    for parsed in files:
        if parsed.extension != ".go" or parsed.path.endswith("_test.go") or not parsed.routes:
            continue
        package_path = posixpath.dirname(parsed.path)
        external_prefixes = package_prefixes.get(package_path)
        if not external_prefixes:
            continue
        composed: list[Route] = []
        definitions = [
            (name, definition)
            for (candidate_package, name), definition in function_defs.items()
            if candidate_package == package_path and definition[0] == parsed.path
        ]
        for route in parsed.routes:
            function_name = next(
                (
                    name for name, definition in definitions
                    if definition[1] <= route.line <= definition[2]
                ),
                "",
            )
            local_prefixes = function_prefixes.get((package_path, function_name), {""})
            for external_prefix in sorted(external_prefixes):
                for local_prefix in sorted(local_prefixes):
                    path = _join_route(
                        external_prefix,
                        _join_route(local_prefix, route.route_path),
                    )
                    normalized = normalize_url(path)
                    composed.append(
                        Route(
                            f"{settings.repo_name}:{route.method}:{normalized}",
                            route.method,
                            path,
                            normalized,
                            route.line,
                            route.handler_id,
                        )
                    )
        parsed.routes = composed


def scan_repository(settings: Settings = SETTINGS) -> ScanResult:
    started = time.monotonic()
    paths = iter_source_files(settings)
    files: list[ParsedFile] = []
    errors: list[str] = []
    worker_count = _scan_worker_count(len(paths))
    if worker_count == 1:
        results = map(_parse_file_worker, paths, itertools.repeat(settings))
        for parsed, error in results:
            if parsed is not None:
                files.append(parsed)
            if error is not None:
                errors.append(error)
    else:
        chunksize = max(1, len(paths) // (worker_count * 32))
        with concurrent.futures.ProcessPoolExecutor(max_workers=worker_count) as executor:
            results = executor.map(
                _parse_file_worker,
                paths,
                itertools.repeat(settings),
                chunksize=chunksize,
            )
            for parsed, error in results:
                if parsed is not None:
                    files.append(parsed)
                if error is not None:
                    errors.append(error)
    _compose_go_repository_routes(files, settings)
    return ScanResult(files, time.monotonic() - started, len(errors), errors)


CLEAR_REPOSITORY_STRUCTURE = """
MATCH (n {repo_name: $repo_name})
WHERE n:Class OR n:Function OR n:Page OR n:APIEndpoint OR n:BackendRoute
   OR n:CallSite OR n:WorkflowProcess OR n:WorkflowStep OR n:WorkflowStart OR n:UIAction
   OR (n:ExternalSystem AND n.configured_at IS NULL) OR n:MessageChannel
DETACH DELETE n
"""

PRUNE_STALE_FILES = """
MATCH (f:CodeFile {repo_name: $repo_name})
WHERE NOT f.id IN $file_ids
DETACH DELETE f
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
    n.qualified_name = row.qualified_name, n.aliases = row.aliases,
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
    a.line = row.line, a.system = row.system, a.repo_name = $repo_name, a.source_file_id = row.file_id
MERGE (p)-[:MAKES_REQUEST]->(a)
WITH a, row
OPTIONAL MATCH (fn:Function {id: row.source_function_id})
FOREACH (_ IN CASE WHEN fn IS NULL THEN [] ELSE [1] END | MERGE (fn)-[:MAKES_REQUEST]->(a))
"""

UPSERT_EXTERNAL_SYSTEMS = """
UNWIND $rows AS row
MATCH (a:APIEndpoint {id: row.request_id})
MERGE (s:ExternalSystem {id: row.system_id})
SET s.name = row.name, s.repo_name = $repo_name,
    s.kind = 'http-service', s.technology = 'http', s.role = 'external-http',
    s.evidence_status = 'OBSERVED',
    s.base_url = coalesce(s.base_url, ''),
    s.path_prefix = coalesce(s.path_prefix, ''),
    s.target_repo = coalesce(s.target_repo, '')
MERGE (a)-[:TARGETS_SYSTEM]->(s)
"""

UPSERT_OBSERVED_SYSTEMS = """
UNWIND $rows AS row
MATCH (f:CodeFile {id: row.file_id})
MERGE (s:ExternalSystem {id: row.id})
SET s.name = row.name, s.kind = row.kind, s.technology = row.technology,
    s.role = row.role, s.evidence = row.evidence, s.line = row.line,
    s.scope = row.scope, s.config_key = row.config_key,
    s.evidence_status = 'OBSERVED', s.repo_name = $repo_name,
    s.source_file_id = row.file_id,
    s.base_url = coalesce(s.base_url, ''), s.path_prefix = coalesce(s.path_prefix, ''),
    s.target_repo = coalesce(s.target_repo, '')
MERGE (f)-[:USES_SYSTEM {evidence_status: 'OBSERVED'}]->(s)
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

UPSERT_WORKFLOW_PROCESSES = """
UNWIND $rows AS row
MATCH (f:CodeFile {id: row.file_id})
MERGE (p:WorkflowProcess {id: row.id})
SET p.process_key = row.process_key, p.name = row.name, p.repo_name = $repo_name,
    p.source_file_id = row.file_id
MERGE (f)-[:CONTAINS]->(p)
"""

UPSERT_WORKFLOW_STEPS = """
UNWIND $rows AS row
MATCH (p:WorkflowProcess {id: row.process_id})
MERGE (s:WorkflowStep {id: row.id})
SET s.step_key = row.step_key, s.name = row.name, s.kind = row.kind,
    s.bindings = row.bindings, s.called_process = row.called_process,
    s.repo_name = $repo_name, s.source_file_id = row.file_id
MERGE (p)-[:HAS_STEP]->(s)
"""

UPSERT_WORKFLOW_FLOWS = """
UNWIND $rows AS row
MATCH (source:WorkflowStep {id: row.source_id}), (target:WorkflowStep {id: row.target_id})
MERGE (source)-[flow:NEXT {id: row.id}]->(target)
SET flow.name = row.name, flow.condition = row.condition, flow.is_default = row.is_default
"""

UPSERT_UI_ACTIONS = """
UNWIND $rows AS row
MATCH (f:CodeFile {id: row.file_id})
OPTIONAL MATCH (p:Page {id: row.file_id})
MERGE (a:UIAction {id: row.id})
SET a.name = row.name, a.event = row.event, a.line = row.line,
    a.effects = row.effects, a.repo_name = $repo_name, a.source_file_id = row.file_id
MERGE (f)-[:CONTAINS]->(a)
FOREACH (_ IN CASE WHEN p IS NULL THEN [] ELSE [1] END |
    MERGE (p)-[:HAS_ACTION]->(a)
)
WITH a, row
MATCH (fn:Function {id: row.handler_id})
MERGE (a)-[:DECLARED_IN]->(fn)
MERGE (fn)-[:DECLARES_ACTION]->(a)
"""

UPSERT_CALL_SITES = """
UNWIND $rows AS row
MATCH (source {id: row.source_id})
WHERE source:Function OR source:UIAction
MERGE (call:CallSite {id: row.id})
SET call.target_type = row.target_type, call.target_method = row.target_method,
    call.line = row.line, call.condition = row.condition,
    call.repo_name = source.repo_name, call.source_file_id = source.source_file_id
MERGE (source)-[:DECLARES_CALL]->(call)
"""

STITCH_FUNCTION_CALLS = """
OPTIONAL MATCH (source)-[old:CALLS]->(:Function)
WHERE (source:Function OR source:UIAction) AND old.managed_by = 'callsite'
WITH collect(old) AS old_relationships
FOREACH (relationship IN old_relationships | DELETE relationship)
WITH 1 AS ready
MATCH (source)-[:DECLARES_CALL]->(call:CallSite)
WHERE source:Function OR source:UIAction
OPTIONAL MATCH (qualified_owner:Class {qualified_name: call.target_type})
OPTIONAL MATCH (simple_owner:Class {name: call.target_type})
WHERE NOT call.target_type CONTAINS '.'
WITH source, call,
     collect(DISTINCT qualified_owner) + collect(DISTINCT simple_owner) AS owners
UNWIND owners AS owner
MATCH (file:CodeFile)-[:DEFINES]->(owner)
MATCH (file)-[:DEFINES]->(target:Function)
WHERE target.name = owner.name + '.' + call.target_method
WITH source, call, collect(DISTINCT target) AS candidates
WITH source, call, candidates,
     [candidate IN candidates WHERE candidate.repo_name = source.repo_name] AS local_candidates
WITH source, call, CASE
    WHEN size(local_candidates) > 0 THEN local_candidates
    WHEN size(candidates) = 1 THEN candidates
    ELSE []
END AS selected
UNWIND selected AS target
MERGE (source)-[relationship:CALLS {id: call.id, target_id: target.id}]->(target)
SET relationship.line = call.line, relationship.condition = call.condition,
    relationship.managed_by = 'callsite'
"""

UPSERT_PROCESS_STARTS = """
UNWIND $rows AS row
MATCH (source:Function {id: row.source_id})
MERGE (start:WorkflowStart {id: row.id})
SET start.process_key = row.process_key, start.line = row.line,
    start.repo_name = source.repo_name, start.source_file_id = source.source_file_id
MERGE (source)-[:DECLARES_START]->(start)
"""

STITCH_WORKFLOW_STARTS = """
OPTIONAL MATCH (:WorkflowStart)-[old_resolution:RESOLVES_TO]->(:WorkflowProcess)
WITH collect(old_resolution) AS old_resolutions
FOREACH (relationship IN old_resolutions | DELETE relationship)
WITH 1 AS resolutions_cleared
OPTIONAL MATCH (:Function)-[old_start:STARTS_PROCESS]->(:WorkflowProcess)
WHERE old_start.managed_by = 'workflow-start'
WITH collect(old_start) AS old_starts
FOREACH (relationship IN old_starts | DELETE relationship)
WITH 1 AS ready
MATCH (source:Function)-[:DECLARES_START]->(start:WorkflowStart)
MATCH (process:WorkflowProcess {process_key: start.process_key})
WITH source, start, collect(DISTINCT process) AS candidates
WITH source, start, candidates,
     [candidate IN candidates WHERE candidate.repo_name = source.repo_name] AS local_candidates
WITH source, start, CASE
    WHEN size(local_candidates) = 1 THEN local_candidates
    WHEN size(local_candidates) > 1 THEN []
    WHEN size(candidates) = 1 THEN candidates
    ELSE []
END AS selected
UNWIND selected AS process
MERGE (start)-[:RESOLVES_TO]->(process)
MERGE (source)-[starts:STARTS_PROCESS {id: start.id}]->(process)
SET starts.line = start.line, starts.managed_by = 'workflow-start'
"""

UPSERT_MESSAGE_CHANNELS = """
UNWIND $rows AS row
MERGE (channel:MessageChannel {id: row.channel_id})
SET channel.name = row.channel, channel.channel = row.channel, channel.broker = row.broker,
    channel.repo_name = $repo_name, channel.source_file_id = row.file_id
"""

UPSERT_MESSAGE_PUBLISHERS = """
UNWIND $rows AS row
MATCH (source:Function {id: row.source_id}), (channel:MessageChannel {id: row.channel_id})
MERGE (source)-[rel:PUBLISHES_TO {id: row.id}]->(channel)
SET rel.line = row.line
"""

UPSERT_MESSAGE_CONSUMERS = """
UNWIND $rows AS row
MATCH (channel:MessageChannel {id: row.channel_id}), (target:Function {id: row.source_id})
MERGE (channel)-[rel:CONSUMED_BY {id: row.id}]->(target)
SET rel.line = row.line
"""

UPSERT_MESSAGE_BINDINGS = """
UNWIND $rows AS row
MATCH (source:MessageChannel {id: row.source_channel_id})
MATCH (target:MessageChannel {id: row.target_channel_id})
MERGE (source)-[rel:ROUTES_TO {id: row.id}]->(target)
SET rel.line = row.line
"""

STITCH_MESSAGE_CHANNELS = """
MATCH (a:MessageChannel), (b:MessageChannel)
WHERE a.broker = b.broker AND a.channel = b.channel AND a.id < b.id
MERGE (a)-[:SAME_CHANNEL]->(b)
MERGE (b)-[:SAME_CHANNEL]->(a)
"""

STITCH_WORKFLOW_BINDINGS = """
OPTIONAL MATCH (:WorkflowStep)-[old:INVOKES]->(:Class)
WITH collect(old) AS old_relationships
FOREACH (relationship IN old_relationships | DELETE relationship)
WITH 1 AS ready
MATCH (s:WorkflowStep)
UNWIND s.bindings AS binding
MATCH (c:Class)
WHERE binding = c.qualified_name OR binding IN c.aliases
WITH s, binding, collect(DISTINCT c) AS candidates
WITH s, candidates,
     [candidate IN candidates WHERE candidate.repo_name = s.repo_name] AS local_candidates
WITH s, CASE
    WHEN size(local_candidates) = 1 THEN local_candidates
    WHEN size(local_candidates) > 1 THEN []
    WHEN size(candidates) = 1 THEN candidates
    ELSE []
END AS selected
UNWIND selected AS c
MERGE (s)-[:INVOKES]->(c)
"""

STITCH_CALLED_PROCESSES = """
OPTIONAL MATCH (s:WorkflowStep)-[old:CALLS]->(:WorkflowProcess)
WITH collect(old) AS old_relationships
FOREACH (relationship IN old_relationships | DELETE relationship)
WITH 1 AS ready
MATCH (s:WorkflowStep)
WHERE s.called_process <> ''
MATCH (p:WorkflowProcess {process_key: s.called_process})
WITH s, collect(DISTINCT p) AS candidates
WITH s, candidates,
     [candidate IN candidates WHERE candidate.repo_name = s.repo_name] AS local_candidates
WITH s, CASE
    WHEN size(local_candidates) = 1 THEN local_candidates
    WHEN size(local_candidates) > 1 THEN []
    WHEN size(candidates) = 1 THEN candidates
    ELSE []
END AS selected
UNWIND selected AS p
MERGE (s)-[:CALLS]->(p)
"""

STITCH_JAVA_IMPORTS = """
OPTIONAL MATCH (:CodeFile)-[old:IMPORTS]->(:Class)
WITH collect(old) AS old_relationships
FOREACH (relationship IN old_relationships | DELETE relationship)
WITH 1 AS ready
MATCH (file:CodeFile {extension: '.java'})
UNWIND file.imports AS imported
MATCH (candidate:Class {qualified_name: imported})
WITH file, imported, collect(DISTINCT candidate) AS candidates
WITH file, candidates,
     [candidate IN candidates WHERE candidate.repo_name = file.repo_name] AS local_candidates
WITH file, CASE
    WHEN size(local_candidates) > 0 THEN local_candidates
    WHEN size(candidates) = 1 THEN candidates
    ELSE []
END AS selected
UNWIND selected AS target
MERGE (file)-[:IMPORTS]->(target)
"""

STITCH_SCOPED_JAVA_IMPORTS = """
MATCH (file:CodeFile)
WHERE file.id IN $file_ids
   OR any(imported IN file.imports WHERE imported IN $class_identifiers)
OPTIONAL MATCH (file)-[old:IMPORTS]->(:Class)
WITH file, collect(old) AS old_relationships
FOREACH (relationship IN old_relationships | DELETE relationship)
WITH file
WHERE file.extension = '.java'
UNWIND file.imports AS imported
MATCH (candidate:Class {qualified_name: imported})
WITH file, imported, collect(DISTINCT candidate) AS candidates
WITH file, candidates,
     [candidate IN candidates WHERE candidate.repo_name = file.repo_name] AS local_candidates
WITH file, CASE
    WHEN size(local_candidates) > 0 THEN local_candidates
    WHEN size(candidates) = 1 THEN candidates
    ELSE []
END AS selected
UNWIND selected AS target
MERGE (file)-[:IMPORTS]->(target)
"""

STITCH_SCOPED_FUNCTION_CALLS = """
MATCH (source)-[:DECLARES_CALL]->(seed:CallSite)
WHERE (source:Function OR source:UIAction)
  AND (source.source_file_id IN $file_ids OR seed.target_type IN $class_identifiers)
WITH DISTINCT source
OPTIONAL MATCH (source)-[old:CALLS]->(:Function)
WHERE old.managed_by = 'callsite'
WITH source, collect(old) AS old_relationships
FOREACH (relationship IN old_relationships | DELETE relationship)
WITH source
MATCH (source)-[:DECLARES_CALL]->(call:CallSite)
OPTIONAL MATCH (qualified_owner:Class {qualified_name: call.target_type})
OPTIONAL MATCH (simple_owner:Class {name: call.target_type})
WHERE NOT call.target_type CONTAINS '.'
WITH source, call,
     collect(DISTINCT qualified_owner) + collect(DISTINCT simple_owner) AS owners
UNWIND owners AS owner
MATCH (file:CodeFile)-[:DEFINES]->(owner)
MATCH (file)-[:DEFINES]->(target:Function)
WHERE target.name = owner.name + '.' + call.target_method
WITH source, call, collect(DISTINCT target) AS candidates
WITH source, call, candidates,
     [candidate IN candidates WHERE candidate.repo_name = source.repo_name] AS local_candidates
WITH source, call, CASE
    WHEN size(local_candidates) > 0 THEN local_candidates
    WHEN size(candidates) = 1 THEN candidates
    ELSE []
END AS selected
UNWIND selected AS target
MERGE (source)-[relationship:CALLS {id: call.id, target_id: target.id}]->(target)
SET relationship.line = call.line, relationship.condition = call.condition,
    relationship.managed_by = 'callsite'
"""

STITCH_SCOPED_WORKFLOW_BINDINGS = """
MATCH (s:WorkflowStep)
WHERE s.source_file_id IN $file_ids
   OR any(binding IN s.bindings WHERE binding IN $class_identifiers)
OPTIONAL MATCH (s)-[old:INVOKES]->(:Class)
WITH s, collect(old) AS old_relationships
FOREACH (relationship IN old_relationships | DELETE relationship)
WITH s
UNWIND s.bindings AS binding
MATCH (c:Class)
WHERE binding = c.qualified_name OR binding IN c.aliases
WITH s, binding, collect(DISTINCT c) AS candidates
WITH s, candidates,
     [candidate IN candidates WHERE candidate.repo_name = s.repo_name] AS local_candidates
WITH s, CASE
    WHEN size(local_candidates) = 1 THEN local_candidates
    WHEN size(local_candidates) > 1 THEN []
    WHEN size(candidates) = 1 THEN candidates
    ELSE []
END AS selected
UNWIND selected AS c
MERGE (s)-[:INVOKES]->(c)
"""


def reconcile_structural_links(
    db: GraphDB,
    *,
    file_ids: list[str] | None = None,
    class_identifiers: list[str] | None = None,
) -> None:
    if file_ids is None:
        db.execute_write(STITCH_JAVA_IMPORTS)
        db.execute_write(STITCH_FUNCTION_CALLS)
    else:
        parameters = {
            "file_ids": file_ids,
            "class_identifiers": class_identifiers or [],
        }
        db.execute_write(STITCH_SCOPED_JAVA_IMPORTS, **parameters)
        db.execute_write(STITCH_SCOPED_FUNCTION_CALLS, **parameters)
    db.execute_write(STITCH_WORKFLOW_STARTS)
    db.execute_write(STITCH_MESSAGE_CHANNELS)
    if file_ids is None:
        db.execute_write(STITCH_WORKFLOW_BINDINGS)
    else:
        db.execute_write(STITCH_SCOPED_WORKFLOW_BINDINGS, **parameters)
    db.execute_write(STITCH_CALLED_PROCESSES)


def ingest_files(
    db: GraphDB,
    files: list[ParsedFile],
    settings: Settings = SETTINGS,
    *,
    replace_all: bool = False,
    reconcile: bool = True,
) -> None:
    if replace_all:
        db.execute_write(CLEAR_REPOSITORY_STRUCTURE, repo_name=settings.repo_name)
        db.execute_write(
            PRUNE_STALE_FILES,
            repo_name=settings.repo_name,
            file_ids=[item.id for item in files],
        )
    if not files:
        db.execute_write(
            "MERGE (r:Repository {name: $repo_name}) SET r.root_path = $root_path, r.updated_at = datetime()",
            repo_name=settings.repo_name,
            root_path=str(settings.repo_root),
        )
        if reconcile:
            reconcile_structural_links(db)
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
        system_rows = [
            {
                "request_id": request.id,
                "system_id": f"{settings.repo_name}:system:{request.system}",
                "name": request.system,
            }
            for item in files
            for request in item.requests
            if request.system
        ]
        if system_rows:
            db.execute_write(UPSERT_EXTERNAL_SYSTEMS, repo_name=settings.repo_name, rows=system_rows)
    observed_system_rows = [
        dict(asdict(system), file_id=item.id)
        for item in files
        for system in item.system_dependencies
    ]
    if observed_system_rows:
        db.execute_write(
            UPSERT_OBSERVED_SYSTEMS,
            repo_name=settings.repo_name,
            rows=observed_system_rows,
        )
    route_rows = [dict(asdict(route), file_id=item.id) for item in files for route in item.routes]
    if route_rows:
        db.execute_write(UPSERT_ROUTES, repo_name=settings.repo_name, rows=route_rows)
    process_rows = [dict(asdict(process), file_id=item.id) for item in files for process in item.workflow_processes]
    if process_rows:
        db.execute_write(UPSERT_WORKFLOW_PROCESSES, repo_name=settings.repo_name, rows=process_rows)
    step_rows = [dict(asdict(step), file_id=item.id) for item in files for step in item.workflow_steps]
    if step_rows:
        db.execute_write(UPSERT_WORKFLOW_STEPS, repo_name=settings.repo_name, rows=step_rows)
    flow_rows = [asdict(flow) for item in files for flow in item.workflow_flows]
    if flow_rows:
        db.execute_write(UPSERT_WORKFLOW_FLOWS, repo_name=settings.repo_name, rows=flow_rows)
    action_rows = [dict(asdict(action), file_id=item.id) for item in files for action in item.ui_actions]
    if action_rows:
        db.execute_write(UPSERT_UI_ACTIONS, repo_name=settings.repo_name, rows=action_rows)

    call_rows = []
    for item in files:
        for call in item.function_calls:
            row = asdict(call)
            row["target_type"] = call.target_type.split("<", 1)[0]
            call_rows.append(row)
    if call_rows:
        db.execute_write(UPSERT_CALL_SITES, rows=call_rows)

    process_start_rows = [asdict(start) for item in files for start in item.process_starts]
    if process_start_rows:
        db.execute_write(UPSERT_PROCESS_STARTS, rows=process_start_rows)

    channel_rows_by_id: dict[str, dict[str, Any]] = {}
    message_use_rows: list[dict[str, Any]] = []
    for item in files:
        for use in item.message_uses:
            channel_id = f"{item.id}::message:{use.broker}:{use.channel}"
            channel_rows_by_id[channel_id] = {
                "channel_id": channel_id, "channel": use.channel, "broker": use.broker,
                "file_id": item.id,
            }
            message_use_rows.append(dict(asdict(use), channel_id=channel_id))
        for binding in item.message_bindings:
            for channel in (binding.source_channel, binding.target_channel):
                channel_id = f"{item.id}::message:{binding.broker}:{channel}"
                channel_rows_by_id[channel_id] = {
                    "channel_id": channel_id, "channel": channel, "broker": binding.broker,
                    "file_id": item.id,
                }
    if channel_rows_by_id:
        db.execute_write(
            UPSERT_MESSAGE_CHANNELS,
            repo_name=settings.repo_name,
            rows=list(channel_rows_by_id.values()),
        )
        publisher_rows = [row for row in message_use_rows if row["direction"] == "publish"]
        consumer_rows = [row for row in message_use_rows if row["direction"] == "consume"]
        if publisher_rows:
            db.execute_write(UPSERT_MESSAGE_PUBLISHERS, rows=publisher_rows)
        if consumer_rows:
            db.execute_write(UPSERT_MESSAGE_CONSUMERS, rows=consumer_rows)
        binding_rows = [
            dict(
                asdict(binding),
                source_channel_id=f"{item.id}::message:{binding.broker}:{binding.source_channel}",
                target_channel_id=f"{item.id}::message:{binding.broker}:{binding.target_channel}",
            )
            for item in files
            for binding in item.message_bindings
        ]
        if binding_rows:
            db.execute_write(UPSERT_MESSAGE_BINDINGS, rows=binding_rows)
    if reconcile:
        reconcile_structural_links(db)


def build_graph(db: GraphDB, settings: Settings = SETTINGS) -> ScanResult:
    result = scan_repository(settings)
    db.ensure_schema()
    ingest_files(db, result.files, settings, replace_all=True)
    return result
