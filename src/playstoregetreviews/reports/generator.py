"""Rendu échappé et sauvegardes atomiques de fichiers privés."""
import os
from pathlib import Path
import tempfile
from jinja2 import Environment, PackageLoader, StrictUndefined, select_autoescape
from .repository import ReportRepository
from .schemas import ReportDocument


def template_environment() -> Environment:
    return Environment(loader=PackageLoader('playstoregetreviews', 'templates'),
                       autoescape=select_autoescape(['html']), undefined=StrictUndefined)


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.pending-')
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def render_html(document: ReportDocument, template: str = 'report.html') -> str:
    return template_environment().get_template(template).render(report=document)


def generate_report(repository: ReportRepository, report_id: int, directory: Path) -> tuple[Path, Path]:
    document = repository.get(report_id)
    json_path = directory / f'report-{report_id}.json'
    html_path = directory / f'report-{report_id}.html'
    atomic_write(json_path, document.model_dump_json(indent=2).encode('utf-8'))
    atomic_write(html_path, render_html(document).encode('utf-8'))
    repository.rendered(report_id)
    return json_path, html_path
