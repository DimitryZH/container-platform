"""Release orchestration tests use local Git objects and no real registry."""

import copy
import io
import json
import os
from pathlib import Path
from contextlib import redirect_stdout
import unittest
from unittest.mock import patch

import test_validation as fixtures
from container_platform import ci


class OrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ValidationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.repo = self.fixture.repo
        self.base = self.fixture.revision
        self.counter = 0

    def record(self, path, data):
        p = self.repo / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data if isinstance(data, bytes) else json.dumps(data).encode())

    def commit(self):
        self.fixture.git("add", "-A")
        self.fixture.git("commit", "-qm", "test: change release tree", "--allow-empty")
        return self.fixture.git("rev-parse", "HEAD").decode().strip()

    def candidate(self, identity="test-release"):
        c = copy.deepcopy(self.fixture.candidate)
        c["releaseId"] = identity
        return c

    def evaluate(self, head, runner=None, base=None, pull_request=False):
        return ci.evaluate(self.repo, base or self.base, head, pull_request=pull_request,
                           runner=runner or (lambda repo, data: (0, {"exitCode": 0})))

    def test_cumulative_additions_and_edits_within_same_pr(self):
        self.record("releases/one.json", self.candidate("one"))
        self.commit()
        self.record("releases/one.json", self.candidate("one-edited"))
        self.record("releases/two.json", self.candidate("two"))
        result = self.evaluate(self.commit(), pull_request=True)
        self.assertEqual(result["exitCode"], 0)
        self.assertEqual(result["newCandidateCount"], 2)

    def test_existing_modification_deletion_rename_and_excluded_moves(self):
        for action in ["modify", "delete", "rename", "observation", "example"]:
            with self.subTest(action=action):
                self.fixture.git("reset", "--hard", self.base)
                self.record("releases/old.json", self.candidate())
                merged = self.commit()
                old = self.repo / "releases/old.json"
                if action == "modify":
                    self.record("releases/old.json", self.candidate("different"))
                elif action == "delete":
                    old.unlink()
                else:
                    target = {"rename": "releases/new.json", "observation": "releases/old.observations.json", "example": "examples/old.json"}[action]
                    (self.repo / target).parent.mkdir(parents=True, exist_ok=True)
                    old.rename(self.repo / target)
                result = self.evaluate(self.commit(), base=merged)
                self.assertEqual(result["exitCode"], 1)
                self.assertIn("merged_release_immutable", result["diagnostics"])
                self.assertEqual(result["candidates"], [])

    def test_excluded_paths_and_no_changes(self):
        for path in ["README.md", "examples/sample.json", "releases/baseline.observations.json", "releases/nested/example.json"]:
            self.record(path, b'{not candidate JSON')
        result = self.evaluate(self.commit())
        self.assertEqual(result["result"], "no_candidate_changes")
        self.assertEqual(result["exitCode"], 0)

    def test_move_excluded_to_candidate_is_new_and_malformed_is_not_hidden(self):
        self.record("releases/old.observations.json", b'{not JSON')
        base = self.commit()
        (self.repo / "releases/old.observations.json").rename(self.repo / "releases/new.json")
        called = []
        def runner(repo, data):
            called.append(data)
            return 1, {"exitCode": 1}
        result = self.evaluate(self.commit(), base=base, runner=runner)
        self.assertEqual(result["newCandidateCount"], 1)
        self.assertEqual(called, [b'{not JSON'])
        self.assertEqual(result["exitCode"], 1)

    def test_duplicate_ids_against_historical_and_new_tree(self):
        self.record("releases/old.json", self.candidate("same"))
        base = self.commit()
        self.record("releases/new.json", self.candidate("same"))
        result = self.evaluate(self.commit(), base=base)
        self.assertIn("release_id_duplicate", result["diagnostics"])
        self.fixture.git("reset", "--hard", self.base)
        self.record("releases/a.json", self.candidate("same"))
        self.record("releases/b.json", self.candidate("same"))
        self.assertIn("release_id_duplicate", self.evaluate(self.commit())["diagnostics"])

    def test_base_advance_is_not_a_pr_deletion_and_ids_still_checked(self):
        self.record("releases/feature.json", self.candidate("same"))
        feature = self.commit()
        self.fixture.git("checkout", "--detach", self.base)
        self.record("releases/main.json", self.candidate("same"))
        main = self.commit()
        result = self.evaluate(feature, base=main, pull_request=True)
        self.assertNotIn("merged_release_immutable", result["diagnostics"])
        self.assertIn("release_id_duplicate", result["diagnostics"])

    def test_no_registry_for_policy_failure_and_nonzero_codes_preserved(self):
        self.record("releases/a.json", self.candidate("a"))
        self.record("releases/b.json", self.candidate("b"))
        head = self.commit()
        for code in (1, 2):
            result = self.evaluate(head, runner=lambda repo, data: (code, {"exitCode": code}))
            self.assertEqual(result["exitCode"], code)
            self.assertEqual(len(result["candidates"]), 2)
        self.record("releases/old.json", self.candidate("a"))
        head = self.commit()
        def forbidden(repo, data):
            raise AssertionError("Must not validate duplicate IDs")
        result = self.evaluate(head, runner=forbidden)
        self.assertEqual(result["exitCode"], 1)
        self.assertEqual(result["candidates"], [])

    def test_missing_comparison_objects_and_invalid_events(self):
        for base, head in [("f" * 40, self.base), (self.base, "f" * 40), ("0" * 40, self.base), ("main", self.base)]:
            result = ci.evaluate(self.repo, base, head)
            self.assertEqual(result["exitCode"], 2)
            self.assertEqual(result["discovery"], "not_evaluated")
        for name, payload in [("pull_request_target", {}), ("workflow_dispatch", {}), ("push", {}), ("pull_request", [])]:
            with self.assertRaises(ci.Failure):
                ci.comparison_from_event(name, payload)

    def test_precise_event_comparison_selection(self):
        self.assertEqual(ci.comparison_from_event("push", {"before": "a" * 40, "after": "b" * 40}), ("a" * 40, "b" * 40, False))
        self.assertEqual(ci.comparison_from_event("pull_request", {"pull_request": {"base": {"sha": "a" * 40}, "head": {"sha": "b" * 40}}}), ("a" * 40, "b" * 40, True))

    def test_safe_filenames_and_snapshot_not_worktree(self):
        path = 'releases/space ; $(no-command)' + ('' if os.name == 'nt' else '\n') + 'name.json'
        self.record(path, self.candidate())
        head = self.commit()
        self.record(path, b'{bad working tree')
        received = []
        def runner(repo, data):
            received.append(json.loads(data)["releaseId"])
            return 0, {"exitCode": 0}
        result = self.evaluate(head, runner=runner)
        self.assertEqual(result["exitCode"], 0)
        self.assertEqual(received, ["test-release"])
        self.assertNotIn("no-command", json.dumps(result))

    def test_real_cli_nonzero_and_diagnostics_without_network(self):
        self.record("releases/malformed.json", b'{')
        result = ci.evaluate(self.repo, self.base, self.commit())
        self.assertEqual(result["exitCode"], 1)
        report = result["candidates"][0]["report"]
        self.assertEqual(report["checks"][0]["status"], "fail")
        self.assertEqual(report["checks"][-1]["status"], "skipped")

    def test_github_summary_no_changes_is_explicit_and_small(self):
        event = Path(self.fixture.temp.name) / "event.json"
        summary = Path(self.fixture.temp.name) / "summary.md"
        event.write_text(json.dumps({"before": self.base, "after": self.base}))
        with patch.dict(os.environ, {"GITHUB_EVENT_NAME": "push", "GITHUB_EVENT_PATH": str(event), "GITHUB_STEP_SUMMARY": str(summary)}):
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(ci.main(["--repository", str(self.repo), "--github-event"]), 0)
            self.assertEqual(json.loads(out.getvalue())["result"], "no_candidate_changes")
        self.assertIn("no_candidate_changes", summary.read_text())
        self.assertLess(len(summary.read_text()), 400)


if __name__ == "__main__":
    unittest.main()
