"""Contrats stricts du fournisseur et résultat enrichi avec des comptes locaux."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Issue(StrictModel):
    category: Literal['bug', 'performance', 'ux_ui', 'security', 'feature', 'other']
    title: str = Field(min_length=1, max_length=300)
    priority: Literal['low', 'medium', 'high', 'critical']
    review_ids: list[str] = Field(min_length=1, max_length=100)
    recommendations: list[str] = Field(min_length=1, max_length=10)
    confidence: float = Field(ge=0, le=1)

    @field_validator('review_ids')
    @classmethod
    def unique_references(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values):
            raise ValueError('Références dupliquées')
        return values

    @field_validator('recommendations')
    @classmethod
    def useful_recommendations(cls, values: list[str]) -> list[str]:
        if any(not v.strip() or len(v) > 2000 for v in values):
            raise ValueError('Recommandation invalide')
        return values


class ModelAnalysis(StrictModel):
    summary: str = Field(min_length=1, max_length=4000)
    issues: list[Issue] = Field(max_length=30)
    limitations: list[str] = Field(min_length=1, max_length=20)

    @field_validator('summary')
    @classmethod
    def nonempty_summary(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('Résumé vide')
        return value

    @field_validator('limitations')
    @classmethod
    def useful_limitations(cls, values: list[str]) -> list[str]:
        if any(not v.strip() or len(v) > 2000 for v in values):
            raise ValueError('Limite invalide')
        return values


class VerifiedIssue(Issue):
    review_count: int = Field(ge=1)


class AnalysisResult(StrictModel):
    app_name: str
    package_name: str
    reviews_analyzed: int = Field(ge=1)
    summary: str = Field(min_length=1, max_length=4000)
    issues: list[VerifiedIssue]
    limitations: list[str]
