"""Bundle pequeno e realista para os testes do editor (construído em código).

Cobre as 7 seções da configuração, notícias em pt e en, o mesmo fato em várias
fontes, artigos com e sem imagem, sem data, antigos, "ao vivo", título curto e
um título malicioso com HTML. As datas são relativas a :data:`NOW`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from qijournal.models import Article, Bundle, CityWeather, Quote, SourceStatus

NOW = datetime(2026, 9, 29, 8, 7, tzinfo=UTC)

SOURCE_NAMES = {
    "valor": "Valor Econômico",
    "folha": "Folha de S.Paulo",
    "estadao": "Estadão",
    "g1": "g1",
    "infomoney": "InfoMoney",
    "moneytimes": "Money Times",
    "ft": "Financial Times",
    "wsj": "The Wall Street Journal",
    "nyt": "The New York Times",
    "guardian": "The Guardian",
    "jota": "JOTA",
    "conjur": "Conjur",
    "poder360": "Poder360",
    "cnbc": "CNBC",
    "scmp": "South China Morning Post",
    "braziljournal": "Brazil Journal",
    "imobireport": "Imobi Report",
}


def make_article(
    article_id: str,
    title: str,
    summary: str = "",
    *,
    source_id: str = "valor",
    lang: str = "pt",
    hours_ago: float | None = 2.0,
    image: str | None = None,
    topics: tuple[str, ...] = (),
    weight: float = 1.0,
) -> Article:
    """Artigo de teste; ``hours_ago=None`` gera artigo sem data."""
    published = None if hours_ago is None else (NOW - timedelta(hours=hours_ago)).isoformat()
    return Article(
        id=article_id,
        url=f"https://{source_id}.example.com/noticia/{article_id}",
        title=title,
        summary=summary,
        source_id=source_id,
        source_name=SOURCE_NAMES.get(source_id, source_id),
        lang=lang,
        published=published,
        image=image,
        topics=list(topics),
        weight=weight,
    )


def _img(name: str) -> str:
    return f"https://img.example.com/{name}.jpg"


# Seções criadas depois deste exemplo (sem artigos nele): Esporte, Natureza e
# Cultura & Variedades.
SECTIONS_WITHOUT_SAMPLE = {"esporte", "natureza", "variedades"}


def sample_sections(config) -> set[str]:
    """Seções que têm artigos no exemplo."""
    return set(config.section_ids) - SECTIONS_WITHOUT_SAMPLE


def sample_articles() -> list[Article]:
    a = make_article
    return [
        # ── Brasil: Copom (mesmo fato em 4 fontes) ─────────────────────────
        a(
            "copom-valor",
            "Copom mantém Selic em 15% ao ano pela quinta reunião seguida",
            "O Comitê de Política Monetária (Copom) do Banco Central manteve a taxa Selic em 15% ao ano, "
            "em decisão unânime. No comunicado, o colegiado afirmou que a inflação segue acima da meta e "
            "que o cenário externo exige cautela. A decisão veio em linha com as projeções do mercado.\n"
            "Economistas ouvidos pelo Valor esperam que o ciclo de cortes comece apenas no próximo ano, "
            "a depender da trajetória do IPCA e das expectativas.",
            source_id="valor",
            hours_ago=3,
            image=_img("copom"),
            topics=("brasil",),
            weight=1.3,
        ),
        a(
            "copom-folha",
            "Copom mantém Selic em 15% e sinaliza cautela com inflação",
            "O Banco Central manteve a Selic em 15% ao ano nesta terça. O Copom citou inflação ainda acima da meta.",
            source_id="folha",
            hours_ago=2.5,
            topics=("brasil",),
            weight=1.1,
        ),
        a(
            "copom-g1",
            "Copom mantém taxa Selic em 15% ao ano",
            "Decisão do Copom foi unânime e veio em linha com o esperado pelo mercado financeiro.",
            source_id="g1",
            hours_ago=2,
            topics=("brasil",),
            weight=1.0,
        ),
        a(
            "copom-estadao",
            "Copom mantém Selic em 15% ao ano pela quinta vez",
            "",
            source_id="estadao",
            hours_ago=2.2,
            topics=("brasil",),
            weight=1.1,
        ),
        # ── Brasil: fiscal ─────────────────────────────────────────────────
        a(
            "fiscal-valor",
            "Governo bloqueia R$ 12 bilhões do Orçamento para cumprir arcabouço fiscal",
            "O Ministério da Fazenda anunciou bloqueio de R$ 12 bilhões em despesas discricionárias. "
            "Segundo Haddad, a medida garante o cumprimento da meta fiscal do ano.",
            source_id="valor",
            hours_ago=5,
            image=_img("fiscal"),
            topics=("brasil",),
            weight=1.3,
        ),
        # ── Mercados ───────────────────────────────────────────────────────
        a(
            "ibov-infomoney",
            "Ibovespa fecha em alta de 1,2% puxado por bancos e Petrobras",
            "O Ibovespa subiu 1,2% e fechou aos 182.991 pontos. Ações de bancos e da Petrobras lideraram "
            "os ganhos, enquanto o dólar recuou frente ao real.",
            source_id="infomoney",
            hours_ago=10,
            image=_img("ibov"),
            topics=("mercados",),
            weight=1.0,
        ),
        a(
            "ibov-moneytimes",
            "Ibovespa fecha em alta de 1,2% com bancos e Petrobras",
            "Índice fechou aos 182.991 pontos.",
            source_id="moneytimes",
            hours_ago=10.5,
            topics=("mercados",),
            weight=0.8,
        ),
        a(
            "fed-ft",
            "Federal Reserve cuts interest rates by a quarter point",
            "The Federal Reserve lowered its benchmark rate by 0.25 percentage points, citing a cooling "
            "labour market. Chair Jerome Powell said further cuts would depend on incoming data.",
            source_id="ft",
            lang="en",
            hours_ago=12,
            image=_img("fed"),
            topics=("mercados",),
            weight=1.2,
        ),
        a(
            "fed-wsj",
            "Federal Reserve cuts interest rates by quarter point amid cooling labor market",
            "The Fed cut rates by a quarter point.",
            source_id="wsj",
            lang="en",
            hours_ago=12.5,
            topics=("mercados",),
            weight=1.1,
        ),
        a(
            "ipo-bj",
            "Empresa de energia solar protocola pedido de IPO na B3",
            "A companhia pretende levantar cerca de R$ 2 bilhões na oferta, segundo o prospecto preliminar "
            "enviado à CVM.",
            source_id="braziljournal",
            hours_ago=7,
            topics=("mercados",),
            weight=1.1,
        ),
        # ── Jurídico ───────────────────────────────────────────────────────
        a(
            "stf-jota",
            "STF decide que ICMS não integra base de cálculo de contribuição previdenciária",
            "Por maioria, o Supremo Tribunal Federal (STF) concluiu o julgamento e fixou a tese. "
            "A decisão tem repercussão geral e afeta milhares de processos tributários.",
            source_id="jota",
            hours_ago=6,
            topics=("juridico",),
            weight=1.2,
        ),
        a(
            "stf-conjur",
            "STF decide que ICMS não integra base de cálculo da contribuição previdenciária",
            "Julgamento no plenário virtual terminou com placar de 7 a 4.",
            source_id="conjur",
            hours_ago=6.5,
            topics=("juridico",),
            weight=1.2,
        ),
        a(
            "cvm-valor",
            "CVM aprova nova regra para fundos de investimento em direitos creditórios",
            "A Comissão de Valores Mobiliários (CVM) publicou resolução que altera a regulação dos FIDCs.",
            source_id="valor",
            hours_ago=8,
            topics=("juridico",),
            weight=1.3,
        ),
        # ── Política ───────────────────────────────────────────────────────
        a(
            "camara-poder",
            "Câmara aprova projeto que muda regras do Imposto de Renda",
            "O plenário da Câmara dos Deputados aprovou o texto-base do projeto. A proposta segue para o Senado.",
            source_id="poder360",
            hours_ago=9,
            topics=("politica",),
            weight=0.9,
        ),
        a(
            "pesquisa-folha",
            "Pesquisa Datafolha mostra Lula e Tarcísio empatados no segundo turno",
            "Levantamento ouviu 2.000 eleitores entre segunda e terça.",
            source_id="folha",
            hours_ago=14,
            image=_img("pesquisa"),
            topics=("politica",),
            weight=1.1,
        ),
        # ── Mundo ──────────────────────────────────────────────────────────
        a(
            "tarifa-nyt",
            "China retaliates with new tariffs on American farm goods",
            "Beijing announced tariffs of up to 25% on soybeans and pork imported from the United States.",
            source_id="nyt",
            lang="en",
            hours_ago=11,
            image=_img("china"),
            topics=("mundo",),
            weight=1.1,
        ),
        a(
            "tarifa-guardian",
            "China retaliates with new tariffs on US farm goods",
            "The move escalates the trade war between the two largest economies.",
            source_id="guardian",
            lang="en",
            hours_ago=11.5,
            topics=("mundo",),
            weight=1.0,
        ),
        a(
            "milei-valor",
            "Argentina de Milei fecha acordo com FMI para liberar US$ 5 bilhões",
            "O governo argentino anunciou entendimento com o Fundo Monetário Internacional.",
            source_id="valor",
            hours_ago=15,
            topics=("mundo",),
            weight=1.3,
        ),
        # ── Tecnologia ─────────────────────────────────────────────────────
        a(
            "nvidia-cnbc",
            "Nvidia unveils new AI chip as demand from data centers surges",
            "The company said the new semiconductor doubles performance for artificial intelligence workloads.",
            source_id="cnbc",
            lang="en",
            hours_ago=13,
            image=_img("nvidia"),
            topics=("tecnologia",),
            weight=0.9,
        ),
        a(
            "startup-valor",
            "Startup brasileira de pagamentos capta R$ 300 milhões em rodada liderada por fundo americano",
            "A empresa de tecnologia financeira vai usar os recursos para expandir para o México.",
            source_id="valor",
            hours_ago=16,
            topics=("tecnologia",),
            weight=1.3,
        ),
        # ── Imobiliário ────────────────────────────────────────────────────
        a(
            "fii-infomoney",
            "IFIX renova máxima e fundos imobiliários de galpões lideram ganhos",
            "O índice de fundos imobiliários subiu 0,8%. FIIs de logística tiveram a maior alta do mês.",
            source_id="infomoney",
            hours_ago=9,
            image=_img("ifix"),
            topics=("imobiliario",),
            weight=1.0,
        ),
        a(
            "credito-imobireport",
            "Crédito imobiliário com recursos da poupança cai 12% em agosto, diz Abecip",
            "Financiamentos imobiliários somaram R$ 15 bilhões no mês, segundo a associação.",
            source_id="imobireport",
            hours_ago=20,
            topics=("imobiliario",),
            weight=0.9,
        ),
        a(
            "china-property-scmp",
            "China property developers face fresh debt crunch as sales slump",
            "Home sales by the top 100 developers fell 30% in September, data showed.",
            source_id="scmp",
            lang="en",
            hours_ago=18,
            topics=("imobiliario",),
            weight=0.7,
        ),
        # ── Casos de borda ─────────────────────────────────────────────────
        a(
            "aovivo-g1",
            "Ao vivo: acompanhe a sessão da CPI no Senado",
            "Acompanhe em tempo real.",
            source_id="g1",
            hours_ago=1,
            topics=("politica",),
            weight=1.0,
        ),
        a(
            "curto-g1",
            "Dólar sobe",
            "Moeda americana avançou.",
            source_id="g1",
            hours_ago=1,
            topics=("mercados",),
            weight=1.0,
        ),
        a(
            "xss-estadao",
            "<script>alert(1)</script> Banco Central anuncia **nova** regra para o #Pix",
            "O Banco Central anunciou novas regras de segurança para o Pix, que passam a valer em novembro.",
            source_id="estadao",
            hours_ago=4,
            topics=("brasil",),
            weight=1.1,
        ),
        a(
            "semdata-guardian",
            "European Central Bank holds rates steady for third meeting",
            "The ECB kept its deposit rate unchanged.",
            source_id="guardian",
            lang="en",
            hours_ago=None,
            topics=("mundo",),
            weight=1.0,
        ),
        a(
            "antigo-valor",
            "Vale conclui venda de participação em unidade de metais básicos",
            "A mineradora recebeu US$ 3 bilhões pela fatia.",
            source_id="valor",
            hours_ago=29,
            topics=("brasil",),
            weight=1.3,
        ),
    ]


def sample_bundle(articles: list[Article] | None = None) -> Bundle:
    """Bundle completo com cotações, clima e status de fontes (uma com erro)."""
    return Bundle(
        collected_at=NOW.isoformat(),
        articles=sample_articles() if articles is None else articles,
        quotes=[
            Quote(id="usd", label="Dólar", value=5.2226, display="R$ 5,22", change_pct=0.1938, kind="fx"),
            Quote(id="ibov", label="Ibovespa", value=182991, display="182.991 pts", change_pct=1.2, kind="index"),
            Quote(
                id="selic", label="Selic", value=15.0, display="15,00%", change_pct=None, kind="rate", as_of="2026-09"
            ),
        ],
        weather=[
            CityWeather(
                city="São Paulo",
                current_c=19.9,
                current_emoji="☁️",
                current_desc="nublado",
                today_min=17.9,
                today_max=31.6,
                tomorrow_min=18.8,
                tomorrow_max=32.6,
                tomorrow_emoji="⛈️",
                tomorrow_desc="trovoadas",
            )
        ],
        sources=[
            SourceStatus(
                source_id="valor", source_name="Valor Econômico", url="https://valor.example.com/rss", ok=True, items=8
            ),
            SourceStatus(
                source_id="folha", source_name="Folha de S.Paulo", url="https://folha.example.com/rss", ok=True, items=2
            ),
            SourceStatus(
                source_id="ft", source_name="Financial Times", url="https://ft.example.com/rss", ok=True, items=1
            ),
            SourceStatus(
                source_id="bbc", source_name="BBC News", url="https://bbc.example.com/rss", ok=False, error="HTTP 403"
            ),
        ],
    )
