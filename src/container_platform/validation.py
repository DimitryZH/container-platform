"""Deterministic offline evaluation; no registry or deployment operations."""

import json
import math
import os
import re
import subprocess
from importlib.resources import files
from pathlib import Path

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from referencing import Registry
from referencing.exceptions import NoSuchResource, Unresolvable


STAGES = (
    "candidate.parse",
    "candidate.schema",
    "candidate.purpose",
    "inventory.resolve",
    "inventory.parse",
    "inventory.policy",
    "candidate.inventory",
    "candidate.traceability",
)
LIMITATION = (
    "Offline rules only. Registry availability, verified provenance, integration "
    "compatibility, inventory approval, release approval and deployment "
    "authorization are not evaluated."
)


class Failure(Exception):
    def __init__(self, code, message, *, execution=False):
        super().__init__(message)
        self.code = code
        self.execution = execution


def diagnostic(code, message, path="$"):
    return {"code": code, "path": path, "message": message}


def new_result():
    return {
        "outputSchemaVersion": 1,
        "result": "validation_failed",
        "exitCode": 1,
        "scope": LIMITATION,
        "diagnostics": [],
        "checks": [
            {
                "id": stage,
                "status": "skipped",
                "diagnostics": [diagnostic("prerequisite_failed", "Not evaluated: prerequisite checks did not pass.")],
            }
            for stage in STAGES
        ],
    }


def strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise Failure("duplicate_json_key", "Duplicate object key; input values are not echoed.")
            result[key] = value
        return result

    def constant(_):
        raise Failure("non_json_number", "NaN and Infinity are not JSON numbers.")

    def finite(value):
        result = float(value)
        if not math.isfinite(result):
            raise Failure("non_json_number", "Number exceeds the supported finite numeric range.")
        return result

    try:
        text = data.decode("utf-8", errors="strict")
        return json.loads(text, object_pairs_hook=pairs, parse_constant=constant, parse_float=finite)
    except UnicodeDecodeError:
        raise Failure("invalid_utf8", "Input must be valid UTF-8.") from None
    except json.JSONDecodeError as error:
        raise Failure("malformed_json", f"Malformed JSON at line {error.lineno}, column {error.colno}.") from None
    except (ValueError, RecursionError):
        raise Failure("malformed_json", "JSON exceeds supported parsing limits.") from None


def deny_retrieval(uri):
    # Explicitly replace referencing's default retrieval boundary, even for local refs.
    raise NoSuchResource(ref=uri)


def schema_validator(schema):
    def inspect(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("$ref", "$dynamicRef") and (not isinstance(value, str) or not value.startswith("#")):
                    raise Failure("schema_reference_unsupported", "Only in-document schema references are supported.", execution=True)
                inspect(value)
        elif isinstance(node, list):
            for value in node:
                inspect(value)

    inspect(schema)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError:
        raise Failure("schema_invalid", "Packaged release schema is invalid.", execution=True) from None
    return Draft202012Validator(schema, registry=Registry(retrieve=deny_retrieval))


def load_schema():
    try:
        return strict_json(files("container_platform.schemas").joinpath("release-v1.schema.json").read_bytes())
    except (OSError, ImportError, Failure):
        raise Failure("schema_unavailable", "Packaged release schema could not be read.", execution=True) from None


def git_read(repository, *args):
    # Ambient Git variables must not redirect object reads or inject configuration.
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update({
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_ALLOW_PROTOCOL": "",  # Also blocks promisor retrieval on older Git versions.
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
    })
    try:
        return subprocess.run(
            ["git", "--no-replace-objects", "-C", str(repository), *args],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            shell=False, timeout=30, check=False,
        )
    except FileNotFoundError:
        raise Failure("git_unavailable", "Git executable is unavailable.", execution=True) from None
    except (OSError, subprocess.TimeoutExpired):
        raise Failure("git_execution_error", "Local Git read could not complete.", execution=True) from None


def resolve_inventory(repository, reference):
    revision, path = reference["revision"], reference["path"]
    # Defense in depth; never pass unchecked revision/path operands to Git.
    if not re.fullmatch(r"[0-9a-f]{40}", revision) or not re.fullmatch(r"(?:[a-zA-Z0-9_-]+/)*[a-zA-Z0-9_-]+\.json", path):
        raise Failure("inventory_reference_invalid", "Inventory revision/path violate release contract v1.")
    if git_read(repository, "rev-parse", "--git-dir").returncode:
        raise Failure("repository_unavailable", "Local Git repository is unavailable.", execution=True)
    commit = git_read(repository, "cat-file", "-t", revision)
    if commit.returncode:
        raise Failure("inventory_commit_missing", "Inventory commit is not available locally; a shallow/partial clone may omit it. No fetch was attempted.")
    if commit.stdout != b"commit\n":
        raise Failure("inventory_revision_not_commit", "Inventory revision must name a commit object.")
    tree = git_read(repository, "ls-tree", "-z", revision, "--", path)
    if tree.returncode:
        raise Failure("inventory_object_missing", "Inventory tree objects are unavailable locally; no fetch was attempted.")
    if not tree.stdout:
        raise Failure("inventory_path_missing", "Inventory path does not exist at the recorded commit.")
    record = tree.stdout.rstrip(b"\0").split(b"\t", 1)
    mode, kind, oid = record[0].split()
    if len(record) != 2 or record[1] != path.encode("ascii") or mode not in (b"100644", b"100755") or kind != b"blob":
        raise Failure("inventory_not_regular_file", "Inventory must be a regular file blob, not a symlink, directory or submodule.")
    blob = git_read(repository, "cat-file", "blob", oid.decode("ascii"))
    if blob.returncode:
        raise Failure("inventory_object_missing", "Inventory blob is unavailable locally; no fetch was attempted.")
    return blob.stdout


def inventory_errors(inventory, schema):
    errors = []

    def fail(code, path, message):
        errors.append(diagnostic(code, message, path))

    def matches(value, pattern):
        return isinstance(value, str) and re.search(pattern, value) is not None

    component = schema["properties"]["components"]["items"]["properties"]
    image = component["image"]["properties"]
    id_pattern = component["id"]["pattern"]
    platform_pattern = image["platforms"]["items"]["pattern"]

    def string_list(value, pattern):
        return (isinstance(value, list) and bool(value)
                and all(matches(item, pattern) for item in value)
                and len(set(value)) == len(value))

    if not isinstance(inventory, dict):
        return [diagnostic("inventory_type_invalid", "Inventory must be an object.")]
    if type(inventory.get("inventoryVersion")) is not int or inventory["inventoryVersion"] != 1:
        fail("inventory_version_unsupported", "$.inventoryVersion", "Only integer inventoryVersion 1 is supported.")
    if inventory.get("application") != "online-shop":
        fail("inventory_application_invalid", "$.application", "Inventory application must be online-shop.")
    if not matches(inventory.get("profile"), schema["properties"]["releaseId"]["pattern"]):
        fail("inventory_profile_invalid", "$.profile", "A valid nonempty profile identity is required.")
    top_platforms = inventory.get("targetPlatforms")
    if "targetPlatforms" in inventory and not string_list(top_platforms, platform_pattern):
        fail("inventory_platforms_invalid", "$.targetPlatforms", "Platforms must be nonempty, unique canonical platform strings.")
    entries = inventory.get("components")
    if not isinstance(entries, list) or not entries:
        fail("inventory_components_invalid", "$.components", "Inventory requires a nonempty component array.")
        return errors
    seen = set()
    for index, entry in enumerate(entries):
        path = f"$.components[{index}]"
        if not isinstance(entry, dict):
            fail("inventory_component_invalid", path, "Component must be an object.")
            continue
        identity = entry.get("id")
        if not matches(identity, id_pattern):
            fail("inventory_id_invalid", path + ".id", "Component ID must follow release contract syntax.")
        elif identity in seen:
            fail("inventory_id_duplicate", path + ".id", "Component ID occurs more than once.")
        else:
            seen.add(identity)
        if entry.get("class") not in ("first-party", "third-party"):
            fail("inventory_class_invalid", path + ".class", "Artifact class must be first-party or third-party.")
        if entry.get("required") is not True:
            fail("inventory_required_invalid", path + ".required", "V1 profile entries must explicitly be required: true.")
        repos = entry.get("allowedRepositories")
        if not string_list(repos, image["repository"]["pattern"]):
            fail("inventory_repositories_invalid", path + ".allowedRepositories", "Exact canonical repositories must be nonempty and unique.")
        elif entry.get("class") == "first-party" and any(not repo.startswith("docker.io/dmitryzhuravlev/online-shop-") for repo in repos):
            fail("inventory_first_party_namespace", path + ".allowedRepositories", "First-party entries must use the contract's application namespace.")
        platforms = entry.get("targetPlatforms")
        if not string_list(platforms, platform_pattern):
            fail("inventory_platforms_invalid", path + ".targetPlatforms", "Platforms must be nonempty, unique canonical platform strings.")
        elif isinstance(top_platforms, list) and string_list(top_platforms, platform_pattern) and set(platforms) != set(top_platforms):
            fail("inventory_platforms_inconsistent", path + ".targetPlatforms", "Component and top-level platform sets must agree.")
    return errors


def consistency_errors(candidate, inventory):
    errors = []
    if candidate["application"]["name"] != inventory["application"]:
        errors.append(diagnostic("application_mismatch", "Candidate and inventory application identities differ.", "$.application.name"))
    policy = {item["id"]: item for item in inventory["components"]}
    seen = set()
    for index, item in enumerate(candidate["components"]):
        path = f"$.components[{index}]"
        identity = item["id"]
        if identity in seen:
            errors.append(diagnostic("component_duplicate", "Candidate component ID occurs more than once.", path + ".id"))
        seen.add(identity)
        if identity not in policy:
            errors.append(diagnostic("component_unknown", "Component is absent from the selected inventory.", path + ".id"))
            continue
        required = policy[identity]
        if item["class"] != required["class"]:
            errors.append(diagnostic("component_class_mismatch", "Artifact class differs from inventory.", path + ".class"))
        if item["image"]["repository"] not in required["allowedRepositories"]:
            errors.append(diagnostic("repository_disallowed", "Repository is not an exact inventory allowlist entry.", path + ".image.repository"))
        if set(item["image"]["platforms"]) != set(required["targetPlatforms"]):
            errors.append(diagnostic("platforms_mismatch", "Candidate and inventory platform sets must be equal.", path + ".image.platforms"))
    for index, item in enumerate(inventory["components"]):
        if item["id"] not in seen:
            errors.append(diagnostic("component_missing", "Required inventory component is missing from candidate.", f"inventory.components[{index}].id"))
    return errors


def traceability_errors(candidate):
    # Schema has already enforced source/build and publisher-reference requirements.
    return [
        diagnostic("legacy_ineligible", "Legacy artifact is traceability-ineligible; exceptions are not supported.", f"$.components[{index}].traceability.status")
        for index, component in enumerate(candidate["components"])
        if component["traceability"]["status"] == "legacy"
    ]


def validate(candidate_path, repository):
    result = new_result()
    candidate = inventory_bytes = inventory = schema = None
    for check in result["checks"]:
        try:
            stage = check["id"]
            errors = []
            if stage == "candidate.parse":
                try:
                    data = Path(candidate_path).read_bytes()
                except OSError:
                    raise Failure("candidate_unreadable", "Candidate file could not be read.", execution=True) from None
                candidate = strict_json(data)
            elif stage == "candidate.schema":
                schema = load_schema()
                validator = schema_validator(schema)
                violations = sorted(validator.iter_errors(candidate), key=lambda e: (json.dumps(list(e.absolute_path)), json.dumps(list(e.absolute_schema_path))))
                for error in violations:
                    # Never use jsonschema's message: it embeds untrusted values/URLs.
                    path = "$" + "".join(f"[{part}]" if isinstance(part, int) else "." + part for part in error.absolute_path)
                    errors.append(diagnostic("schema_violation", f"Value violates local release schema ({error.validator}).", path))
            elif stage == "candidate.purpose":
                if candidate["purpose"] != "candidate":
                    errors.append(diagnostic("example_not_candidate", "Examples cannot be evaluated as release candidates.", "$.purpose"))
            elif stage == "inventory.resolve":
                inventory_bytes = resolve_inventory(repository, candidate["inventory"])
            elif stage == "inventory.parse":
                inventory = strict_json(inventory_bytes)
            elif stage == "inventory.policy":
                errors = inventory_errors(inventory, schema)
            elif stage == "candidate.inventory":
                errors = consistency_errors(candidate, inventory)
            elif stage == "candidate.traceability":
                errors = traceability_errors(candidate)
            check.update(status="fail" if errors else "pass", diagnostics=errors)
            if errors:
                return result
        except Failure as error:
            check.update(status="fail", diagnostics=[diagnostic(error.code, str(error))])
            if error.execution:
                result.update(result="execution_error", exitCode=2)
            return result
        except Unresolvable:
            check.update(status="fail", diagnostics=[diagnostic("schema_reference_unavailable", "Schema reference is unavailable in the local document; retrieval is disabled.")])
            result.update(result="execution_error", exitCode=2)
            return result
        except (RecursionError, OverflowError):
            check.update(status="fail", diagnostics=[diagnostic("validation_limits_exceeded", "Input exceeds supported evaluation limits.")])
            return result
    result.update(result="offline_rules_passed", exitCode=0)
    return result
