class AutocoderError(Exception):
    """Base application error."""


class ModelProtocolError(AutocoderError):
    """The model could not produce a valid structured response."""


class WorkspaceValidationError(AutocoderError):
    """Generated files violated workspace constraints."""


class InfrastructureError(AutocoderError):
    """A required local or remote service is unavailable."""


class SandboxExecutionError(InfrastructureError):
    """A sandbox lifecycle operation failed."""
