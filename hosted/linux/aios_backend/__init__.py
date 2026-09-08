"""AIOS-owned lifecycle for one external Linux model backend."""

VERSION = "0.1.0"
MODEL_NAME = "Qwen3-0.6B-Q8_0.gguf"
MODEL_BYTES = 639446688
MODEL_SHA = "9465e63a22add5354d9bb4b99e90117043c7124007664907259bd16d043bb031"
BACKEND_NAME = "llamafile-0.10.5-thin.exe"
BACKEND_SHA = "55c69c1be9d6ad2172e2d1c0acc677a60ea8ff60232009a8c8170f5bcb917611"
MODEL_ID = "aios-qwen3-0.6b-q8_0"
MAX_STARTS = 64
IDENTITY_KEYS = ("service_id", "instance_id", "start_generation")
PROCESS_KEYS = frozenset({"host_boot_id", "process_id", "process_start_ticks", "uid"})
RECORD_KEYS = frozenset({"schema_version", *IDENTITY_KEYS, "supervisor_identity", "child_identity",
    "lifecycle_state", "backend_ready", "config_sha256", "profile", "source_only", "backend_source_instance"})


class BackendError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code
