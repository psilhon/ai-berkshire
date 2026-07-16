class AsharePluginError(Exception):
    """Base exception for expected plugin failures."""


class InvalidCodeError(AsharePluginError, ValueError):
    """The supplied security code is not a supported A-share code."""


class TransportError(AsharePluginError):
    """A provider request failed before usable data could be decoded."""

    def __init__(self, message: str, error_type: str = "http_error"):
        super().__init__(message)
        self.error_type = error_type
