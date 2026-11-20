"""The four kinds of failure, the same as ``arthur.sources.whisker.WhiskerSource.error``.

- ``auth``: the login or a token was rejected. Retried slowly (600 s rising to an hour), so
  the account cannot be locked.
- ``api``: the network, TLS, Whisker or Amazon failed, or answered with something we cannot
  read. Retried with back-off from 30 s to 600 s.
- ``timeout``: a request or the connection took longer than its limit. Retried like ``api``.
- ``no_robots``: the account has no Litter-Robot 4, or several and none chosen. Retried like
  ``api``.
"""

AUTH = "auth"
API = "api"
TIMEOUT = "timeout"
NO_ROBOTS = "no_robots"


class WireError(Exception):
    """A failure with its kind. ``status`` is the HTTP status when there was one, ``code`` the
    service's error code (Cognito's ``NotAuthorizedException``, a GraphQL ``errorType``)."""

    def __init__(self, kind, message, status=None, code=None):
        Exception.__init__(self, message)
        self.kind = kind
        self.message = message
        self.status = status
        self.code = code

    def __str__(self):
        return self.message


def auth_error(message, status=None, code=None):
    return WireError(AUTH, message, status, code)


def api_error(message, status=None, code=None):
    return WireError(API, message, status, code)


def kind_of(exc):
    """The kind of any exception: a WireError says; a timeout is ``timeout``; anything else
    (OSError, a TLS failure, a bug in parsing) is ``api``."""
    if isinstance(exc, WireError):
        return exc.kind
    name = type(exc).__name__
    if name in ("TimeoutError", "CancelledTimeout"):
        return TIMEOUT
    return API
