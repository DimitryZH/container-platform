"""Offline fixtures use isolated Git repositories and synthetic artifact metadata."""

import copy
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from referencing import Registry
from referencing.exceptions import NoSuchResource

from container_platform.cli import main, render_text
from container_platform.validation import (
    Failure, deny_retrieval, git_read, load_schema, schema_validator, validate,
)


ROOT = Path(__file__).resolve().parents[1]
INVENTORY_PATH = "inventories/profile.json"
DEFAULT_CANDIDATE = object()


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        self.network = patch.object(socket, "socket", side_effect=AssertionError("Network is forbidden in tests"))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.git("init", "-q")
        self.inventory = {
            "inventoryVersion": 1, "application": "online-shop", "profile": "test-profile",
            "targetPlatforms": ["linux/amd64", "linux/arm64"],
            "components": [
                {"id": "frontend", "class": "first-party", "required": True,
                 "allowedRepositories": ["docker.io/dmitryzhuravlev/online-shop-frontend"],
                 "targetPlatforms": ["linux/amd64", "linux/arm64"]},
                {"id": "cache", "class": "third-party", "required": True,
                 "allowedRepositories": ["docker.io/example/cache"],
                 "targetPlatforms": ["linux/amd64", "linux/arm64"]},
            ],
        }
        self.revision = self.commit_inventory(self.inventory)
        self.candidate = {
            "schemaVersion": 1, "purpose": "candidate", "notice": "Synthetic offline test only.",
            "releaseId": "test-bundle", "application": {"name": "online-shop", "upstreamVersion": None},
            "inventory": {"revision": self.revision, "path": INVENTORY_PATH},
            "components": [],
        }
        for item in self.inventory["components"]:
            first = item["class"] == "first-party"
            self.candidate["components"].append({
                "id": item["id"], "class": item["class"],
                "image": {"repository": item["allowedRepositories"][0], "digest": "sha256:" + "a" * 64,
                          "kind": "index", "platforms": list(item["targetPlatforms"])},
                "traceability": {
                    "status": "recorded" if first else "upstream",
                    "source": {"repository": "https://example.invalid/source", "commit": "b" * 40} if first else None,
                    "build": {"url": "https://example.invalid/build/1"} if first else None,
                    "upstream": None if first else "https://example.invalid/releases/1", "reason": None,
                },
            })
        self.input = Path(self.temp.name) / "candidate.json"

    def git(self, *args):
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        return subprocess.check_output([
            "git", "-C", str(self.repo), "-c", "user.name=Fixture",
            "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
            "-c", "core.hooksPath=" + os.devnull, *args,
        ], env=env, stderr=subprocess.PIPE)

    def commit_bytes(self, data):
        p = self.repo / INVENTORY_PATH
        p.parent.mkdir(exist_ok=True)
        p.write_bytes(data)
        self.git("add", "--", INVENTORY_PATH)
        self.git("commit", "-qm", "test: record fixture", "--allow-empty")
        return self.git("rev-parse", "HEAD").decode().strip()

    def commit_inventory(self, inventory):
        return self.commit_bytes(json.dumps(inventory).encode())

    def run_candidate(self, candidate=DEFAULT_CANDIDATE):
        self.input.write_text(json.dumps(self.candidate if candidate is DEFAULT_CANDIDATE else candidate), encoding="utf-8")
        return validate(self.input, self.repo)

    def check(self, result, stage):
        return next(item for item in result["checks"] if item["id"] == stage)

    def codes(self, result):
        return [d["code"] for stage in result["checks"] for d in stage["diagnostics"]]

    def assert_failure(self, result, code, stage=None):
        self.assertEqual(result["exitCode"], 1)
        self.assertIn(code, self.codes(result))
        if stage:
            self.assertEqual(self.check(result, stage)["status"], "fail")

    def test_nonlegacy_passes_offline_without_provenance_claim(self):
        result = self.run_candidate()
        self.assertEqual(result["exitCode"], 0)
        self.assertEqual(result["result"], "offline_rules_passed")
        self.assertTrue(all(c["status"] == "pass" for c in result["checks"]))
        self.assertIn("not evaluated", result["scope"])

    def test_committed_baseline_has_twelve_legacy_diagnostics(self):
        result = validate(ROOT / "releases/baseline-2026-10-18.json", ROOT)
        self.assertEqual(result["exitCode"], 1)
        self.assertTrue(all(c["status"] == "pass" for c in result["checks"][:-1]))
        self.assertEqual(self.codes(result), ["legacy_ineligible"] * 12)

    def test_all_legacy_entries_are_reported(self):
        for c in self.candidate["components"]:
            c["traceability"].update(status="legacy", reason="Synthetic missing records.")
        self.assertEqual(self.codes(self.run_candidate()), ["legacy_ineligible"] * 2)

    def test_duplicate_keys_at_every_nesting_level(self):
        for data in [b'{"purpose":1,"purpose":2}', b'{"nested":{"value":1,"value":2}}']:
            with self.subTest(data=data):
                self.input.write_bytes(data)
                result = validate(self.input, self.repo)
                self.assert_failure(result, "duplicate_json_key", "candidate.parse")
                self.assertTrue(all(s["status"] == "skipped" for s in result["checks"][1:]))

    def test_invalid_json_and_encoding_and_numbers(self):
        for data, code in [
            (b'{"secret":"do-not-echo",', "malformed_json"),
            (b'\xff', "invalid_utf8"), (b'{"x":NaN}', "non_json_number"),
            (b'{"x":Infinity}', "non_json_number"), (b'{"x":-Infinity}', "non_json_number"),
            (b'{"x":1e9999}', "non_json_number"),
        ]:
            with self.subTest(code=code, data=data):
                self.input.write_bytes(data)
                result = validate(self.input, self.repo)
                self.assert_failure(result, code)
                self.assertNotIn("do-not-echo", json.dumps(result))

    def test_candidate_root_types(self):
        for value in [None, [], "secret", True, 1]:
            with self.subTest(value=value):
                self.assert_failure(self.run_candidate(value), "schema_violation", "candidate.schema")

    def test_trailing_lf_in_all_reference_fields(self):
        paths = [
            ["releaseId"], ["inventory", "revision"], ["inventory", "path"],
            ["components", 0, "id"], ["components", 0, "image", "digest"],
            ["components", 0, "image", "repository"], ["components", 0, "image", "platforms", 0],
            ["components", 0, "traceability", "source", "commit"],
            ["components", 0, "traceability", "source", "repository"],
            ["components", 0, "traceability", "build", "url"],
            ["components", 1, "traceability", "upstream"],
        ]
        for path in paths:
            with self.subTest(path=path):
                candidate = copy.deepcopy(self.candidate)
                node = candidate
                for key in path[:-1]:
                    node = node[key]
                node[path[-1]] += "\n"
                self.assert_failure(self.run_candidate(candidate), "schema_violation")

    def test_metadata_requirements_and_no_identifier_normalization(self):
        for field in ["source", "build"]:
            with self.subTest(field=field):
                c = copy.deepcopy(self.candidate)
                c["components"][0]["traceability"][field] = None
                self.assert_failure(self.run_candidate(c), "schema_violation")
        c = copy.deepcopy(self.candidate)
        c["components"][1]["traceability"]["upstream"] = None
        self.assert_failure(self.run_candidate(c), "schema_violation")
        c = copy.deepcopy(self.candidate)
        c["components"][0]["image"]["digest"] = "SHA256:" + "A" * 64
        self.assert_failure(self.run_candidate(c), "schema_violation")

    def test_duplicate_unknown_missing_components(self):
        c = copy.deepcopy(self.candidate)
        duplicate = copy.deepcopy(c["components"][0])
        duplicate["image"]["digest"] = "sha256:" + "c" * 64
        c["components"].append(duplicate)
        self.assert_failure(self.run_candidate(c), "component_duplicate")
        c["components"][-1]["id"] = "unknown"
        self.assert_failure(self.run_candidate(c), "component_unknown")
        c["components"] = c["components"][:1]
        self.assert_failure(self.run_candidate(c), "component_missing")
        c["components"].append(copy.deepcopy(c["components"][0]))
        self.assert_failure(self.run_candidate(c), "schema_violation")

    def test_repository_class_and_platform_policy(self):
        for field, value, code in [
            ("repository", "docker.io/dmitryzhuravlev/online-shop-other", "repository_disallowed"),
            ("platforms", ["linux/ppc64le"], "platforms_mismatch"),
            ("platforms", ["linux/amd64"], "platforms_mismatch"),
        ]:
            with self.subTest(field=field, value=value):
                c = copy.deepcopy(self.candidate)
                c["components"][0]["image"][field] = value
                result = self.run_candidate(c)
                self.assert_failure(result, code)
                self.assertEqual(self.check(result, "candidate.traceability")["status"], "skipped")
        c = copy.deepcopy(self.candidate)
        c["components"][0]["class"] = "third-party"
        c["components"][0]["traceability"].update(status="upstream", upstream="https://example.invalid/releases/1")
        self.assert_failure(self.run_candidate(c), "component_class_mismatch")

    def test_platform_order_is_irrelevant(self):
        for c in self.candidate["components"]:
            c["image"]["platforms"].reverse()
        self.assertEqual(self.run_candidate()["exitCode"], 0)

    def test_inventory_structure_and_supported_version(self):
        cases = [
            (None, "inventory_type_invalid"),
            ({**self.inventory, "inventoryVersion": 2}, "inventory_version_unsupported"),
            ({**self.inventory, "inventoryVersion": True}, "inventory_version_unsupported"),
            ({**self.inventory, "profile": ""}, "inventory_profile_invalid"),
            ({**self.inventory, "application": "other"}, "inventory_application_invalid"),
            ({**self.inventory, "components": []}, "inventory_components_invalid"),
            ({**self.inventory, "components": [None]}, "inventory_component_invalid"),
            ({**self.inventory, "targetPlatforms": ["linux/amd64", "linux/amd64"]}, "inventory_platforms_invalid"),
        ]
        for inventory, code in cases:
            with self.subTest(code=code, inventory=inventory):
                self.candidate["inventory"]["revision"] = self.commit_inventory(inventory)
                self.assert_failure(self.run_candidate(), code, "inventory.policy")

    def test_inventory_component_policy_fields(self):
        for field, value, code in [
            ("id", "", "inventory_id_invalid"), ("id", "frontend\n", "inventory_id_invalid"),
            ("class", "other", "inventory_class_invalid"),
            ("required", False, "inventory_required_invalid"), ("required", 1, "inventory_required_invalid"),
            ("allowedRepositories", [], "inventory_repositories_invalid"),
            ("allowedRepositories", ["docker.io/example/image\n"], "inventory_repositories_invalid"),
            ("allowedRepositories", ["docker.io/example/image"], "inventory_first_party_namespace"),
            ("targetPlatforms", ["linux/amd64\n"], "inventory_platforms_invalid"),
            ("targetPlatforms", ["linux/amd64"], "inventory_platforms_inconsistent"),
        ]:
            with self.subTest(field=field, value=value):
                inv = copy.deepcopy(self.inventory)
                inv["components"][0][field] = value
                self.candidate["inventory"]["revision"] = self.commit_inventory(inv)
                self.assert_failure(self.run_candidate(), code)
        inv = copy.deepcopy(self.inventory)
        inv["components"].append(copy.deepcopy(inv["components"][0]))
        self.candidate["inventory"]["revision"] = self.commit_inventory(inv)
        self.assert_failure(self.run_candidate(), "inventory_id_duplicate")

    def test_inventory_required_fields_and_optional_top_platforms(self):
        for field in ["id", "class", "required", "allowedRepositories", "targetPlatforms"]:
            with self.subTest(field=field):
                inv = copy.deepcopy(self.inventory)
                del inv["components"][0][field]
                self.candidate["inventory"]["revision"] = self.commit_inventory(inv)
                self.assertEqual(self.run_candidate()["exitCode"], 1)
        inv = copy.deepcopy(self.inventory)
        del inv["targetPlatforms"]
        self.candidate["inventory"]["revision"] = self.commit_inventory(inv)
        self.assertEqual(self.run_candidate()["exitCode"], 0)

    def test_inventory_strict_json(self):
        for raw, code in [
            (b'{"x":1,"x":2}', "duplicate_json_key"), (b'{"x":{"y":1,"y":2}}', "duplicate_json_key"),
            (b'{"x":NaN}', "non_json_number"), (b'{"x":Infinity}', "non_json_number"),
            (b'{"x":1e9999}', "non_json_number"), (b'\xff', "invalid_utf8"), (b'{', "malformed_json"),
        ]:
            with self.subTest(code=code, raw=raw):
                self.candidate["inventory"]["revision"] = self.commit_bytes(raw)
                self.assert_failure(self.run_candidate(), code, "inventory.parse")

    def test_missing_commit_and_path(self):
        c = copy.deepcopy(self.candidate)
        c["inventory"]["revision"] = "f" * 40
        self.assert_failure(self.run_candidate(c), "inventory_commit_missing")
        c = copy.deepcopy(self.candidate)
        c["inventory"]["path"] = "missing.json"
        self.assert_failure(self.run_candidate(c), "inventory_path_missing")

    def test_blob_revision_rejected(self):
        self.candidate["inventory"]["revision"] = self.git("rev-parse", "HEAD:" + INVENTORY_PATH).decode().strip()
        self.assert_failure(self.run_candidate(), "inventory_revision_not_commit")

    def test_shallow_clone_gap(self):
        self.commit_inventory(self.inventory)
        clone = Path(self.temp.name) / "shallow"
        self.git("clone", "--depth=1", self.repo.as_uri(), str(clone))
        self.input.write_text(json.dumps(self.candidate), encoding="utf-8")
        result = validate(self.input, clone)
        self.assert_failure(result, "inventory_commit_missing")
        self.assertIn("shallow/partial", self.check(result, "inventory.resolve")["diagnostics"][0]["message"])

    def test_working_inventory_and_head_do_not_replace_pinned_inventory(self):
        before = self.run_candidate()
        self.commit_inventory({"inventoryVersion": 999})
        (self.repo / INVENTORY_PATH).write_bytes(b'not json')
        self.assertEqual(before, self.run_candidate())

    def test_nonfile_inventory_objects(self):
        for mode in ["120000", "160000"]:
            with self.subTest(mode=mode):
                oid = (self.git("rev-parse", "HEAD:" + INVENTORY_PATH) if mode == "120000"
                       else self.git("rev-parse", "HEAD")).decode().strip()
                self.git("update-index", "--add", "--cacheinfo", f"{mode},{oid},{INVENTORY_PATH}")
                self.git("commit", "-qm", "test: nonfile inventory")
                self.candidate["inventory"]["revision"] = self.git("rev-parse", "HEAD").decode().strip()
                self.assert_failure(self.run_candidate(), "inventory_not_regular_file")
        self.candidate["inventory"]["path"] = "inventories"
        # The contract disallows this path before Git; a directory ending in .json is tested below.
        self.assert_failure(self.run_candidate(), "schema_violation")
        p = self.repo / "directory.json"
        p.mkdir()
        (p / "data").write_text("fixture")
        self.git("add", "directory.json")
        self.git("commit", "-qm", "test: directory inventory")
        self.candidate["inventory"].update(revision=self.git("rev-parse", "HEAD").decode().strip(), path="directory.json")
        self.assert_failure(self.run_candidate(), "inventory_not_regular_file")

    def test_no_git_mutation_or_hooks_and_environment_isolation(self):
        before = {p.relative_to(self.repo): p.read_bytes() for p in self.repo.rglob("*") if p.is_file()}
        # A repository-misdirecting environment variable must be ignored.
        with patch.dict(os.environ, {"GIT_DIR": str(self.repo / "missing"), "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "alias.cat-file", "GIT_CONFIG_VALUE_0": "!exit 99"}):
            self.assertEqual(self.run_candidate()["exitCode"], 0)
        after = {p.relative_to(self.repo): p.read_bytes() for p in self.repo.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_git_subprocess_boundary(self):
        with patch("container_platform.validation.subprocess.run") as run:
            git_read(self.repo, "cat-file", "-t", self.revision)
            args, kwargs = run.call_args
            self.assertIs(kwargs["shell"], False)
            self.assertIn("--no-replace-objects", args[0])
            self.assertEqual(kwargs["env"]["GIT_ALLOW_PROTOCOL"], "")
            self.assertEqual(kwargs["env"]["GIT_NO_LAZY_FETCH"], "1")

    def test_remote_schema_references_never_retrieve(self):
        for keyword in ["$ref", "$dynamicRef"]:
            schema = load_schema()
            schema["properties"]["notice"] = {keyword: "https://user:do-not-echo@example.invalid/schema"}
            with self.subTest(keyword=keyword), patch("container_platform.validation.load_schema", return_value=schema):
                result = self.run_candidate()
                self.assertEqual(result["exitCode"], 2)
                self.assertIn("schema_reference_unsupported", self.codes(result))
                self.assertNotIn("do-not-echo", json.dumps(result))
        # The explicit retrieval boundary also denies requests independently of preflight.
        with self.assertRaises(NoSuchResource):
            Registry(retrieve=deny_retrieval).get_or_retrieve("https://example.invalid/schema")
        validator = schema_validator({"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": "https://example.invalid/local", "type": "object"})
        validator.validate({})

    def test_schema_execution_errors_are_sanitized(self):
        for schema, code in [
            ({"type": "not-a-type"}, "schema_invalid"),
            ({"$ref": "#/$defs/missing"}, "schema_reference_unavailable"),
        ]:
            with self.subTest(code=code), patch("container_platform.validation.load_schema", return_value=schema):
                result = self.run_candidate()
                self.assertEqual(result["exitCode"], 2)
                self.assertIn(code, self.codes(result))

    def test_example_rejection(self):
        result = validate(ROOT / "examples/release-v1.example.json", ROOT)
        self.assert_failure(result, "example_not_candidate", "candidate.purpose")
        self.assertEqual(self.check(result, "inventory.resolve")["status"], "skipped")

    def test_diagnostics_do_not_echo_untrusted_fields(self):
        self.candidate["components"][0]["traceability"]["build"]["url"] = "https://user:do-not-echo@example.invalid/\n"
        result = self.run_candidate()
        self.assertNotIn("do-not-echo", json.dumps(result))
        self.assertNotIn("do-not-echo", render_text(result))

    def test_cli_outputs_exit_codes_and_determinism(self):
        for legacy in [False, True]:
            if legacy:
                self.candidate["components"][0]["traceability"].update(status="legacy", reason="Synthetic gap.")
            expected = self.run_candidate()
            for fmt in ["json", "text"]:
                out = io.StringIO()
                with redirect_stdout(out):
                    exit_code = main([str(self.input), "--repository", str(self.repo), "--format", fmt])
                self.assertEqual(exit_code, expected["exitCode"])
                if fmt == "json":
                    self.assertEqual(json.loads(out.getvalue()), expected)
                else:
                    self.assertEqual(out.getvalue().rstrip(), render_text(expected))
            self.assertEqual(expected, self.run_candidate())

    def test_usage_and_execution_errors(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(main(["--format=json", "--invalid=do-not-echo"]), 2)
        result = json.loads(out.getvalue())
        self.assertEqual(result["diagnostics"][0]["code"], "usage_error")
        self.assertTrue(all(c["status"] == "skipped" for c in result["checks"]))
        self.assertNotIn("do-not-echo", out.getvalue())
        result = validate(self.input, self.repo)
        self.assertEqual(result["exitCode"], 2)
        self.assertIn("candidate_unreadable", self.codes(result))
        self.run_candidate()
        result = validate(self.input, self.repo / "missing")
        self.assertEqual(result["exitCode"], 2)
        self.assertIn("repository_unavailable", self.codes(result))
        with patch("container_platform.validation.subprocess.run", side_effect=FileNotFoundError):
            self.assertEqual(validate(self.input, self.repo)["exitCode"], 2)

    def test_installed_module_outside_repository_cwd(self):
        self.run_candidate()
        result = subprocess.run([
            sys.executable, "-m", "container_platform", str(self.input),
            "--repository", str(self.repo), "--format", "json",
        ], cwd=self.temp.name, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        report = json.loads(result.stdout)
        self.assertEqual(report["result"], "offline_rules_passed")
        self.assertNotIn(str(self.repo), result.stdout.decode())


if __name__ == "__main__":
    unittest.main()
