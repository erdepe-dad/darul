from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from graph_engine.boundaries import build_boundary_report, render_boundary_text
from graph_engine.config import Settings
from graph_engine.parser import parse_file, scan_repository


def settings_for(root: Path) -> Settings:
    return Settings(root, "fixture", "bolt://unused", "neo4j", "x", None, 1.0, frozenset())


class BoundaryReportTests(unittest.TestCase):
    def test_configuration_and_code_report_surrounding_systems(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            properties = root / "src" / "main" / "resources" / "application.properties"
            properties.parent.mkdir(parents=True)
            properties.write_text(
                """spring.datasource.url=jdbc:mysql://db.internal:3306/app
spring.cache.type=redis
spring.data.redis.url=redis://127.0.0.1:6379
spring.mail.host=localhost
spring.mail.port=1025
WHATSAPP_API_URL=http://127.0.0.1:9001/messages
PAYMENT_PROVIDER=stripe
""",
                encoding="utf-8",
            )
            source = root / "Notifications.java"
            source.write_text(
                '''public class Notifications {
    String image = "https://drive.google.com/thumbnail?id=1";
    String checkout = "https://api.stripe.com/v1/payment_intents";
}
''',
                encoding="utf-8",
            )
            scan = scan_repository(settings_for(root))
            report = build_boundary_report(scan, settings_for(root))

        systems = {
            (item["kind"], item["technology"], item["role"])
            for item in report["systems"]
        }
        names = {item["name"] for item in report["systems"]}
        self.assertIn(("database", "mysql", "database"), systems)
        self.assertIn(("cache", "redis", "cache"), systems)
        self.assertIn(("cache", "redis", "datastore"), systems)
        self.assertIn(("email", "smtp", "email"), systems)
        self.assertIn(("communications", "whatsapp", "messaging"), systems)
        self.assertIn(("payment", "stripe", "payment-gateway"), systems)
        self.assertIn("localhost:1025", names)
        self.assertIn("127.0.0.1:9001", names)
        self.assertIn("drive.google.com", names)
        self.assertIn("api.stripe.com", names)
        rendered = render_boundary_text(report)
        self.assertIn("observed code/configuration only", rendered)
        self.assertNotIn("TARGETS_ROUTE", rendered)

    def test_java_dynamic_receiver_is_not_promoted_to_service_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "Service.java"
            source.write_text(
                '''import org.springframework.web.client.RestTemplate;
public class Service {
    RestTemplate restTemplate;
    void load() {
        String url = StringUtils.defaultString(baseUrl) + "/api/items";
        restTemplate.exchange(url, HttpMethod.GET, null, String.class);
    }
}
''',
                encoding="utf-8",
            )
            parsed = parse_file(source, settings_for(root))

        self.assertEqual(len(parsed.requests), 1)
        self.assertEqual(parsed.requests[0].system, "")

    def test_system_evidence_does_not_retain_url_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "application.properties"
            source.write_text(
                "service.url=http://operator:secret@localhost:8123/api\n",
                encoding="utf-8",
            )
            parsed = parse_file(source, settings_for(root))

        evidence = " ".join(item.evidence for item in parsed.system_dependencies)
        names = {item.name for item in parsed.system_dependencies}
        self.assertIn("localhost:8123", names)
        self.assertNotIn("operator", evidence)
        self.assertNotIn("secret", evidence)

    def test_localhost_ports_are_distinct_surrounding_systems(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "application.properties"
            source.write_text(
                "service.a.url=http://localhost:8081/api\n"
                "service.b.url=http://localhost:8082/api\n"
                "service.c.url=http://127.0.0.1:8083/api\n",
                encoding="utf-8",
            )
            parsed = parse_file(source, settings_for(root))

        names = {item.name for item in parsed.system_dependencies}
        self.assertIn("localhost:8081", names)
        self.assertIn("localhost:8082", names)
        self.assertIn("127.0.0.1:8083", names)

    def test_http_request_storage_removes_credentials_and_query_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "client.py"
            source.write_text(
                'import requests\nrequests.get("https://operator:secret@service.test:8443/api?q=token")\n',
                encoding="utf-8",
            )
            parsed = parse_file(source, settings_for(root))

        self.assertEqual(parsed.requests[0].url, "https://service.test:8443/api")
        self.assertNotIn("operator", parsed.requests[0].id)
        self.assertNotIn("secret", parsed.requests[0].id)
        self.assertNotIn("token", parsed.requests[0].id)

    def test_commented_systems_are_not_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "Service.java"
            source.write_text(
                '''public class Service {
    // String old = "http://localhost:9999/api";
    // RedisTemplate legacy;
}
''',
                encoding="utf-8",
            )
            parsed = parse_file(source, settings_for(root))

        self.assertEqual(parsed.system_dependencies, [])

    def test_redis_pubsub_is_distinct_from_datastore_use(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            publisher = root / "Publisher.java"
            publisher.write_text(
                "class Publisher { void send(RedisTemplate redis) { redis.convertAndSend(\"events\", \"x\"); } }",
                encoding="utf-8",
            )
            repository = root / "Repository.java"
            repository.write_text(
                "class Repository { void save(RedisTemplate redis) { redis.opsForValue().set(\"k\", \"v\"); } }",
                encoding="utf-8",
            )
            pubsub = parse_file(publisher, settings_for(root))
            datastore = parse_file(repository, settings_for(root))

        self.assertEqual(
            {item.role for item in pubsub.system_dependencies if item.technology == "redis"},
            {"pubsub"},
        )
        self.assertEqual(
            {item.role for item in datastore.system_dependencies if item.technology == "redis"},
            {"datastore"},
        )


if __name__ == "__main__":
    unittest.main()
