"""Offline rendering checks; all Git repositories and outputs are temporary."""

import copy
import io
import json
from pathlib import Path
from contextlib import redirect_stdout
import unittest
from unittest.mock import patch

import test_validation as fixtures
from container_platform import compose


class ComposeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ValidationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.repo = self.fixture.repo
        self.inventory = json.loads((fixtures.ROOT / "inventories/online-shop-sre-dev-v1.json").read_text())
        self.candidate = json.loads((fixtures.ROOT / "releases/baseline-2026-10-18.json").read_text())
        self.pin_inventory()
        self.input = self.fixture.input
        self.output = Path(self.fixture.temp.name) / "compose.json"

    def pin_inventory(self):
        self.candidate["inventory"] = {"path": fixtures.INVENTORY_PATH,
                                       "revision": self.fixture.commit_inventory(self.inventory)}

    def render(self, inspection=True, **kwargs):
        self.input.write_text(json.dumps(self.candidate), encoding="utf-8")
        return compose.render(self.input, self.repo, compose.PROFILE, self.output,
                              inspection_only=inspection, **kwargs)

    def test_deterministic_exact_mapping_images_and_safe_topology(self):
        result = self.render()
        self.assertEqual(result["exitCode"], 0)
        self.assertEqual(result["validation"]["exitCode"], 1)
        self.assertEqual([d["code"] for d in result["validation"]["checks"][-1]["diagnostics"]], ["legacy_ineligible"] * 12)
        original = self.output.read_bytes()
        self.candidate["components"].reverse()
        self.assertEqual(self.render(overwrite=True)["exitCode"], 0)
        self.assertEqual(self.output.read_bytes(), original)
        document = json.loads(original)
        services = document["services"]
        self.assertEqual(set(services), compose.IDS)
        for component in self.candidate["components"]:
            service = services[component["id"]]
            self.assertEqual(service["image"], component["image"]["repository"] + "@" + component["image"]["digest"])
            self.assertEqual(service["platform"], "linux/amd64")
            self.assertFalse(set(service) & {"build", "volumes", "privileged", "network_mode", "container_name", "command", "entrypoint", "healthcheck"})
            if component["id"] != "frontend":
                self.assertNotIn("ports", service)
        self.assertEqual(services["frontend"]["ports"], [{"host_ip": "127.0.0.1", "published": "8080", "target": 8080, "protocol": "tcp"}])
        self.assertEqual(services["redis-cart"]["tmpfs"], ["/data"])
        self.assertEqual(services["loadgenerator"]["environment"]["FRONTEND_ADDR"], "frontend:8080")
        self.assertEqual(services["checkoutservice"]["environment"]["EMAIL_SERVICE_ADDR"], "emailservice:8080")
        self.assertEqual(services["frontend"]["environment"]["ENABLE_ASSISTANT"], "false")
        self.assertNotIn("${", original.decode())
        self.assertNotIn(str(self.repo), original.decode())
        self.assertTrue(document["x-container-platform"]["inspectionOnly"])

    def test_candidate_digest_is_the_only_image_source(self):
        self.candidate["components"][0]["image"]["digest"] = "sha256:" + "b" * 64
        self.assertEqual(self.render()["exitCode"], 0)
        self.assertEqual(json.loads(self.output.read_bytes())["services"][self.candidate["components"][0]["id"]]["image"].split("@")[1], "sha256:" + "b" * 64)
        self.assertNotIn("sha256:", json.dumps(compose.load_profile(compose.PROFILE)))

    def test_ordinary_render_requires_traceability(self):
        result = self.render(inspection=False)
        self.assertEqual(result["exitCode"], 1)
        self.assertFalse(self.output.exists())
        for c in self.candidate["components"]:
            c["traceability"].update(status="upstream", reason=None, upstream="https://example.invalid/publisher")
        result = self.render(inspection=False)
        self.assertEqual(result["exitCode"], 0)
        self.assertEqual(result["validation"]["exitCode"], 0)
        self.assertFalse(result["inspectionOnly"])

    def test_inspection_does_not_bypass_other_failures(self):
        mutations = [lambda c: c.update(purpose="example", notice="NON-DEPLOYABLE SYNTHETIC EXAMPLE"),
                     lambda c: c.update(schemaVersion=2),
                     lambda c: c["components"].pop(),
                     lambda c: c["components"][0]["image"].update(repository="docker.io/other/disallowed"),
                     lambda c: c["inventory"].update(revision="f" * 40),
                     lambda c: c["components"][0].update(command="untrusted")]
        original = copy.deepcopy(self.candidate)
        self.output.write_bytes(b"preserve existing output")
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.candidate = copy.deepcopy(original)
                mutation(self.candidate)
                self.assertNotEqual(self.render(overwrite=True)["exitCode"], 0)
                self.assertEqual(self.output.read_bytes(), b"preserve existing output")
        self.output.unlink()
        self.assertNotEqual(self.render()["exitCode"], 0)
        self.assertFalse(self.output.exists())

    def test_invalid_inventory_and_profile_membership_platform(self):
        self.inventory["components"][0]["required"] = False
        self.pin_inventory()
        self.assertNotEqual(self.render()["exitCode"], 0)
        self.inventory["components"][0]["required"] = True
        self.inventory["profile"] = "other-profile"
        self.pin_inventory()
        self.assertIn("profile_membership_mismatch", self.render()["diagnostics"])
        self.inventory["profile"] = "sre-dev-online-shop-v1"
        self.inventory["targetPlatforms"] = ["linux/arm64"]
        for entry in self.inventory["components"]:
            entry["targetPlatforms"] = ["linux/arm64"]
        for c in self.candidate["components"]:
            c["image"]["platforms"] = ["linux/arm64"]
        self.pin_inventory()
        self.assertIn("profile_membership_mismatch", self.render()["diagnostics"])
        self.assertFalse(self.output.exists())

    def test_valid_but_different_inventory_membership_is_not_this_profile(self):
        identity = self.inventory["components"][0]["id"]
        self.inventory["components"][0]["id"] = "other-service"
        for component in self.candidate["components"]:
            if component["id"] == identity:
                component["id"] = "other-service"
        self.pin_inventory()
        result = self.render()
        self.assertEqual(result["validation"]["checks"][-2]["status"], "pass")
        self.assertEqual(result["diagnostics"], ["profile_membership_mismatch"])
        self.assertFalse(self.output.exists())

    def test_strict_parse_and_unsupported_profile(self):
        for raw in [b"{", b'{"schemaVersion":1,"schemaVersion":1}', b'\xff']:
            self.input.write_bytes(raw)
            result = compose.render(self.input, self.repo, compose.PROFILE, self.output, inspection_only=True)
            self.assertNotEqual(result["exitCode"], 0)
            self.assertFalse(self.output.exists())
        result = compose.render(self.input, self.repo, "../untrusted", self.output)
        self.assertEqual(result["diagnostics"], ["profile_unsupported"])

    def test_invalid_configuration_mapping_rejected(self):
        profile = compose.load_profile(compose.PROFILE)
        profile["services"]["loadgenerator"]["environment"]["FRONTEND_ADDR"] = "frontend:80"
        with patch.object(compose, "strict_json", return_value=profile):
            with self.assertRaises(compose.Failure):
                compose.load_profile(compose.PROFILE)

    def test_overwrite_input_protection_and_write_failure(self):
        self.assertEqual(self.render()["exitCode"], 0)
        original = self.output.read_bytes()
        self.assertEqual(self.render()["diagnostics"], ["output_exists"])
        self.assertEqual(self.output.read_bytes(), original)
        self.assertEqual(self.render(overwrite=True)["exitCode"], 0)
        for target in [self.input, self.repo / fixtures.INVENTORY_PATH]:
            before = target.read_bytes()
            result = compose.render(self.input, self.repo, compose.PROFILE, target, inspection_only=True, overwrite=True)
            self.assertEqual(result["diagnostics"], ["output_is_input"])
            self.assertEqual(target.read_bytes(), before)
        result = compose.render(self.input, self.repo, compose.PROFILE, self.output / "missing" / "config.json", inspection_only=True)
        self.assertEqual(result["exitCode"], 2)
        self.assertEqual(result["diagnostics"], ["output_write_failed"])

    def test_cli_reports_separate_from_compose_document(self):
        self.input.write_text(json.dumps(self.candidate))
        output = io.StringIO()
        with redirect_stdout(output):
            code = compose.main([str(self.input), "--repository", str(self.repo), "--profile", compose.PROFILE,
                                 "--output", str(self.output), "--inspection-only"])
        self.assertEqual(code, 0)
        report = json.loads(output.getvalue())
        self.assertEqual(report["validation"]["exitCode"], 1)
        self.assertNotIn("services", report)
        self.assertIn("services", json.loads(self.output.read_bytes()))


if __name__ == "__main__":
    unittest.main()
