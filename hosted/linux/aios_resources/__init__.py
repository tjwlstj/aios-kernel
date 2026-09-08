"""Source-only Linux resource observations, separate from resource authority."""
VERSION = "0.1.0"


class ResourceError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code
