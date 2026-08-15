from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from graph_engine.config import Settings
from graph_engine.parser import _mask_java_comments, parse_file, scan_repository


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
    def test_java_comment_mask_preserves_strings_offsets_and_newlines(self) -> None:
        source = '''String url = "http://service/path/*literal*/"; // line comment
char slash = '/'; /* block
comment */ call();
'''

        masked = _mask_java_comments(source)

        self.assertEqual(len(masked), len(source))
        self.assertEqual(masked.count("\n"), source.count("\n"))
        self.assertIn('"http://service/path/*literal*/"', masked)
        self.assertIn("call();", masked)
        self.assertNotIn("line comment", masked)
        self.assertNotIn("block", masked)

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

    def test_parallel_scan_preserves_file_and_error_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index in range(12):
                (root / f"valid_{index:02}.py").write_text(
                    f"def function_{index}():\n    return {index}\n",
                    encoding="utf-8",
                )
            for name in ("broken_a.py", "broken_b.py"):
                (root / name).write_text("def invalid(:\n", encoding="utf-8")
            with mock.patch.dict("os.environ", {"GRAPH_ENGINE_WORKERS": "2"}):
                result = scan_repository(settings_for(root))

        self.assertEqual(
            [item.path for item in result.files],
            [f"valid_{index:02}.py" for index in range(12)],
        )
        self.assertEqual(result.skipped_files, 2)
        self.assertIn("broken_a.py", result.errors[0])
        self.assertIn("broken_b.py", result.errors[1])

    def test_scan_rejects_invalid_worker_count(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.dict("os.environ", {"GRAPH_ENGINE_WORKERS": "0"}):
                with self.assertRaisesRegex(ValueError, "positive integer"):
                    scan_repository(settings_for(root))

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
        self.assertEqual(
            {start.process_key for start in parsed.process_starts},
            {"user-onboarding"},
        )
        self.assertIn("delegate:UsersView", parsed.workflow_refs)

    def test_java_resolves_flow_service_process_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "ExampleWorkflow.java"
            source.write_text(
                '''import org.flowable.engine.RuntimeService;
public class ExampleWorkflow {
    private static final String PROC_DEF_KEY = "example_process";
    private String processDefinitionKey = "fallback_process";

    protected String getProcessDefinitionKey() {
        return PROC_DEF_KEY;
    }

    public void start(RuntimeService runtimeService, FlowService flowService) {
        if (runtimeService != null) {
            flowService.startProcess(getProcessDefinitionKey(), new Object(), null);
        }
        runtimeService.startProcessInstanceByKeyAndTenantId(processDefinitionKey, "1", null, "tenant");
        flowService.startProcess("literal_process", new Object(), null);
    }
}
''',
                encoding="utf-8",
            )
            parsed = parse_file(source, settings_for(root))

        self.assertEqual(
            {start.process_key for start in parsed.process_starts},
            {"example_process", "fallback_process", "literal_process"},
        )
        self.assertNotIn("ExampleWorkflow.if", {function.name for function in parsed.functions})

    def test_java_commented_process_starts_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "CommentedWorkflowView.java"
            source.write_text(
                '''import org.flowable.engine.RuntimeService;
public class CommentedWorkflowView {
    public void submit(RuntimeService runtimeService) {
        // runtimeService.startProcessInstanceByKey("disabled-process");
        /* runtimeService.startProcessInstanceByKey("also-disabled"); */
    }
}
''',
                encoding="utf-8",
            )
            parsed = parse_file(source, settings_for(root))

        self.assertEqual(parsed.process_starts, [])
        self.assertNotIn("disabled-process", parsed.workflow_refs)
        self.assertNotIn("also-disabled", parsed.workflow_refs)

    def test_java_extracts_message_publishers_consumers_and_rabbit_bindings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "MessagingService.java"
            source.write_text(
                '''import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Service;

@Service
public class MessagingService {
    @Value("${app.rabbit.queue:worker.queue}")
    private String workerQueue;
    @Value("${app.rabbit.routing:worker.save}")
    private String workerRoutingKey;
    private RabbitTemplate rabbitTemplate;
    private KafkaTemplate<String, String> kafkaTemplate;

    public void publish() {
        rabbitTemplate.convertAndSend("worker.exchange", workerRoutingKey, "payload");
        kafkaTemplate.send("audit.events", "payload");
    }

    @RabbitListener(queues = "${app.rabbit.queue:worker.queue}")
    public void consumeRabbit(String payload) {}

    @KafkaListener(topics = "audit.events")
    public void consumeKafka(String payload) {}

    public Object binding() {
        return BindingBuilder.bind(workerQueue()).to(workerExchange()).with(workerRoutingKey);
    }
}
''',
                encoding="utf-8",
            )
            parsed = parse_file(source, settings_for(root))

        uses = {(use.direction, use.broker, use.channel) for use in parsed.message_uses}
        self.assertIn(("publish", "rabbitmq", "worker.save"), uses)
        self.assertIn(("consume", "rabbitmq", "worker.queue"), uses)
        self.assertIn(("publish", "kafka", "audit.events"), uses)
        self.assertIn(("consume", "kafka", "audit.events"), uses)
        self.assertEqual(
            [(binding.source_channel, binding.target_channel) for binding in parsed.message_bindings],
            [("worker.save", "worker.queue")],
        )

    def test_java_map_put_is_not_an_http_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "Metadata.java"
            source.write_text(
                '''import java.util.Map;
public class Metadata {
    public void update(Map<String, String> values) {
        values.put("title", "Safety report");
    }
}
''',
                encoding="utf-8",
            )
            parsed = parse_file(source, settings_for(root))

        self.assertEqual(parsed.requests, [])

    def test_java_commented_requests_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "RemoteResourceClient.java"
            source.write_text(
                '''import org.springframework.web.client.RestTemplate;
public class RemoteResourceClient {
    RestTemplate restTemplate;
    String getServiceUrl() { return baseUrl + "/api/surveys"; }
    void deleteSurvey(String id) {
        UriComponentsBuilder builder = UriComponentsBuilder.fromHttpUrl(getServiceUrl())
            .path("/delete/" + id);
        restTemplate.delete(builder.build().toString());
        // findMany(builder.build().toString());
        // restTemplate.delete("http://localhost/api/surveys/delete/" + id);
    }
}
''',
                encoding="utf-8",
            )
            parsed = parse_file(source, settings_for(root))

        requests = [(request.method, request.normalized_url) for request in parsed.requests]
        self.assertEqual(requests, [("DELETE", "/api/surveys/delete/{param}")])

    def test_java_request_resolves_url_helpers_and_quoted_config_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "ConfiguredApiClient.java"
            source.write_text(
                '''import org.springframework.web.client.RestTemplate;
public class ConfiguredApiClient {
    static String KEY = "BACKEND_API_URL";
    RestTemplate restTemplate;
    String base_url;
    ConfiguredApiClient() {
        this.base_url = config.getConfig(this.KEY).getValue();
    }
    String getBaseUrl() {
        return config.getConfigValue(Constants.SECONDARY_API_URL) + "/api/resources";
    }
    void categories() {
        String url = getBaseUrl() + "/categories";
        restTemplate.exchange(url, HttpMethod.GET, null, String.class);
    }
    void download() {
        String url = new Config().getConfig("ASSET_API_URL").getValue() + "/api/car";
        restTemplate().getForObject(url, String.class);
    }
    void roles() {
        String url = this.base_url + "/roleMapping";
        restTemplate.exchange(url, HttpMethod.GET, null, String.class);
    }
}
''',
                encoding="utf-8",
            )
            parsed = parse_file(source, settings_for(root))

        requests = {
            (request.method, request.normalized_url, request.system)
            for request in parsed.requests
        }
        self.assertIn(("GET", "/api/resources/categories", "SECONDARY_API_URL"), requests)
        self.assertIn(("GET", "/api/car", "ASSET_API_URL"), requests)
        self.assertIn(("GET", "/roleMapping", "BACKEND_API_URL"), requests)

    def test_java_rest_template_variables_resolve_get_and_post_urls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "ExampleTaskService.java"
            source.write_text(
                '''import org.springframework.stereotype.Service;
import org.springframework.web.client.RestTemplate;
@Service
public class ExampleTaskService {
    String baseUrl;
    RestTemplate restTemplate() { return null; }
    public Task getOne(String id) {
        String url = baseUrl + "/api/tasks/" + id;
        RestTemplate client = restTemplate();
        return client.getForObject(url, Task.class);
    }
    public Task save(Task task) {
        String url = baseUrl + "/api/tasks";
        RestTemplate client = restTemplate();
        return client.postForEntity(url, task, Task.class).getBody();
    }
}
class Task {}
''',
                encoding="utf-8",
            )
            parsed = parse_file(source, settings_for(root))

        requests = {
            (request.method, request.normalized_url, request.source_function_id)
            for request in parsed.requests
        }
        self.assertIn(
            ("GET", "/api/tasks/{param}", "sample:ExampleTaskService.java::ExampleTaskService.getOne(String)"),
            requests,
        )
        self.assertIn(
            ("POST", "/api/tasks", "sample:ExampleTaskService.java::ExampleTaskService.save(Task)"),
            requests,
        )

    def test_java_vaadin_8_spring_view_and_exchange_variable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "LegacyExampleView.java"
            source.write_text(
                '''import com.vaadin.navigator.View;
import com.vaadin.spring.annotation.SpringView;
import org.springframework.http.HttpMethod;
import org.springframework.web.client.RestTemplate;
@SpringView(name = LegacyExampleView.VIEW_NAME)
public class LegacyExampleView implements View {
    public static final String VIEW_NAME = "example_form";
    public void load(String id) {
        String url = baseUrl + "/api/resources/" + id;
        RestTemplate client = new RestTemplate();
        client.exchange(url, HttpMethod.GET, null, String.class);
    }
}
''',
                encoding="utf-8",
            )
            parsed = parse_file(source, settings_for(root))

        self.assertEqual(parsed.route_path, "/example_form")
        self.assertEqual(len(parsed.requests), 1)
        self.assertEqual(parsed.requests[0].method, "GET")
        self.assertEqual(parsed.requests[0].normalized_url, "/api/resources/{param}")

    def test_bpmn_extracts_process_steps_flow_and_bindings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "approval.bpmn20.xml"
            source.write_text(
                '''<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL"
             xmlns:flowable="http://flowable.org/bpmn">
  <process id="example_approval" name="Example Approval">
    <startEvent id="start" />
    <serviceTask id="notify" name="Notify" flowable:delegateExpression="${notificationDelegate}" />
    <userTask id="approve" name="Approve" />
    <sequenceFlow id="to_notify" sourceRef="start" targetRef="notify" />
    <sequenceFlow id="to_approve" sourceRef="notify" targetRef="approve" />
  </process>
</definitions>
''',
                encoding="utf-8",
            )
            parsed = parse_file(source, settings_for(root))

        self.assertEqual(parsed.extension, ".bpmn20.xml")
        self.assertEqual(parsed.workflow_processes[0].process_key, "example_approval")
        self.assertEqual(len(parsed.workflow_steps), 3)
        self.assertEqual(parsed.workflow_steps[1].bindings, ["notificationDelegate"])
        self.assertEqual(len(parsed.workflow_flows), 2)

    def test_java_json_api_resource_creates_crud_routes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "application.properties").write_text(
                "crnk.pathPrefix=/japi\n", encoding="utf-8"
            )
            source = root / "Question.java"
            source.write_text(
                '''import io.crnk.core.resource.annotations.JsonApiResource;
@JsonApiResource(type="questions")
public class Question {}
''',
                encoding="utf-8",
            )
            parsed = parse_file(source, settings_for(root))

        route_keys = {(route.method, route.normalized_url) for route in parsed.routes}
        self.assertIn(("GET", "/japi/questions"), route_keys)
        self.assertIn(("POST", "/japi/questions"), route_keys)
        self.assertIn(("DELETE", "/japi/questions/{param}"), route_keys)
        self.assertIn("crnk-jsonapi", parsed.frameworks)

    def test_java_json_api_client_dto_does_not_create_backend_routes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "Question.java"
            source.write_text(
                '''import io.crnk.core.resource.annotations.JsonApiResource;
@JsonApiResource(type="questions")
public class Question {}
''',
                encoding="utf-8",
            )
            parsed = parse_file(source, settings_for(root))

        self.assertEqual(parsed.routes, [])
        self.assertIn("crnk-jsonapi", parsed.frameworks)

    def test_java_katharsis_uses_configured_resource_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "application.properties").write_text(
                "katharsis.pathPrefix=/api/v2/\n", encoding="utf-8"
            )
            source = root / "Question.java"
            source.write_text(
                '''import io.katharsis.resource.annotations.JsonApiResource;
@JsonApiResource(type="questions")
public class Question {}
''',
                encoding="utf-8",
            )
            parsed = parse_file(source, settings_for(root))

        self.assertIn(
            ("GET", "/api/v2/questions"),
            {(route.method, route.normalized_url) for route in parsed.routes},
        )

    def test_java_inherited_jpa_rest_controller_creates_crud_routes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "CampaignController.java"
            source.write_text(
                '''import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
@RestController
@RequestMapping("${rest.pathPrefix:api}/campaignContent")
public class CampaignController
        extends FilterableJpaRestController<Campaign, Long, QCampaign> {}
''',
                encoding="utf-8",
            )
            parsed = parse_file(source, settings_for(root))

        route_keys = {(route.method, route.normalized_url) for route in parsed.routes}
        self.assertIn(("GET", "/api/campaignContent"), route_keys)
        self.assertIn(("POST", "/api/campaignContent"), route_keys)
        self.assertIn(("DELETE", "/api/campaignContent/{param}"), route_keys)
        self.assertIn("spring-jpa-rest", parsed.frameworks)

    def test_java_inherited_read_only_and_flow_controllers_create_routes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            read_only = root / "DashboardController.java"
            read_only.write_text(
                '''@RestController
@RequestMapping("${rest.pathPrefix:api}/dashboard")
public class DashboardController extends ReadOnlyJpaRestController<Dashboard, Long> {}
''',
                encoding="utf-8",
            )
            flow = root / "ApprovalFlowController.java"
            flow.write_text(
                '''@RestController
@RequestMapping("/approval/flow")
public class ApprovalFlowController extends FlowController<Approval, QApproval> {}
''',
                encoding="utf-8",
            )
            parsed_read_only = parse_file(read_only, settings_for(root))
            parsed_flow = parse_file(flow, settings_for(root))

        read_only_routes = {(route.method, route.normalized_url) for route in parsed_read_only.routes}
        self.assertEqual(
            read_only_routes,
            {("GET", "/api/dashboard"), ("GET", "/api/dashboard/{param}")},
        )
        flow_routes = {(route.method, route.normalized_url) for route in parsed_flow.routes}
        self.assertIn(("GET", "/approval/flow"), flow_routes)
        self.assertIn(("GET", "/approval/flow/{param}"), flow_routes)
        self.assertIn(("POST", "/approval/flow"), flow_routes)
        self.assertIn(("POST", "/approval/flow/submit"), flow_routes)
        self.assertIn(("POST", "/approval/flow/update"), flow_routes)
        self.assertIn("spring-read-only-rest", parsed_read_only.frameworks)
        self.assertIn("flowable-rest", parsed_flow.frameworks)

    def test_java_inherited_controllers_support_qualified_names_and_ignore_comments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            qualified = root / "QualifiedController.java"
            qualified.write_text(
                '''@RestController
@RequestMapping("/qualified")
public class QualifiedController
        extends com.example.web.ReadOnlyJpaRestController<Item, Long> {}
''',
                encoding="utf-8",
            )
            commented = root / "CommentedController.java"
            commented.write_text(
                '''@RestController
@RequestMapping("/commented")
public class CommentedController {
    // Historical: extends FlowController<Item, QItem>
}
''',
                encoding="utf-8",
            )
            parsed_qualified = parse_file(qualified, settings_for(root))
            parsed_commented = parse_file(commented, settings_for(root))

        self.assertEqual(
            {(route.method, route.normalized_url) for route in parsed_qualified.routes},
            {("GET", "/qualified"), ("GET", "/qualified/{param}")},
        )
        self.assertNotIn("flowable-rest", parsed_commented.frameworks)
        self.assertEqual(parsed_commented.routes, [])

    def test_java_vaadin_actions_calls_requests_and_systems(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "ExampleTaskView.java"
            source.write_text(
                '''package sample;
import com.vaadin.spring.annotation.SpringView;
import org.springframework.web.client.RestTemplate;
@SpringView(name="tasks")
public class ExampleTaskView {
    String KEY = "TASK_API";
    String baseUrl;
    Service service;
    void wire() {
        button.addClickListener(event -> { service.load(); });
    }
    void request(String id) {
        String url = baseUrl + "/api/tasks/" + id;
        restTemplate().getForEntity(url, String.class);
    }
    RestTemplate restTemplate() { return null; }
}
class Service { void load() {} }
''',
                encoding="utf-8",
            )
            parsed = parse_file(source, settings_for(root))

        self.assertEqual(parsed.ui_actions[0].event, "click")
        self.assertEqual(parsed.function_calls[0].target_method, "load")
        self.assertEqual(parsed.requests[0].normalized_url, "/api/tasks/{param}")
        self.assertEqual(parsed.requests[0].source_function_id, "sample:ExampleTaskView.java::ExampleTaskView.request(String)")

    def test_java_vaadin_actions_capture_component_state_effects(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "TaskComponent.java"
            source.write_text(
                '''@Component
public class TaskComponent {
    void wire() {
        grid.addSelectionListener(event -> {
            if (grid.asSingleSelect().getValue() != null) {
                btDelete.setEnabled(true);
                btDetail.setEnabled(true);
            } else {
                btDelete.setEnabled(false);
                btDetail.setEnabled(false);
            }
        });
    }
}
''',
                encoding="utf-8",
            )
            parsed = parse_file(source, settings_for(root))

        self.assertEqual(len(parsed.ui_actions), 1)
        self.assertEqual(
            parsed.ui_actions[0].effects,
            [
                "if grid.asSingleSelect().getValue() != null: btDelete.setEnabled(true)",
                "if grid.asSingleSelect().getValue() != null: btDetail.setEnabled(true)",
                "if NOT (grid.asSingleSelect().getValue() != null): btDelete.setEnabled(false)",
                "if NOT (grid.asSingleSelect().getValue() != null): btDetail.setEnabled(false)",
            ],
        )

    def test_java_calls_preserve_complete_multiline_conditions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "TaskComponent.java"
            source.write_text(
                '''@Component
public class TaskComponent {
    ExampleTaskService service;
    public void cancel(String status) {
        if (status.equals("PENDING") || status.equals("READY") || status.equals("FAILED") ||
                status.equals("CANCELLED") || status.equals("COMPLETE") || status.equals("RETRY")) {
            service.cancel();
        }
    }
}
class ExampleTaskService { void cancel() {} }
''',
                encoding="utf-8",
            )
            parsed = parse_file(source, settings_for(root))

        call = next(item for item in parsed.function_calls if item.target_method == "cancel")
        self.assertIn('status.equals("PENDING")', call.condition)
        self.assertIn('status.equals("RETRY")', call.condition)


    def test_bpmn_flow_preserves_condition_and_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "decision.bpmn20.xml"
            source.write_text(
                '''<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL">
  <process id="approval">
    <exclusiveGateway id="decision" default="rejected" />
    <userTask id="approved" /><userTask id="revise" />
    <sequenceFlow id="accepted" sourceRef="decision" targetRef="approved">
      <conditionExpression>${approved == true}</conditionExpression>
    </sequenceFlow>
    <sequenceFlow id="rejected" sourceRef="decision" targetRef="revise" />
  </process>
</definitions>''',
                encoding="utf-8",
            )
            parsed = parse_file(source, settings_for(root))

        self.assertEqual(parsed.workflow_flows[0].condition, "${approved == true}")
        self.assertTrue(parsed.workflow_flows[1].is_default)


if __name__ == "__main__":
    unittest.main()
