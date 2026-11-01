"""Schéma SQLite V2."""
from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Application(Base):
    __tablename__ = "applications"
    id: Mapped[int] = mapped_column(primary_key=True)
    package_name: Mapped[str] = mapped_column(unique=True)
    name: Mapped[str]
    description: Mapped[str]
    language: Mapped[str]
    country: Mapped[str]
    created_at: Mapped[str]
    updated_at: Mapped[str]


class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = (UniqueConstraint("application_id", "review_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id"))
    review_id: Mapped[str]
    score: Mapped[int]
    content: Mapped[str]
    review_date: Mapped[str]
    first_seen_at: Mapped[str]
    last_seen_at: Mapped[str]
    content_hash: Mapped[str]
    analysis_status: Mapped[str] = mapped_column(default="pending")
