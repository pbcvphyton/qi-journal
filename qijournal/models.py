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


STANCES = ("a", "b", "neutro")


@dataclass
class CoverageOutlet:
    """Como um veículo conduziu a cobertura de um assunto."""

    name: str  # ex.: "Folha de S.Paulo"
    stance: str  # "a" | "b" | "neutro" (lados definidos em Coverage)
    # Interpretação do foco do veículo (1-2 frases): o que priorizou, que
    # enquadramento deu e por que isso o põe no lado A, B ou neutro; texto puro.
    framing: str = ""
    url: str | None = None  # artigo do veículo sobre o assunto

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CoverageOutlet":
        return _from_dict(cls, data)


@dataclass
class Coverage:
    """Cobertura comparada de um assunto: os dois lados do debate, a posição de
    cada veículo e a conclusão (em que sentido a cobertura seguiu).

    Sem debate (todos relatam o fato do mesmo jeito), ``side_a``/``side_b`` ficam
    vazios e todos os veículos, ``"neutro"``.
    """

    topic: str  # título curto e neutro do assunto, texto puro
    conclusion: str  # 1-2 frases, texto puro
    outlets: list[CoverageOutlet]
    side_a: str = ""
    side_b: str = ""
    section: str | None = None
    published: str | None = None  # ISO 8601 UTC do artigo mais recente
    url: str | None = None  # artigo principal (assuntos que não viraram matéria)
    article_ids: list[str] = field(default_factory=list)  # notícias do assunto (Article.id)

    @property
    def has_debate(self) -> bool:
        return bool(self.side_a and self.side_b)

    def count(self, stance: str) -> int:
        return sum(1 for o in self.outlets if o.stance == stance)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["outlets"] = [o.to_dict() for o in self.outlets]
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Coverage":
        d = dict(data)
        d["outlets"] = [CoverageOutlet.from_dict(o) for o in d.get("outlets") or [] if isinstance(o, dict)]
        return _from_dict(cls, d)


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
    lang: str = "pt"  # idioma de título/linha fina/corpo ("en" quando a edição automática usa o original)
    coverage: Coverage | None = None  # cobertura comparada (só na edição por IA com análise completa)
    block: str | None = None  # bloco por editoria em que a IA compilou a matéria (modo blocos)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["sources"] = [s.to_dict() for s in self.sources]
        d["coverage"] = self.coverage.to_dict() if self.coverage else None
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Story":
        d = dict(data)
        d["sources"] = [SourceRef.from_dict(s) for s in d.get("sources", [])]
        d["coverage"] = Coverage.from_dict(d["coverage"]) if isinstance(d.get("coverage"), dict) else None
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
class IndexItem:
    """Uma notícia na lista completa do dia."""

    source: str
    title: str
    url: str
    published: str | None = None
    lang: str = "pt"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "IndexItem":
        return _from_dict(cls, data)


@dataclass
class IndexTopic:
    """Um assunto na lista completa do dia ("Todas as notícias"): as notícias
    sobre ele, da mais relevante para a menos. Nenhuma notícia coletada fica de
    fora da lista."""

    section: str
    title: str
    items: list[IndexItem]
    story_id: str | None = None  # virou matéria da edição
    compared: bool = False  # tem cobertura comparada (seção "Cobertura comparada")

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["items"] = [i.to_dict() for i in self.items]
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "IndexTopic":
        d = dict(data)
        d["items"] = [IndexItem.from_dict(i) for i in d.get("items") or [] if isinstance(i, dict)]
        return _from_dict(cls, d)


@dataclass
class BlockInfo:
    """Um bloco da compilação por editoria (modo blocos)."""

    name: str  # ex.: "Economia & Mercados" ou "Economia & Mercados (parte 1 de 2)"
    sections: list[str]
    articles: int = 0  # notícias lidas pela IA no bloco
    groups: int = 0  # grupos automáticos (títulos parecidos) no bloco
    stories: int = 0  # matérias redigidas no bloco
    compared: int = 0  # outros assuntos com cobertura comparada
    ok: bool = True  # a chamada do bloco deu certo

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BlockInfo":
        return _from_dict(cls, data)


@dataclass
class Rationale:
    """Racional da compilação: como a edição foi montada a partir do noticiário."""

    method: str = "automática"  # "blocos" | "etapas" | "duas chamadas" | "automática"
    provider: str | None = None  # editor por IA usado (ex.: "aiml")
    articles: int = 0  # notícias coletadas
    outlets: int = 0  # veículos distintos
    groups: int = 0  # grupos automáticos por títulos parecidos
    topics: int = 0  # assuntos depois de a IA unir as notícias repetidas (0 = sem união por IA)
    calls: int = 0  # chamadas à IA
    blocks: list[BlockInfo] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["blocks"] = [b.to_dict() for b in self.blocks]
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Rationale":
        d = dict(data)
        d["blocks"] = [BlockInfo.from_dict(b) for b in d.get("blocks") or [] if isinstance(b, dict)]
        return _from_dict(cls, d)


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
    # Radar: notícias recentes que não viraram matéria
    # ({"title", "url", "source", "published", "section"}); vazio em edições antigas.
    wire: list[dict[str, Any]] = field(default_factory=list)
    # Cobertura comparada dos demais assuntos do dia (vistos por 2+ veículos) que
    # não viraram matéria; vazio sem a análise completa e em edições antigas.
    compared: list[Coverage] = field(default_factory=list)
    # Racional da compilação e lista completa do dia (todas as notícias por
    # assunto); vazios em edições antigas.
    rationale: Rationale | None = None
    index: list[IndexTopic] = field(default_factory=list)

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
            "wire": [dict(item) for item in self.wire],
            "compared": [c.to_dict() for c in self.compared],
            "rationale": self.rationale.to_dict() if self.rationale else None,
            "index": [t.to_dict() for t in self.index],
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
            wire=[dict(item) for item in data.get("wire") or [] if isinstance(item, dict)],
            compared=[Coverage.from_dict(c) for c in data.get("compared") or [] if isinstance(c, dict)],
            rationale=Rationale.from_dict(data["rationale"]) if isinstance(data.get("rationale"), dict) else None,
            index=[IndexTopic.from_dict(t) for t in data.get("index") or [] if isinstance(t, dict)],
        )
