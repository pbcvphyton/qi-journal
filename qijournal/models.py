"""Modelos de dados compartilhados por todas as etapas do pipeline.

Tudo aqui é serializável em JSON (``to_dict``/``from_dict``) para que cada
etapa (coleta → edição → renderização) possa ser executada e testada de forma
isolada a partir de arquivos.

Convenções:
- Datas/horas são strings ISO 8601 em UTC (``2026-09-29T08:07:00+00:00``).
- Textos de artigos coletados são *texto puro* (sem HTML, entidades já
  decodificadas).
- Textos escritos pelo editor (``Story.body``, ``Edition.briefing``,
  ``Edition.editorial``) podem conter apenas a marcação ``**negrito**``;
  ``headline`` e ``dek`` são sempre texto puro, sem marcação.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any


def _from_dict(cls, data: dict[str, Any]):
    """Constrói um dataclass ignorando chaves desconhecidas (compatibilidade)."""
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in names})


@dataclass
class Article:
    """Uma notícia coletada de um feed."""

    id: str  # sha1(url canônica)[:12]
    url: str  # URL canônica (sem utm_*, redirecionadores desembrulhados)
    title: str
    summary: str  # texto puro, até ~1500 caracteres
    source_id: str  # ex.: "valor"
    source_name: str  # ex.: "Valor Econômico"
    lang: str  # "pt" | "en" | "es"
    published: str | None = None  # ISO 8601 UTC
    image: str | None = None  # URL http(s) de imagem, se houver
    topics: list[str] = field(default_factory=list)  # dicas de seção vindas do feed
    weight: float = 1.0  # peso editorial da fonte
    feed_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Article":
        return _from_dict(cls, data)


@dataclass
class SourceStatus:
    """Resultado da coleta de um feed."""

    source_id: str
    source_name: str
    url: str
    ok: bool
    items: int = 0  # itens aproveitados após filtros
    error: str | None = None
    elapsed_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SourceStatus":
        return _from_dict(cls, data)


@dataclass
class Quote:
    """Cotação ou indicador exibido no ticker."""

    id: str  # ex.: "usd", "ibov", "ipca12m"
    label: str  # ex.: "Dólar", "Ibovespa", "IPCA 12m"
    value: float
    display: str  # já formatado em pt-BR, ex.: "R$ 5,22", "182.991 pts", "4,22%"
    change_pct: float | None = None  # variação diária em %, None para indicadores
    kind: str = "quote"  # "fx" | "index" | "commodity" | "crypto" | "rate" | "inflation"
    as_of: str | None = None  # ISO 8601 ou data de referência (ex.: "2026-08")
    source: str | None = None  # ex.: "Yahoo Finance", "BCB/SGS"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Quote":
        return _from_dict(cls, data)


@dataclass
class CityWeather:
    """Clima de uma cidade (hoje e amanhã)."""

    city: str
    current_c: float | None
    current_emoji: str
    current_desc: str
    today_min: float | None
    today_max: float | None
    tomorrow_min: float | None
    tomorrow_max: float | None
    tomorrow_emoji: str
    tomorrow_desc: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CityWeather":
        return _from_dict(cls, data)


@dataclass
class Bundle:
    """Tudo o que foi coletado numa execução (entrada do editor)."""

    collected_at: str  # ISO 8601 UTC
    articles: list[Article]
    quotes: list[Quote]
    weather: list[CityWeather]
    sources: list[SourceStatus]

    def to_dict(self) -> dict[str, Any]:
        return {
            "collected_at": self.collected_at,
            "articles": [a.to_dict() for a in self.articles],
            "quotes": [q.to_dict() for q in self.quotes],
            "weather": [w.to_dict() for w in self.weather],
            "sources": [s.to_dict() for s in self.sources],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Bundle":
        return cls(
            collected_at=data["collected_at"],
            articles=[Article.from_dict(a) for a in data.get("articles", [])],
            quotes=[Quote.from_dict(q) for q in data.get("quotes", [])],
            weather=[CityWeather.from_dict(w) for w in data.get("weather", [])],
            sources=[SourceStatus.from_dict(s) for s in data.get("sources", [])],
        )


@dataclass
class SourceRef:
    """Crédito/link para a matéria original."""

    name: str
    url: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SourceRef":
        return _from_dict(cls, data)


@dataclass
class Story:
    """Uma matéria da edição (pode agrupar vários artigos sobre o mesmo fato)."""

    id: str  # slug único e estável dentro da edição (usado em âncoras #s-<id>)
    section: str  # id de seção definido em config/site.yaml
    headline: str  # texto puro, pt-BR
    dek: str  # linha fina (1 frase), texto puro
    body: list[str]  # 1-4 parágrafos; só "**negrito**" é permitido
    sources: list[SourceRef]
    article_ids: list[str]
    importance: int = 3  # 1 (baixa) a 5 (manchete)
    why_it_matters: str | None = None  # "Por que importa" (1 frase), texto puro
    image: str | None = None
    published: str | None = None  # ISO 8601 UTC do artigo principal

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["sources"] = [s.to_dict() for s in self.sources]
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Story":
        d = dict(data)
        d["sources"] = [SourceRef.from_dict(s) for s in d.get("sources", [])]
        return _from_dict(cls, d)


@dataclass
class Section:
    id: str
    title: str
    color: str
    story_ids: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Section":
        return _from_dict(cls, data)


@dataclass
class EditionStats:
    sources_total: int = 0
    sources_ok: int = 0
    sources_failed: list[dict[str, Any]] = field(default_factory=list)  # {source_id, url, error}
    articles_collected: int = 0
    articles_considered: int = 0
    llm_input_tokens: int | None = None
    llm_output_tokens: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EditionStats":
        return _from_dict(cls, data)


@dataclass
class Edition:
    """A edição do dia, pronta para renderização."""

    date: str  # "2026-09-29" (data local, America/Sao_Paulo)
    date_label: str  # "Terça-feira, 29 de setembro de 2026"
    generated_at: str  # ISO 8601 UTC
    mode: str  # "ai" | "heuristic"
    model: str | None  # modelo usado no modo "ai"
    editorial: str  # abertura do dia (2-3 frases); pode ser "" no modo heurístico
    briefing: list[str]  # "Em 1 minuto": 4-6 frases curtas
    lead: str  # id da manchete
    secondary: list[str]  # ids das 3 chamadas ao lado da manchete
    highlights: list[str]  # ids da lista numerada "Destaques"
    sections: list[Section]
    stories: list[Story]
    quotes: list[Quote]
    weather: list[CityWeather]
    stats: EditionStats = field(default_factory=EditionStats)

    def story(self, story_id: str) -> Story:
        for s in self.stories:
            if s.id == story_id:
                return s
        raise KeyError(story_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "date_label": self.date_label,
            "generated_at": self.generated_at,
            "mode": self.mode,
            "model": self.model,
            "editorial": self.editorial,
            "briefing": list(self.briefing),
            "lead": self.lead,
            "secondary": list(self.secondary),
            "highlights": list(self.highlights),
            "sections": [s.to_dict() for s in self.sections],
            "stories": [s.to_dict() for s in self.stories],
            "quotes": [q.to_dict() for q in self.quotes],
            "weather": [w.to_dict() for w in self.weather],
            "stats": self.stats.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Edition":
        return cls(
            date=data["date"],
            date_label=data["date_label"],
            generated_at=data["generated_at"],
            mode=data["mode"],
            model=data.get("model"),
            editorial=data.get("editorial", ""),
            briefing=list(data.get("briefing", [])),
            lead=data["lead"],
            secondary=list(data.get("secondary", [])),
            highlights=list(data.get("highlights", [])),
            sections=[Section.from_dict(s) for s in data.get("sections", [])],
            stories=[Story.from_dict(s) for s in data.get("stories", [])],
            quotes=[Quote.from_dict(q) for q in data.get("quotes", [])],
            weather=[CityWeather.from_dict(w) for w in data.get("weather", [])],
            stats=EditionStats.from_dict(data.get("stats", {})),
        )
