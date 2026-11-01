"""Report document independent of provider responses."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from ..analysis.schemas import Issue


class ReportIssue(Issue):
    review_ids: list[str] = Field(min_length=1)
    review_count: int = Field(ge=1)


class SourceVersion(BaseModel):
    review_id: str
    content_hash: str
    review_date: str | None


class ReportDocument(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: Literal['1'] = '1'
    app_name: str
    package_name: str
    generated_at: str
    analysis_ids: list[int] = Field(min_length=1)
    analysis_period_start: str
    analysis_period_end: str
    review_period_start: str | None
    review_period_end: str | None
    reviews_analyzed: int = Field(ge=1)
    summaries: list[str] = Field(min_length=1)
    issues: list[ReportIssue]
    limitations: list[str]
    sources: list[SourceVersion] = Field(min_length=1)
    models: list[str]
    input_tokens: int | None
    output_tokens: int | None
    estimated_cost: float | None
