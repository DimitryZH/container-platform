"""Manual workflow input binding, evidence and exact-project recovery.

Runtime orchestration is supplied exclusively by the installed smoke runner.
Only the final sanitized report is uploaded; state remains private to the job.
"""

import hashlib
import html
from importlib.resources import files
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import signal
import stat
import sys
import time


PROFILE = "aml-online-shop-compose-v1"
PROJECT = re.compile(r"cp-smoke-[0-9a-f]{32}", re.ASCII)


class InputError(Exception):
    pass


def boolean(value):
    if value not in ("true", "false"):
        raise InputError("inspection_boolean_invalid")
    return value == "true"


def json_read(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise InputError("report_invalid")
            result[key] = value
        return result
    def constant(_):
        raise InputError("report_invalid")
    def finite(value):
        result = float(value)
        if not math.isfinite(result):
            raise InputError("report_invalid")
        return result
    try:
        with path.open("rb") as stream:
            data = stream.read(2 * 1024 * 1024 + 1)
        if len(data) > 2 * 1024 * 1024:
            raise InputError("report_invalid")
        return json.loads(data, object_pairs_hook=pairs,
                          parse_constant=constant, parse_float=finite)
    except (OSError, ValueError, UnicodeError, RecursionError):
        raise InputError("report_missing_or_invalid") from None


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def git(repo, *args):
    from container_platform.validation import Failure, git_read
    try:
        result = git_read(repo, *args)
    except Failure:
        raise InputError("selected_git_objects_unavailable") from None
    if result.returncode:
        raise InputError("selected_git_objects_unavailable")
    return result.stdout


def selected_file(repo, commit, path):
    records = git(repo, "ls-tree", "-z", commit, "--", path).split(b"\0")
    if len(records) != 2 or not records[0]:
        raise InputError("candidate_not_tracked_regular_file")
    meta, name = records[0].split(b"\t", 1)
    mode, kind, oid = meta.split()
    if mode not in (b"100644", b"100755") or kind != b"blob" or name != path.encode("utf-8"):
        raise InputError("candidate_not_tracked_regular_file")
    return git(repo, "cat-file", "blob", oid.decode("ascii"))


def validate_path(repo, value, commit):
    if (not isinstance(value, str) or not value or "\\" in value or ":" in value
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise InputError("candidate_path_invalid")
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts or relative.as_posix() != value or value.startswith("-"):
        raise InputError("candidate_path_invalid")
    current = repo
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise InputError("candidate_symlink_disallowed")
    if not current.is_file() or not stat.S_ISREG(current.stat().st_mode) or not current.resolve().is_relative_to(repo.resolve()):
        raise InputError("candidate_path_invalid")
    with current.open("rb") as stream:
        data = stream.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024 or data != selected_file(repo, commit, value):
        raise InputError("candidate_differs_from_selected_commit")
    return current, data


def verify_install(repo, commit):
    # Check every shipped module and both resources, not only the CLI wrapper.
    for source in (repo / "src/container_platform").rglob("*.py"):
        expected = selected_file(repo, commit, source.relative_to(repo).as_posix())
        installed = files("container_platform").joinpath(*source.relative_to(repo / "src/container_platform").parts)
        if installed.read_bytes().replace(b"\r\n", b"\n") != expected:
            raise InputError("installed_code_mismatch")
    for package, name, path in [
        ("container_platform.schemas", "release-v1.schema.json", "schemas/release-v1.schema.json"),
        ("container_platform.profiles", PROFILE + ".json", "src/container_platform/profiles/" + PROFILE + ".json"),
    ]:
        if files(package).joinpath(name).read_bytes().replace(b"\r\n", b"\n") != selected_file(repo, commit, path):
            raise InputError("installed_resource_mismatch")


def prepare(repo, env):
    commit = env.get("SMOKE_COMMIT", "")
    if not re.fullmatch(r"[0-9a-f]{40}", commit) or git(repo, "rev-parse", "HEAD").decode().strip() != commit:
        raise InputError("workflow_commit_mismatch")
    if env.get("SMOKE_PROFILE") != PROFILE:
        raise InputError("profile_unsupported")
    inspection = boolean(env.get("SMOKE_INSPECTION_ONLY"))
    path, data = validate_path(repo, env.get("SMOKE_CANDIDATE"), commit)
    verify_install(repo, commit)
    from container_platform.validation import strict_json, resolve_inventory, Failure
    try:
        candidate = strict_json(data)
        # Full schema/policy/eligibility stays in the runner; establish local objects now.
        if not isinstance(candidate, dict) or not isinstance(candidate.get("inventory"), dict):
            raise InputError("candidate_inventory_invalid")
        resolve_inventory(repo, candidate["inventory"])
    except (Failure, KeyError, TypeError):
        raise InputError("candidate_inventory_unavailable") from None
    return path, {"testedCommit": commit, "candidatePath": path.relative_to(repo).as_posix(),
                  "candidateSha256": hashlib.sha256(data).hexdigest(), "profile": PROFILE,
                  "inspectionOnly": inspection}


def save_project(state, metadata, project, directory):
    if not PROJECT.fullmatch(project):
        raise OSError("Invalid generated project identity")
    data = (directory / "compose.json").read_bytes()
    (state / "compose.json").write_bytes(data)
    (state / "empty.env").write_bytes(b"")
    write_json(state / "project.json", {"project": project, "testedCommit": metadata["testedCommit"],
                                       "composeSha256": hashlib.sha256(data).hexdigest()})


def execute(repo, state, env, runner=None):
    state.mkdir(parents=True, exist_ok=True)
    metadata = {}
    code = 2
    primary = None
    try:
        path, metadata = prepare(repo, env)
        write_json(state / "metadata.json", metadata)
        snapshot = state / "candidate.json"
        snapshot.write_bytes(selected_file(repo, metadata["testedCommit"], metadata["candidatePath"]))
        if runner is None:
            from container_platform.smoke import run
            runner = run
        result = runner(snapshot, repo, PROFILE, execute=True, inspection_only=metadata["inspectionOnly"],
                        project_ready=lambda project, directory: save_project(state, metadata, project, directory))
        code = result["exitCode"]
        if type(code) is not int or code not in (0, 1, 2, 3, 130):
            code = 2
            raise InputError("runtime_exit_invalid")
        primary = code
        write_json(state / "runtime.json", result)
    except InputError as error:
        write_json(state / "error.json", {"reason": str(error)})
    except KeyboardInterrupt:
        code = 130
        write_json(state / "error.json", {"reason": "workflow_interrupted"})
    except (OSError, ValueError, KeyError, ImportError):
        code = code if code in (1, 2, 3, 130) else 2
        write_json(state / "error.json", {"reason": "workflow_execution_failed"})
    finally:
        write_json(state / "exit.json", {"primaryExitCode": primary if primary is not None else code})
    return code


def cleanup(state, metadata):
    if not (state / "project.json").exists():
        return {"outcome": "not_required"}
    from container_platform.smoke import checked, resources
    from container_platform.smoke_transport import Docker
    identity = json_read(state / "project.json")
    project = identity.get("project", "")
    if (not isinstance(project, str) or not PROJECT.fullmatch(project)
            or identity.get("testedCommit") != metadata.get("testedCommit")
            or hashlib.sha256((state / "compose.json").read_bytes()).hexdigest() != identity.get("composeSha256")):
        raise InputError("cleanup_identity_invalid")
    deadline = time.monotonic() + 120
    docker = Docker(project, state)
    if any(resources(docker, deadline).values()):
        checked(docker.compose(["down", "--volumes", "--remove-orphans", "--timeout", "10"], deadline), "workflow_cleanup_failed")
        if any(resources(docker, deadline).values()):
            raise InputError("workflow_resources_remaining")
    return {"outcome": "verified_absent", "project": project}


def runtime_report(state, metadata, primary):
    result = json_read(state / "runtime.json")
    if (not isinstance(result, dict) or result.get("smokeReportVersion") != 1
            or type(result.get("exitCode")) is not int or result["exitCode"] != primary
            or result.get("inspectionOnly") is not metadata.get("inspectionOnly")
            or result.get("profile") != metadata.get("profile")
            or not isinstance(result.get("cleanup"), dict)
            or not isinstance(result.get("checks"), list)
            or not isinstance(result.get("diagnostics"), list)
            or not isinstance(result.get("runtimeOutcome"), str)):
        raise InputError("report_contract_invalid")
    candidate = result.get("candidate")
    if candidate is not None and (not isinstance(candidate, dict) or candidate.get("sha256") != metadata.get("candidateSha256")):
        raise InputError("report_candidate_mismatch")
    if primary == 0:
        validation = result.get("validation", {})
        from container_platform.compose import IDS
        services = result.get("services", [])
        assertions = result.get("smokeAssertions", [])
        if (candidate is None or not isinstance(validation, dict)
                or result.get("runtimeOutcome") != "success" or result.get("cleanup", {}).get("outcome") != "success"
                or not isinstance(services, list) or len(services) != len(IDS)
                or {s.get("service") for s in services} != IDS or any(s.get("state") != "running" for s in services)
                or assertions != ["frontend_catalog_pass", "product_browse_pass", "cart_add_pass", "cart_remove_pass", "synthetic_checkout_pass"]):
            raise InputError("report_success_invalid")
        if validation.get("exitCode") != 0:
            checks = validation.get("checks", [])
            if (not metadata["inspectionOnly"] or validation.get("exitCode") != 1 or not checks
                    or any(c.get("status") != "pass" for c in checks[:-1])
                    or checks[-1].get("id") != "candidate.traceability" or checks[-1].get("status") != "fail"
                    or not checks[-1].get("diagnostics")
                    or any(d.get("code") != "legacy_ineligible" for d in checks[-1]["diagnostics"])):
                raise InputError("report_eligibility_inconsistent")
    # The runner already sanitizes diagnostic values. Drop manual commands and any
    # unknown top-level payload before artifact upload; never upload private state.
    allowed = ("smokeReportVersion", "runtimeOutcome", "exitCode", "inspectionOnly", "profile", "candidate",
               "validation", "checks", "services", "smokeAssertions", "cleanup", "diagnostics", "elapsedSeconds", "scope")
    return {key: result[key] for key in allowed if key in result}


def summary(evidence, path):
    runtime = evidence.get("runtime", {})
    candidate = runtime.get("candidate") or {}
    validation = runtime.get("validation") or {}
    fields = {"Tested commit": evidence.get("testedCommit"), "Candidate": evidence.get("candidatePath"),
              "Candidate byte SHA-256": evidence.get("candidateSha256"), "Release ID": candidate.get("releaseId"),
              "Profile": evidence.get("profile"), "Inspection only": evidence.get("inspectionOnly"),
              "Runtime": runtime.get("runtimeOutcome", "execution/report failure"),
              "Runner cleanup": runtime.get("cleanup", {}).get("outcome", "unknown"),
              "Workflow cleanup": evidence["workflowCleanup"]["outcome"],
              "Offline eligibility": validation.get("result", "not evaluated"),
              "Offline validation exit": validation.get("exitCode"), "Evidence": evidence["reportStatus"]}
    rows = ["<tr><th>" + key + "</th><td><code>" + html.escape(json.dumps(value, ensure_ascii=True)) + "</code></td></tr>" for key, value in fields.items()]
    path.write_text("### Manual Compose smoke\n\n<table>" + "".join(rows) + "</table>\n\nNo release approval or deployment authorization.\n", encoding="utf-8")


def finalize(state, output, summary_path):
    evidence = {"workflowEvidenceVersion": 1, "reportStatus": "execution/report failure", "runtime": {},
                "primaryExitCode": None, "workflowExitCode": 2, "workflowCleanup": {"outcome": "not_required"}}
    primary = 2
    try:
        metadata = json_read(state / "metadata.json")
        if not isinstance(metadata, dict):
            raise InputError("metadata_invalid")
        metadata = {key: metadata[key] for key in ("testedCommit", "candidatePath", "candidateSha256", "profile", "inspectionOnly") if key in metadata}
        evidence.update(metadata)
    except InputError:
        commit = os.environ.get("SMOKE_COMMIT", "")
        metadata = {"testedCommit": commit if re.fullmatch(r"[0-9a-f]{40}", commit) else None}
        evidence.update(metadata)
    try:
        primary = json_read(state / "exit.json")["primaryExitCode"]
        if type(primary) is not int or primary not in (0, 1, 2, 3, 130):
            raise InputError("runtime_exit_invalid")
        evidence["primaryExitCode"] = primary
        evidence["runtime"] = runtime_report(state, metadata, primary)
        evidence.update(reportStatus="valid", workflowExitCode=primary)
    except (InputError, KeyError, TypeError, AttributeError):
        evidence["workflowExitCode"] = primary if primary in (1, 2, 3, 130) else 2
    try:
        reason = json_read(state / "error.json").get("reason", "")
        if isinstance(reason, str) and re.fullmatch(r"[a-z_]+", reason):
            evidence["workflowDiagnostic"] = reason
            evidence["reportStatus"] = "execution/report failure"
            if evidence["workflowExitCode"] == 0:
                evidence["workflowExitCode"] = 2
    except (InputError, AttributeError):
        pass
    try:
        evidence["workflowCleanup"] = cleanup(state, metadata)
    except Exception:
        evidence["workflowCleanup"] = {"outcome": "failed", "reason": "workflow_cleanup_unverified"}
        if evidence["workflowExitCode"] == 0:
            evidence["workflowExitCode"] = 2
    write_json(output, evidence)
    summary(evidence, summary_path)
    return evidence["workflowExitCode"]


def main():
    root = Path(os.environ["RUNNER_TEMP"])
    state = root / "container-platform-smoke-state"
    previous = signal.getsignal(signal.SIGTERM)
    def interrupted(*_):
        raise KeyboardInterrupt()
    try:
        signal.signal(signal.SIGTERM, interrupted)
        if sys.argv[1:] == ["execute"]:
            return execute(Path.cwd(), state, os.environ)
        if sys.argv[1:] == ["finalize"]:
            return finalize(state, root / "container-platform-smoke-evidence/report.json", Path(os.environ["GITHUB_STEP_SUMMARY"]))
        return 2
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    raise SystemExit(main())
