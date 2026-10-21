"""Runtime orchestration tests never call Docker or the network."""

import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import test_compose as fixtures
from container_platform import smoke
from container_platform.smoke_transport import Docker, Session, SmokeError, process_output


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now

    def sleep(self, delay):
        self.now += delay


class FakeDocker:
    def __init__(self, project, directory, clock):
        self.project, self.clock = project, clock
        self.document = json.loads((directory / "compose.json").read_bytes())
        self.calls, self.failure, self.states, self.cleanup_fail = [], None, "running", False

    def raw(self, args, deadline):
        smoke.remaining(deadline, self.clock)
        self.calls.append(args)
        if args[0] == "version":
            return 0, b'{"Os":"linux","Arch":"amd64"}'
        if args[0] == "context":
            return 0, b'"unix:///var/run/docker.sock"'
        return 0, b""

    def compose(self, args, deadline):
        smoke.remaining(deadline, self.clock)
        self.calls.append(args)
        if self.failure and args[0] == self.failure[0]:
            raise self.failure[1]
        if args[0] == "down" and self.cleanup_fail:
            return 1, b"private paths must never be echoed"
        if args[0] == "config":
            return 0, json.dumps(self.document).encode()
        if args[0] == "ps":
            return 0, json.dumps([{"Service": k, "State": self.states, "Image": v["image"]}
                                  for k, v in self.document["services"].items()]).encode()
        return 0, b""


class FakeSession:
    def __init__(self, clock):
        self.quantity, self.bad_checkout = 0, False

    def request(self, path, deadline, form=None):
        if path == "/":
            return '<a href="/product/' + smoke.PRODUCT + '">Catalog</a>'
        if path.startswith("/product/"):
            return '<input name="product_id" value="' + smoke.PRODUCT + '">'
        if path == "/cart/empty":
            self.quantity = 0
        if path == "/cart" and form:
            self.quantity += int(form["quantity"])
        if path == "/cart/checkout":
            assert form["email"].endswith(".invalid")
            self.quantity = 0
            return "unrelated HTTP 200 page" if self.bad_checkout else "Your order is complete! Confirmation # 12345678-abcd Tracking # TRACK123"
        if self.quantity:
            return f"Cart ({self.quantity}) SKU #{smoke.PRODUCT} Quantity: {self.quantity}"
        return "Your shopping cart is empty!"


class SmokeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ComposeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.input, self.repo = self.fixture.input, self.fixture.repo
        self.input.write_text(json.dumps(self.fixture.candidate))
        self.clock, self.docker, self.session = Clock(), None, None
        self.configure = lambda d: None
        self.configure_session = lambda s: None
        self.port = patch.object(smoke, "port_available")
        self.port.start()
        self.addCleanup(self.port.stop)

    def docker_factory(self, *args):
        self.docker = FakeDocker(*args)
        self.configure(self.docker)
        return self.docker

    def session_factory(self, clock):
        self.session = FakeSession(clock)
        self.configure_session(self.session)
        return self.session

    def run_smoke(self, **kwargs):
        options = dict(execute=True, inspection_only=True)
        options.update(kwargs)
        return smoke.run(self.input, self.repo, "aml-online-shop-compose-v1", docker_factory=self.docker_factory,
                         session_factory=self.session_factory, clock=self.clock, sleep=self.clock.sleep, **options)

    def test_no_opt_in_does_not_even_read_input_or_construct_docker(self):
        self.input.unlink()
        result = self.run_smoke(execute=False)
        self.assertEqual(result["diagnostics"], ["runtime_opt_in_required"])
        self.assertEqual(result["exitCode"], 2)
        self.assertIsNone(self.docker)

    def test_validation_and_inspection_restrictions_before_docker(self):
        self.assertEqual(self.run_smoke(inspection_only=False)["exitCode"], 1)
        self.assertIsNone(self.docker)
        self.fixture.candidate["components"].pop()
        self.input.write_text(json.dumps(self.fixture.candidate))
        result = self.run_smoke()
        self.assertEqual(result["exitCode"], 1)
        self.assertIsNone(self.docker)
        self.assertEqual(result["candidate"]["sha256"], hashlib.sha256(self.input.read_bytes()).hexdigest())

    def test_success_exact_images_snapshot_isolation_report_and_cleanup(self):
        original = self.input.read_bytes()
        def change_input(docker):
            self.input.write_bytes(b"changed after validation")
        self.configure = change_input
        result = self.run_smoke()
        self.assertEqual(result["exitCode"], 0)
        self.assertEqual(result["runtimeOutcome"], "success")
        self.assertEqual(result["cleanup"]["outcome"], "success")
        self.assertEqual(result["validation"]["exitCode"], 1)
        self.assertEqual(result["candidate"]["sha256"], hashlib.sha256(original).hexdigest())
        self.assertEqual(len(result["candidate"]["images"]), 12)
        self.assertEqual(result["smokeAssertions"], ["frontend_catalog_pass", "product_browse_pass", "cart_add_pass", "cart_remove_pass", "synthetic_checkout_pass"])
        self.assertEqual(result["project"], self.docker.project)
        self.assertRegex(result["project"], r"^cp-smoke-[a-f0-9]{32}$")
        self.assertIn(["up", "--detach", "--no-build", "--pull", "never"], self.docker.calls)
        self.assertIn(["down", "--volumes", "--remove-orphans", "--timeout", "10"], self.docker.calls)
        self.assertNotIn(str(self.repo), json.dumps(result))
        self.assertNotIn("private paths", json.dumps(result))

    def test_partial_startup_and_interruption_always_cleanup(self):
        for failure, expected in [(SmokeError("partial_up_failed"), 2), (KeyboardInterrupt(), 130)]:
            self.configure = lambda d: setattr(d, "failure", ("up", failure))
            result = self.run_smoke()
            self.assertEqual(result["exitCode"], expected)
            self.assertEqual(result["cleanup"]["outcome"], "success")

    def test_pull_timeout_has_no_project_resources(self):
        self.configure = lambda d: setattr(d, "failure", ("pull", SmokeError("pull_timeout", "timeout")))
        result = self.run_smoke()
        self.assertEqual(result["exitCode"], 3)
        self.assertEqual(result["cleanup"]["outcome"], "not_required")
        self.assertFalse(any(c[0] == "up" for c in self.docker.calls))

    def test_readiness_retries_share_one_deadline(self):
        self.configure = lambda d: setattr(d, "states", "created")
        result = self.run_smoke()
        self.assertEqual(result["exitCode"], 3)
        self.assertEqual(result["elapsedSeconds"], smoke.LIMITS["readiness"])
        self.assertEqual(result["cleanup"]["outcome"], "success")

    def test_http_200_without_order_confirmation_fails(self):
        self.configure_session = lambda s: setattr(s, "bad_checkout", True)
        result = self.run_smoke()
        self.assertEqual(result["exitCode"], 1)
        self.assertIn("checkout_confirmation_missing", result["diagnostics"])
        self.assertEqual(result["cleanup"]["outcome"], "success")

    def test_cleanup_failure_never_gives_success_or_hides_primary(self):
        self.configure = lambda d: setattr(d, "cleanup_fail", True)
        result = self.run_smoke()
        self.assertEqual(result["runtimeOutcome"], "success")
        self.assertEqual(result["exitCode"], 2)
        self.assertEqual(result["cleanup"]["outcome"], "failed")
        self.configure_session = lambda s: setattr(s, "bad_checkout", True)
        result = self.run_smoke()
        self.assertEqual(result["exitCode"], 1)
        self.assertEqual(result["diagnostics"][0], "checkout_confirmation_missing")
        self.assertEqual(result["cleanup"]["outcome"], "failed")

    def test_missing_services_and_image_mismatch_are_not_ready(self):
        def wrong_image(d):
            d.document["services"]["frontend"]["image"] = "other"
        self.configure = wrong_image
        result = self.run_smoke()
        self.assertEqual(result["diagnostics"], ["runtime_model_mismatch"])
        self.assertFalse(any(c[0] == "pull" for c in self.docker.calls))

    def test_engine_or_busy_port_failure_has_no_mutations(self):
        with patch.object(smoke, "port_available", side_effect=SmokeError("frontend_port_unavailable")):
            result = self.run_smoke()
        self.assertEqual(result["diagnostics"], ["frontend_port_unavailable"])
        self.assertFalse(any(c[0] in ("pull", "up", "down") for c in self.docker.calls))
        def unavailable(docker):
            docker.raw = lambda args, deadline: (1, b"machine-specific connection error")
        self.configure = unavailable
        result = self.run_smoke()
        self.assertEqual(result["diagnostics"], ["docker_engine_unavailable"])
        self.assertEqual(result["cleanup"]["outcome"], "not_required")

    def test_remote_engine_context_is_rejected_before_pull(self):
        def remote(docker):
            docker.env = {"DOCKER_HOST": "ssh://remote.invalid"}
        self.configure = remote
        result = self.run_smoke()
        self.assertEqual(result["diagnostics"], ["remote_docker_transport_unsupported"])
        self.assertFalse(any(c[0] in ("pull", "up", "down") for c in self.docker.calls))

    def test_failed_container_is_application_failure_and_cleanup_is_checked(self):
        self.configure = lambda d: setattr(d, "states", "exited")
        result = self.run_smoke()
        self.assertEqual(result["runtimeOutcome"], "application_failure")
        self.assertIn("service_not_running", result["diagnostics"])
        self.assertEqual(result["cleanup"]["outcome"], "success")

    def test_missing_service_records_cannot_satisfy_readiness(self):
        def missing_service(docker):
            original = docker.compose
            def compose(args, deadline):
                if args[0] == "ps":
                    code, output = original(args, deadline)
                    return code, json.dumps(json.loads(output)[:-1]).encode()
                return original(args, deadline)
            docker.compose = compose
        self.configure = missing_service
        result = self.run_smoke()
        self.assertEqual(result["exitCode"], 3)
        self.assertEqual(len(result["services"]), 11)
        self.assertEqual(result["cleanup"]["outcome"], "success")

    def test_cleanup_verification_detects_resources_remaining(self):
        def residue(docker):
            original_raw = docker.raw
            def raw(args, deadline):
                if any(c[0] == "down" for c in docker.calls) and args[0] == "ps":
                    return 0, b"remaining-project-container"
                return original_raw(args, deadline)
            docker.raw = raw
        self.configure = residue
        result = self.run_smoke()
        self.assertEqual(result["exitCode"], 2)
        self.assertEqual(result["cleanup"]["reason"], "cleanup_resources_remaining")


class TransportTests(unittest.TestCase):
    def test_compose_environment_and_explicit_arguments(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {"COMPOSE_FILE": "hostile", "COMPOSE_PROFILES": "other", "COMPOSE_PROJECT_NAME": "foreign", "DOCKER_DEFAULT_PLATFORM": "linux/arm64"}):
            docker = Docker("cp-smoke-test", Path(temporary))
            self.assertEqual([k for k in docker.env if k.startswith("COMPOSE_")], ["COMPOSE_DISABLE_ENV_FILE"])
            self.assertNotIn("DOCKER_DEFAULT_PLATFORM", docker.env)
            with patch.object(docker, "raw", return_value=(0, b"")) as call:
                docker.compose(["up", "--no-build"], time.monotonic() + 1)
            args = call.call_args.args[0]
            self.assertIn("empty.env", args)
            self.assertIn("compose.json", args)
            self.assertIn("cp-smoke-test", args)

    def test_process_output_and_timeout_are_bounded(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(SmokeError) as error:
                process_output([sys.executable, "-c", "import time; time.sleep(10)"], temporary, dict(os.environ), time.monotonic() + 0.2)
            self.assertEqual(error.exception.outcome, "timeout")
            with self.assertRaises(SmokeError) as error:
                process_output([sys.executable, "-c", "print('x'*2000000)"], temporary, dict(os.environ), time.monotonic() + 10)
            self.assertEqual(error.exception.code, "subprocess_output_limit")

    def test_http_cannot_redirect_to_external_origin(self):
        import urllib.error
        from email.message import Message
        session = Session()
        headers = Message()
        headers["Location"] = "https://external.invalid/"
        error = urllib.error.HTTPError(session.origin, 302, "redirect", headers, io.BytesIO())
        with patch.object(session.opener, "open", side_effect=error) as opened:
            with self.assertRaises(SmokeError) as result:
                session.request("/", time.monotonic() + 10)
        self.assertEqual(result.exception.code, "http_redirect_outside_frontend")
        self.assertEqual(opened.call_count, 1)

    def test_http_response_size_and_request_deadline(self):
        response = io.BytesIO(b"x" * (2 * 1024 * 1024 + 1))
        response.code = 200
        session = Session()
        with patch.object(session.opener, "open", return_value=response):
            with self.assertRaises(SmokeError) as error:
                session.request("/", time.monotonic() + 10)
        self.assertEqual(error.exception.code, "http_response_limit")
        with patch.object(session.opener, "open", side_effect=TimeoutError):
            with self.assertRaises(SmokeError) as error:
                session.request("/", time.monotonic() + 10)
        self.assertEqual(error.exception.outcome, "timeout")


if __name__ == "__main__":
    unittest.main()
