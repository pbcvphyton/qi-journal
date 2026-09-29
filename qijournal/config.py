"""Carregamento da configuração (config/site.yaml + config/sources.yaml + ambiente)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class SourceConfig:
    id: str
    name: str
    url: str
    lang: str = "pt"
    weight: float = 1.0
    topics: list[str] = field(default_factory=list)
    enabled: bool = True
    exclude_url_patterns: list[str] = field(default_factory=list)


@dataclass
class SectionConfig:
    id: str
    title: str
    description: str
    keywords: list[str]
    color: str = "#1C49A5"  # atribuída a partir de brand.section_palette


@dataclass
class BrandConfig:
    key: str
    name: str
    wordmark: list[str]  # [destaque, normal, resto] — ver site.yaml
    tagline: str
    colors: dict[str, str]
    section_palette: list[str]
    logo_svg: str | None = None  # conteúdo SVG já lido do arquivo (ou None)
    favicon_svg: str | None = None


@dataclass
class SiteConfig:
    base_url: str
    repo_url: str
    timezone: str = "America/Sao_Paulo"
    archive_keep_days: int = 400


@dataclass
class EditionConfig:
    max_age_hours: int = 30
    max_candidates: int = 180
    target_stories: int = 24
    secondary_count: int = 3
    highlights_count: int = 8
    min_articles: int = 15
    min_sources_ok: int = 4
    enrich_limit: int = 40
    exclude_url_patterns: list[str] = field(default_factory=list)


@dataclass
class LLMConfig:
    enabled: bool = True
    model: str = "claude-opus-5-5"
    effort: str = "medium"
    max_tokens_select: int = 16000
    max_tokens_write: int = 48000
    timeout_seconds: int = 600


@dataclass
class CityConfig:
    name: str
    lat: float
    lon: float
    timezone: str


@dataclass
class EmailConfig:
    subject_template: str = "{brand} — {date_label}"
    max_stories: int = 14
    to: list[str] = field(default_factory=list)  # só via ambiente (EMAIL_TO)


@dataclass
class Config:
    root: Path
    site: SiteConfig
    brand: BrandConfig
    sections: list[SectionConfig]
    sources: list[SourceConfig]
    market: list[dict[str, Any]]  # entradas cruas de site.yaml → market (ver collect/market.py)
    weather: list[CityConfig]
    edition: EditionConfig
    llm: LLMConfig
    email: EmailConfig

    def section(self, section_id: str) -> SectionConfig:
        for s in self.sections:
            if s.id == section_id:
                return s
        raise KeyError(section_id)

    @property
    def section_ids(self) -> list[str]:
        return [s.id for s in self.sections]


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "sim", "on"}


def _read_svg(root: Path, rel: str | None) -> str | None:
    if not rel:
        return None
    path = root / rel
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8").strip()


def load_config(root: Path | None = None, env: Mapping[str, str] | None = None) -> Config:
    """Lê os YAMLs de ``<root>/config`` e aplica sobrescritas do ambiente."""
    root = Path(root) if root else ROOT
    env = os.environ if env is None else env

    site_raw = yaml.safe_load((root / "config" / "site.yaml").read_text(encoding="utf-8"))
    sources_raw = yaml.safe_load((root / "config" / "sources.yaml").read_text(encoding="utf-8"))

    site = SiteConfig(**site_raw["site"])
    if env.get("QIJ_BASE_URL"):
        site.base_url = env["QIJ_BASE_URL"]
    if not site.base_url.endswith("/"):
        site.base_url += "/"

    brand_key = env.get("QIJ_BRAND") or site_raw.get("brand", "qi")
    brand_raw = dict(site_raw["brands"][brand_key])
    brand = BrandConfig(
        key=brand_key,
        name=brand_raw["name"],
        wordmark=list(brand_raw.get("wordmark") or [brand_raw["name"], "", ""]),
        tagline=brand_raw.get("tagline", ""),
        colors=dict(brand_raw["colors"]),
        section_palette=list(brand_raw.get("section_palette") or ["#1C49A5"]),
        logo_svg=_read_svg(root, brand_raw.get("logo_svg")),
        favicon_svg=_read_svg(root, brand_raw.get("favicon_svg")),
    )

    sections = []
    for i, s in enumerate(site_raw["sections"]):
        sections.append(
            SectionConfig(
                id=s["id"],
                title=s["title"],
                description=s.get("description", ""),
                keywords=list(s.get("keywords", [])),
                color=brand.section_palette[i % len(brand.section_palette)],
            )
        )

    sources = [SourceConfig(**f) for f in sources_raw.get("feeds", [])]
    sources = [s for s in sources if s.enabled]

    edition = EditionConfig(**site_raw.get("edition", {}))

    llm = LLMConfig(**site_raw.get("llm", {}))
    if env.get("QIJ_MODEL"):
        llm.model = env["QIJ_MODEL"]
    if env.get("QIJ_EFFORT"):
        llm.effort = env["QIJ_EFFORT"]
    if _truthy(env.get("QIJ_NO_LLM")):
        llm.enabled = False

    weather = [CityConfig(**c) for c in site_raw.get("weather", [])]

    email = EmailConfig(**site_raw.get("email", {}))
    if env.get("EMAIL_TO"):
        email.to = [e.strip() for e in env["EMAIL_TO"].split(",") if e.strip()]

    return Config(
        root=root,
        site=site,
        brand=brand,
        sections=sections,
        sources=sources,
        market=list(site_raw.get("market", [])),
        weather=weather,
        edition=edition,
        llm=llm,
        email=email,
    )
