"""Carregamento da configuração (config/site.yaml + config/sources.yaml + ambiente)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent

# Títulos de conteúdo de serviço, sem valor jornalístico para a edição (regex
# sobre o título normalizado: minúsculas, sem acento e sem pontuação). Evita
# "ao vivo" solto, que também derrubaria coberturas de mercado legítimas.
DEFAULT_EXCLUDE_TITLE_PATTERNS = [
    r"veja (?:o )?numero e nome",
    r"quem sao os candidatos",
    r"resultado d[oa] (?:concurso|mega ?sena|lotofacil|quina|lotomania|timemania|dupla ?sena)",
    r"confira o resultado",
    r"horoscopo",
    r"(?:como|onde) assistir",
    r"gabarito",
    r"transmite ao vivo",
]


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
    logo_svg_dark: str | None = None  # versão do logo para o modo escuro (sem ela: filtro de inversão)
    logo_height: int | None = None  # altura do logo no cabeçalho, em px (desktop)
    logo_height_mobile: int | None = None  # no celular (padrão: 85% de logo_height)
    favicon_svg: str | None = None
    email_logo: dict[str, Any] | None = None  # {src, width, height}: imagem servida em base_url + src
    email_logo_dark: dict[str, Any] | None = None  # versão do modo escuro (mesmo formato)


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
    # Fração mínima de feeds ok e de fontes em português com artigos: uma coleta
    # quase toda fora do ar (ex.: runner bloqueado pelos sites brasileiros) não
    # publica uma edição só em inglês por cima da boa.
    min_sources_ratio: float = 0.0
    min_pt_sources_ok: int = 0
    enrich_limit: int = 40
    exclude_url_patterns: list[str] = field(default_factory=list)
    exclude_title_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDE_TITLE_PATTERNS))


@dataclass
class LLMConfig:
    enabled: bool = True
    # Editores por IA, na ordem de tentativa. Cada um só entra com a sua chave
    # (MISTRAL_API_KEY, ANTHROPIC_API_KEY); se falhar, tenta o próximo e, por
    # fim, a edição automática.
    providers: list[str] = field(default_factory=lambda: ["mistral", "claude"])
    model: str = "claude-opus-5-5"
    effort: str = "medium"
    # O raciocínio do modelo conta dentro de max_tokens: com folga, uma pauta
    # grande não é cortada (o que descartaria a edição por IA inteira).
    max_tokens_select: int = 64000
    max_tokens_write: int = 64000
    timeout_seconds: int = 600
    # Prazo total da edição por IA (s): novas tentativas após erro passageiro só
    # acontecem se ainda couberem nele (o job do Actions tem 55 min).
    deadline_seconds: int = 2700
    # ── Mistral ──
    mistral_model: str = "mistral-large-latest"
    mistral_base_url: str = "https://api.mistral.ai/v1"
    mistral_max_tokens: int = 16000  # saída máxima de cada chamada
    # Plano gratuito (Experiment): poucas requisições por minuto. As chamadas saem
    # uma de cada vez, com pelo menos este intervalo entre o início de cada uma.
    mistral_parallel: int = 1  # chamadas simultâneas nas etapas em lotes
    mistral_min_interval_seconds: float = 30.0
    # ── Análise completa (editor Mistral) ──
    # Todas as notícias do dia são lidas e agrupadas por assunto, em lotes de
    # até topics_batch_chars caracteres; a redação sai em lotes de
    # write_batch_size matérias (respostas menores, sem corte por tamanho).
    topics_batch_chars: int = 100000
    write_batch_size: int = 12
    # Cobertura comparada: para cada assunto com coverage_min_outlets veículos
    # ou mais, os dois lados do debate, a posição de cada veículo e a conclusão.
    # Além das matérias da edição, até coverage_max_topics outros assuntos.
    coverage: bool = True
    coverage_min_outlets: int = 2
    coverage_max_topics: int = 80
    coverage_batch_chars: int = 45000


@dataclass
class CityConfig:
    name: str
    lat: float
    lon: float
    timezone: str


@dataclass
class EmailConfig:
    subject_template: str = "{brand} · {date}: {lead}"
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

    brands = site_raw["brands"]
    default_brand = site_raw.get("brand") or next(iter(brands))
    brand_key = env.get("QIJ_BRAND") or default_brand
    if brand_key not in brands:
        # Ex.: variável QIJ_BRAND=qi que sobrou da marca antiga no GitHub.
        log.warning("Marca %r não existe em config/site.yaml; usando %r", brand_key, default_brand)
        brand_key = default_brand
    brand_raw = dict(brands[brand_key])
    brand = BrandConfig(
        key=brand_key,
        name=brand_raw["name"],
        wordmark=list(brand_raw.get("wordmark") or [brand_raw["name"], "", ""]),
        tagline=brand_raw.get("tagline", ""),
        colors=dict(brand_raw["colors"]),
        section_palette=list(brand_raw.get("section_palette") or ["#1C49A5"]),
        logo_svg=_read_svg(root, brand_raw.get("logo_svg")),
        logo_svg_dark=_read_svg(root, brand_raw.get("logo_svg_dark")),
        logo_height=int(brand_raw["logo_height"]) if brand_raw.get("logo_height") else None,
        logo_height_mobile=int(brand_raw["logo_height_mobile"]) if brand_raw.get("logo_height_mobile") else None,
        favicon_svg=_read_svg(root, brand_raw.get("favicon_svg")),
        email_logo=dict(brand_raw["email_logo"]) if brand_raw.get("email_logo") else None,
        email_logo_dark=dict(brand_raw["email_logo_dark"]) if brand_raw.get("email_logo_dark") else None,
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
    if env.get("QIJ_MISTRAL_MODEL"):
        llm.mistral_model = env["QIJ_MISTRAL_MODEL"]
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
