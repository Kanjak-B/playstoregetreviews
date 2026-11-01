"""Pagination bornée et reprises ; aucun échec réseau silencieux."""
import logging
import time
import ssl
import importlib
from typing import Any, Callable
from google_play_scraper import Sort, search as search_apps, app as app_details

# La version épinglée modifie ce global à son import : rétablir TLS vérifié.
ssl._create_default_https_context = ssl.create_default_context
_scraper = importlib.import_module("google_play_scraper.features.reviews")


def fetch_page(package: str, *, lang: str, country: str, sort: Sort, count: int,
               continuation_token: str | None):
    """Adaptateur à la version épinglée : laisser remonter les erreurs réseau.

    La fonction publique reviews() absorbe toutes les exceptions. Son parseur
    interne est réutilisé pour conserver une pagination dont les erreurs sont visibles.
    """
    items, token = _scraper._fetch_review_items(
        _scraper.Formats.Reviews.build(lang=lang, country=country),
        package, sort.value, count, None, None, continuation_token)
    page = [{key: spec.extract_content(item)
             for key, spec in _scraper.ElementSpecs.Review.items()} for item in items]
    return page, None if isinstance(token, list) else token

from ..config import AppConfig, Settings

logger = logging.getLogger(__name__)


def fetch_reviews(app: AppConfig, settings: Settings, fetch: Callable = fetch_page,
                  sleep: Callable = time.sleep) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    token = None
    while len(collected) < app.max_fetch:
        for attempt in range(settings.retries + 1):
            try:
                page, next_token = fetch(app.package_name, lang=app.language, country=app.country,
                                         sort=Sort.NEWEST, count=min(200, app.max_fetch-len(collected)),
                                         continuation_token=token)
                break
            except Exception:
                # Ne pas journaliser le message externe : il peut contenir des données privées.
                logger.warning('fetch_retry', extra={'package': app.package_name, 'attempt': attempt+1})
                if attempt == settings.retries:
                    raise RuntimeError("Échec de collecte après reprises") from None
                sleep(min(2 ** attempt, 30))
        collected.extend(page[:app.max_fetch-len(collected)])
        if not page or not next_token:
            break
        if next_token == token:
            raise RuntimeError("Pagination bloquée")
        token = next_token
        sleep(settings.pause_seconds)
    return collected
