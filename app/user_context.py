"""Request-local user context without importing web configuration."""
from contextvars import ContextVar
from uuid import UUID

_current_user_id: ContextVar[UUID | None] = ContextVar("current_user_id", default=None)


def set_current_user_id(user_id: UUID | None) -> None:
    _current_user_id.set(user_id)


def get_current_user_id() -> UUID | None:
    return _current_user_id.get()
