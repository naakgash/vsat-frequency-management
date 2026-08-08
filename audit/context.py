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
from typing import Any


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


class RequestIdFilter:
    """A logging filter that stamps the request id onto every log record. §21.14.

    Not decoration. An incident starts with a log line and ends in the audit trail, and the
    request id is the only thing that joins the two: `audit_event.request_id` holds the same
    value, so a stack trace in the log leads directly to the events that request produced —
    and to none of the thousands it did not. Correlating by timestamp alone is how an
    investigation of a busy minute goes wrong.

    A class rather than a function because ``logging.config.dictConfig`` instantiates filters
    from a ``()`` key, and outside a request the id is simply ``-``, which is correct: a
    management command has no request (see the module note).
    """

    def filter(self, record: Any) -> bool:
        record.request_id = str(current().request_id or "-")
        # Always True: this filter adds a field, it never suppresses a line. A logging filter
        # that could drop records is a logging filter that will one day drop the one that
        # mattered.
        return True


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
