from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    # ``now()`` in Postgres returns the *transaction* start time, so
    # two statements in the same tx get identical stamps. That breaks
    # ETag rotation (and any "did this change?" check) inside a single
    # request that re-reads the row. ``clock_timestamp()`` returns
    # wall-clock time per statement, which is what you actually want
    # for ``updated_at``.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.clock_timestamp(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.clock_timestamp(),
        onupdate=func.clock_timestamp(),
        nullable=False,
    )
