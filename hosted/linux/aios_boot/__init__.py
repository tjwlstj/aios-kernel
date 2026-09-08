"""Installed Linux delivery boot; Linux IDs remain source-only metadata."""

MAX_FILES = 512
MAX_HISTORIES = 512
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024
SESSION_UID = 1000
SESSION_GID = 1000


class BootError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code
