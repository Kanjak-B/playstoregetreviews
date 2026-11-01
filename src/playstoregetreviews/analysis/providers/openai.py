"""Adaptateur OpenAI facultatif : pas de clé ni de réponse brute dans les logs."""
from dataclasses import dataclass
import os
from typing import Any, Protocol
from ...config import AnalysisConfig
from ..schemas import ModelAnalysis


class ProviderError(RuntimeError):
    """Erreur volontairement indépendante du message distant."""


@dataclass(frozen=True)
class ProviderResponse:
    content: str | None
    input_tokens: int | None = None
    output_tokens: int | None = None
    finish_reason: str = 'stop'
    refused: bool = False


class Provider(Protocol):
    def analyze(self, messages: list[dict[str, str]]) -> ProviderResponse: ...


class OpenAIProvider:
    def __init__(self, config: AnalysisConfig, client: Any = None):
        self.config = config
        self.client = client

    def _client(self):
        if self.client is None:
            key = os.getenv('PLAYSTORE_LLM_API_KEY') or os.getenv('OPENAI_API_KEY')
            if not key:
                raise ProviderError('llm_key_missing')
            try:
                from openai import OpenAI
            except ImportError:
                raise ProviderError('llm_dependency_missing') from None
            # Endpoint fixe et reprises désactivées pour éviter des dépenses implicites.
            self.client = OpenAI(api_key=key, base_url='https://api.openai.com/v1',
                                 max_retries=0, timeout=self.config.timeout_seconds)
        return self.client

    def analyze(self, messages: list[dict[str, str]]) -> ProviderResponse:
        try:
            response = self._client().chat.completions.create(
                model=self.config.model, messages=messages,
                max_completion_tokens=self.config.max_output_tokens,
                store=False,
                response_format={'type': 'json_schema', 'json_schema': {
                    'name': 'review_analysis', 'strict': True,
                    'schema': ModelAnalysis.model_json_schema()}},
            )
            usage = response.usage
            choice = response.choices[0] if response.choices else None
            return ProviderResponse(
                content=choice.message.content if choice else None,
                input_tokens=usage.prompt_tokens if usage else None,
                output_tokens=usage.completion_tokens if usage else None,
                finish_reason=choice.finish_reason if choice else 'missing_choice',
                refused=bool(choice.message.refusal) if choice else False,
            )
        except ProviderError:
            raise
        except Exception:
            raise ProviderError('llm_request_failed') from None

    def close(self) -> None:
        if self.client is not None:
            self.client.close()
