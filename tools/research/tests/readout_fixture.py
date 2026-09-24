"""MOCK readout fixtures: real producer/wrapper files, no model or network.

The origin is a synthetic 24-call old v2 episode bundle. Only tests replace
its whole-bundle and selected-decision pins; production offers no override.
"""
import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import uuid
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import self_reference_contract as contract
import self_reference_lab as lab
import self_reference_model as model
import self_reference_readout as readout
import self_reference_replay as replay
from prompt_comparison_fixture import produce as old_produce


def origin_path(root):
    return root.parent / (root.name + "-origin")


def make_origin(root):
    """Synthetic origin only; unchanged old producer and pinned runtime bytes."""
    cache = root.parent / "cache"
    cache.mkdir(exist_ok=True)
    receipt = cache / "provenance-receipt.json"
    if not receipt.exists():
        receipt.write_bytes(b'{"fixture":"MOCK synthetic pinned-cache provenance"}\n')
    with patch.object(lab.uuid, "uuid4", return_value=uuid.UUID(readout.ORIGIN_RUN_ID)):
        if old_produce(root, failure="observe_loop") != 0:
            raise AssertionError("synthetic old origin failed")
    manifest = readout.inventory(root, replay)
    if manifest["file_count"] != 289:
        raise AssertionError("synthetic origin must retain exactly 289 files: " + str(manifest["file_count"]))
    return root


@contextlib.contextmanager
def patched_origin(origin, *modules):
    """Explicit test-only synthetic provenance patch; never changes core source pins."""
    manifest = readout.inventory(origin, replay)
    pins = {sample: readout.sha((origin / "00-normal-action-first-public-feedback-v1" /
                               f"decision-{step + 1:03d}.json").read_bytes())
            for sample, step in readout.SAMPLE_STEPS.items()}
    with contextlib.ExitStack() as stack:
        for module in modules:
            stack.enter_context(patch.object(module, "ORIGIN_MANIFEST_SHA256", manifest["manifest_sha256"]))
            stack.enter_context(patch.object(module, "DECISION_PINS", pins))
        yield


def produce(root, *, failure=None):
    """Run six MOCK HTTP replies or one bounded diagnostic failure tail."""
    if failure not in (None, "tokenizer", "transport", "limit", "settings_drift", "cleanup",
                       "wrong_semantics", "condition_difference", "syntax", "missing_receipt"):
        raise ValueError("unknown readout fixture failure")
    origin = origin_path(root)
    if not origin.exists():
        make_origin(origin)
    source = readout.source_manifest()

    def source_record():
        # Keep Git metadata stable under the process mock, but reread source bytes.
        checkout = Path(readout.__file__).resolve().parents[2]
        return {**source, "files": {name: readout.sha((checkout / name).read_bytes()) for name in source["files"]}}
    generation = json.loads((origin / "model/query-0001/completion.response.json").read_bytes())["generation_settings"]
    process = Mock(pid=4321, returncode=None)
    process.poll.side_effect = lambda: process.returncode

    def wait(timeout):
        if failure == "cleanup":
            raise OSError("mock owned backend cleanup blocked")
        process.returncode = 1
        return 1

    process.wait.side_effect = wait
    original_record = model._file_record

    def record(path):
        if path.name == model.MODEL_NAME:
            return {"path": str(path), "bytes": 639446688, "sha256": model.MODEL_SHA256}
        if path.name == model.BACKEND_NAME:
            return {"path": str(path), "bytes": 42328074, "sha256": model.BACKEND_SHA256}
        return original_record(path)

    completions = 0

    def exchange(endpoint, payload, deadline, limit):
        nonlocal completions
        if endpoint == "/health":
            return 200, b'{"status":"ok"}'
        if endpoint == "/tokenize":
            # A start record must exist before tokenizer preflight is entered.
            slot = len(list((root / "ledger").glob("*.start.json")))
            if slot != completions + 1 or not 1 <= slot <= 6:
                raise AssertionError("query reservation missing or over cap")
            count = 2000 if failure == "tokenizer" else 100
            return 200, contract.encoded({"tokens": list(range(count))})
        if endpoint != "/completion":
            raise AssertionError("unexpected endpoint")
        completions += 1
        if failure == "transport":
            raise model.ModelError("transport_error", raw_response=b'{"partial":')
        request = json.loads(payload)
        user = request["prompt"].split("<|im_start|>user\n", 1)[1].split(" /no_think<|im_end|>", 1)[0]
        proposal = lab.rules_proposal(json.loads(user)["related_facts"])
        is_c1 = list(json.loads(user)["related_facts"]).index("observation") == 2
        if failure == "wrong_semantics" or failure == "condition_difference" and is_c1:
            proposal = {"action": "OBSERVE", "expected_revision": None, "prediction": "OBSERVED", "attribution": "SELF"}
        content = json.dumps(proposal, separators=(",", ":"))
        if failure == "syntax":
            content = " " + content
        settings = copy.deepcopy(generation)
        if failure == "settings_drift":
            settings["top_k"] += 1
        response = {"content": content, "model": model.MODEL_ID, "prompt": request["prompt"],
            "tokens_predicted": 192 if failure == "limit" else 30, "tokens_evaluated": 100,
            "truncated": False, "stop": True, "stop_type": "limit" if failure == "limit" else "eos",
            "generation_settings": settings}
        return 200, contract.encoded(response)

    original_query = model.LocalModel.query

    def query(instance, *args, **kwargs):
        result = original_query(instance, *args, **kwargs)
        if failure == "missing_receipt":
            (Path(result["query_artifact_dir"]) / "completion.http.json").unlink()
        return result

    with patched_origin(origin, readout), patch.object(readout, "source_manifest", side_effect=source_record), \
            patch.object(model, "_file_record", side_effect=record), \
            patch.object(model.subprocess, "Popen", return_value=process), \
            patch.object(model.LocalModel, "_exchange", side_effect=exchange), \
            patch.object(model.LocalModel, "query", new=query), \
            patch.object(model.socket, "socket") as reservation, contextlib.redirect_stdout(io.StringIO()):
        reservation.return_value.__enter__.return_value.getsockname.return_value = ("127.0.0.1", 56789)
        result = readout.main(["--origin", str(origin), "--artifacts", str(root),
                               "--cache", str(root.parent / "cache")])
    if failure != "cleanup" and process.poll() is None:
        raise AssertionError("owned mocked backend was not reaped")
    if completions > 6:
        raise AssertionError("completion cap exceeded")
    return result
