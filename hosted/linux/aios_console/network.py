"""Bounded DNS and HTTP(S) operations for the AIOS userspace console.

The worker is a separate, owned process so the deadline includes blocking
system DNS, connection, TLS, and response reads. OS process creation and
termination scheduling can add overhead; this is not a real-time guarantee.
No shell, proxy auto-discovery, redirect following, or certificate bypass is
provided. The result describes actual user-requested network I/O, not a
canonical Kernel Room binding or a resource-policy action.
"""
from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import socket
import ssl
import subprocess
import sys
import time
import unicodedata
from urllib.parse import quote, urlsplit


MAX_URL_BYTES = 2048
MAX_BODY_BYTES = 65536
MAX_PREVIEW_BYTES = 2048
MAX_ADDRESSES = 32
MAX_WORKER_BYTES = 32768
_HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", re.I)
_ENCODED_CONTROL = re.compile(r"%(?:0[0-9a-f]|1[0-9a-f]|7f)", re.I)
_BAD_PERCENT = re.compile(r"%(?![0-9a-f]{2})", re.I)
_ERRORS = {"invalid_host", "invalid_url", "unsupported_protocol", "invalid_timeout",
           "invalid_max_bytes", "dns_failed", "timeout", "tls_certificate", "tls_failed",
           "connection_failed", "http_protocol", "http_status", "worker_failed"}


def _safe_text(value: str, limit: int) -> str:
    text = "".join(c if c.isprintable() and unicodedata.category(c)[0] != "C"
                   else " " for c in value)
    return text.encode("utf-8", errors="replace")[:limit].decode("utf-8", errors="ignore")


def _host(value: object) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 253:
        return None
    if any(c.isspace() or not c.isprintable() or unicodedata.category(c)[0] == "C"
           for c in value) or any(c in value for c in "/\\@%[]?#"):
        return None
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        pass
    try:
        ascii_host = value.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None
    labels = (ascii_host[:-1] if ascii_host.endswith(".") else ascii_host).split(".")
    if len(ascii_host) > 253 or any(not _HOST_LABEL.fullmatch(part) for part in labels):
        return None
    return ascii_host


def _url(value: object) -> tuple[object | None, str | None]:
    if not isinstance(value, str) or not value or len(value) > MAX_URL_BYTES:
        return None, "invalid_url"
    try:
        if len(value.encode("utf-8")) > MAX_URL_BYTES:
            return None, "invalid_url"
    except UnicodeError:
        return None, "invalid_url"
    if any(c.isspace() or not c.isprintable() or unicodedata.category(c)[0] == "C"
           for c in value) or "\\" in value or _ENCODED_CONTROL.search(value):
        return None, "invalid_url"
    if _BAD_PERCENT.search(value):
        return None, "invalid_url"
    try:
        parsed = urlsplit(value)
        # Reject before reporting unsupported schemes so credentials are never echoed.
        if parsed.username is not None or parsed.password is not None:
            return None, "invalid_url"
        if parsed.scheme not in {"http", "https"}:
            return None, "unsupported_protocol"
        if not parsed.netloc or parsed.fragment or _host(parsed.hostname) is None:
            return None, "invalid_url"
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            return None, "invalid_url"
        if parsed.netloc.endswith(":"):
            return None, "invalid_url"
        return parsed, None
    except (UnicodeError, ValueError):
        return None, "invalid_url"


def _timeout_valid(value: object) -> bool:
    return (type(value) in {float, int} and 0.1 <= value <= 30.0
            and math.isfinite(value))


def _base(operation: str, target: str) -> dict:
    result = {"operation": operation, "outcome": "ERROR", "error": None, "elapsed_ms": 0}
    if operation == "resolve":
        result.update(host=target, addresses=[])
    else:
        result.update(url=target, status=None, protocol=None, tls_verified=False,
                      received_bytes=0, truncated=False, content_type=None,
                      body_preview="", body_sha256=None)
    return result


def _error(result: dict, code: str) -> dict:
    result.update(outcome="ERROR", error=code)
    return result


def _unique_object(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


def _resolve_worker(host: str, timeout: float) -> dict:
    result = _base("resolve", host)
    try:
        infos = socket.getaddrinfo(host, None, family=socket.AF_UNSPEC,
                                   type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
        addresses = []
        for family, _kind, _protocol, _canonical, sockaddr in infos:
            if family not in {socket.AF_INET, socket.AF_INET6}:
                continue
            address = str(ipaddress.ip_address(sockaddr[0]))
            if address not in addresses:
                addresses.append(address)
            if len(addresses) == MAX_ADDRESSES:
                break
        if not addresses:
            return _error(result, "dns_failed")
        result.update(outcome="OK", error=None, addresses=addresses)
    except socket.gaierror:
        return _error(result, "dns_failed")
    except (TimeoutError, socket.timeout):
        return _error(result, "timeout")
    except (OSError, ValueError):
        return _error(result, "dns_failed")
    return result


def _fetch_worker(url: str, timeout: float, max_bytes: int) -> dict:
    result = _base("fetch", url)
    parsed, invalid = _url(url)
    if invalid:
        result["url"] = "<invalid>"
        return _error(result, invalid)
    result["protocol"] = parsed.scheme
    connection = None
    try:
        hostname = _host(parsed.hostname)
        target = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
        if parsed.query:
            target += "?" + quote(parsed.query, safe="/%?:@!$&'()*+,;=-._~")
        if parsed.scheme == "https":
            context = ssl.create_default_context()
            if not context.check_hostname or context.verify_mode != ssl.CERT_REQUIRED:
                return _error(result, "tls_failed")
            connection = http.client.HTTPSConnection(hostname, parsed.port,
                                                     timeout=timeout, context=context)
        else:
            connection = http.client.HTTPConnection(hostname, parsed.port, timeout=timeout)
        connection.connect()
        result["tls_verified"] = parsed.scheme == "https"
        connection.request("GET", target, headers={"User-Agent": "AIOS-Console/0.1",
                                                   "Accept": "*/*", "Connection": "close"})
        response = connection.getresponse()
        if type(response.status) is not int or not 100 <= response.status <= 599:
            return _error(result, "http_protocol")
        result["status"] = response.status
        content_type = response.getheader("Content-Type")
        if content_type is not None:
            result["content_type"] = _safe_text(content_type, 256)
        expected_length = response.length
        body = response.read(max_bytes + 1)
        # read(amt) may return short data without raising IncompleteRead even when
        # Content-Length promised more. A short fixed-length response is not OK.
        if expected_length is not None and len(body) < min(expected_length, max_bytes + 1):
            return _error(result, "http_protocol")
        truncated = len(body) > max_bytes
        body = body[:max_bytes]
        result.update(received_bytes=len(body), truncated=truncated,
                      body_preview=_safe_text(body.decode("utf-8", errors="replace"),
                                              MAX_PREVIEW_BYTES),
                      body_sha256=hashlib.sha256(body).hexdigest())
        if not 200 <= response.status < 300:
            return _error(result, "http_status")
        result.update(outcome="OK", error=None)
        return result
    except ssl.SSLCertVerificationError:
        return _error(result, "tls_certificate")
    except ssl.SSLError:
        return _error(result, "tls_failed")
    except socket.gaierror:
        return _error(result, "dns_failed")
    except (TimeoutError, socket.timeout):
        return _error(result, "timeout")
    except (http.client.HTTPException, UnicodeError, ValueError):
        return _error(result, "http_protocol")
    except OSError:
        return _error(result, "connection_failed")
    finally:
        if connection is not None:
            connection.close()


def _valid_result(result: object, expected: dict, max_bytes: int) -> bool:
    if not isinstance(result, dict) or set(result) != set(expected):
        return False
    if (result["operation"] != expected["operation"]
            or result["outcome"] not in {"OK", "ERROR"}
            or type(result["elapsed_ms"]) is not int or result["elapsed_ms"] < 0):
        return False
    if ((result["outcome"] == "OK" and result["error"] is not None)
            or (result["outcome"] == "ERROR" and result["error"] not in _ERRORS)):
        return False
    if expected["operation"] == "resolve":
        addresses = result["addresses"]
        if (result["host"] != expected["host"] or not isinstance(addresses, list)
                or len(addresses) > MAX_ADDRESSES):
            return False
        try:
            if any(type(a) is not str or str(ipaddress.ip_address(a)) != a for a in addresses):
                return False
        except ValueError:
            return False
        return (len(set(addresses)) == len(addresses)
                and bool(addresses) == (result["outcome"] == "OK"))
    if (result["url"] != expected["url"] or result["protocol"] != expected["protocol"]
            or type(result["tls_verified"]) is not bool
            or type(result["received_bytes"]) is not int
            or not 0 <= result["received_bytes"] <= max_bytes
            or type(result["truncated"]) is not bool):
        return False
    if (result["protocol"] == "http" and result["tls_verified"]
            or result["truncated"] and result["received_bytes"] != max_bytes):
        return False
    for key, limit in (("body_preview", MAX_PREVIEW_BYTES), ("content_type", 256)):
        value = result[key]
        if key == "content_type" and value is None:
            continue
        if type(value) is not str or _safe_text(value, limit) != value:
            return False
    status, digest = result["status"], result["body_sha256"]
    if status is not None and (type(status) is not int or not 100 <= status <= 599):
        return False
    if digest is not None and (type(digest) is not str or not re.fullmatch(r"[0-9a-f]{64}", digest)):
        return False
    if digest is None and (result["received_bytes"] or result["body_preview"] or result["truncated"]):
        return False
    if result["outcome"] == "OK":
        return (type(status) is int and 200 <= status < 300 and digest is not None
                and (result["protocol"] != "https" or result["tls_verified"]))
    return result["error"] != "http_status" or (type(status) is int and not 200 <= status < 300
                                              and digest is not None)


def _run_worker(request: dict, result: dict) -> dict:
    environment = os.environ.copy()
    # create_default_context otherwise enables file-based key logging implicitly.
    environment.pop("SSLKEYLOGFILE", None)
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    try:
        process = subprocess.run([sys.executable, "-I", str(Path(__file__).resolve()),
                                  "--network-worker"],
                                 input=json.dumps(request, ensure_ascii=False).encode("utf-8"),
                                 capture_output=True, timeout=request["timeout"],
                                 env=environment, check=False, **options)
        if (process.returncode != 0 or process.stderr
                or len(process.stdout) > MAX_WORKER_BYTES):
            return _error(result, "worker_failed")
        payload = json.loads(process.stdout, object_pairs_hook=_unique_object)
        if not _valid_result(payload, result, request.get("max_bytes", MAX_BODY_BYTES)):
            return _error(result, "worker_failed")
        return payload
    except subprocess.TimeoutExpired:
        return _error(result, "timeout")
    except (OSError, ValueError, TypeError):
        return _error(result, "worker_failed")


def resolve_host(host: str, *, timeout: float = 5.0) -> dict:
    """Resolve up to 32 unique IP addresses with a process-bounded DNS deadline."""
    start = time.monotonic_ns()
    normal = _host(host)
    result = _base("resolve", normal if normal is not None else "<invalid>")
    if normal is None:
        _error(result, "invalid_host")
    elif not _timeout_valid(timeout):
        _error(result, "invalid_timeout")
    else:
        result = _run_worker({"operation": "resolve", "host": normal,
                              "timeout": timeout}, result)
    result["elapsed_ms"] = max(0, (time.monotonic_ns() - start) // 1_000_000)
    return result


def fetch_url(url: str, *, timeout: float = 8.0, max_bytes: int = 16384) -> dict:
    """GET one HTTP(S) URL; hash retained bytes and sanitize a bounded preview.

    body_sha256 covers the first received_bytes bytes, never the whole resource
    when truncated is true. Redirects are reported as http_status errors.
    """
    start = time.monotonic_ns()
    parsed, invalid = _url(url)
    result = _base("fetch", url if parsed is not None else "<invalid>")
    if invalid:
        _error(result, invalid)
    else:
        result["protocol"] = parsed.scheme
        if not _timeout_valid(timeout):
            _error(result, "invalid_timeout")
        elif type(max_bytes) is not int or not 1 <= max_bytes <= MAX_BODY_BYTES:
            _error(result, "invalid_max_bytes")
        else:
            result = _run_worker({"operation": "fetch", "url": url, "timeout": timeout,
                                  "max_bytes": max_bytes}, result)
    result["elapsed_ms"] = max(0, (time.monotonic_ns() - start) // 1_000_000)
    return result


def _worker_main() -> int:
    try:
        raw = sys.stdin.buffer.read(8193)
        if len(raw) > 8192:
            return 2
        request = json.loads(raw, object_pairs_hook=_unique_object)
        if not isinstance(request, dict) or not _timeout_valid(request.get("timeout")):
            return 2
        if (request.get("operation") == "resolve"
                and set(request) == {"operation", "host", "timeout"}
                and _host(request["host"]) == request["host"]):
            result = _resolve_worker(request["host"], request["timeout"])
        elif (request.get("operation") == "fetch"
              and set(request) == {"operation", "url", "timeout", "max_bytes"}
              and type(request["max_bytes"]) is int
              and 1 <= request["max_bytes"] <= MAX_BODY_BYTES
              and _url(request["url"])[1] is None):
            result = _fetch_worker(request["url"], request["timeout"], request["max_bytes"])
        else:
            return 2
        output = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(output) > MAX_WORKER_BYTES:
            return 2
        sys.stdout.buffer.write(output)
        sys.stdout.buffer.flush()
        return 0
    except Exception:
        # Unexpected internal failures cross the boundary only as worker_failed.
        return 2


if __name__ == "__main__":
    raise SystemExit(_worker_main() if sys.argv[1:] == ["--network-worker"] else 2)
