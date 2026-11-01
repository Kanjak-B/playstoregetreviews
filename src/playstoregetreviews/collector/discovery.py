"""Play Store name/URL discovery without selecting an ambiguous result silently."""
from dataclasses import dataclass
import re
import socket
from urllib.parse import parse_qs, urlparse
from . import google_play
from ..analysis.preparation import redact

PACKAGE = re.compile(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+')


@dataclass(frozen=True)
class StoreApp:
    package_name: str
    title: str
    developer: str
    description: str = ''


def store_app(data: dict) -> StoreApp | None:
    package = data.get('appId', '')
    if not isinstance(package, str) or not PACKAGE.fullmatch(package):
        return None
    return StoreApp(package, redact(str(data.get('title') or package)),
                    redact(str(data.get('developer') or '')), str(data.get('summary') or ''))


def discover(query: str, language: str = 'fr', country: str = 'cd',
             timeout: float = 20) -> list[StoreApp]:
    query = query.strip()
    if not query or len(query) > 500:
        raise ValueError("Saisissez un nom d'application ou son lien Google Play")
    package = None
    if query.startswith(('https://', 'http://')):
        url = urlparse(query)
        if url.hostname != 'play.google.com' or url.path != '/store/apps/details':
            raise ValueError('Lien Google Play invalide')
        package = parse_qs(url.query).get('id', [''])[0]
        if not PACKAGE.fullmatch(package):
            raise ValueError('Identifiant absent du lien Google Play')
    previous = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(timeout)
        if package is not None:
            data = google_play.app_details(package, lang=language, country=country)
            data['appId'] = package
            result = store_app(data)
            return [result] if result else []
        data = google_play.search_apps(query, n_hits=10, lang=language, country=country)
        results = {}
        for item in data:
            result = store_app(item)
            if result:
                results.setdefault(result.package_name, result)
        return list(results.values())
    except Exception:
        raise ValueError('Recherche Google Play impossible : vérifiez le réseau, le pays ou utilisez le lien exact') from None
    finally:
        socket.setdefaulttimeout(previous)
