"""Makes the current request's user reachable from inside a model signal, which never
receives the request itself. contextvars (not a thread-local) so it stays correct under
the ASGI/uvicorn worker this app runs behind."""

from contextvars import ContextVar

_current_user = ContextVar("audit_current_user", default=None)


# [rows written during this request]: a mutable list, so a copy of the context (sync_to_async) still shares it
_request_rows = ContextVar("audit_request_rows", default=None)


def set_current_user(user):
    _current_user.set(user)


def start_request():
    _request_rows.set([0])


def note_row():
    counter = _request_rows.get()
    if counter is not None:
        counter[0] += 1


def rows_this_request():
    counter = _request_rows.get()
    return counter[0] if counter else 0


def get_current_user():
    return _current_user.get()
