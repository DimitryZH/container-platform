"""Explicit, isolated and bounded Online Shop runtime smoke validation."""

from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import signal
import socket
import tempfile
import time
import uuid

from .cli import Parser
from .compose import IDS, PROFILE, render
from .smoke_transport import Docker, Session, SmokeError, remaining
from .validation import Failure, strict_json


LIMITS = {"total": 1200, "prerequisites": 30, "pull": 600, "startup": 120,
          "readiness": 180, "smoke": 60, "diagnostics": 20, "cleanup": 120}
EXIT = {"success": 0, "validation_failed": 1, "application_failure": 1,
        "execution_error": 2, "timeout": 3, "interrupted": 130}
PRODUCT = "0PUK6V6EV0"


def checked(result, reason):
    code, output = result
    if code:
        raise SmokeError(reason)
    return output


def decoded(data):
    try:
        return strict_json(data)
    except Failure:
        raise SmokeError("docker_output_invalid") from None


def resources(docker, deadline):
    label = "label=com.docker.compose.project=" + docker.project
    return {kind: checked(docker.raw(args + ["--filter", label, "--format", "{{.ID}}" if kind != "volumes" else "{{.Name}}"], deadline),
                          "project_resource_query_failed").strip()
            for kind, args in (("containers", ["ps", "-a"]), ("networks", ["network", "ls"]), ("volumes", ["volume", "ls"]))}


def port_available():
    try:
        with socket.socket() as connection:
            connection.bind(("127.0.0.1", 8080))
    except OSError:
        raise SmokeError("frontend_port_unavailable") from None


def prerequisites(docker, images, deadline):
    server = decoded(checked(docker.raw(["version", "--format", "{{json .Server}}"], deadline), "docker_engine_unavailable"))
    if not isinstance(server, dict) or server.get("Os") != "linux" or server.get("Arch") not in ("amd64", "x86_64"):
        raise SmokeError("runtime_platform_unsupported")
    endpoint = decoded(checked(docker.raw(["context", "inspect", "--format", "{{json .Endpoints.docker.Host}}"], deadline), "docker_context_unavailable"))
    endpoints = [endpoint, getattr(docker, "env", {}).get("DOCKER_HOST", endpoint)]
    if any(not isinstance(value, str) or not value.startswith(("unix:///", "npipe://")) for value in endpoints):
        raise SmokeError("remote_docker_transport_unsupported")
    checked(docker.raw(["compose", "version", "--short"], deadline), "compose_unavailable")
    if any(resources(docker, deadline).values()):
        raise SmokeError("project_name_collision")
    config = decoded(checked(docker.compose(["config", "--format", "json"], deadline), "compose_config_failed"))
    services = config.get("services", {}) if isinstance(config, dict) else {}
    if not isinstance(services, dict) or set(services) != IDS:
        raise SmokeError("runtime_model_mismatch")
    for name, service in services.items():
        if (service.get("image") != images[name] or service.get("platform") != "linux/amd64"
                or set(service) & {"build", "privileged", "network_mode", "container_name", "volumes", "env_file", "extends"}
                or (name != "frontend" and service.get("ports"))):
            raise SmokeError("runtime_model_mismatch")
    ports = services["frontend"].get("ports", [])
    if len(ports) != 1 or ports[0].get("host_ip") != "127.0.0.1" or str(ports[0].get("published")) != "8080" or ports[0].get("target") != 8080:
        raise SmokeError("runtime_model_mismatch")
    port_available()


def service_state(docker, images, deadline):
    output = checked(docker.compose(["ps", "--all", "--format", "json"], deadline), "service_query_failed")
    # Compose versions emit either one JSON array or one JSON object per line.
    rows = decoded(output) if output.lstrip().startswith(b"[") else [decoded(line) for line in output.splitlines() if line.strip()]
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise SmokeError("docker_output_invalid")
    names = [r.get("Service") for r in rows]
    if len(names) != len(set(names)) or set(names) - IDS:
        raise SmokeError("unexpected_project_services", "application_failure")
    states = []
    for row in rows:
        name, state = row.get("Service"), row.get("State")
        if row.get("Image") != images[name]:
            raise SmokeError("running_image_mismatch", "application_failure")
        if state in ("exited", "dead", "removing"):
            raise SmokeError("service_not_running", "application_failure")
        states.append({"service": name, "state": state if state in ("running", "created", "restarting", "paused") else "unknown"})
    return states, len(rows) == 12 and all(r.get("State") == "running" for r in rows)


class Page(HTMLParser):
    def __init__(self, body):
        super().__init__()
        self.inputs, self.links, self.text = {}, [], []
        self.feed(body)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input" and "name" in attrs:
            self.inputs[attrs["name"]] = attrs.get("value", "")
        if tag == "a":
            self.links.append(attrs.get("href", ""))

    def handle_data(self, data):
        self.text.append(data)

    @property
    def words(self):
        return " ".join(" ".join(self.text).split())


def require(condition, code):
    if not condition:
        raise SmokeError(code, "application_failure")


def smoke_flow(session, deadline, results):
    def get(path, form=None):
        return Page(session.request(path, deadline, form))

    home = get("/")
    require("/product/" + PRODUCT in home.links, "frontend_catalog_missing")
    results.append("frontend_catalog_pass")
    product = get("/product/" + PRODUCT)
    require(product.inputs.get("product_id") == PRODUCT, "product_selection_failed")
    results.append("product_browse_pass")
    get("/cart/empty", {})
    require("Your shopping cart is empty!" in get("/cart").words, "cart_empty_failed")
    get("/cart", {"product_id": PRODUCT, "quantity": "2"})
    cart = get("/cart")
    require("SKU #" + PRODUCT in cart.words and "Quantity: 2" in cart.words and "Cart (2)" in cart.words,
            "cart_add_semantics_failed")
    results.append("cart_add_pass")
    # Exercise removal and re-add in this cookie session before checkout.
    get("/cart/empty", {})
    require("Your shopping cart is empty!" in get("/cart").words, "cart_remove_failed")
    results.append("cart_remove_pass")
    get("/cart", {"product_id": PRODUCT, "quantity": "1"})
    cart = get("/cart")
    require("SKU #" + PRODUCT in cart.words and "Quantity: 1" in cart.words, "checkout_cart_missing")
    order = get("/cart/checkout", {
        "email": "smoke@example.invalid", "street_address": "123 Test Street", "zip_code": "12345",
        "city": "Test City", "state": "CA", "country": "United States",
        "credit_card_number": "4111111111111111", "credit_card_expiration_month": "12",
        "credit_card_expiration_year": str(datetime.now(timezone.utc).year + 1), "credit_card_cvv": "123",
    })
    require("Your order is complete!" in order.words and re.search(r"Confirmation # [a-zA-Z0-9-]{8,}", order.words)
            and re.search(r"Tracking # [a-zA-Z0-9-]{4,}", order.words), "checkout_confirmation_missing")
    require("Your shopping cart is empty!" in get("/cart").words, "checkout_cart_not_cleared")
    results.append("synthetic_checkout_pass")


def run(candidate_path, repository, profile, *, execute=False, inspection_only=False,
        docker_factory=Docker, session_factory=Session, clock=time.monotonic, sleep=time.sleep,
        project_ready=None):
    start = clock()
    total_deadline = start + LIMITS["total"]
    work_deadline = total_deadline - LIMITS["cleanup"] - LIMITS["diagnostics"]
    report = {"smokeReportVersion": 1, "runtimeOutcome": "not_started", "exitCode": 2,
              "inspectionOnly": inspection_only, "profile": profile if profile == PROFILE else None, "candidate": None,
              "validation": None, "checks": [], "smokeAssertions": [], "services": [],
              "diagnostics": [], "cleanup": {"outcome": "not_required"}, "limitsSeconds": dict(LIMITS),
              "scope": "Temporary runtime smoke only; release eligibility is separate. No approval or deployment authorization."}
    if not execute:
        report["diagnostics"] = ["runtime_opt_in_required"]
        return report
    project = "cp-smoke-" + uuid.uuid4().hex
    report["project"] = project
    label = "label=com.docker.compose.project=" + project
    report["manualCleanupPowerShell"] = (
        "docker ps -aq --filter " + label + " | ForEach-Object { docker rm -f -v $_ }; "
        "docker network ls -q --filter " + label + " | ForEach-Object { docker network rm $_ }; "
        "docker volume ls -q --filter " + label + " | ForEach-Object { docker volume rm $_ }")
    mutated, docker = False, None
    active = None

    def stage(name, operation):
        nonlocal active
        active = {"id": name, "status": "running"}
        report["checks"].append(active)
        deadline = min(work_deadline, clock() + LIMITS[name])
        operation(deadline)
        remaining(deadline, clock)
        active.update(status="pass")

    with tempfile.TemporaryDirectory(prefix="cp-smoke-") as temporary:
        directory = Path(temporary)
        try:
            with Path(candidate_path).open("rb") as stream:
                data = stream.read(1024 * 1024 + 1)
            if len(data) > 1024 * 1024:
                raise SmokeError("candidate_size_limit", "validation_failed")
            report["candidate"] = {"releaseId": None, "sha256": hashlib.sha256(data).hexdigest(),
                                   "inventory": None, "images": {}}
            snapshot = directory / "candidate.json"
            snapshot.write_bytes(data)
            rendered = render(snapshot, repository, profile, directory / "compose.json", inspection_only=inspection_only)
            report["validation"] = rendered["validation"]
            if rendered["validation"] and all(c["status"] == "pass" for c in rendered["validation"]["checks"][:2]):
                metadata = strict_json(data)
                report["candidate"].update(releaseId=metadata["releaseId"], inventory=metadata["inventory"],
                    images=dict(sorted((c["id"], c["image"]["repository"] + "@" + c["image"]["digest"]) for c in metadata["components"])))
            if rendered["exitCode"]:
                report["checks"].append({"id": "render", "status": "fail", "reason": "release_render_failed"})
                report["diagnostics"] = rendered["diagnostics"]
                report.update(runtimeOutcome="validation_failed" if rendered["exitCode"] == 1 else "execution_error",
                              exitCode=rendered["exitCode"])
                return report
            candidate = strict_json(data)
            images = {c["id"]: c["image"]["repository"] + "@" + c["image"]["digest"] for c in candidate["components"]}
            report["candidate"] = {"releaseId": candidate["releaseId"], "sha256": hashlib.sha256(data).hexdigest(),
                                   "inventory": candidate["inventory"], "images": dict(sorted(images.items()))}
            report["checks"].append({"id": "render", "status": "pass"})
            (directory / "empty.env").write_bytes(b"")
            docker = docker_factory(project, directory, clock)
            stage("prerequisites", lambda deadline: prerequisites(docker, images, deadline))
            if project_ready is not None:
                # Persist exact cleanup identity only after confirming no name collision,
                # and before any image pull or startup can be interrupted.
                project_ready(project, directory)
            stage("pull", lambda deadline: checked(docker.compose(["pull", "--policy", "always"], deadline), "image_pull_failed"))
            mutated = True  # A failed/interrupted up can already have created resources.
            stage("startup", lambda deadline: checked(docker.compose(["up", "--detach", "--no-build", "--pull", "never"], deadline), "compose_startup_failed"))
            session = session_factory(clock)

            def ready(deadline):
                while True:
                    states, running = service_state(docker, images, deadline)
                    report["services"] = states
                    if running:
                        try:
                            body = session.request("/", deadline)
                            if "/product/" + PRODUCT in Page(body).links:
                                return
                        except SmokeError as failure:
                            if failure.outcome == "execution_error":
                                raise
                    sleep(min(2, remaining(deadline, clock)))

            stage("readiness", ready)
            stage("smoke", lambda deadline: smoke_flow(session, deadline, report["smokeAssertions"]))
            states, running = service_state(docker, images, min(work_deadline, clock() + 10))
            report["services"] = states
            require(running, "services_not_running_after_smoke")
            report.update(runtimeOutcome="success", exitCode=0)
        except KeyboardInterrupt:
            report.update(runtimeOutcome="interrupted", exitCode=130)
            report["diagnostics"].append("runtime_interrupted")
        except SmokeError as failure:
            report.update(runtimeOutcome=failure.outcome, exitCode=EXIT[failure.outcome])
            report["diagnostics"].append(failure.code)
        except (OSError, Failure):
            report.update(runtimeOutcome="execution_error", exitCode=2)
            report["diagnostics"].append("runtime_input_or_io_failed")
        finally:
            if active and active["status"] == "running":
                active.update(status="fail", reason=report["diagnostics"][-1] if report["diagnostics"] else "runtime_failed")
            if mutated:
                # Preserve primary outcome; diagnostics/cleanup never erase it.
                try:
                    deadline = min(total_deadline - LIMITS["cleanup"], clock() + LIMITS["diagnostics"])
                    states, _ = service_state(docker, report["candidate"]["images"], deadline)
                    report["services"] = states
                except (SmokeError, KeyboardInterrupt, OSError):
                    report["diagnostics"].append("diagnostics_unavailable")
                try:
                    deadline = min(total_deadline, clock() + LIMITS["cleanup"])
                    checked(docker.compose(["down", "--volumes", "--remove-orphans", "--timeout", "10"], deadline), "cleanup_command_failed")
                    if any(resources(docker, deadline).values()):
                        raise SmokeError("cleanup_resources_remaining")
                    report["cleanup"] = {"outcome": "success"}
                except (SmokeError, KeyboardInterrupt, OSError) as failure:
                    report["cleanup"] = {"outcome": "failed", "reason": failure.code if isinstance(failure, SmokeError) else "cleanup_interrupted_or_io_failed"}
                    if report["exitCode"] == 0:
                        report["exitCode"] = 2
            report["elapsedSeconds"] = round(clock() - start, 3)
    return report


def main(argv=None):
    parser = Parser(description=__doc__, allow_abbrev=False)
    parser.add_argument("candidate")
    parser.add_argument("--repository", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--inspection-only", action="store_true")
    previous = signal.getsignal(signal.SIGTERM)
    def interrupted(*_):
        raise KeyboardInterrupt()

    try:
        signal.signal(signal.SIGTERM, interrupted)
        args = parser.parse_args(argv)
        report = run(args.candidate, args.repository, args.profile, execute=args.execute, inspection_only=args.inspection_only)
    except Failure:
        report = {"smokeReportVersion": 1, "runtimeOutcome": "execution_error", "exitCode": 2, "diagnostics": ["usage_error"]}
    finally:
        signal.signal(signal.SIGTERM, previous)
    print(json.dumps(report, indent=2, sort_keys=True))
    return report["exitCode"]


if __name__ == "__main__":
    raise SystemExit(main())
