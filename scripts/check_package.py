"""Build output smoke test in a clean venv, independent of checkout cwd."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import venv


def run(args, cwd, env, expected=0):
    result = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, check=False, timeout=600)
    if result.returncode != expected:
        print(result.stdout)
        print(result.stderr, file=sys.stderr)
        raise RuntimeError("Package/test command did not produce the expected exit code.")
    return result.stdout


def check(repository, wheel_directory):
    repo = Path(repository).resolve()
    wheels = list(Path(wheel_directory).resolve().glob("container_platform-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError("Exactly one Container Platform wheel is required.")
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    with tempfile.TemporaryDirectory(prefix="package-check-") as temporary:
        outside = Path(temporary)
        environment = outside / "venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        binaries = environment / ("Scripts" if os.name == "nt" else "bin")
        python = str(binaries / ("python.exe" if os.name == "nt" else "python"))
        console = str(binaries / ("container-release.exe" if os.name == "nt" else "container-release"))
        run([python, "-m", "pip", "install", str(wheels[0])], outside, env)
        # Tests import the installed wheel, not an editable source package.
        run([python, "-m", "unittest", "discover", "-s", str(repo / "tests"), "-v"], repo, env)
        resource = run([python, "-c", "from importlib.resources import files; print(files('container_platform.schemas').joinpath('release-v1.schema.json').read_text(encoding='utf-8'), end='')"], outside, env)
        if resource != (repo / "schemas/release-v1.schema.json").read_text(encoding="utf-8"):
            raise RuntimeError("Packaged schema differs from the canonical source.")
        reports = []
        for command in ([python, "-m", "container_platform"], [console]):
            output = run([*command, str(repo / "releases/baseline-2026-10-18.json"), "--repository", str(repo), "--format", "json"], outside, env, expected=1)
            report = json.loads(output)
            if (report["outputSchemaVersion"] != 1 or report["exitCode"] != 1
                    or any(stage["status"] != "pass" for stage in report["checks"][:-1])
                    or [d["code"] for d in report["checks"][-1]["diagnostics"]] != ["legacy_ineligible"] * 12):
                raise RuntimeError("Baseline regression semantics changed.")
            reports.append(report)
        if reports[0] != reports[1]:
            raise RuntimeError("Installed module and console results differ.")
        profile = "aml-online-shop-compose-v1"
        resource = run([python, "-c", "from importlib.resources import files; print(files('container_platform.profiles').joinpath('aml-online-shop-compose-v1.json').read_text(encoding='utf-8'), end='')"], outside, env)
        if resource != (repo / "src/container_platform/profiles" / (profile + ".json")).read_text(encoding="utf-8"):
            raise RuntimeError("Packaged Compose profile differs from its source.")
        compose_console = str(binaries / ("container-compose.exe" if os.name == "nt" else "container-compose"))
        artifacts = []
        for index, command in enumerate(([python, "-m", "container_platform.compose"], [compose_console])):
            destination = outside / f"compose-{index}.json"
            report = json.loads(run([*command, str(repo / "releases/baseline-2026-10-18.json"),
                                     "--repository", str(repo), "--profile", profile,
                                     "--output", str(destination), "--inspection-only"], outside, env))
            if report["exitCode"] != 0 or report["validation"]["exitCode"] != 1 or not report["inspectionOnly"]:
                raise RuntimeError("Installed Compose inspection semantics changed.")
            artifacts.append(destination.read_bytes())
        if artifacts[0] != artifacts[1]:
            raise RuntimeError("Installed rendering interfaces differ.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--wheel-directory", required=True)
    args = parser.parse_args()
    passed = False
    try:
        check(args.repository, args.wheel_directory)
        passed = True
        print("Automated tests and installed-wheel checks passed; baseline remains legacy-ineligible.")
    finally:
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            Path(os.environ["GITHUB_STEP_SUMMARY"]).write_text(
                "### Tests and package\n\n"
                + ("Pass" if passed else "Fail")
                + ": automated offline tests; clean installed wheel; module/console and schema outside checkout.\n"
                  "Compose: installed offline renderer/profile; baseline inspection preserves negative validation.\n"
                  "Baseline: expected negative regression, not release eligibility.\n",
                encoding="utf-8",
            )


if __name__ == "__main__":
    main()
