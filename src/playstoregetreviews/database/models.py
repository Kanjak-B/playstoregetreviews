"""Schéma SQLite V2."""
from sqlalchemy import ForeignKey, UniqueConstraint, Index, text
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
    recipient_email: Mapped[str | None]
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


class Analysis(Base):
    __tablename__ = 'analyses'
    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey('applications.id'))
    model_provider: Mapped[str]
    model_name: Mapped[str]
    prompt_version: Mapped[str]
    status: Mapped[str]
    total_reviews: Mapped[int]
    result_json: Mapped[str | None]
    input_tokens: Mapped[int | None]
    output_tokens: Mapped[int | None]
    estimated_cost: Mapped[float | None]
    created_at: Mapped[str]
    completed_at: Mapped[str | None]
    error_code: Mapped[str | None]


class AnalysisReview(Base):
    __tablename__ = 'analysis_reviews'
    analysis_id: Mapped[int] = mapped_column(ForeignKey('analyses.id'), primary_key=True)
    review_id: Mapped[int] = mapped_column(ForeignKey('reviews.id'), primary_key=True)
    content_hash: Mapped[str]
    review_date: Mapped[str | None]


# Une application ne doit pas déclencher deux appels concurrents.
Index('one_running_analysis_per_app', Analysis.application_id, unique=True,
      sqlite_where=text("status = 'running'"))


class Report(Base):
    __tablename__ = 'reports'
    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey('applications.id'))
    fingerprint: Mapped[str] = mapped_column(unique=True)
    document_json: Mapped[str]
    created_at: Mapped[str]
    rendered_at: Mapped[str | None]


class ReportAnalysis(Base):
    __tablename__ = 'report_analyses'
    analysis_id: Mapped[int] = mapped_column(ForeignKey('analyses.id'), primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey('reports.id'))


class EmailDelivery(Base):
    __tablename__ = 'email_deliveries'
    __table_args__ = (UniqueConstraint('report_id', 'recipient'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey('reports.id'))
    recipient: Mapped[str]
    status: Mapped[str]
    message_id: Mapped[str] = mapped_column(unique=True)
    attempts: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[str]
    updated_at: Mapped[str]
    sent_at: Mapped[str | None]
    error_code: Mapped[str | None]


class WorkflowState(Base):
    __tablename__ = 'workflow_states'
    package_name: Mapped[str] = mapped_column(primary_key=True)
    status: Mapped[str]
    started_at: Mapped[str]
    completed_at: Mapped[str | None]
    last_success_at: Mapped[str | None]
