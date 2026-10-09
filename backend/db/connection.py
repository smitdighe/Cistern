"""Async SQLAlchemy engines and session factories.

Two engines are created and kept deliberately separate:

* ``execution_engine`` — bound to ``DATABASE_URL``, a read-only Postgres role.
  Every user-driven query runs here. Write attempts are rejected by the
  database itself, not by application logic.
* ``admin_engine`` — bound to ``DATABASE_ADMIN_URL``. Used only by
  ``introspect.py`` and by alembic migrations.

There is intentionally no shared engine, no shared sessionmaker, and no
parameterised factory that could hand an admin session to an execution code
path. The two halves of this module do not touch.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from backend.config import settings

# Query-string keys libpq understands but asyncpg does not; asyncpg takes TLS
# configuration through connect_args instead.
_LIBPQ_ONLY_PARAMS = ("sslmode", "channel_binding", "gssencmode", "target_session_attrs")


def _normalise_async_url(raw_url: str) -> tuple[str, dict]:
    """Return an asyncpg-compatible URL plus the connect_args it needs.

    Hosted providers (Neon) hand out libpq-style URLs such as
    ``postgresql://…?sslmode=require``. asyncpg rejects ``sslmode`` as an
    unknown keyword, so it is stripped from the query string and re-expressed
    as an ``ssl`` connect arg.
    """
    url = make_url(raw_url)

    if url.drivername in ("postgres", "postgresql"):
        url = url.set(drivername="postgresql+asyncpg")

    query = dict(url.query)
    sslmode = query.get("sslmode")
    for key in _LIBPQ_ONLY_PARAMS:
        query.pop(key, None)
    url = url.set(query=query)

    connect_args: dict = {}
    if url.drivername == "postgresql+asyncpg":
        # Neon always requires TLS; default to it unless explicitly disabled.
        connect_args["ssl"] = False if sslmode == "disable" else "require"

    return url.render_as_string(hide_password=False), connect_args


def _build_engine(raw_url: str) -> AsyncEngine:
    url, connect_args = _normalise_async_url(raw_url)
    return create_async_engine(
        url,
        connect_args=connect_args,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        echo=False,
    )


# --- execution: read-only role, user query traffic only ---------------------

execution_engine: AsyncEngine = _build_engine(settings.DATABASE_URL)

ExecutionSessionLocal = async_sessionmaker(
    bind=execution_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_execution_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a session on the read-only role. Never used for DDL or seeding."""
    async with ExecutionSessionLocal() as session:
        try:
            yield session
        finally:
            await session.rollback()


# --- admin: introspection and migrations only -------------------------------

admin_engine: AsyncEngine = _build_engine(settings.DATABASE_ADMIN_URL)

AdminSessionLocal = async_sessionmaker(
    bind=admin_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_admin_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a session on the admin role. Introspection and migrations only."""
    async with AdminSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def dispose_engines() -> None:
    """Close both connection pools. Called on application shutdown."""
    await execution_engine.dispose()
    await admin_engine.dispose()
