"""Bounded real local model requests with reproducible input/output receipts."""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import stat
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CONFIG_KEYS = {"schema_version", "endpoint", "model_id", "model_path", "model_sha256",
               "backend_path", "backend_sha256", "provenance_sha256"}
MAX_BODY = 16384
MAX_WORKER_OUTPUT = 128 * 1024
MAX_ARTIFACT = 1024 * 1024 * 1024
TIMEOUT = 420


def encoded(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def parse(raw: bytes, *, maximum: int = MAX_BODY) -> dict:
    if len(raw) > maximum:
        raise ValueError("response-size")
    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result:
                raise ValueError("duplicate-field")
            result[key] = value
        return result
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite")))
    if type(value) is not dict:
        raise ValueError("object-required")
    pending = [(value, 0)]
    count = 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if count > 4096 or depth > 16:
            raise ValueError("response-complexity")
        children = item.values() if type(item) is dict else item if type(item) is list else ()
        pending.extend((v, depth + 1) for v in children)
    return value


def hash_text(value: object) -> bool:
    return type(value) is str and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def validate_config(value: dict) -> dict:
    if type(value) is not dict or set(value) != CONFIG_KEYS or type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("config-schema")
    for key in ("model_sha256", "backend_sha256", "provenance_sha256"):
        if not hash_text(value[key]):
            raise ValueError("config-hash")
    for key in ("model_path", "backend_path", "model_id", "endpoint"):
        if type(value[key]) is not str or not 0 < len(value[key]) <= 1024 or any(not c.isprintable() for c in value[key]):
            raise ValueError("config-text")
    address = urlsplit(value["endpoint"])
    if (address.scheme != "http" or address.hostname not in ("127.0.0.1", "10.0.2.2") or address.username is not None
            or address.password is not None or address.path not in ("", "/") or address.query or address.fragment
            or address.port is None or not 1024 <= address.port <= 65535):
        raise ValueError("local-endpoint-required")
    return value


def file_hash(path: Path) -> str:
    if path.is_symlink():
        raise ValueError("artifact-symlink")
    with path.open("rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_ARTIFACT:
            raise ValueError("artifact-size")
        result = hashlib.sha256()
        total = 0
        while block := stream.read(1024 * 1024):
            total += len(block)
            if total > MAX_ARTIFACT:
                raise ValueError("artifact-size")
            result.update(block)
        if total != info.st_size:
            raise ValueError("artifact-changed")
        return result.hexdigest()


def load_config(path: Path, *, verify_artifacts: bool = True) -> dict:
    with path.open("rb") as stream:
        value = validate_config(parse(stream.read(MAX_BODY + 1)))
    if verify_artifacts:
        for name in ("model", "backend"):
            if file_hash(Path(value[name + "_path"])) != value[name + "_sha256"]:
                raise ValueError(name + "-hash-mismatch")
    return value


def request_body(prompt: str, *, warmup: bool = False) -> bytes:
    if type(prompt) is not str or not prompt.strip() or len(prompt.encode("utf-8")) > 4096 or any(ord(c) < 32 and c not in "\n\t" for c in prompt):
        raise ValueError("prompt-invalid")
    # Explicit ChatML formatting for the pinned Qwen3 instruct model. Model
    # text is output only and cannot become a shell command or resource action.
    text = ("<|im_start|>system\nYou are the AIOS MAIN assistant. Answer briefly. "
            "Describe only the provided facts; you cannot execute commands. /no_think<|im_end|>\n"
            "<|im_start|>user\n" + prompt + " /no_think<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n")
    return encoded({"prompt": text, "n_predict": 8 if warmup else 64, "temperature": 0.0,
                    "seed": 1, "cache_prompt": False, "stream": False})


def worker(config: dict, body: bytes, backend_descriptor=None, backend_capture_kind="live") -> dict:
    address = urlsplit(config["endpoint"])
    connection = http.client.HTTPConnection(address.hostname, address.port, timeout=TIMEOUT - 5)
    guard = None
    try:
        before = sent = None
        if backend_descriptor is not None:
            from aios_agent.backend_binding import ExecutionBinding
            guard = ExecutionBinding.from_descriptor(backend_descriptor, config, backend_capture_kind)
            before = guard.check()
            connection.connect()
            # Once authenticated, this exact TCP connection carries the request.
            # A connection failure must not silently open another recipient.
            connection.auto_open = 0
            sent = guard.connected(connection.sock)
        connection.request("POST", "/completion", body=body, headers={"Content-Type": "application/json", "Connection": "close"})
        response = connection.getresponse()
        raw = response.read(MAX_BODY + 1)
        if response.status != 200:
            raise ValueError("backend-http-status")
        payload = parse(raw)
        content, tokens = payload.get("content"), payload.get("tokens_predicted")
        if (type(content) is not str or not content.strip() or type(tokens) is not int or not 1 <= tokens <= 64
                or payload.get("model") != config["model_id"]):
            raise ValueError("empty-or-invalid-completion")
        execution = None
        if guard is not None:
            execution = {"schema_version": 1, "descriptor": guard.descriptor, "capture_kind": backend_capture_kind,
                         "before": before, "send": sent, "after": guard.check()}
        return {"response_body": raw.decode("utf-8"), "content": content, "tokens_predicted": tokens,
                "backend_execution": execution}
    finally:
        connection.close()
        if guard is not None:
            guard.close()


def infer(config: dict, prompt: str, *, warmup: bool = False, backend_descriptor=None, backend_capture_kind="live") -> dict:
    validate_config(config)
    body = request_body(prompt, warmup=warmup)
    started = time.monotonic_ns()
    result = {"schema_version": 2, "request_id": str(uuid.uuid4()), "started_at": datetime.now(timezone.utc).isoformat(),
              "purpose": "warmup" if warmup else "user", "model_id": config["model_id"], "model_sha256": config["model_sha256"],
              "backend_sha256": config["backend_sha256"], "provenance_sha256": config["provenance_sha256"],
              "request_body": body.decode("utf-8"), "request_sha256": digest(body), "response_body": None,
              "response_sha256": None, "content": None, "tokens_predicted": 0, "elapsed_ns": 0,
              "outcome": "ERROR", "error": None, "backend_execution": None}
    try:
        environment = {k: v for k, v in os.environ.items() if k.upper() != "SSLKEYLOGFILE" and not k.lower().endswith("_proxy")}
        process = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker"],
                    input=encoded({"config": config, "request": body.decode("utf-8"), "backend_descriptor": backend_descriptor,
                                   "backend_capture_kind": backend_capture_kind}), capture_output=True,
                    timeout=TIMEOUT, env=environment, check=False)
        if process.returncode != 0 or process.stderr:
            raise ValueError("backend-worker-failed")
        output = parse(process.stdout, maximum=MAX_WORKER_OUTPUT)
        if set(output) != {"response_body", "content", "tokens_predicted", "backend_execution"}:
            raise ValueError("backend-worker-schema")
        if backend_descriptor is not None and (type(output["backend_execution"]) is not dict
                or output["backend_execution"].get("descriptor") != backend_descriptor
                or output["backend_execution"].get("capture_kind") != backend_capture_kind):
            raise ValueError("backend-worker-execution")
        if backend_descriptor is None and output["backend_execution"] is not None:
            raise ValueError("backend-worker-execution")
        # Validate the response a second time at the supervising boundary.
        raw = output["response_body"].encode("utf-8")
        parsed = parse(raw)
        if (parsed.get("content") != output["content"] or parsed.get("tokens_predicted") != output["tokens_predicted"]
                or parsed.get("model") != config["model_id"]
                or type(output["tokens_predicted"]) is not int or not 1 <= output["tokens_predicted"] <= (8 if warmup else 64)
                or type(output["content"]) is not str or not output["content"].strip()):
            raise ValueError("backend-worker-response")
        result.update(output, response_sha256=digest(raw), outcome="OK")
    except subprocess.TimeoutExpired:
        result["error"] = "backend-timeout"
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        result["error"] = "backend-failed"
    result["elapsed_ns"] = time.monotonic_ns() - started
    return result


if __name__ == "__main__":
    try:
        value = parse(sys.stdin.buffer.read(MAX_BODY + 1))
        config = validate_config(value["config"])
        body = value["request"].encode("utf-8")
        parse(body)
        sys.stdout.buffer.write(encoded(worker(config, body, value.get("backend_descriptor"), value.get("backend_capture_kind", "live"))))
    except (OSError, ValueError, TypeError, KeyError, http.client.HTTPException):
        raise SystemExit(1)
