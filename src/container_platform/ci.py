"""Git-based candidate discovery and release-check orchestration."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from .cli import Parser
from .validation import Failure, git_read, strict_json


def candidate_path(path):
    return (path.startswith("releases/") and path.count("/") == 1
            and path.endswith(".json") and not path.endswith(".observations.json"))


def read_git(repo, *args):
    result = git_read(repo, *args)
    if result.returncode:
        raise Failure("comparison_objects_unavailable", "Required local Git objects are unavailable; no fetch or fallback was attempted.", execution=True)
    return result.stdout


def commit(repo, revision):
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise Failure("comparison_revision_invalid", "Comparison requires full commit SHAs; initial/deleted push revisions are unsupported.", execution=True)
    if read_git(repo, "cat-file", "-t", revision) != b"commit\n":
        raise Failure("comparison_revision_invalid", "Comparison revision must name a commit.", execution=True)


def tree(repo, revision):
    entries = {}
    for row in read_git(repo, "ls-tree", "-r", "-z", "--full-tree", revision).split(b"\0"):
        if not row:
            continue
        meta, raw_path = row.split(b"\t", 1)
        try:
            path = raw_path.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise Failure("comparison_filename_invalid", "Git filenames must be valid UTF-8.") from None
        if candidate_path(path):
            mode, kind, oid = meta.split()
            entries[path] = (mode, kind, oid.decode("ascii"))
    return entries


def changes(repo, base, head):
    fields = read_git(repo, "diff", "--no-ext-diff", "--no-textconv", "--name-status", "-z", "--find-renames", base, head, "--").split(b"\0")
    result = []
    index = 0
    while index < len(fields) and fields[index]:
        status = fields[index].decode("ascii")
        count = 2 if status[0] in ("R", "C") else 1
        try:
            paths = [p.decode("utf-8", errors="strict") for p in fields[index + 1:index + count + 1]]
        except UnicodeDecodeError:
            raise Failure("comparison_filename_invalid", "Git filenames must be valid UTF-8.") from None
        result.append((status[0], paths))
        index += count + 1
    return result


def discovery(repo, base, head, pull_request=False):
    commit(repo, base)
    commit(repo, head)
    comparison = read_git(repo, "merge-base", base, head).decode("ascii").strip() if pull_request else base
    old, current, base_tree = tree(repo, comparison), tree(repo, head), tree(repo, base)
    new = set()
    errors = []
    for status, paths in changes(repo, comparison, head):
        source, target = paths[0], paths[-1]
        if status == "A" or (status in ("R", "C") and not candidate_path(source)):
            if candidate_path(target):
                new.add(target)
        if candidate_path(source) and status in ("M", "T", "D", "R"):
            errors.append("merged_release_immutable")
        if status == "R" and candidate_path(source) and candidate_path(target):
            # A rename is forbidden, not an eligibility test for the renamed artifact.
            new.discard(target)
    for path in sorted(new):
        if path in base_tree:
            errors.append("release_path_already_merged")
        if current[path][0] not in (b"100644", b"100755") or current[path][1] != b"blob":
            errors.append("candidate_not_regular_file")
    # PR merge policy includes releases already added to base since divergence.
    resultant = {**base_tree, **current} if pull_request else current
    return sorted(new), resultant, sorted(set(errors))


def identities(repo, new, entries):
    ids = {}
    errors = []
    for path in sorted(entries):
        mode, kind, oid = entries[path]
        if mode not in (b"100644", b"100755") or kind != b"blob":
            errors.append("release_identity_unavailable")
            continue
        try:
            data = strict_json(read_git(repo, "cat-file", "blob", oid))
            identity = data.get("releaseId") if isinstance(data, dict) else None
            if not isinstance(identity, str) or not identity:
                raise Failure("release_identity_unavailable", "Cannot establish release identity.")
        except Failure as failure:
            if failure.execution:
                raise
            if path not in new:
                errors.append("release_identity_unavailable")
            # New malformed candidates remain selected and fail through the CLI.
            continue
        ids.setdefault(identity, []).append(path)
    if any(len(paths) > 1 and any(p in new for p in paths) for paths in ids.values()):
        errors.append("release_id_duplicate")
    return sorted(set(errors))


def run_candidate(repo, data):
    with tempfile.TemporaryDirectory(prefix="release-check-") as temporary:
        path = Path(temporary) / "candidate.json"
        path.write_bytes(data)
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "container_platform", str(path), "--repository", str(repo), "--registry", "--format", "json"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, shell=False, timeout=240,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise Failure("candidate_execution_failed", "Release CLI could not complete.", execution=True) from None
    try:
        report = strict_json(completed.stdout)
    except Failure:
        raise Failure("candidate_output_invalid", "Release CLI did not produce a valid result document.", execution=True) from None
    if (completed.returncode not in (0, 1, 2) or not isinstance(report, dict)
            or report.get("exitCode") != completed.returncode or report.get("outputSchemaVersion") != 2):
        raise Failure("candidate_output_invalid", "Release CLI exit/result contract is inconsistent.", execution=True)
    return completed.returncode, report


def evaluate(repo, base, head, *, pull_request=False, runner=run_candidate):
    result = {"ciOutputVersion": 1, "result": "execution_error", "exitCode": 2,
              "newCandidateCount": 0, "discovery": "not_evaluated", "diagnostics": [], "candidates": []}
    try:
        new, entries, errors = discovery(repo, base, head, pull_request)
        result["newCandidateCount"] = len(new)
        if new and not errors:
            errors.extend(identities(repo, new, entries))
        result["discovery"] = "fail" if errors else "pass"
        if errors:
            result.update(result="validation_failed", exitCode=1, diagnostics=errors)
            return result
        if not new:
            result.update(result="no_candidate_changes", exitCode=0)
            return result
        overall = 0
        for index, path in enumerate(new):
            try:
                data = read_git(repo, "cat-file", "blob", entries[path][2])
                code, report = runner(repo, data)
                if code not in (0, 1, 2):
                    raise Failure("candidate_execution_failed", "Release CLI returned an unsupported exit code.", execution=True)
                result["candidates"].append({"candidateIndex": index, "exitCode": code, "report": report})
            except Failure as failure:
                code = 2 if failure.execution else 1
                result["candidates"].append({"candidateIndex": index, "exitCode": code, "diagnostic": failure.code})
            overall = max(overall, code)
        result.update(exitCode=overall, result="execution_error" if overall == 2 else "validation_failed" if overall else "release_checks_passed")
    except Failure as failure:
        result.update(result="execution_error" if failure.execution else "validation_failed",
                      exitCode=2 if failure.execution else 1, diagnostics=[failure.code])
    return result


def comparison_from_event(name, payload):
    try:
        if name == "pull_request":
            return payload["pull_request"]["base"]["sha"], payload["pull_request"]["head"]["sha"], True
        if name == "push":
            return payload["before"], payload["after"], False
    except (KeyError, TypeError):
        raise Failure("event_invalid", "Event comparison fields are missing.", execution=True) from None
    raise Failure("event_unsupported", "Only pull_request and push events are supported.", execution=True)


def write_summary(result, destination):
    Path(destination).write_text(
        "### Release check\n\n"
        f"New candidates: {result['newCandidateCount']}\n\n"
        f"Discovery/immutability: {result['discovery']}. Result: {result['result']}.\n\n"
        "Scope: local policy and opt-in registry metadata; no approval or deployment authorization.\n",
        encoding="utf-8",
    )


def main(argv=None):
    parser = Parser(description="Compare immutable release files and validate new candidates.", allow_abbrev=False)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--base")
    parser.add_argument("--head")
    parser.add_argument("--pull-request", action="store_true", help="Use merge-base cumulative comparison")
    parser.add_argument("--github-event", action="store_true", help="Read GITHUB_EVENT_NAME/PATH")
    try:
        args = parser.parse_args(argv)
        if args.github_event:
            if args.base or args.head or args.pull_request:
                raise Failure("usage_error", "Choose event mode or explicit revisions.", execution=True)
            payload = strict_json(Path(os.environ["GITHUB_EVENT_PATH"]).read_bytes())
            base, head, is_pr = comparison_from_event(os.environ.get("GITHUB_EVENT_NAME"), payload)
        else:
            base, head, is_pr = args.base, args.head, args.pull_request
        result = evaluate(args.repository, base, head, pull_request=is_pr)
    except (Failure, OSError, KeyError) as failure:
        result = {"ciOutputVersion": 1, "result": "execution_error", "exitCode": 2,
                  "newCandidateCount": 0, "discovery": "not_evaluated", "diagnostics": [failure.code if isinstance(failure, Failure) else "event_unavailable"], "candidates": []}
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        try:
            write_summary(result, os.environ["GITHUB_STEP_SUMMARY"])
        except OSError:
            result.update(result="execution_error", exitCode=2)
            result["diagnostics"].append("summary_unavailable")
    print(json.dumps(result, indent=2))
    return result["exitCode"]


if __name__ == "__main__":
    raise SystemExit(main())
