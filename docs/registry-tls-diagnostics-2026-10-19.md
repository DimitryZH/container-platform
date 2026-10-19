# Registry TLS follow-up — 2026-10-19

This follow-up preserves the [14:13 UTC failed live record](registry-validation-2026-10-19.md). No new full registry run was made: the installed CLI's Python default TLS verification still fails at the public token endpoint. No certificates, trust settings, system clock, VPN/proxy settings, endpoint allowlist or TLS verification flags were changed.

## Time and client comparison

OS UTC and Python UTC were checked at **2026-10-19T14:23:58Z**; the available UTC reference returned **2026-10-19T14:23:59Z**. They agree within sampling time. This is not an independent NTP audit and gives no reason to roll back the system clock.

TLS comparisons were observed at **2026-10-19T14:24:32Z**, followed by selected-chain/store diagnostics at **14:25:01Z**:

| Client | auth.docker.io | registry-1.docker.io |
|---|---|---|
| Python 3.13.3, default SSL context; OpenSSL 3.0.16 (11 Feb 2025) | Certificate verification fails, code 10: expired certificate | TLS handshake passes |
| curl 8.21.0, Windows Schannel, default routing | HTTPS succeeds, HTTP 200 | HTTPS succeeds, HTTP 401 (expected public registry challenge) |
| Same Schannel client, explicit direct connection | HTTPS succeeds, HTTP 200 | HTTPS succeeds, HTTP 401 |

All clients kept certificate verification enabled. HTTP response bodies were discarded. No token, signed URL, private proxy identity, internal certificate identity or local path is published. The installed CLI environment was checked separately: its Python/OpenSSL versions match the table and its package modules/schema match commit `235291f386a0d4a7c4229248580fa1bf2ecde3e0`.

## Identified expired certificate

The auth leaf is **not expired**: its observed validity is 6 October 2026 through 4 January 2027. All four certificates in the server-presented chain are within their validity periods at the observation time.

After the failed verified handshake, OpenSSL's selected verification-chain diagnostics identify the expired certificate at **depth/index 3**:

- public subject **ISRG Root X2**, cross-signed by **ISRG Root X1**;
- expiry **2025-09-15T16:00:00Z**;
- not self-issued: despite its subject name, this object is a **cross-signed CA/intermediate certificate**, not the auth leaf or the self-signed root;
- the exact DER certificate is present in Python's default CA set and in the visible Windows **CA / Intermediate Certification Authorities** store; it is absent from the visible ROOT store;
- the server supplied a different certificate with the same subject/issuer, valid until **2032-09-02T23:59:59Z**, while OpenSSL selected the expired local copy.

Python's default context has trusted-first and partial-chain verification enabled. The observed selection is consistent with a stale locally loaded cross-certificate shadowing the valid server-supplied alternative. Schannel succeeds with the same two public destinations, but its chosen certificate chain was not captured, so the exact Schannel alternate path is not established here. A chain obtained after a failed handshake is diagnostic evidence of selection, not a successfully validated chain.

This identifies a public CA cross-certificate loaded locally as the immediate expired-chain blocker. It does **not** establish a private TLS-inspection chain as the cause. These checks are not a complete audit of VPN/security software or every store/location available outside this process.

## Owner action and rerun

Have the owner or managed-device administrator review the stale **ISRG Root X2 cross-certificate expiring 15 September 2025** in the Intermediate Certification Authorities store and repair/update the trust material through the approved Windows/organization certificate-maintenance process. This is a targeted stale-intermediate review, not a recommendation to delete the self-signed ISRG root or disable verification. Alternatively use the same installed implementation in a normal local PowerShell environment whose Python default context already validates both endpoints. Schannel success alone does not establish that the Python CLI can proceed.

No automatic certificate installation/removal, clock change, proxy/VPN change or verification-flag workaround was attempted. Recheck Python default TLS after the owner action. Only then rerun one bounded CLI inspection; expected outcome is **12 registry passes, 12 legacy failures, exit 1**, never approval.

Run the following from a Container Platform checkout in **ordinary local PowerShell**. It uses the existing temporary virtual environment, verifies modules/schema against the recorded implementation commit, reads the baseline from that immutable commit, performs default TLS preflight, and invokes the unchanged installed CLI. If the environment or Git objects are unavailable, it stops rather than substituting another installation or fetching objects.

```powershell
$python = Join-Path $env:TEMP 'container-platform-issue-7\venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Recorded CLI environment is unavailable.' }
$repo = (git rev-parse --show-toplevel).Trim()
@'
import importlib.resources
import json
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
import container_platform
from container_platform.validation import git_read

repo = sys.argv[1]
revision = "235291f386a0d4a7c4229248580fa1bf2ecde3e0"
def stop(message):
    print(message, file=sys.stderr)
    raise SystemExit(2)
def recorded(path):
    result = git_read(repo, "show", revision + ":" + path)
    if result.returncode:
        stop("Recorded Git object unavailable; no fetch attempted.")
    return result.stdout.decode("utf-8").replace("\r\n", "\n")
base = Path(container_platform.__file__).parent
for name in ("__init__.py", "__main__.py", "cli.py", "validation.py", "registry.py"):
    if (base / name).read_text(encoding="utf-8") != recorded("src/container_platform/" + name):
        stop("Installed implementation differs from recorded commit.")
schema = importlib.resources.files("container_platform.schemas").joinpath("release-v1.schema.json")
if schema.read_text(encoding="utf-8") != recorded("schemas/release-v1.schema.json"):
    stop("Installed schema differs from recorded commit.")
try:
    for host in ("auth.docker.io", "registry-1.docker.io"):
        with socket.create_connection((host, 443), timeout=10) as raw:
            with ssl.create_default_context().wrap_socket(raw, server_hostname=host):
                pass
except (OSError, ssl.SSLError):
    stop("Python default TLS preflight failed; review the documented trust-store blocker.")
started = datetime.now(timezone.utc)
with tempfile.TemporaryDirectory() as temporary:
    candidate = Path(temporary) / "baseline.json"
    candidate.write_text(recorded("releases/baseline-2026-10-18.json"), encoding="utf-8")
    run = subprocess.run([sys.executable, "-m", "container_platform", str(candidate),
                          "--repository", repo, "--registry", "--format", "json"],
                         capture_output=True, timeout=240)
    report = json.loads(run.stdout)
finished = datetime.now(timezone.utc)
folder = Path(tempfile.gettempdir()) / "container-platform-registry-followup"
folder.mkdir(exist_ok=True)
filename = "registry-" + started.strftime("%Y%m%dT%H%M%SZ") + ".json"
(folder / filename).write_text(json.dumps({"startedAt": started.isoformat(),
    "finishedAt": finished.isoformat(), "implementationAndBaselineRevision": revision,
    "processExitCode": run.returncode, "report": report}, indent=2), encoding="utf-8")
print("Saved sanitized observation:", filename)
print("Registry components:", report["checks"][-1]["components"])
print("Traceability diagnostics:", len(report["checks"][-2]["diagnostics"]))
print("Exit code:", run.returncode)
raise SystemExit(run.returncode)
'@ | & $python - $repo
$LASTEXITCODE
```

The sanitized observation is saved under the temporary directory's `container-platform-registry-followup` folder. Return that JSON as a new observation; do not overwrite the earlier failed record. The wrapper's UTC values are separate from deterministic normal CLI output.

Successful live artifact verification remains pending. The previous full-run result remains **0/12 registry successes, 12 TLS failures, 12 legacy failures, exit 2**. This follow-up does not claim registry availability, layer availability, retention, provenance, compatibility, approval or deployment authorization.
