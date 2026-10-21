"""Bounded process and loopback HTTP adapters for explicit runtime checks."""

import http.cookiejar
import os
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


OUTPUT_LIMIT = 1024 * 1024
HTTP_LIMIT = 2 * 1024 * 1024


class SmokeError(Exception):
    def __init__(self, code, outcome="execution_error"):
        super().__init__(code)
        self.code, self.outcome = code, outcome


def remaining(deadline, clock=time.monotonic):
    value = deadline - clock()
    if value <= 0:
        raise SmokeError("deadline_exceeded", "timeout")
    return value


def stop_process(process):
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=False)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.kill() if process.poll() is None else None
    process.wait(timeout=5)


def process_output(args, cwd, env, deadline, clock=time.monotonic):
    """Drain into capped memory and stop the child process group on every abort."""
    remaining(deadline, clock)
    try:
        process = subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=False, bufsize=0,
                                   start_new_session=os.name != "nt",
                                   creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
    except OSError:
        raise SmokeError("docker_executable_unavailable") from None
    output = bytearray()
    overflow = threading.Event()

    def drain():
        while True:
            try:
                chunk = process.stdout.read(8192)
            except (OSError, ValueError):
                return
            if not chunk:
                return
            if len(output) + len(chunk) > OUTPUT_LIMIT:
                overflow.set()
                return
            output.extend(chunk)

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    try:
        while process.poll() is None:
            if overflow.is_set():
                raise SmokeError("subprocess_output_limit")
            time.sleep(min(0.05, remaining(deadline, clock)))
        reader.join(timeout=min(1, remaining(deadline, clock)))
        if reader.is_alive() or overflow.is_set():
            raise SmokeError("subprocess_output_limit")
        return process.returncode, bytes(output)
    except BaseException:
        try:
            stop_process(process)
        except (OSError, subprocess.TimeoutExpired):
            pass  # Preserve the original error; project cleanup is still attempted.
        raise
    finally:
        process.stdout.close()


class Docker:
    def __init__(self, project, directory, clock=time.monotonic):
        self.project, self.directory, self.clock = project, directory, clock
        self.env = {k: v for k, v in os.environ.items() if not k.upper().startswith("COMPOSE_")}
        self.env.pop("DOCKER_DEFAULT_PLATFORM", None)
        self.env["COMPOSE_DISABLE_ENV_FILE"] = "1"

    def raw(self, args, deadline):
        return process_output(["docker", *args], self.directory, self.env, deadline, self.clock)

    def compose(self, args, deadline):
        return self.raw(["compose", "--ansi", "never", "--project-name", self.project,
                         "--project-directory", str(self.directory), "--env-file", "empty.env",
                         "-f", "compose.json", *args], deadline)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Session:
    """One private cookie jar; no proxies, external redirects, assets or scripts."""
    origin = "http://127.0.0.1:8080"

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
                                                  urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def request(self, path, deadline, form=None):
        # Per-request deadline spans redirects and response reads, not just connect.
        deadline = min(deadline, self.clock() + 10)
        method = "POST" if form is not None else "GET"
        data = urllib.parse.urlencode(form).encode() if form is not None else None
        url = urllib.parse.urljoin(self.origin, path)
        for _ in range(4):
            if urllib.parse.urlsplit(url)[:2] != ("http", "127.0.0.1:8080"):
                raise SmokeError("http_redirect_outside_frontend", "application_failure")
            request = urllib.request.Request(url, data=data, method=method)
            try:
                try:
                    response = self.opener.open(request, timeout=min(1, remaining(deadline, self.clock)))
                except urllib.error.HTTPError as error:
                    response = error
                with response:
                    if response.code in (301, 302, 303, 307, 308):
                        target = response.headers.get("Location")
                        if not target:
                            raise SmokeError("http_redirect_invalid", "application_failure")
                        url = urllib.parse.urljoin(url, target)
                        if response.code in (301, 302, 303):
                            method, data = "GET", None
                        continue
                    if response.code != 200:
                        raise SmokeError("http_status_failed", "application_failure")
                    result = bytearray()
                    while True:
                        remaining(deadline, self.clock)
                        chunk = response.read1(16384)
                        if not chunk:
                            return result.decode("utf-8", errors="replace")
                        result.extend(chunk)
                        if len(result) > HTTP_LIMIT:
                            raise SmokeError("http_response_limit", "application_failure")
            except TimeoutError:
                raise SmokeError("http_request_timeout", "timeout") from None
            except urllib.error.URLError as error:
                if isinstance(error.reason, TimeoutError):
                    raise SmokeError("http_request_timeout", "timeout") from None
                remaining(deadline, self.clock)
                raise SmokeError("frontend_connection_failed", "application_failure") from None
            except OSError:
                remaining(deadline, self.clock)
                raise SmokeError("frontend_connection_failed", "application_failure") from None
        raise SmokeError("http_redirect_limit", "application_failure")
