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


API_MODES = ("blocos", "etapas")


@dataclass
class ApiConfig:
    """Um editor por IA com API de chat no formato OpenAI (``/chat/completions``).

    ``mode``: ``"blocos"`` (todas as notícias divididas por editoria, uma
    chamada por bloco, mais o fechamento) ou ``"etapas"`` (agrupamento, pauta,
    redação e cobertura em chamadas separadas).
    """

    key_env: str  # variável de ambiente (segredo do GitHub) com a chave
    base_url: str
    model: str
    mode: str = "blocos"
    max_tokens: int = 16000  # saída máxima de cada chamada (inclui o raciocínio do modelo)
    token_param: str = "max_tokens"  # modelos de raciocínio da OpenAI: "max_completion_tokens"
    temperature: float | None = 0.2  # None: não envia (modelos que só aceitam o padrão)
    context_tokens: int = 128000  # janela de contexto do modelo (entrada + saída)
    parallel: int = 1  # chamadas simultâneas
    min_interval_seconds: float = 0.0  # intervalo mínimo entre o início de duas chamadas
    max_requests: int = 0  # teto de requisições por edição, contando novas tentativas (0 = sem teto)
    max_retries: int = 3  # novas tentativas após erro passageiro
    timeout_seconds: int = 300  # espera máxima pela resposta de cada chamada


DEFAULT_APIS: dict[str, dict[str, Any]] = {
    # Plano gratuito: todos os modelos (inclusive GPT-5.5), 10 requisições por hora.
    "aiml": {
        "key_env": "AIMLAPI_KEY",
        "base_url": "https://api.aimlapi.com/v1",
        "model": "openai/gpt-5-5",
        "max_tokens": 64000,
        "token_param": "max_completion_tokens",
        "temperature": None,
        "context_tokens": 1050000,
        "parallel": 3,
        "max_requests": 10,
        "max_retries": 1,
        "timeout_seconds": 900,
    },
    # Beta gratuito: 1.500 requisições a cada 5 horas por modelo; 256k de contexto, 64k de saída.
    "sensenova": {
        "key_env": "SENSENOVA_API_KEY",
        "base_url": "https://token.sensenova.ai/v1",
        "model": "sensenova-6.8-flash-lite",
        "max_tokens": 48000,
        "context_tokens": 256000,
        "parallel": 2,
        "timeout_seconds": 600,
    },
    # Plano gratuito (Experiment): cota mensal folgada, poucas requisições por minuto.
    "mistral": {
        "key_env": "MISTRAL_API_KEY",
        "base_url": "https://api.mistral.ai/v1",
        "model": "mistral-large-latest",
        "max_tokens": 32000,
        "context_tokens": 128000,
        "min_interval_seconds": 30,
        "timeout_seconds": 600,
    },
    # Pago (recarga mínima de US$ 1; nível 0: 3 requisições por minuto).
    "kimi": {
        "key_env": "MOONSHOT_API_KEY",
        "base_url": "https://api.moonshot.ai/v1",
        "model": "kimi-k3",
        "max_tokens": 64000,
        "temperature": None,
        "context_tokens": 1048576,
        "min_interval_seconds": 21,
        "timeout_seconds": 900,
    },
}


@dataclass
class BlockGroup:
    """Um bloco da compilação por editoria: seções afins lidas numa mesma chamada."""

    name: str
    sections: list[str]


DEFAULT_BLOCK_GROUPS: list[dict[str, Any]] = [
    {"name": "Economia & Mercados", "sections": ["brasil", "mercados"]},
    {"name": "Política & Justiça", "sections": ["politica", "juridico"]},
    {"name": "Empresas, Tecnologia & Imobiliário", "sections": ["tecnologia", "imobiliario"]},
    {"name": "Mundo & Natureza", "sections": ["mundo", "natureza"]},
    {"name": "Esporte, Cultura & Variedades", "sections": ["esporte", "variedades"]},
]


def _block_groups(raw: Any) -> list[BlockGroup]:
    items = DEFAULT_BLOCK_GROUPS if raw is None else raw
    return [BlockGroup(name=str(g["name"]), sections=[str(x) for x in g.get("sections") or []]) for g in items or []]


def _api_configs(raw: Mapping[str, Any] | None) -> dict[str, ApiConfig]:
    """``llm.apis`` do site.yaml por cima de :data:`DEFAULT_APIS` (campo a campo)."""
    merged: dict[str, dict[str, Any]] = {name: dict(values) for name, values in DEFAULT_APIS.items()}
    for name, values in (raw or {}).items():
        merged.setdefault(name, {}).update(values or {})
    apis = {name: ApiConfig(**values) for name, values in merged.items()}
    for name, api in apis.items():
        if api.mode not in API_MODES:
            log.warning("llm.apis.%s.mode inválido (%r); usando 'blocos'", name, api.mode)
            api.mode = "blocos"
    return apis


@dataclass
class LLMConfig:
    enabled: bool = True
    # Editores por IA, na ordem de tentativa. Cada um só entra com a sua chave
    # (ver apis e ANTHROPIC_API_KEY); se falhar, tenta o próximo e, por fim, a
    # edição automática.
    providers: list[str] = field(default_factory=lambda: ["aiml", "sensenova", "mistral", "kimi", "claude"])
    apis: dict[str, ApiConfig] = field(default_factory=lambda: _api_configs(None))
    # ── Modo "blocos" ──
    # Todas as notícias do dia divididas por editoria (block_groups: seções
    # afins numa mesma chamada), mais uma chamada de fechamento (manchete,
    # editorial, "Em 1 minuto" e assuntos repetidos entre blocos). Um bloco que
    # não cabe no limite da chamada (entrada ou saída do modelo) é dividido em
    # partes iguais, até max_calls chamadas no total. Sem block_groups: `blocks`
    # partes de tamanho igual.
    block_groups: list[BlockGroup] = field(default_factory=lambda: _block_groups(None))
    blocks: int = 5
    max_calls: int = 9
    block_closing: bool = True
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
    # ── Modo "etapas" ──
    # Todas as notícias do dia são lidas e agrupadas por assunto, em lotes de
    # até topics_batch_chars caracteres; a redação sai em lotes de
    # write_batch_size matérias (respostas menores, sem corte por tamanho).
    topics_batch_chars: int = 100000
    write_batch_size: int = 12
    # Cobertura comparada (os dois modos): para cada assunto com
    # coverage_min_outlets veículos ou mais, os dois lados do debate, a posição
    # de cada veículo e a conclusão. Além das matérias da edição, até
    # coverage_max_topics outros assuntos.
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

    llm_raw = dict(site_raw.get("llm") or {})
    apis = _api_configs(llm_raw.pop("apis", None))
    groups = _block_groups(llm_raw.pop("block_groups", None))
    llm = LLMConfig(**llm_raw, apis=apis, block_groups=groups)
    if env.get("QIJ_MODEL"):
        llm.model = env["QIJ_MODEL"]
    if env.get("QIJ_EFFORT"):
        llm.effort = env["QIJ_EFFORT"]
    for name, api in llm.apis.items():  # QIJ_AIML_MODEL, QIJ_MISTRAL_MODEL, QIJ_KIMI_MODEL…
        model = env.get(f"QIJ_{name.upper()}_MODEL")
        if model:
            api.model = model
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
