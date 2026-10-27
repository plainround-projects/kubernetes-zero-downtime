"""Named errors for the load generator."""


class LoadgenError(Exception):
    """Base class of every error this program raises on purpose."""


class InvalidRateError(LoadgenError):
    """A request rate outside the supported range."""
