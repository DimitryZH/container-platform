"""Offline, digest-pinned Compose rendering; never executes Docker."""

import json
import os
from importlib.resources import files
from pathlib import Path
import tempfile

from .cli import Parser
from .validation import Failure, resolve_inventory, strict_json, validate


PROFILE = "aml-online-shop-compose-v1"
IDS = frozenset("frontend cartservice checkoutservice paymentservice adservice currencyservice emailservice productcatalogservice recommendationservice shippingservice redis-cart loadgenerator".split())


def load_profile(name):
    if name != PROFILE:
        raise Failure("profile_unsupported", "Select the supported explicit Compose profile.")
    profile = strict_json(files("container_platform.profiles").joinpath(PROFILE + ".json").read_bytes())
    services = profile.get("services", {})
    if (profile.get("profile") != PROFILE or profile.get("platform") != "linux/amd64"
            or profile.get("inventoryProfile") != "sre-dev-online-shop-v1" or set(services) != IDS):
        raise Failure("profile_mapping_invalid", "Packaged profile mapping is invalid.")
    for service in services.values():
        if not isinstance(service, dict) or set(service) - {"environment", "expose"}:
            raise Failure("profile_mapping_invalid", "Unsupported profile configuration.")
        env = service.get("environment", {})
        ports = service.get("expose", [])
        if (not isinstance(env, dict) or any(not isinstance(k, str) or not isinstance(v, str) or "$" in k + v for k, v in env.items())
                or not isinstance(ports, list) or any(not isinstance(p, str) or not p.isascii() or not p.isdigit() or not 1 <= int(p) <= 65535 for p in ports)):
            raise Failure("profile_mapping_invalid", "Profile values must be literal environment strings and ports.")
    for service in services.values():
        for key, value in service["environment"].items():
            if key.endswith("_ADDR") and value != "127.0.0.1:1":
                host, separator, port = value.partition(":")
                if not separator or host not in services or port not in services[host].get("expose", []):
                    raise Failure("profile_mapping_invalid", "Service addresses must resolve to profile container ports.")
    if (services["frontend"].get("expose") != ["8080"]
            or services["frontend"]["environment"].get("ENABLE_ASSISTANT") != "false"
            or services["frontend"]["environment"].get("SHOPPING_ASSISTANT_SERVICE_ADDR") != "127.0.0.1:1"
            or services["loadgenerator"]["environment"].get("FRONTEND_ADDR") != "frontend:8080"
            or services["redis-cart"].get("expose") != ["6379"]):
        raise Failure("profile_mapping_invalid", "Required profile runtime settings are inconsistent.")
    return profile


def model(candidate, inventory, profile, inspection):
    components = {c["id"]: c for c in candidate["components"]}
    if (set(components) != IDS or {c["id"] for c in inventory["components"]} != IDS
            or inventory["profile"] != profile["inventoryProfile"]
            or any(c["image"]["platforms"] != [profile["platform"]] for c in components.values())):
        raise Failure("profile_membership_mismatch", "Compose profile requires its exact inventory membership and linux/amd64 platform.")
    services = {}
    for identity in sorted(IDS):
        image = components[identity]["image"]
        config = profile["services"][identity]
        service = {"image": image["repository"] + "@" + image["digest"], "platform": profile["platform"], **config}
        # Container ports replace Kubernetes Service port translation (email 5000 -> 8080,
        # frontend 80 -> 8080). No auxiliary proxy image is introduced.
        dependencies = sorted({v.split(":")[0] for v in config["environment"].values() if ":" in v and v.split(":")[0] in IDS})
        if dependencies:
            service["depends_on"] = dependencies
        if identity == "frontend":
            service["ports"] = [{"target": 8080, "published": "8080", "host_ip": "127.0.0.1", "protocol": "tcp"}]
        if identity == "redis-cart":
            service["tmpfs"] = ["/data"]
        services[identity] = service
    return {"services": services, "x-container-platform": {
        "releaseId": candidate["releaseId"], "profile": profile["profile"],
        "inventoryRevision": candidate["inventory"]["revision"], "inspectionOnly": inspection,
    }}


def write_output(destination, document, overwrite):
    """Publish complete bytes atomically; exclusive creation by default."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".compose-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(document)
        if overwrite:
            os.replace(temporary, destination)
        else:
            os.link(temporary, destination)
    except FileExistsError:
        raise Failure("output_exists", "Output exists; explicit --overwrite is required.") from None
    except OSError:
        raise Failure("output_write_failed", "Output could not be written atomically.", execution=True) from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def render(candidate_path, repository, profile_name, output, *, inspection_only=False, overwrite=False):
    report = {"renderOutputVersion": 1, "result": "render_failed", "exitCode": 1,
              "inspectionOnly": inspection_only, "validation": None, "diagnostics": [],
              "scope": "Offline configuration artifact only; no registry/runtime verification, approval or deployment authorization."}
    try:
        profile = load_profile(profile_name)
        # Validate and render the same snapshot even if the caller edits the original file.
        data = Path(candidate_path).read_bytes()
        with tempfile.TemporaryDirectory(prefix="compose-input-") as temporary:
            snapshot = Path(temporary) / "candidate.json"
            snapshot.write_bytes(data)
            validation = validate(snapshot, repository)
        report["validation"] = validation
        checks = validation["checks"]
        legacy_only = (all(c["status"] == "pass" for c in checks[:-1])
                       and checks[-1]["id"] == "candidate.traceability"
                       and checks[-1]["status"] == "fail" and bool(checks[-1]["diagnostics"])
                       and all(d["code"] == "legacy_ineligible" for d in checks[-1]["diagnostics"]))
        if validation["exitCode"] != 0 and not (inspection_only and legacy_only):
            report["exitCode"] = validation["exitCode"]
            report["diagnostics"].append("release_validation_failed")
            return report
        candidate = strict_json(data)
        inventory = strict_json(resolve_inventory(repository, candidate["inventory"]))
        document = model(candidate, inventory, profile, inspection_only)
        destination = Path(output).absolute()
        protected = [Path(candidate_path), Path(repository) / candidate["inventory"]["path"],
                     Path(str(files("container_platform.schemas").joinpath("release-v1.schema.json"))),
                     Path(str(files("container_platform.profiles").joinpath(PROFILE + ".json")))]
        if destination.is_symlink() or any(destination.resolve() == p.resolve() or (destination.exists() and p.exists() and destination.samefile(p)) for p in protected):
            raise Failure("output_is_input", "Output must not replace an input or packaged resource.")
        write_output(destination, (json.dumps(document, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode(), overwrite)
        report.update(result="render_succeeded", exitCode=0)
    except Failure as failure:
        report.update(exitCode=2 if failure.execution else 1, diagnostics=[failure.code])
    except OSError:
        report.update(exitCode=2, diagnostics=["render_io_failed"])
    return report


def main(argv=None):
    parser = Parser(description=__doc__, allow_abbrev=False)
    parser.add_argument("candidate")
    parser.add_argument("--repository", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--inspection-only", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    try:
        args = parser.parse_args(argv)
        report = render(args.candidate, args.repository, args.profile, args.output,
                        inspection_only=args.inspection_only, overwrite=args.overwrite)
    except Failure as failure:
        report = {"result": "render_failed", "exitCode": 2, "diagnostics": [failure.code]}
    print(json.dumps(report, indent=2))
    return report["exitCode"]


if __name__ == "__main__":
    raise SystemExit(main())
