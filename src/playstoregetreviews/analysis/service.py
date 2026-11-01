"""Orchestration de lots, vérification des références et comptabilité locale."""
from dataclasses import dataclass
import logging
from ..config import AppConfig, AnalysisConfig
from .preparation import Batch, prepare_batches, redact
from .repository import AnalysisRepository, AnalysisBusy
from .schemas import AnalysisResult, ModelAnalysis, VerifiedIssue
from .providers.openai import Provider, ProviderResponse, ProviderError

logger = logging.getLogger(__name__)


@dataclass
class AnalysisStats:
    completed: int = 0
    failed: int = 0
    calls: int = 0
    skipped: int = 0
    deferred: int = 0


def validate_result(batch: Batch, app: AppConfig, response: ProviderResponse) -> AnalysisResult:
    if response.refused or response.finish_reason != 'stop' or not response.content:
        raise ValueError('incomplete_or_refused_response')
    if len(response.content) > 250000:
        raise ValueError('response_too_large')
    model = ModelAnalysis.model_validate_json(response.content)
    issues = []
    for issue in model.issues:
        if any(reference not in batch.references for reference in issue.review_ids):
            raise ValueError('unknown_review_reference')
        # Les vrais identifiants ne sont jamais envoyés au fournisseur.
        selected = {batch.references[reference].id for reference in issue.review_ids}
        # Un groupe dédupliqué représente tous ses avis, même si le modèle ne cite
        # qu'une des références du groupe. Compter chaque source une seule fois.
        keys = {(source.score, redact(source.content)) for source in batch.sources if source.id in selected}
        ids = [source.review_id for source in batch.sources if (source.score, redact(source.content)) in keys]
        values = issue.model_dump()
        values.update(review_ids=ids, title=redact(issue.title),
                      recommendations=[redact(value) for value in issue.recommendations])
        issues.append(VerifiedIssue(**values, review_count=len(ids)))
    return AnalysisResult(app_name=app.name, package_name=app.package_name,
                          reviews_analyzed=len(batch.sources), summary=redact(model.summary),
                          issues=issues, limitations=[redact(value) for value in model.limitations])


def cost(response: ProviderResponse | None, config: AnalysisConfig) -> float | None:
    if (response is None or response.input_tokens is None or response.output_tokens is None or
            config.input_cost_per_million is None or config.output_cost_per_million is None):
        return None
    return (response.input_tokens * config.input_cost_per_million +
            response.output_tokens * config.output_cost_per_million) / 1000000


def analyze_app(repository: AnalysisRepository, provider: Provider, app: AppConfig,
                config: AnalysisConfig) -> AnalysisStats:
    batches, skipped = prepare_batches(app, repository.candidates(app), config)
    stats = AnalysisStats(skipped=skipped,
                          deferred=sum(len(batch.sources) for batch in batches[config.max_batches_per_run:]))
    selected_batches = batches[:config.max_batches_per_run]
    for index, batch in enumerate(selected_batches):
        try:
            analysis_id = repository.reserve(app, batch.sources, config.model)
        except AnalysisBusy:
            stats.failed += 1
            stats.deferred += sum(len(b.sources) for b in selected_batches[index:])
            logger.error('analysis_busy', extra={'package': app.package_name})
            break
        if analysis_id is None:
            stats.deferred += len(batch.sources)
            continue
        response = None
        result = None
        error = None
        stats.calls += 1
        logger.info('analysis_started', extra={'package': app.package_name, 'analysis_id': analysis_id})
        try:
            response = provider.analyze(batch.messages)
            result = validate_result(batch, app, response).model_dump()
        except ProviderError as exc:
            # Seuls les codes définis localement peuvent être enregistrés.
            code = str(exc)
            error = code if code in {'llm_key_missing', 'llm_dependency_missing', 'llm_request_failed'} else 'llm_request_failed'
        except Exception:
            error = 'invalid_analysis_response'
        try:
            repository.finish(analysis_id, result, response, cost(response, config), error)
        except AnalysisBusy:
            # Une récupération manuelle ne doit pas faire oublier un appel déjà effectué.
            stats.failed += 1
            stats.deferred += sum(len(b.sources) for b in selected_batches[index+1:])
            logger.error('analysis_reservation_lost', extra={'package': app.package_name, 'analysis_id': analysis_id})
            break
        if result is None:
            stats.failed += 1
            stats.deferred += sum(len(b.sources) for b in selected_batches[index+1:])
            logger.error('analysis_failed', extra={'package': app.package_name, 'analysis_id': analysis_id})
            # Pas de nouvelle dépense implicite sur un lot invalide ni les lots suivants.
            break
        stats.completed += 1
        logger.info('analysis_completed', extra={'package': app.package_name, 'analysis_id': analysis_id})
    return stats
