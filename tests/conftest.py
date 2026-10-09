"""Shared pytest configuration.

Loads ``.env`` into ``os.environ`` before any test module is imported, so the
suite sees the same configuration the application does. This must happen here:
several test modules call ``os.environ.setdefault(...)`` with placeholder URLs
at import time, and pydantic-settings gives process environment priority over
the ``.env`` file — without this hook, the placeholders would silently
override real credentials for the entire test session.

When no ``.env`` exists (bare CI, fresh clone) the placeholders apply as
before and live-database tests skip themselves.
"""

from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
