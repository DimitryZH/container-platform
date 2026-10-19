"""Cross-platform text/JSON command-line interface."""

import argparse
import json
import sys

from .validation import Failure, diagnostic, new_result, validate


class Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default error includes arbitrary supplied arguments.
        raise Failure("usage_error", "Invalid command arguments; use --help.", execution=True)


def render_text(result):
    lines = [result["result"].upper(), result["scope"]]
    for issue in result["diagnostics"]:
        lines.append(f"{issue['code']}: {issue['message']}")
    for check in result["checks"]:
        lines.append(f"{check['id']}: {check['status']}")
        for component in check.get("components", []):
            lines.append(f"  component[{component['componentIndex']}]: {component['status']}")
        for issue in check["diagnostics"]:
            lines.append(f"  {issue['code']} at {issue['path']}: {issue['message']}")
    lines.append(f"Exit code: {result['exitCode']}")
    return "\n".join(lines)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # Preserve JSON output even if parsing the remaining command line fails.
    output_format = "json" if "--format=json" in argv or any(
        arg == "--format" and argv[index + 1:index + 2] == ["json"]
        for index, arg in enumerate(argv)
    ) else "text"
    parser = Parser(description="Validate release rules; registry access requires opt-in. Never approves or deploys.", allow_abbrev=False)
    parser.add_argument("candidate", help="Path to UTF-8 release candidate JSON")
    parser.add_argument("--repository", required=True, help="Local Container Platform Git repository")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--registry", action="store_true", help="Opt in to bounded public Docker Hub metadata verification")
    try:
        args = parser.parse_args(argv)
        output_format = args.format
        result = validate(args.candidate, args.repository, registry=args.registry)
    except Failure as error:
        result = new_result("--registry" in argv)
        result.update(result="execution_error", exitCode=2, diagnostics=[diagnostic(error.code, str(error))])
    print(json.dumps(result, indent=2, ensure_ascii=True) if output_format == "json" else render_text(result))
    return result["exitCode"]
