"""Manual workflow helper tests use local Git and fake runtime/cleanup adapters."""

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import test_compose as fixtures
from container_platform.compose import IDS

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("manual_smoke", ROOT / "scripts/manual_smoke.py")
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)


class ManualSmokeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ComposeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.repo = self.fixture.repo
        self.path = "releases/candidate.json"
        self.candidate = self.fixture.candidate
        self.commit_candidate()
        self.state = Path(self.fixture.fixture.temp.name) / "state"
        self.state.mkdir()
        self.output = self.state.parent / "evidence/report.json"
        self.summary = self.state.parent / "summary.md"
        self.install = patch.object(helper, "verify_install")
        self.install.start()
        self.addCleanup(self.install.stop)

    def commit_candidate(self):
        path = self.repo / self.path
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(json.dumps(self.candidate).encode())
        git = self.fixture.fixture.git
        git("add", "-A")
        git("commit", "-qm", "test: add candidate")
        self.commit = git("rev-parse", "HEAD").decode().strip()
        self.env = {"SMOKE_COMMIT": self.commit, "SMOKE_PROFILE": helper.PROFILE,
                    "SMOKE_CANDIDATE": self.path, "SMOKE_INSPECTION_ONLY": "true"}

    def report(self, code=0):
        validation = self.fixture.render()["validation"]
        return {"smokeReportVersion": 1, "exitCode": code, "inspectionOnly": True, "profile": helper.PROFILE,
                "candidate": {"releaseId": self.candidate["releaseId"], "sha256": hashlib.sha256((self.repo / self.path).read_bytes()).hexdigest()},
                "validation": validation, "cleanup": {"outcome": "success"},
                "runtimeOutcome": "success" if code == 0 else "application_failure",
                "checks": [], "diagnostics": [], "services": [{"service": name, "state": "running"} for name in sorted(IDS)],
                "smokeAssertions": ["frontend_catalog_pass", "product_browse_pass", "cart_add_pass", "cart_remove_pass", "synthetic_checkout_pass"],
                "unknown_private_payload": "must not be uploaded"}

    def write_state(self, report, primary=None):
        _, metadata = helper.prepare(self.repo, self.env)
        helper.write_json(self.state / "metadata.json", metadata)
        helper.write_json(self.state / "runtime.json", report)
        helper.write_json(self.state / "exit.json", {"primaryExitCode": report["exitCode"] if primary is None else primary})

    def test_strict_boolean_profile_and_commit(self):
        self.assertFalse(helper.boolean("false"))
        self.assertTrue(helper.boolean("true"))
        for value in [None, True, False, "True", "FALSE", "1", " false", "true\n"]:
            with self.assertRaises(helper.InputError):
                helper.boolean(value)
        for field, value in [("SMOKE_PROFILE", "other"), ("SMOKE_COMMIT", "main"), ("SMOKE_COMMIT", "f" * 40)]:
            env = {**self.env, field: value}
            with self.assertRaises(helper.InputError):
                helper.prepare(self.repo, env)

    def test_paths_are_tracked_regular_contained_and_unchanged(self):
        for value in ["/absolute.json", "C:/absolute.json", "../outside.json", "releases/../../outside.json", "releases\\candidate.json", "releases/./candidate.json", "", "-x.json", "releases/candidate.json\n"]:
            with self.subTest(value=value), self.assertRaises(helper.InputError):
                helper.validate_path(self.repo, value, self.commit)
        untracked = self.repo / "untracked.json"
        untracked.write_bytes(b"{}")
        with self.assertRaises(helper.InputError):
            helper.validate_path(self.repo, "untracked.json", self.commit)
        (self.repo / self.path).write_bytes(b"changed")
        with self.assertRaises(helper.InputError):
            helper.validate_path(self.repo, self.path, self.commit)

    def test_symlink_input_is_rejected(self):
        link = self.repo / "link.json"
        try:
            link.symlink_to(self.repo / self.path)
        except OSError:
            self.skipTest("Symlink creation is unavailable")
        with self.assertRaises(helper.InputError):
            helper.validate_path(self.repo, "link.json", self.commit)

    def test_missing_inventory_objects_fail_before_runtime(self):
        self.candidate["inventory"]["revision"] = "f" * 40
        self.commit_candidate()
        called = []
        code = helper.execute(self.repo, self.state, self.env, runner=lambda *a, **k: called.append(a))
        self.assertEqual(code, 2)
        self.assertEqual(called, [])

    def test_filename_is_data_and_runner_receives_committed_snapshot(self):
        self.path = "releases/space $(touch forbidden);.json"
        self.commit_candidate()
        result = self.report()
        calls = []
        def runner(path, repo, profile, **kwargs):
            calls.append((path, kwargs))
            self.assertEqual(path.read_bytes(), helper.selected_file(self.repo, self.commit, self.path))
            self.assertNotEqual(path, self.repo / self.path)
            return result
        self.assertEqual(helper.execute(self.repo, self.state, self.env, runner), 0)
        self.assertTrue(calls[0][1]["execute"])
        self.assertTrue(calls[0][1]["inspection_only"])
        self.assertFalse((self.repo / "forbidden").exists())

    def test_primary_nonzero_codes_survive_report_and_cleanup_failures(self):
        for code in (1, 2, 3, 130):
            with self.subTest(code=code):
                result = self.report(code)
                self.assertEqual(helper.execute(self.repo, self.state, self.env, lambda *a, **k: result), code)
                with patch.object(helper, "cleanup", side_effect=helper.InputError("cleanup_failed")):
                    self.assertEqual(helper.finalize(self.state, self.output, self.summary), code)
                self.assertEqual(json.loads(self.output.read_bytes())["primaryExitCode"], code)

    def test_report_write_error_preserves_original_runtime_code(self):
        original = helper.write_json
        def write(path, value):
            if path.name == "runtime.json":
                raise OSError("Simulated report write failure")
            return original(path, value)
        for primary in (0, 3):
            result = self.report(primary)
            with patch.object(helper, "write_json", side_effect=write):
                code = helper.execute(self.repo, self.state, self.env, lambda *a, **k: result)
            self.assertEqual(code, primary if primary else 2)
            self.assertEqual(helper.json_read(self.state / "exit.json")["primaryExitCode"], primary)
            self.assertEqual(helper.finalize(self.state, self.output, self.summary), primary if primary else 2)

    def test_missing_malformed_and_inconsistent_report_fail_closed(self):
        helper.write_json(self.state / "exit.json", {"primaryExitCode": 0})
        for data in [None, b"{", b'{"exitCode":0,"exitCode":1}', b'{"elapsedSeconds":1e999}', b'{}']:
            with self.subTest(data=data):
                if data is not None:
                    (self.state / "runtime.json").write_bytes(data)
                self.assertEqual(helper.finalize(self.state, self.output, self.summary), 2)
                self.assertIn("execution/report failure", self.summary.read_text())
        result = self.report()
        self.write_state(result)
        result["candidate"]["sha256"] = "f" * 64
        helper.write_json(self.state / "runtime.json", result)
        self.assertEqual(helper.finalize(self.state, self.output, self.summary), 2)

    def test_success_cannot_hide_eligibility_or_cleanup_failure(self):
        result = self.report()
        self.write_state(result)
        self.assertEqual(helper.finalize(self.state, self.output, self.summary), 0)
        for mutate in [lambda r: r.update(inspectionOnly=False),
                       lambda r: r["cleanup"].update(outcome="failed"),
                       lambda r: r["services"].pop(),
                       lambda r: r["validation"]["checks"][-1]["diagnostics"][0].update(code="other_failure")]:
            changed = copy.deepcopy(result)
            mutate(changed)
            self.write_state(changed)
            self.assertEqual(helper.finalize(self.state, self.output, self.summary), 2)

    def test_summary_escapes_candidate_markup_and_artifact_drops_unknown_payload(self):
        result = self.report()
        result["candidate"]["releaseId"] = '<script>bad</script> | `text`\n::warning::bad'
        self.write_state(result)
        self.assertEqual(helper.finalize(self.state, self.output, self.summary), 0)
        text = self.summary.read_text()
        self.assertNotIn("<script>", text)
        self.assertIn("&lt;script&gt;", text)
        self.assertNotIn("\n::warning::", text)
        self.assertNotIn("unknown_private_payload", self.output.read_text())

    def test_cleanup_uses_persisted_identity_and_only_its_exact_label(self):
        _, metadata = helper.prepare(self.repo, self.env)
        (self.state / "compose.json").write_bytes(b'{"services":{}}')
        project = "cp-smoke-" + "a" * 32
        helper.save_project(self.state, metadata, project, self.state)
        with patch("container_platform.smoke.resources", side_effect=[{"containers": b"one"}, {}]) as query, patch("container_platform.smoke_transport.Docker") as factory:
            factory.return_value.compose.return_value = (0, b"")
            outcome = helper.cleanup(self.state, metadata)
        self.assertEqual(outcome["outcome"], "verified_absent")
        self.assertEqual(factory.call_args.args[0], project)
        self.assertEqual(query.call_count, 2)
        identity = json.loads((self.state / "project.json").read_bytes())
        identity["project"] = "another-project"
        helper.write_json(self.state / "project.json", identity)
        with patch("container_platform.smoke_transport.Docker") as factory, self.assertRaises(helper.InputError):
            helper.cleanup(self.state, metadata)
        factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
