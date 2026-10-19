"""Mocked Docker Hub exchanges; no network services, Docker or credentials."""

import copy
import hashlib
import io
import json
import socket
import ssl
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import test_validation as offline
from container_platform import registry as hub
from container_platform.cli import main, render_text
from container_platform.validation import validate


OCI = "application/vnd.oci.image.manifest.v1+json"
DOCKER = "application/vnd.docker.distribution.manifest.v2+json"
INDEX = "application/vnd.oci.image.index.v1+json"
LIST = "application/vnd.docker.distribution.manifest.list.v2+json"
CONFIG = "application/vnd.oci.image.config.v1+json"
REPO = "docker.io/example/image"
SECRET = "test-secret.token"


def encode(value):
    return json.dumps(value, indent=1).encode()


def digest(body):
    return "sha256:" + hashlib.sha256(body).hexdigest()


def response(body, content_type, status=200):
    return hub.Response(status, {"content-type": content_type, "docker-content-digest": digest(body)}, body)


def descriptor(body, media_type, platform=None):
    value = {"mediaType": media_type, "digest": digest(body), "size": len(body)}
    if platform is not None:
        value["platform"] = platform
    return value


def fixture(os="linux", architecture="amd64", variant=None, media_type=OCI):
    config = {"os": os, "architecture": architecture}
    if variant is not None:
        config["variant"] = variant
    config_bytes = encode(config)
    config_descriptor = descriptor(config_bytes, CONFIG)
    config_descriptor["urls"] = ["https://user:secret@localhost/config"]
    manifest = {"schemaVersion": 2, "mediaType": media_type, "config": config_descriptor,
                "layers": [{"mediaType": "application/vnd.oci.image.layer.v1.tar+gzip", "digest": "sha256:" + "f" * 64, "size": 999,
                            "urls": ["https://user:secret@localhost/layer"]}]}
    body = encode(manifest)
    platform = "/".join([os, architecture] + ([variant] if variant is not None else []))
    image = {"repository": REPO, "digest": digest(body), "kind": "manifest", "platforms": [platform]}
    responses = {digest(body): response(body, media_type), digest(config_bytes): response(config_bytes, "application/octet-stream")}
    return image, responses, manifest


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.no_network = patch.object(socket, "socket", side_effect=AssertionError("Unexpected network access"))
        self.no_network.start()
        self.addCleanup(self.no_network.stop)
        self.calls = []

    def transport(self, outcomes):
        transport = hub.Transport()

        def exchange(url, headers, limit, budget):
            self.calls.append((url, dict(headers), limit))
            if isinstance(outcomes, list):
                outcome = outcomes.pop(0)
            elif callable(outcomes):
                outcome = outcomes(url, headers)
            else:
                outcome = outcomes[url.rsplit("/", 1)[-1]]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        transport.exchange = exchange
        return transport

    def inspect(self, image, outcomes):
        client = hub.HubClient(image["repository"], self.transport(outcomes), hub.ComponentBudget(hub.RunBudget()))
        hub.inspect_image(image, client)

    def assert_code(self, code, image, outcomes):
        with self.assertRaises(hub.RegistryFailure) as caught:
            self.inspect(image, outcomes)
        self.assertEqual(caught.exception.code, code)
        self.assertNotIn(SECRET, str(caught.exception))

    def rewrite(self, image, responses, manifest):
        body = encode(manifest)
        image["digest"] = digest(body)
        responses[digest(body)] = response(body, manifest.get("mediaType", OCI))

    def test_manifest_happy_paths_raw_bytes_parameters_and_no_layers(self):
        for media in sorted(hub.MANIFESTS):
            with self.subTest(media=media):
                image, responses, _ = fixture(media_type=media)
                responses[image["digest"]].headers["content-type"] += '; charset="utf-8"'
                self.inspect(image, responses)
        self.assertTrue(all("/manifests/" in url or "/blobs/" in url for url, _, _ in self.calls))
        self.assertFalse(any("f" * 64 in url or "localhost" in url for url, _, _ in self.calls))

    def test_body_media_type_can_be_omitted_if_http_type_present(self):
        image, responses, manifest = fixture()
        del manifest["mediaType"]
        self.rewrite(image, responses, manifest)
        self.inspect(image, responses)

    def test_raw_digest_header_and_size_mismatches(self):
        image, responses, manifest = fixture()
        wrong = copy.deepcopy(responses)
        wrong[image["digest"]].body += b" "
        self.assert_code("registry_digest_mismatch", image, wrong)
        wrong = copy.deepcopy(responses)
        wrong[image["digest"]].headers["docker-content-digest"] = "sha256:" + "b" * 64
        self.assert_code("registry_digest_mismatch", image, wrong)
        wrong = copy.deepcopy(responses)
        config_digest = manifest["config"]["digest"]
        wrong[config_digest].body += b" "
        self.assert_code("registry_digest_mismatch", image, wrong)
        manifest["config"]["size"] += 1
        self.rewrite(image, responses, manifest)
        self.assert_code("registry_size_mismatch", image, responses)

    def test_content_and_descriptor_media_type_errors(self):
        for value in ["text/html", DOCKER]:
            image, responses, _ = fixture()
            responses[image["digest"]].headers["content-type"] = value
            self.assert_code("registry_unsupported_media_type", image, responses)
        image, responses, manifest = fixture()
        manifest["config"]["mediaType"] = "application/example"
        self.rewrite(image, responses, manifest)
        self.assert_code("registry_unsupported_media_type", image, responses)
        image, responses, manifest = fixture()
        responses[manifest["config"]["digest"]].headers["content-type"] = "text/html"
        self.assert_code("registry_unsupported_media_type", image, responses)
        for bad_type in [[], {}, 123, "text/html"]:
            image, responses, manifest = fixture()
            manifest["mediaType"] = bad_type
            body = encode(manifest)
            image["digest"] = digest(body)
            responses[digest(body)] = response(body, OCI)
            self.assert_code("registry_unsupported_media_type", image, responses)

    def test_kind_and_config_platform_mismatch(self):
        image, responses, _ = fixture()
        image["kind"] = "index"
        self.assert_code("registry_kind_mismatch", image, responses)
        for platform in ["linux/arm64", "windows/amd64", "linux/amd64/v3"]:
            image, responses, _ = fixture()
            image["platforms"] = [platform]
            self.assert_code("registry_platform_mismatch", image, responses)

    def test_strict_manifest_config_json_and_structures(self):
        for raw in [b'{', b'{"schemaVersion":2,"schemaVersion":2}', b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e9999}', b'\xff', b'[]']:
            with self.subTest(raw=raw):
                image, responses, _ = fixture()
                image["digest"] = digest(raw)
                responses[digest(raw)] = response(raw, OCI)
                self.assert_code("registry_malformed_response", image, responses)
        for field, value in [("schemaVersion", True), ("schemaVersion", 1), ("config", None), ("layers", {}), ("layers", [None])]:
            image, responses, manifest = fixture()
            manifest[field] = value
            self.rewrite(image, responses, manifest)
            self.assert_code("registry_malformed_response", image, responses)
        for raw in [b'{"os":"linux","os":"windows"}', b'{"x":NaN}', b'[]', b'{']:
            image, responses, manifest = fixture()
            manifest["config"] = descriptor(raw, CONFIG)
            responses[digest(raw)] = response(raw, "application/octet-stream")
            self.rewrite(image, responses, manifest)
            self.assert_code("registry_malformed_response", image, responses)
        for field, value in [("digest", "sha512:" + "a" * 128), ("size", True), ("size", -1), ("digest", "sha256:" + "a" * 64 + "\n")]:
            image, responses, manifest = fixture()
            manifest["config"][field] = value
            self.rewrite(image, responses, manifest)
            self.assert_code("registry_malformed_response", image, responses)

    def index_fixture(self, index_type=INDEX):
        amd, responses, _ = fixture()
        arm, more, _ = fixture(architecture="arm", variant="v7")
        responses.update(more)
        children = [descriptor(responses[amd["digest"]].body, OCI, {"os": "linux", "architecture": "amd64"}),
                    descriptor(responses[arm["digest"]].body, OCI, {"os": "linux", "architecture": "arm", "variant": "v7"})]
        body = {"schemaVersion": 2, "mediaType": index_type, "manifests": children}
        raw = encode(body)
        image = {"repository": REPO, "digest": digest(raw), "kind": "index", "platforms": ["linux/arm/v7", "linux/amd64"]}
        responses[digest(raw)] = response(raw, index_type)
        return image, responses, body

    def test_index_required_children_and_extra_unknown_attestations(self):
        for media in sorted(hub.INDEXES):
            image, responses, body = self.index_fixture(media)
            body["manifests"].extend([
                {"mediaType": "application/vnd.oci.artifact.manifest.v1+json", "digest": "sha256:" + "f" * 64, "size": 100,
                 "platform": {"os": "unknown", "architecture": "unknown"}, "urls": ["https://localhost/secret"]},
                {"mediaType": OCI, "digest": "sha256:" + "e" * 64, "size": 100},
                {"mediaType": OCI, "digest": "sha256:" + "e" * 64, "size": 100, "platform": {}},
                {"mediaType": OCI, "digest": "sha256:" + "e" * 64, "size": 100,
                 "platform": {"os": "linux", "architecture": "arm64", "variant": "v8.1"}},
            ])
            self.rewrite(image, responses, body)
            self.inspect(image, responses)
        self.assertFalse(any("f" * 64 in url or "e" * 64 in url for url, _, _ in self.calls))

    def test_index_missing_ambiguous_nested_and_bad_descriptor(self):
        for mutation, code in [
            (lambda b: b.update(manifests=[]), "registry_platform_missing"),
            (lambda b: b["manifests"].append(copy.deepcopy(b["manifests"][0])), "registry_platform_ambiguous"),
            (lambda b: b["manifests"][0].update(mediaType=INDEX), "registry_unsupported_media_type"),
            (lambda b: b.update(manifests={}), "registry_malformed_response"),
            (lambda b: b["manifests"][0].update(size=-1), "registry_malformed_response"),
        ]:
            image, responses, body = self.index_fixture()
            mutation(body)
            self.rewrite(image, responses, body)
            self.assert_code(code, image, responses)

    def test_selected_child_size_media_hash_and_config_consistency(self):
        image, responses, body = self.index_fixture()
        body["manifests"][0]["size"] += 1
        self.rewrite(image, responses, body)
        self.assert_code("registry_size_mismatch", image, responses)
        image, responses, body = self.index_fixture()
        body["manifests"][0]["mediaType"] = DOCKER
        self.rewrite(image, responses, body)
        self.assert_code("registry_unsupported_media_type", image, responses)
        image, responses, body = self.index_fixture()
        responses[body["manifests"][0]["digest"]].body += b" "
        self.assert_code("registry_digest_mismatch", image, responses)
        image, responses, body = self.index_fixture()
        body["manifests"][0]["platform"]["architecture"] = "arm64"
        image["platforms"] = ["linux/arm64"]
        self.rewrite(image, responses, body)
        self.assert_code("registry_platform_mismatch", image, responses)

    def test_public_bearer_flow_and_no_login(self):
        image, objects, _ = fixture()
        challenge = f'Bearer realm="{hub.AUTH}",service="{hub.SERVICE}",scope="repository:example/image:pull"'
        def exchange(url, headers):
            if url.startswith(hub.AUTH):
                self.assertNotIn("Authorization", headers)
                self.assertIn("scope=repository%3Aexample%2Fimage%3Apull", url)
                return hub.Response(200, {"content-type": "application/json; charset=utf-8"}, encode({"token": SECRET}))
            if "Authorization" not in headers:
                return hub.Response(401, {"www-authenticate": challenge})
            self.assertEqual(headers["Authorization"], "Bearer " + SECRET)
            return objects[url.rsplit("/", 1)[-1]]
        self.inspect(image, exchange)
        self.assertEqual(len(self.calls), 4)

    def test_unsafe_challenges_never_contact_auth(self):
        image, _, _ = fixture()
        for challenge in [
            'Basic realm="secret"',
            'Bearer realm="https://localhost/token",service="registry.docker.io",scope="repository:example/image:pull"',
            f'Bearer realm="{hub.AUTH}",service="wrong",scope="repository:example/image:pull"',
            f'Bearer realm="{hub.AUTH}",service="{hub.SERVICE}",scope="repository:example/image:pull,push"',
            f'Bearer realm="{hub.AUTH}",service="{hub.SERVICE}",scope="repository:other/image:pull"',
            f'Bearer realm="{hub.AUTH}",realm="{hub.AUTH}",service="{hub.SERVICE}",scope="repository:example/image:pull"',
            f'Bearer realm="{hub.AUTH}?secret=1",service="{hub.SERVICE}",scope="repository:example/image:pull"',
            None,
        ]:
            before = len(self.calls)
            self.assert_code("registry_unsafe_challenge", image, [hub.Response(401, {"www-authenticate": challenge})])
            self.assertEqual(len(self.calls), before + 1)

    def test_auth_failure_and_malformed_secret_response(self):
        image, _, _ = fixture()
        challenge = f'Bearer realm="{hub.AUTH}",service="{hub.SERVICE}",scope="repository:example/image:pull"'
        start = hub.Response(401, {"www-authenticate": challenge})
        for token_response, code in [
            (hub.Response(403, {}, SECRET.encode()), "registry_auth_failed"),
            (hub.Response(200, {}, encode({"token": SECRET + "\n"})), "registry_auth_failed"),
            (hub.Response(200, {}, ('{"secret":"' + SECRET + '",').encode()), "registry_malformed_response"),
            (hub.Response(200, {}, encode({"token": SECRET, "access_token": "other"})), "registry_auth_failed"),
        ]:
            self.assert_code(code, image, [start, token_response])
        self.assert_code("registry_auth_failed", image, [start, hub.Response(200, {}, encode({"access_token": SECRET})), start])

    def test_redirect_allowlist_hops_and_no_authorization_forwarding(self):
        source = hub.REGISTRY + "/v2/example/image/blobs/sha256:" + "a" * 64
        location = f"https://{hub.CDN}/registry-v2/docker/registry/v2/blobs/sha256/aa/data?signature={SECRET}"
        transport = self.transport([hub.Response(307, {"location": location}), hub.Response(200, {}, b'{}')])
        transport.get(source, {"Authorization": "Bearer " + SECRET}, "config", hub.ComponentBudget(hub.RunBudget()), "/v2/example/image/blobs/sha256:" + "a" * 64)
        self.assertIn("Authorization", self.calls[-2][1])
        self.assertNotIn("Authorization", self.calls[-1][1])
        for destination in ["http://" + hub.CDN + "/data", "https://127.0.0.1/data", "https://localhost/data",
                            "https://evil.example/data", "https://" + hub.CDN + ".evil.example/data",
                            "https://user:secret@" + hub.CDN + "/data", "https://" + hub.CDN + ":444/data",
                            "https://" + hub.CDN + "/arbitrary", "\n" + location]:
            transport = self.transport([hub.Response(307, {"location": destination})])
            with self.subTest(destination=destination), self.assertRaises(hub.RegistryFailure) as caught:
                transport.get(source, {}, "config", hub.ComponentBudget(hub.RunBudget()), "/v2/example/image/blobs/sha256:" + "a" * 64)
            self.assertEqual(caught.exception.code, "registry_unsafe_redirect")
        transport = self.transport([hub.Response(307, {"location": location})] * 3)
        with self.assertRaises(hub.RegistryFailure) as caught:
            transport.get(source, {}, "config", hub.ComponentBudget(hub.RunBudget()), "/v2/example/image/blobs/sha256:" + "a" * 64)
        self.assertEqual(caught.exception.code, "registry_limits_exhausted")
        for kind, url in [("token", hub.AUTH), ("manifest", hub.REGISTRY + "/v2/example/image/manifests/x")]:
            with self.assertRaises(hub.RegistryFailure) as caught:
                self.transport([hub.Response(302, {"location": location})]).get(url, {}, kind, hub.ComponentBudget(hub.RunBudget()))
            self.assertEqual(caught.exception.code, "registry_unsafe_redirect")

    def test_statuses_timeout_tls_and_bounded_retry(self):
        image, objects, manifest = fixture()
        for status, code in [(404, "registry_artifact_missing"), (403, "registry_auth_failed"), (418, "registry_http_error"),
                             (429, "registry_rate_limited"), (500, "registry_server_error"), (503, "registry_server_error")]:
            before = len(self.calls)
            self.assert_code(code, image, [hub.Response(status, {})] * 2)
            self.assertEqual(len(self.calls) - before, 2 if status in (429, 500, 503) else 1)
        self.assert_code("registry_config_missing", image, [objects[image["digest"]], hub.Response(404, {})])
        self.assert_code("registry_timeout", image, [TimeoutError(SECRET)] * 2)
        self.assert_code("registry_network_error", image, [OSError(SECRET)] * 2)
        self.assert_code("registry_tls_error", image, [ssl.SSLError(SECRET)])
        self.inspect(image, [hub.Response(500, {}), objects[image["digest"]], objects[manifest["config"]["digest"]]])
        self.inspect(image, [TimeoutError(), objects[image["digest"]], objects[manifest["config"]["digest"]]])
        for delay in ["999999", "2", "date", "1" * 5000]:
            self.assert_code("registry_rate_limited", image, [hub.Response(429, {"retry-after": delay})])
        with patch.object(hub.time, "sleep") as sleep:
            self.inspect(image, [hub.Response(429, {"retry-after": "1"}), objects[image["digest"]], objects[manifest["config"]["digest"]]])
            sleep.assert_called_once_with(1)

    def test_response_and_request_limits(self):
        image, _, _ = fixture()
        self.assert_code("registry_response_too_large", image, [hub.Response(200, {}, b'x' * (hub.MAX_BYTES["manifest"] + 1))])
        for component_limit, run_limit in [(24, 0), (0, 256)]:
            budget = hub.ComponentBudget(hub.RunBudget(requests=run_limit), requests=component_limit)
            with self.assertRaises(hub.RegistryFailure) as caught:
                self.transport([]).get(hub.REGISTRY + "/v2/example/image/manifests/x", {}, "manifest", budget)
            self.assertEqual(caught.exception.code, "registry_limits_exhausted")
        budget = hub.ComponentBudget(hub.RunBudget())
        budget.started -= hub.COMPONENT_SECONDS + 1
        with self.assertRaises(hub.RegistryFailure) as caught:
            budget.consume()
        self.assertEqual(caught.exception.code, "registry_limits_exhausted")

    def test_actual_stream_size_limit_and_duplicate_headers(self):
        class Stream:
            code = 200
            def __init__(self, headers, data):
                self.headers, self.data = headers, io.BytesIO(data)
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read1(self, count): return self.data.read(count)
        class Headers:
            def __init__(self, pairs): self.pairs = pairs
            def items(self): return self.pairs
        for headers, body, code in [
            ([("Content-Length", "999")], b'', "registry_response_too_large"),
            ([], b'x' * 6, "registry_response_too_large"),
            ([("Content-Type", OCI), ("content-type", DOCKER)], b'', "registry_malformed_response"),
            ([("Content-Length", "5")], b'x', "registry_malformed_response"),
            ([("Content-Encoding", "gzip")], b'{}', "registry_malformed_response"),
        ]:
            transport = hub.Transport()
            with patch.object(transport.opener, "open", return_value=Stream(Headers(headers), body)):
                with self.assertRaises(hub.RegistryFailure) as caught:
                    transport.exchange(hub.REGISTRY, {}, 5, hub.ComponentBudget(hub.RunBudget()))
            self.assertEqual(caught.exception.code, code)

    def test_evaluation_prerequisites_offline_and_legacy_independence(self):
        fixture_case = offline.ValidationTests()
        fixture_case.setUp()
        self.addCleanup(fixture_case.doCleanups)
        fixture_case.run_candidate()
        with patch.object(hub, "inspect_components", side_effect=AssertionError("No opt-in")):
            self.assertEqual(validate(fixture_case.input, fixture_case.repo)["exitCode"], 0)
        fixture_case.candidate["components"][0]["image"]["repository"] = "docker.io/example/disallowed"
        fixture_case.run_candidate()
        with patch.object(hub, "inspect_components", side_effect=AssertionError("Failed prerequisites")):
            result = validate(fixture_case.input, fixture_case.repo, registry=True)
        self.assertEqual(result["checks"][-1]["status"], "skipped")
        baseline = offline.ROOT / "releases/baseline-2026-10-18.json"
        for registry_fail in [False, True]:
            # Exercise every baseline component with mocked HubClient inspection.
            with patch.object(hub, "inspect_image", side_effect=hub.RegistryFailure("registry_artifact_missing") if registry_fail else None) as inspect:
                result = validate(baseline, offline.ROOT, registry=True)
            self.assertEqual(inspect.call_count, 12)
            self.assertEqual(result["exitCode"], 1)
            self.assertEqual(len(result["checks"][-2]["diagnostics"]), 12)
            self.assertEqual(result["checks"][-1]["status"], "fail" if registry_fail else "pass")
            self.assertEqual(len(result["checks"][-1]["components"]), 12)
        with patch.object(hub, "inspect_image", side_effect=[hub.RegistryFailure("registry_digest_mismatch")] + [None] * 11):
            result = validate(baseline, offline.ROOT, registry=True)
        self.assertEqual([c["status"] for c in result["checks"][-1]["components"]], ["fail"] + ["pass"] * 11)
        self.assertEqual(len(result["checks"][-2]["diagnostics"]), 12)

    def test_each_failed_shared_stage_prevents_registry_access(self):
        case = offline.ValidationTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        candidates = []
        c = copy.deepcopy(case.candidate)
        c["components"][0]["image"]["digest"] += "\n"
        candidates.append(c)
        c = copy.deepcopy(case.candidate)
        c.update(purpose="example", notice="NON-DEPLOYABLE SYNTHETIC EXAMPLE")
        candidates.append(c)
        c = copy.deepcopy(case.candidate)
        c["inventory"]["revision"] = "f" * 40
        candidates.append(c)
        for raw in [b'{', encode({"inventoryVersion": 2})]:
            c = copy.deepcopy(case.candidate)
            c["inventory"]["revision"] = case.commit_bytes(raw)
            candidates.append(c)
        c = copy.deepcopy(case.candidate)
        c["components"].pop()
        candidates.append(c)
        with patch.object(hub, "inspect_components", side_effect=AssertionError("No registry before prerequisites")):
            for candidate in candidates:
                case.input.write_bytes(encode(candidate))
                report = validate(case.input, case.repo, registry=True)
                self.assertEqual(report["checks"][-1]["status"], "skipped")
            case.input.write_bytes(b'{')
            self.assertEqual(validate(case.input, case.repo, registry=True)["checks"][-1]["status"], "skipped")

    def test_registry_result_cli_text_json_exit_codes_and_redaction(self):
        fixture_case = offline.ValidationTests()
        fixture_case.setUp()
        self.addCleanup(fixture_case.doCleanups)
        fixture_case.run_candidate()
        for code, expected in [(None, 0), ("registry_digest_mismatch", 1), ("registry_timeout", 2)]:
            with patch.object(hub, "inspect_image", side_effect=hub.RegistryFailure(code) if code else None):
                report = validate(fixture_case.input, fixture_case.repo, registry=True)
                for fmt in ["json", "text"]:
                    out = io.StringIO()
                    with redirect_stdout(out):
                        status = main([str(fixture_case.input), "--repository", str(fixture_case.repo), "--registry", "--format", fmt])
                    self.assertEqual(status, expected)
                    self.assertNotIn(SECRET, out.getvalue())
                    self.assertNotIn("?", out.getvalue())
                    self.assertEqual(json.loads(out.getvalue()) if fmt == "json" else out.getvalue().rstrip(), report if fmt == "json" else render_text(report))
                self.assertEqual(report["outputSchemaVersion"], 2)
                if not code:
                    self.assertEqual(report["result"], "selected_rules_passed")
                    self.assertNotIn("Offline rules only", report["scope"])


if __name__ == "__main__":
    unittest.main()
