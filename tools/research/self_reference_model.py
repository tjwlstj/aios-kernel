"""RESEARCH: an owned native-host model process, not an AIOS product backend.

Every query uses a fresh prompt, deterministic parameters, and retained raw bytes.
Invalid transport, context overflow, and incomplete output invalidate the experiment;
they are never repaired or retried. This module does not interpret model content.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

from self_reference_grammar import DECISION_GRAMMAR


MODEL_NAME = "Qwen3-0.6B-Q8_0.gguf"
MODEL_SHA256 = "9465e63a22add5354d9bb4b99e90117043c7124007664907259bd16d043bb031"
BACKEND_NAME = "llamafile-0.10.5-thin.exe"
BACKEND_SHA256 = "55c69c1be9d6ad2172e2d1c0acc677a60ea8ff60232009a8c8170f5bcb917611"
MODEL_ID = "aios-qwen3-0.6b-q8_0"
STARTUP_SECONDS = 60.0
REQUEST_SECONDS = 120.0
STOP_SECONDS = 5.0
MAX_REQUEST_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024
IS_WINDOWS = os.name == "nt"


class ModelError(RuntimeError):
    """An invalid experiment; artifacts remain available for diagnosis."""

    def __init__(self, code: str, detail: str = "", *, artifact_dir: Path | None = None,
                 raw_response: bytes = b""):
        super().__init__(code + (": " + detail if detail else ""))
        self.code = code
        self.artifact_dir = artifact_dir
        self.raw_response = raw_response


def _encoded(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def _save(path: Path, value: object) -> None:
    path.write_bytes(_encoded(value) + b"\n")


def _file_record(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ModelError("missing_or_symlink_file", str(path))
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (
            after.st_size, after.st_mtime_ns, after.st_ino):
        raise ModelError("file_changed_while_hashing", str(path))
    return {"path": str(path), "bytes": after.st_size, "sha256": digest.hexdigest()}


def _decoded(raw: bytes) -> dict:
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("duplicate JSON key")
            value[key] = item
        return value

    def constant(value):
        raise ValueError("non-finite JSON number: " + value)

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                           parse_constant=constant)
        if type(value) is not dict:
            raise ValueError("JSON object required")
        pending, nodes = [(value, 0)], 0
        while pending:
            item, depth = pending.pop()
            nodes += 1
            if depth > 32 or nodes > 20000:
                raise ValueError("JSON complexity limit")
            if isinstance(item, dict):
                pending.extend((child, depth + 1) for child in item.values())
            elif isinstance(item, list):
                pending.extend((child, depth + 1) for child in item)
        return value
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ModelError("malformed_json", str(exc), raw_response=raw) from exc


class LocalModel:
    """Single-use context manager. ``artifacts`` must be a new directory.

    ``query`` returns content, tokens (prompt/predicted/tokenized_prompt),
    elapsed_seconds, request_sha256, raw_response, raw_request_path,
    raw_response_path and query_artifact_dir. Any failed query closes the process.
    Startup readiness polling is the only retry loop; inference is never retried.
    """

    def __init__(self, cache: Path, artifacts: Path, *, context_size: int = 2048,
                 max_tokens: int = 192, seed: int = 1):
        for name, value, low, high in (("context_size", context_size, 256, 8192),
                                       ("max_tokens", max_tokens, 1, 512),
                                       ("seed", seed, 0, 2147483647)):
            if type(value) is not int or not low <= value <= high:
                raise ModelError("invalid_" + name)
        if max_tokens >= context_size:
            raise ModelError("invalid_token_budget")
        self.cache, self.artifacts = Path(cache).resolve(), Path(artifacts).resolve()
        self.context_size, self.max_tokens, self.seed = context_size, max_tokens, seed
        self.process = None
        self._streams = []
        self._started = False
        self._artifacts_created = False
        self._ready = False
        self._closed = False
        self._query_number = 0
        self._query_lock = threading.Lock()
        self.port = None

    def __enter__(self):
        if self._started or self._closed:
            raise ModelError("single_use_context")
        self._started = True
        try:
            self.artifacts.mkdir(parents=True, exist_ok=False)
            self._artifacts_created = True
        except FileExistsError as exc:
            raise ModelError("artifacts_exist", artifact_dir=self.artifacts) from exc
        except OSError as exc:
            raise ModelError("artifact_directory", str(exc), artifact_dir=self.artifacts) from exc
        started = time.monotonic()
        try:
            files = {}
            for name, size, expected in ((MODEL_NAME, 639446688, MODEL_SHA256),
                                         (BACKEND_NAME, 42328074, BACKEND_SHA256)):
                record = _file_record(self.cache / name)
                files[name] = record
                _save(self.artifacts / "file-checks.json", files)
                if record["bytes"] != size or record["sha256"] != expected:
                    raise ModelError("artifact_pin_mismatch", name)
            provenance = {"maturity": "RESEARCH", "product_backend": False,
                          "platform": {"os_name": os.name, "sys_platform": sys.platform,
                                       "release": list(sys.getwindowsversion()) if hasattr(sys, "getwindowsversion")
                                       else list(os.uname())}, "python": sys.version,
                          "backend_version": "0.10.5", "version_basis": "pinned artifact SHA256",
                          "files": files, "wrapper": _file_record(Path(__file__)),
                          "created_at": datetime.now(timezone.utc).isoformat()}
            retained = self.cache / "provenance-receipt.json"
            provenance["cache_provenance_receipt"] = None
            if retained.is_file():
                raw = retained.read_bytes()
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise ModelError("provenance_receipt_limit")
                (self.artifacts / "cache-provenance-receipt.json").write_bytes(raw)
                provenance["cache_provenance_receipt"] = {
                    "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
            _save(self.artifacts / "provenance.json", provenance)
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
                if IS_WINDOWS and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    reservation.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                reservation.bind(("127.0.0.1", 0))
                self.port = reservation.getsockname()[1]
            command = ([str(self.cache / BACKEND_NAME)] if IS_WINDOWS else
                       ["/bin/sh", str(self.cache / BACKEND_NAME)]) + [
                "--server", "-m", str(self.cache / MODEL_NAME), "--gpu", "disable",
                "--host", "127.0.0.1", "--port", str(self.port), "--no-webui",
                "-c", str(self.context_size), "-b", "64", "-ub", "64", "-t", "2",
                "-np", "1", "--alias", MODEL_ID, "--nologo"]
            _save(self.artifacts / "command.json", {"command": command, "shell": False})
            for name in ("backend-stdout.log", "backend-stderr.log"):
                self._streams.append((self.artifacts / name).open("wb"))
            self.process = subprocess.Popen(
                command, stdin=subprocess.DEVNULL, stdout=self._streams[0], stderr=self._streams[1],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if IS_WINDOWS else 0)
            deadline = time.monotonic() + STARTUP_SECONDS
            attempts = 0
            while True:
                self._require_alive()
                if time.monotonic() >= deadline:
                    raise ModelError("startup_timeout")
                attempts += 1
                try:
                    status, raw = self._exchange("/health", None, min(deadline, time.monotonic() + 2), 65536)
                    if status == 200 and _decoded(raw).get("status") == "ok":
                        (self.artifacts / "health.json").write_bytes(raw)
                        break
                    if status != 503:
                        raise ModelError("health_response", str(status))
                except ModelError as exc:
                    if exc.code not in ("transport_error", "request_timeout"):
                        raise
                time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
            self._require_alive()
            _save(self.artifacts / "startup.json", {"outcome": "READY", "pid": self.process.pid,
                  "port": self.port, "attempts": attempts, "elapsed_seconds": time.monotonic() - started})
            self._ready = True
            return self
        except BaseException as exc:
            error = exc if isinstance(exc, ModelError) else ModelError("startup_error", str(exc))
            error.artifact_dir = self.artifacts
            try:
                _save(self.artifacts / "startup.json", {"outcome": "INVALID", "error": str(exc),
                      "elapsed_seconds": time.monotonic() - started})
            finally:
                self.close()
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise error from exc

    def _require_alive(self):
        if self.process is None or self.process.poll() is not None:
            raise ModelError("backend_exited")

    def _exchange(self, path: str, body: bytes | None, deadline: float, limit: int) -> tuple[int, bytes]:
        """A wall deadline closes the socket even during a slowly supplied HTTP header."""
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ModelError("request_timeout")
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=remaining)
        sockets, expired, raw = [], threading.Event(), bytearray()

        def abort():
            expired.set()
            for transport in sockets:
                try:
                    transport.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

        timer = threading.Timer(remaining, abort)
        timer.daemon = True
        timer.start()
        try:
            connection.connect()
            sockets.append(connection.sock)
            if expired.is_set():
                raise ModelError("request_timeout")
            connection.request("GET" if body is None else "POST", path, body=body,
                               headers={"Content-Type": "application/json", "Connection": "close"})
            response = connection.getresponse()
            try:
                while True:
                    if expired.is_set() or time.monotonic() >= deadline:
                        raise ModelError("request_timeout")
                    chunk = response.read1(min(65536, limit + 1 - len(raw)))
                    if not chunk:
                        break
                    raw.extend(chunk)
                    if len(raw) > limit:
                        raise ModelError("response_body_limit")
                if response.length not in (None, 0):
                    raise ModelError("incomplete_http_body")
                if expired.is_set() or time.monotonic() >= deadline:
                    raise ModelError("request_timeout")
                return response.status, bytes(raw)
            finally:
                response.close()
        except (OSError, http.client.HTTPException, ModelError) as exc:
            code = ("request_timeout" if expired.is_set() or time.monotonic() >= deadline else
                    exc.code if isinstance(exc, ModelError) else "transport_error")
            raise ModelError(code, str(exc), raw_response=bytes(raw)) from exc
        finally:
            timer.cancel()
            connection.close()

    def _json_exchange(self, folder: Path, name: str, endpoint: str, payload: bytes,
                       deadline: float) -> tuple[int, bytes]:
        (folder / (name + ".request.json")).write_bytes(payload)
        try:
            status, raw = self._exchange(endpoint, payload, deadline, MAX_RESPONSE_BYTES)
        except ModelError as exc:
            (folder / (name + ".response.json")).write_bytes(exc.raw_response)
            _save(folder / (name + ".http.json"), {"outcome": "INVALID", "error": exc.code})
            raise
        (folder / (name + ".response.json")).write_bytes(raw)
        _save(folder / (name + ".http.json"), {"status": status, "response_bytes": len(raw)})
        return status, raw

    def query(self, system: str, user: str, *, grammar: str | None = None) -> dict:
        """Optionally apply the one static decision grammar, never arbitrary GBNF."""
        if not self._ready or self._closed:
            raise ModelError("model_not_ready", artifact_dir=self.artifacts)
        if not self._query_lock.acquire(blocking=False):
            raise ModelError("concurrent_query", artifact_dir=self.artifacts)
        folder = None
        started = time.monotonic()
        try:
            if grammar is not None and (type(grammar) is not str or grammar != DECISION_GRAMMAR):
                raise ModelError("invalid_grammar")
            self._query_number += 1
            folder = self.artifacts / ("query-%04d" % self._query_number)
            folder.mkdir()
            self._require_alive()
            if any(type(value) is not str or not value.strip() or "\0" in value or
                   "<|im_start|>" in value or "<|im_end|>" in value for value in (system, user)):
                raise ModelError("invalid_prompt")
            if len(system) + len(user) > MAX_REQUEST_BYTES:
                raise ModelError("request_body_limit")
            prompt = ("<|im_start|>system\n" + system + "<|im_end|>\n<|im_start|>user\n" +
                      user + " /no_think<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n")
            request = {"prompt": prompt, "n_predict": self.max_tokens, "temperature": 0,
                       "seed": self.seed, "cache_prompt": False, "stream": False}
            if grammar is not None:
                request["grammar"] = grammar
            payload = _encoded(request)
            if len(payload) > MAX_REQUEST_BYTES:
                raise ModelError("request_body_limit")
            (folder / "completion.request.json").write_bytes(payload)
            deadline = started + REQUEST_SECONDS
            token_request = _encoded({"content": prompt, "add_special": True, "parse_special": True})
            status, raw = self._json_exchange(folder, "tokenize", "/tokenize", token_request, deadline)
            tokenized = None
            if status == 200:
                tokens = _decoded(raw).get("tokens")
                if type(tokens) is not list or not tokens or any(type(t) is not int or t < 0 for t in tokens):
                    raise ModelError("invalid_tokenizer_response")
                tokenized = len(tokens)
                if tokenized + self.max_tokens > self.context_size:
                    raise ModelError("prompt_token_overflow")
            elif status not in (404, 405, 501):
                raise ModelError("tokenizer_http_status", str(status))
            self._require_alive()
            status, raw = self._json_exchange(folder, "completion", "/completion", payload, deadline)
            if status != 200:
                raise ModelError("completion_http_status", str(status))
            value = _decoded(raw)
            self._require_alive()
            content, predicted, evaluated = value.get("content"), value.get("tokens_predicted"), value.get("tokens_evaluated")
            if (type(content) is not str or not content.strip() or type(predicted) is not int or
                    not 1 <= predicted <= self.max_tokens or type(evaluated) is not int or evaluated < 1):
                raise ModelError("invalid_completion")
            if value.get("model") != MODEL_ID or value.get("prompt") != prompt:
                raise ModelError("completion_identity_or_prompt")
            if value.get("truncated") is not False or evaluated + self.max_tokens > self.context_size:
                raise ModelError("context_truncated_or_overflow")
            if grammar is not None:
                settings = value.get("generation_settings")
                if (type(settings) is not dict or settings.get("grammar") != grammar
                        or settings.get("grammar_lazy") is not False):
                    raise ModelError("grammar_not_applied")
            allowed_stops = ("eos",) if grammar is not None else ("eos", "word")
            if value.get("stop") is not True or value.get("stop_type") not in allowed_stops:
                raise ModelError("incomplete_generation")
            if tokenized is not None and evaluated != tokenized:
                raise ModelError("prompt_token_count_mismatch")
            result = {"content": content, "tokens": {"prompt": evaluated, "predicted": predicted,
                      "tokenized_prompt": tokenized}, "elapsed_seconds": time.monotonic() - started,
                      "request_sha256": hashlib.sha256(payload).hexdigest(), "raw_response": value,
                      "response_sha256": hashlib.sha256(raw).hexdigest(),
                      "raw_request_path": str(folder / "completion.request.json"),
                      "raw_response_path": str(folder / "completion.response.json"),
                      "query_artifact_dir": str(folder)}
            _save(folder / "result.json", {"outcome": "VALID", **result})
            return result
        except BaseException as exc:
            error = exc if isinstance(exc, ModelError) else ModelError("query_error", str(exc))
            error.artifact_dir = folder or self.artifacts
            try:
                if folder is not None:
                    _save(folder / "result.json", {"outcome": "INVALID", "error": error.code,
                          "detail": str(exc), "elapsed_seconds": time.monotonic() - started})
            finally:
                self.close()
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise error from exc
        finally:
            self._query_lock.release()

    def close(self) -> None:
        if self._closed:
            return
        self._closed, self._ready = True, False
        if not self._artifacts_created:
            return
        started, terminated, killed, errors = time.monotonic(), False, False, []
        process = self.process
        try:
            if process is not None:
                if process.poll() is None:
                    try:
                        process.terminate()
                        terminated = True
                    except OSError as exc:
                        errors.append(str(exc))
                try:
                    process.wait(timeout=STOP_SECONDS)
                except subprocess.TimeoutExpired:
                    killed = True
                    process.kill()
                    process.wait(timeout=STOP_SECONDS)
        except (OSError, subprocess.TimeoutExpired) as exc:
            errors.append(str(exc))
        finally:
            for stream in self._streams:
                stream.close()
            report = {"owned_popen_only": True, "pid": process.pid if process else None,
                      "terminate_requested": terminated, "kill_requested": killed,
                      "returncode": process.returncode if process else None,
                      "reaped": process is None or process.returncode is not None,
                      "termination_kind": ("not_started" if process is None else
                                           "host_termination" if terminated or killed else "already_exited"),
                      "linux_normal_stop_claim": False, "errors": errors,
                      "elapsed_seconds": time.monotonic() - started}
            report["logs"] = {}
            for path in (self.artifacts / "backend-stdout.log", self.artifacts / "backend-stderr.log"):
                try:
                    if path.is_file():
                        report["logs"][path.name] = _file_record(path)
                except (OSError, ModelError) as exc:
                    errors.append("log_hash:" + str(exc))
            _save(self.artifacts / "cleanup.json", report)
        if errors or not report["reaped"]:
            raise ModelError("cleanup_failed", "; ".join(errors), artifact_dir=self.artifacts)

    def __exit__(self, exc_type, exc, traceback):
        self.close()
        return False
