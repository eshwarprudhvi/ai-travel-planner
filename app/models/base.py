from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Parent class for every database model. Alembic reads Base.metadata to find tables."""

    pass
