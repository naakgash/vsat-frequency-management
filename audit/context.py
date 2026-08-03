"""Ambient request context for audit records.

Specification section 18 requires every audit event to carry a request ID, source IP and
user agent. Threading an ``HttpRequest`` through every service signature to achieve that
would couple the domain layer to HTTP — and would break outright for the importer and for
management commands, which have no request at all.

Instead the middleware publishes the context into a :mod:`contextvars` variable, and
:func:`current` returns whatever is in scope. Outside a request the context is empty,
which is correct rather than exceptional: a management command genuinely has no source IP.
"""

from __future__ import annotations

import contextvars
import dataclasses
import uuid
from collections.abc import Iterator
from contextlib import contextmanager


@dataclasses.dataclass(frozen=True)
class RequestContext:
    """Request-scoped metadata attached to audit events."""

    request_id: uuid.UUID | None = None
    source_ip: str | None = None
    user_agent: str = ""


_EMPTY = RequestContext()

_current: contextvars.ContextVar[RequestContext] = contextvars.ContextVar(
    "audit_request_context", default=_EMPTY
)


def current() -> RequestContext:
    """Return the request context in scope, or an empty one outside a request."""
    return _current.get()


@contextmanager
def bind(context: RequestContext) -> Iterator[RequestContext]:
    """Bind a request context for the duration of the block.

    ``contextvars`` tokens are reset explicitly rather than relying on the variable being
    overwritten, so nested binds (a management command invoked from a view, say) restore
    the outer context correctly.
    """
    token = _current.set(context)
    try:
        yield context
    finally:
        _current.reset(token)
