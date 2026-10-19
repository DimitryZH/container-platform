"""Bounded public Docker Hub metadata inspection. Never fetch image layers."""

from dataclasses import dataclass, field
import hashlib
import http.client
import re
import socket
import ssl
import time
from urllib import error, parse, request

from .validation import Failure, diagnostic, strict_json


REGISTRY = "https://registry-1.docker.io"
AUTH = "https://auth.docker.io/token"
SERVICE = "registry.docker.io"
# Exact pull CDN from Docker's published allowlist; no suffix/wildcard matching.
CDN = "production.cloudfront.docker.com"
MANIFESTS = frozenset((
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
))
INDEXES = frozenset((
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
))
CONFIGS = frozenset((
    "application/vnd.oci.image.config.v1+json",
    "application/vnd.docker.container.image.v1+json",
))
ACCEPT = ", ".join(sorted(MANIFESTS | INDEXES))
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
REPOSITORY = re.compile(r"docker\.io/[a-z0-9]+(?:[._-][a-z0-9]+)*/[a-z0-9]+(?:[._-][a-z0-9]+)*")
PLATFORM_PART = re.compile(r"[a-z0-9]+")
MAX_BYTES = {"token": 32 * 1024, "manifest": 2 * 1024 * 1024, "config": 1024 * 1024}
TIMEOUT = 10
MAX_REDIRECTS = 2
MAX_ATTEMPTS = 2
MAX_RETRY_AFTER = 1
MAX_COMPONENT_REQUESTS = 24
MAX_RUN_REQUESTS = 256
COMPONENT_SECONDS = 45
RUN_SECONDS = 180

MESSAGES = {
    "registry_artifact_missing": "Manifest artifact was not found.",
    "registry_config_missing": "Configuration blob was not found.",
    "registry_auth_failed": "Public registry authentication failed.",
    "registry_rate_limited": "Registry rate limit prevented verification within retry bounds.",
    "registry_server_error": "Registry service did not succeed within retry bounds.",
    "registry_http_error": "Registry returned an unsupported HTTP status.",
    "registry_network_error": "Registry network request failed within retry bounds.",
    "registry_tls_error": "TLS certificate/connection validation failed.",
    "registry_timeout": "Registry request exceeded its timeout/deadline.",
    "registry_response_too_large": "Metadata response exceeds its byte limit.",
    "registry_malformed_response": "Metadata response is not supported strict JSON or has invalid structure.",
    "registry_digest_mismatch": "Raw response bytes or digest header differ from the expected SHA-256 digest.",
    "registry_size_mismatch": "Response byte length differs from the descriptor size.",
    "registry_unsupported_media_type": "Metadata media type is unsupported or inconsistent.",
    "registry_kind_mismatch": "Actual manifest/index kind differs from the candidate.",
    "registry_platform_mismatch": "Config/descriptor platform differs from the required platform.",
    "registry_platform_missing": "Index has no child for a required platform.",
    "registry_platform_ambiguous": "Index has multiple children for a required platform.",
    "registry_unsafe_challenge": "Bearer challenge is outside the permitted public Docker Hub flow.",
    "registry_unsafe_redirect": "Redirect destination is outside the permitted HTTPS metadata flow.",
    "registry_limits_exhausted": "Request, redirect or elapsed-time budget is exhausted.",
}
EXECUTION_CODES = frozenset((
    "registry_auth_failed", "registry_rate_limited", "registry_server_error",
    "registry_http_error", "registry_network_error", "registry_tls_error",
    "registry_timeout", "registry_limits_exhausted",
))


class RegistryFailure(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(MESSAGES[code])


@dataclass
class Response:
    status: int
    headers: dict
    body: bytes = b""


@dataclass
class RunBudget:
    requests: int = 0
    started: float = field(default_factory=time.monotonic)


@dataclass
class ComponentBudget:
    run: RunBudget
    requests: int = 0
    started: float = field(default_factory=time.monotonic)

    def remaining(self):
        left = min(COMPONENT_SECONDS - (time.monotonic() - self.started),
                   RUN_SECONDS - (time.monotonic() - self.run.started))
        if left <= 0:
            raise RegistryFailure("registry_limits_exhausted")
        return left

    def consume(self):
        self.remaining()
        if self.requests >= MAX_COMPONENT_REQUESTS or self.run.requests >= MAX_RUN_REQUESTS:
            raise RegistryFailure("registry_limits_exhausted")
        self.requests += 1
        self.run.requests += 1


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def checked_url(url):
    try:
        parts = parse.urlsplit(url)
        if (parts.scheme != "https" or parts.port not in (None, 443)
                or parts.username is not None or parts.password is not None or parts.fragment
                or any(ord(char) <= 32 or ord(char) >= 127 for char in url)):
            raise ValueError
        if parts.hostname not in ("registry-1.docker.io", "auth.docker.io", CDN):
            raise ValueError
        return parts
    except ValueError:
        raise RegistryFailure("registry_unsafe_redirect") from None


def redirect_url(current, location, blob_path, hops):
    if hops >= MAX_REDIRECTS:
        raise RegistryFailure("registry_limits_exhausted")
    if not isinstance(location, str) or any(ord(char) <= 32 or ord(char) >= 127 for char in location):
        raise RegistryFailure("registry_unsafe_redirect")
    target = parse.urljoin(current, location)
    previous, destination = checked_url(current), checked_url(target)
    if destination.hostname == "registry-1.docker.io":
        allowed = previous.hostname == destination.hostname and destination.path == blob_path
    else:
        allowed = (destination.hostname == CDN and
                   destination.path.startswith("/registry-v2/docker/registry/v2/blobs/sha256/"))
    if not allowed:
        raise RegistryFailure("registry_unsafe_redirect")
    return target


class Transport:
    def __init__(self):
        # No default redirect, proxy credentials, netrc, or Docker credential stores.
        self.opener = request.build_opener(
            request.ProxyHandler({}), NoRedirect(),
            request.HTTPSHandler(context=ssl.create_default_context()),
        )

    def exchange(self, url, headers, limit, budget):
        deadline = time.monotonic() + min(TIMEOUT, budget.remaining())
        req = request.Request(url, headers=headers, method="GET")
        try:
            stream = self.opener.open(req, timeout=min(TIMEOUT, budget.remaining()))
        except error.HTTPError as response:
            stream = response
        with stream:
            response_headers = {}
            for key, value in stream.headers.items():
                key = key.lower()
                if key in response_headers:
                    if key in ("content-type", "content-encoding", "docker-content-digest", "content-length", "www-authenticate", "location"):
                        raise RegistryFailure("registry_malformed_response")
                response_headers[key] = value
            if stream.code != 200:
                return Response(stream.code, response_headers)
            length = response_headers.get("content-length")
            if length is not None:
                if not re.fullmatch(r"[0-9]+", length):
                    raise RegistryFailure("registry_malformed_response")
                if len(length) > 10 or int(length) > limit:
                    raise RegistryFailure("registry_response_too_large")
            chunks = bytearray()
            while True:
                if time.monotonic() >= deadline:
                    raise RegistryFailure("registry_timeout")
                chunk = stream.read1(min(65536, limit + 1 - len(chunks)))
                if time.monotonic() >= deadline:
                    raise RegistryFailure("registry_timeout")
                if not chunk:
                    break
                chunks.extend(chunk)
                if len(chunks) > limit:
                    raise RegistryFailure("registry_response_too_large")
            budget.remaining()
            if length is not None and len(chunks) != int(length):
                raise RegistryFailure("registry_malformed_response")
            encoding = response_headers.get("content-encoding", "identity").lower()
            if encoding != "identity":
                raise RegistryFailure("registry_malformed_response")
            return Response(200, response_headers, bytes(chunks))

    def get(self, url, headers, kind, budget, blob_path=None):
        initial = checked_url(url)
        if ((kind == "token" and (initial.hostname != "auth.docker.io" or initial.path != "/token"))
                or (kind != "token" and initial.hostname != "registry-1.docker.io")):
            raise RegistryFailure("registry_unsafe_redirect")
        headers = {**headers, "Accept-Encoding": "identity", "User-Agent": "container-platform/0.1"}
        hops = 0
        while True:
            for attempt in range(MAX_ATTEMPTS):
                budget.consume()
                try:
                    response = self.exchange(url, headers, MAX_BYTES[kind], budget)
                except (ssl.SSLError, error.URLError, OSError, http.client.HTTPException) as exc:
                    reason = exc.reason if isinstance(exc, error.URLError) else exc
                    if isinstance(reason, ssl.SSLError):
                        raise RegistryFailure("registry_tls_error") from None
                    code = "registry_timeout" if isinstance(reason, (TimeoutError, socket.timeout)) else "registry_network_error"
                    if attempt + 1 == MAX_ATTEMPTS:
                        raise RegistryFailure(code) from None
                    continue
                # Enforce bounds even for injected/mock transports.
                if len(response.body) > MAX_BYTES[kind]:
                    raise RegistryFailure("registry_response_too_large")
                if response.status not in (429, 500, 502, 503, 504):
                    break
                code = "registry_rate_limited" if response.status == 429 else "registry_server_error"
                delay = response.headers.get("retry-after", "0")
                # HTTP-date and nonnumeric values fail immediately; never guess a long wait.
                if not re.fullmatch(r"[0-9]{1,3}", delay) or int(delay) > MAX_RETRY_AFTER or attempt + 1 == MAX_ATTEMPTS:
                    raise RegistryFailure(code)
                if int(delay):
                    if budget.remaining() <= int(delay):
                        raise RegistryFailure("registry_limits_exhausted")
                    time.sleep(int(delay))
            if response.status not in (301, 302, 303, 307, 308):
                return response
            if kind != "config" or blob_path is None or not response.headers.get("location"):
                raise RegistryFailure("registry_unsafe_redirect")
            new_url = redirect_url(url, response.headers["location"], blob_path, hops)
            if parse.urlsplit(url).netloc != parse.urlsplit(new_url).netloc:
                headers = {key: value for key, value in headers.items() if key.lower() != "authorization"}
            url = new_url
            hops += 1


def bearer_challenge(value, repository):
    if not isinstance(value, str) or not value.lower().startswith("bearer "):
        raise RegistryFailure("registry_unsafe_challenge")
    rest, parameters = value[7:], {}
    while rest:
        match = re.match(r'\s*([A-Za-z]+)="([^"\\\x00-\x20\x7f]+)"(?:,\s*|$)', rest)
        if not match or match.group(1).lower() in parameters:
            raise RegistryFailure("registry_unsafe_challenge")
        parameters[match.group(1).lower()] = match.group(2)
        rest = rest[match.end():]
    if parameters != {"realm": AUTH, "service": SERVICE, "scope": f"repository:{repository}:pull"}:
        raise RegistryFailure("registry_unsafe_challenge")


def response_json(body):
    try:
        result = strict_json(body)
    except Failure:
        raise RegistryFailure("registry_malformed_response") from None
    if not isinstance(result, dict):
        raise RegistryFailure("registry_malformed_response")
    return result


def media_type(value):
    return value.split(";", 1)[0].strip().lower() if isinstance(value, str) else None


def require_status(response, kind):
    if response.status == 200:
        return
    code = {401: "registry_auth_failed", 403: "registry_auth_failed", 429: "registry_rate_limited"}.get(response.status)
    if response.status == 404:
        code = "registry_config_missing" if kind == "config" else "registry_auth_failed" if kind == "token" else "registry_artifact_missing"
    raise RegistryFailure(code or ("registry_server_error" if response.status >= 500 else "registry_http_error"))


class HubClient:
    def __init__(self, repository, transport, budget):
        if not REPOSITORY.fullmatch(repository):
            raise RegistryFailure("registry_malformed_response")
        self.repository = repository.removeprefix("docker.io/")
        self.transport, self.budget, self.token = transport, budget, None

    def get(self, path, kind):
        headers = {"Accept": ACCEPT if kind == "manifest" else "application/octet-stream"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        response = self.transport.get(REGISTRY + path, headers, kind, self.budget, path if kind == "config" else None)
        if response.status == 401:
            # A previously granted token is not refreshed indefinitely.
            if self.token:
                raise RegistryFailure("registry_auth_failed")
            bearer_challenge(response.headers.get("www-authenticate"), self.repository)
            query = parse.urlencode({"service": SERVICE, "scope": f"repository:{self.repository}:pull"})
            token_response = self.transport.get(AUTH + "?" + query, {"Accept": "application/json"}, "token", self.budget)
            require_status(token_response, "token")
            if media_type(token_response.headers.get("content-type", "application/json")) != "application/json":
                raise RegistryFailure("registry_malformed_response")
            token_data = response_json(token_response.body)
            token = token_data.get("token", token_data.get("access_token"))
            if (not isinstance(token, str) or len(token) > 16384
                    or not re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", token)
                    or ("token" in token_data and "access_token" in token_data and token_data["token"] != token_data["access_token"])):
                raise RegistryFailure("registry_auth_failed")
            self.token = token
            headers["Authorization"] = "Bearer " + token
            response = self.transport.get(REGISTRY + path, headers, kind, self.budget, path if kind == "config" else None)
        require_status(response, kind)
        return response

    def object(self, digest, kind, descriptor=None):
        if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
            raise RegistryFailure("registry_malformed_response")
        if descriptor is not None and descriptor["size"] > MAX_BYTES[kind]:
            raise RegistryFailure("registry_response_too_large")
        path = f"/v2/{self.repository}/{'manifests' if kind == 'manifest' else 'blobs'}/{digest}"
        response = self.get(path, kind)
        if "sha256:" + hashlib.sha256(response.body).hexdigest() != digest:
            raise RegistryFailure("registry_digest_mismatch")
        header_digest = response.headers.get("docker-content-digest")
        if header_digest is not None and header_digest != digest:
            raise RegistryFailure("registry_digest_mismatch")
        if descriptor is not None and len(response.body) != descriptor["size"]:
            raise RegistryFailure("registry_size_mismatch")
        body = response_json(response.body)
        response_type = media_type(response.headers.get("content-type"))
        if kind == "config":
            if response_type not in (None, "application/octet-stream", descriptor["mediaType"]):
                raise RegistryFailure("registry_unsupported_media_type")
            return body, None
        body_type = body.get("mediaType")
        if "mediaType" in body and (not isinstance(body_type, str) or body_type not in MANIFESTS | INDEXES):
            raise RegistryFailure("registry_unsupported_media_type")
        if response_type is not None and response_type not in MANIFESTS | INDEXES:
            raise RegistryFailure("registry_unsupported_media_type")
        if body_type and response_type and body_type != response_type:
            raise RegistryFailure("registry_unsupported_media_type")
        actual_type = body_type or response_type
        if actual_type is None or (descriptor and actual_type != descriptor["mediaType"]):
            raise RegistryFailure("registry_unsupported_media_type")
        if type(body.get("schemaVersion")) is not int or body["schemaVersion"] != 2:
            raise RegistryFailure("registry_malformed_response")
        return body, actual_type


def descriptor(value, allowed_types=None):
    if (not isinstance(value, dict) or not isinstance(value.get("digest"), str)
            or not DIGEST.fullmatch(value["digest"]) or type(value.get("size")) is not int or value["size"] < 0
            or not isinstance(value.get("mediaType"), str) or not value["mediaType"]):
        raise RegistryFailure("registry_malformed_response")
    if allowed_types is not None and value["mediaType"] not in allowed_types:
        raise RegistryFailure("registry_unsupported_media_type")
    # Descriptor URLs are never consulted, including foreign/external URLs.
    return value


def platform(value):
    if not isinstance(value, dict):
        raise RegistryFailure("registry_malformed_response")
    parts = [value.get("os"), value.get("architecture")]
    if "variant" in value:
        parts.append(value["variant"])
    if any(not isinstance(part, str) or not PLATFORM_PART.fullmatch(part) for part in parts):
        raise RegistryFailure("registry_malformed_response")
    return "/".join(parts)


def check_manifest(client, manifest, required_platform):
    config_descriptor = descriptor(manifest.get("config"), CONFIGS)
    layers = manifest.get("layers")
    if not isinstance(layers, list):
        raise RegistryFailure("registry_malformed_response")
    for layer in layers:
        descriptor(layer)  # Validate shape only; never fetch layers or descriptor URLs.
    config, _ = client.object(config_descriptor["digest"], "config", config_descriptor)
    if platform(config) != required_platform:
        raise RegistryFailure("registry_platform_mismatch")


def inspect_image(image, client):
    manifest, actual_type = client.object(image["digest"], "manifest")
    actual_kind = "index" if actual_type in INDEXES else "manifest"
    if actual_kind != image["kind"]:
        raise RegistryFailure("registry_kind_mismatch")
    if actual_kind == "manifest":
        check_manifest(client, manifest, image["platforms"][0])
        return
    children = manifest.get("manifests")
    if not isinstance(children, list):
        raise RegistryFailure("registry_malformed_response")
    for child in children:
        descriptor(child)  # Unselected attestation/artifact types need not be images.
    for target in sorted(image["platforms"]):
        target_parts = target.split("/")
        matches = []
        for child in children:
            # Missing/unknown-platform attestations are irrelevant to required selection.
            child_platform = child.get("platform")
            if child_platform is None:
                continue
            if not isinstance(child_platform, dict):
                raise RegistryFailure("registry_malformed_response")
            if (child_platform.get("os") == target_parts[0]
                    and child_platform.get("architecture") == target_parts[1]
                    and child_platform.get("variant") == (target_parts[2] if len(target_parts) == 3 else None)):
                matches.append(child)
        if not matches:
            raise RegistryFailure("registry_platform_missing")
        if len(matches) != 1:
            raise RegistryFailure("registry_platform_ambiguous")
        selected = descriptor(matches[0], MANIFESTS)  # Nested indexes are not supported.
        child_body, _ = client.object(selected["digest"], "manifest", selected)
        check_manifest(client, child_body, target)


def inspect_components(candidate):
    transport, run = None, RunBudget()
    results, errors, execution_error = [], [], False
    for index, component in enumerate(candidate["components"]):
        try:
            if transport is None:
                try:
                    transport = Transport()
                except OSError:
                    raise RegistryFailure("registry_tls_error") from None
            client = HubClient(component["image"]["repository"], transport, ComponentBudget(run))
            inspect_image(component["image"], client)
            results.append({"componentIndex": index, "status": "pass"})
        except RegistryFailure as failure:
            results.append({"componentIndex": index, "status": "fail"})
            errors.append(diagnostic(failure.code, str(failure), f"$.components[{index}].image"))
            execution_error |= failure.code in EXECUTION_CODES
    return results, errors, execution_error
