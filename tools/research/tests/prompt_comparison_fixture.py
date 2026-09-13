"""Real research producer/wrapper disk fixtures; mocked backend and HTTP only."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import self_reference_contract as contract
import self_reference_lab as lab
import self_reference_model as model


def produce(root: Path, *, failure=None) -> int:
    """Controlled HTTP/preflight/cleanup failures or a bounded observe_loop."""
    if failure not in (None, "tokenizer", "transport", "limit", "second_transport", "observe_loop", "baseline_observe_loop", "cleanup"):
        raise ValueError("unknown fixture failure")
    host = lab.platform.platform()
    checkout = Path(lab.__file__).resolve().parents[2]
    files = {"tools/research/self_reference_" + name + ".py":
             hashlib.sha256((checkout / ("tools/research/self_reference_" + name + ".py")).read_bytes()).hexdigest()
             for name in ("world", "contract", "model", "lab", "grammar")}
    source = {"head_sha": "b60bbbd61701c6ae9b9c9a6b888c9c7947947a9d", "dirty": True, "files": files}
    process = Mock(pid=1234, returncode=None)
    process.poll.side_effect = lambda: process.returncode

    def wait(timeout):
        if failure == "cleanup":
            raise OSError("fixture owned backend cleanup blocked")
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
            count = 2000 if failure == "tokenizer" else 100
            return 200, contract.encoded({"tokens": list(range(count))})
        if endpoint != "/completion":
            raise AssertionError("unexpected endpoint")
        completions += 1
        if failure == "transport" or failure == "second_transport" and completions == 5:
            raise model.ModelError("transport_error", raw_response=b'{"partial":')
        request = json.loads(payload)
        user = request["prompt"].split("<|im_start|>user\n", 1)[1].split(" /no_think<|im_end|>", 1)[0]
        context = json.loads(user)["related_facts"]
        proposal = lab.rules_proposal(context)
        if (failure == "observe_loop" or failure == "baseline_observe_loop"
                and request["prompt"].startswith("<|im_start|>system\n" + lab.SYSTEM + "<|im_end|>")):
            proposal = {"action": "OBSERVE", "expected_revision": None,
                        "prediction": "OBSERVED", "attribution": "UNKNOWN"}
        response = {"content": model._encoded(proposal).decode("utf-8"),
            "model": model.MODEL_ID, "prompt": request["prompt"], "tokens_predicted": 30,
            "tokens_evaluated": 100, "truncated": False, "stop": True,
            "stop_type": "limit" if failure == "limit" else "eos",
            "generation_settings": {"seed": 1, "temperature": 0.0,
                "n_predict": 192, "max_tokens": 192, "stream": False, "grammar": request["grammar"],
                "grammar_lazy": False, "lora": [], "top_k": 40, "top_p": 0.949999988079071,
                "min_p": 0.05000000074505806, "repeat_penalty": 1.0,
                "samplers": ["top_k", "top_p", "min_p", "temperature"]}}
        return 200, contract.encoded(response)

    with patch.object(lab, "source_manifest", return_value=source), \
            patch.object(lab.platform, "platform", return_value=host), \
            patch.object(model, "_file_record", side_effect=record), \
            patch.object(model.subprocess, "Popen", return_value=process), \
            patch.object(model.LocalModel, "_exchange", side_effect=exchange), \
            patch.object(model.socket, "socket") as reservation, contextlib.redirect_stdout(io.StringIO()):
        reservation.return_value.__enter__.return_value.getsockname.return_value = ("127.0.0.1", 56789)
        result = lab.main(["--artifacts", str(root), "--cache", str(root.parent / "cache"),
                           "--prompt-comparison", "action-progress-v1"])
        if failure != "cleanup" and process.poll() is None:
            raise AssertionError("owned mocked backend was not reaped")
        return result
