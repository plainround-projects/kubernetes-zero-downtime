"""Named errors for the service."""


class ServiceError(Exception):
    """Base class of every error this service raises on purpose."""


class InvalidProfileError(ServiceError):
    """A request profile has impossible bounds."""
