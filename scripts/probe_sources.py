"""Sonda temporária: testa feeds RSS e APIs a partir do runner do GitHub Actions."""
import json, re, sys, time, urllib.request, urllib.error
import xml.etree.ElementTree as ET

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36 QIJournalBot/1.0"
FEEDS = """
fiis|https://fiis.com.br/feed/
fiis|https://www.fiis.com.br/feed/
clubefii|https://www.clubefii.com.br/feed
suno|https://www.suno.com.br/noticias/feed/
suno|https://www.suno.com.br/noticias/fundos-imobiliarios/feed/
moneytimes|https://www.moneytimes.com.br/feed/
moneytimes|https://www.moneytimes.com.br/tag/fundos-imobiliarios/feed/
seudinheiro|https://www.seudinheiro.com/feed/
imobireport|https://www.imobireport.com.br/feed/
abrainc|https://www.abrainc.org.br/feed/
secovi|https://www.secovi.com.br/feed
exame|https://exame.com/invest/fundos-imobiliarios/feed/
exame|https://exame.com/negocios/feed/
exame|https://exame.com/economia/feed/
exame|https://exame.com/mercados/feed/
infomoney|https://www.infomoney.com.br/guias/fundos-imobiliarios/feed/
infomoney|https://www.infomoney.com.br/tudo-sobre/fundos-imobiliarios/feed/
valor|https://valor.globo.com/rss/valor/imoveis/
valor|https://valor.globo.com/rss/valor/financas/fundos-imobiliarios/
valor|https://valor.globo.com/rss/valor/politica/
valor|https://valor.globo.com/rss/valor/agronegocios/
valor|https://valor.globo.com/rss/valor/eu-e/
g1|https://g1.globo.com/rss/g1/economia/imoveis/
g1|https://g1.globo.com/rss/g1/tecnologia/
estadao|https://www.estadao.com.br/arc/outboundfeeds/feeds/rss/sections/economia/imoveis/
estadao|https://www.estadao.com.br/arc/outboundfeeds/feeds/rss/sections/politica/
estadao|https://www.estadao.com.br/arc/outboundfeeds/feeds/rss/sections/internacional/
estadao|https://www.estadao.com.br/arc/outboundfeeds/feeds/rss/sections/link/
cnnbr|https://www.cnnbrasil.com.br/economia/macroeconomia/feed/
cnnbr|https://www.cnnbrasil.com.br/economia/negocios/feed/
cnnbr|https://www.cnnbrasil.com.br/politica/feed/
cnnbr|https://www.cnnbrasil.com.br/internacional/feed/
migalhas|https://www.migalhas.com.br/rss
migalhas|https://www.migalhas.com.br/feed
cnj|https://www.cnj.jus.br/feed/
tst|https://www.tst.jus.br/web/guest/rss
conjur|https://www.conjur.com.br/rss/tributario.xml
jota|https://www.jota.info/tributos/feed
jota|https://www.jota.info/stf/feed
brazilj|https://braziljournal.com/categoria/real-estate/feed/
neofeed|https://neofeed.com.br/negocios/feed/
bloomberglinea|https://www.bloomberglinea.com.br/arc/outboundfeeds/rss/category/negocios/?outputType=xml
therealdeal|https://therealdeal.com/feed/
globest|https://www.globest.com/feed/
scmpprop|https://www.scmp.com/rss/96/feed
scmpprop|https://www.scmp.com/rss/318208/feed
ftmarkets|https://www.ft.com/markets?format=rss
ftcomp|https://www.ft.com/companies?format=rss
cnbcre|https://www.cnbc.com/id/10000115/device/rss/rss.html
wsj|https://feeds.content.dowjones.io/public/rss/RSSMarketsMain
wsj|https://feeds.content.dowjones.io/public/rss/RSSWorldNews
economist|https://www.economist.com/finance-and-economics/rss.xml
economist|https://www.economist.com/the-world-this-week/rss.xml
nikkei|https://asia.nikkei.com/rss/feed/nar
elpais|https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/section/economia/portada
elobservador|https://www.elobservador.com.uy/rss/home.xml
elpaisuy|https://www.elpais.com.uy/rss/
""".strip().splitlines()

APIS = [
    "https://query1.finance.yahoo.com/v8/finance/chart/USDBRL%3DX?range=5d&interval=1d",
    "https://query1.finance.yahoo.com/v8/finance/chart/EURBRL%3DX?range=5d&interval=1d",
    "https://query1.finance.yahoo.com/v8/finance/chart/CNYBRL%3DX?range=5d&interval=1d",
    "https://query1.finance.yahoo.com/v8/finance/chart/BTC-USD?range=5d&interval=1d",
    "https://query1.finance.yahoo.com/v8/finance/chart/IFIX.SA?range=5d&interval=1d",
    "https://query1.finance.yahoo.com/v8/finance/chart/XFIX11.SA?range=5d&interval=1d",
    "https://query1.finance.yahoo.com/v7/finance/quote?symbols=USDBRL%3DX,%5EBVSP",
    "https://api.bcb.gov.br/dados/serie/bcdata.sgs.21619/dados/ultimos/2?formato=json",
    "https://api.bcb.gov.br/dados/serie/bcdata.sgs.21623/dados/ultimos/2?formato=json",
    "https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/CotacaoMoedaPeriodo(moeda=@moeda,dataInicial=@dataInicial,dataFinalCotacao=@dataFinalCotacao)?@moeda='EUR'&@dataInicial='09-20-2026'&@dataFinalCotacao='09-28-2026'&$format=json&$top=3",
    "https://economia.awesomeapi.com.br/json/last/USD-BRL",
]

def get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*", "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8"})
    t = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.headers.get("Content-Type", ""), r.read(), time.time() - t, r.geturl()

def strip_ns(tag):
    return tag.split("}", 1)[-1]

def parse(body):
    root = ET.fromstring(body)
    items = [e for e in root.iter() if strip_ns(e.tag) in ("item", "entry")]
    out = []
    for it in items[:40]:
        d = {}
        for c in it:
            n = strip_ns(c.tag)
            if n in ("title", "pubDate", "published", "updated", "date", "description", "summary", "encoded", "link"):
                d.setdefault(n, (c.text or c.attrib.get("href") or "").strip())
            if n in ("content", "thumbnail", "enclosure") and ("url" in c.attrib):
                d.setdefault("media:" + n, c.attrib.get("url"))
            if n == "group":
                for g in c:
                    if "url" in g.attrib:
                        d.setdefault("media:group", g.attrib["url"])
        out.append(d)
    return out

print("=== FEEDS ===")
for line in FEEDS:
    sid, url = line.split("|", 1)
    try:
        st, ct, body, dt, final = get(url)
        items = parse(body)
        img = sum(1 for i in items if any(k.startswith("media:") for k in i) or "<img" in (i.get("description", "") + i.get("encoded", "")))
        dates = [i.get("pubDate") or i.get("published") or i.get("updated") or i.get("date") for i in items]
        desc_len = sum(len(re.sub("<[^>]+>", "", i.get("description", "") or i.get("summary", ""))) for i in items) // max(1, len(items))
        print(f"OK  {sid:14} {url} -> {st} {len(body)}B {dt:.1f}s items={len(items)} img={img} avgdesc={desc_len} newest={dates[0] if dates else None}")
        for i in items[:2]:
            print(f"      - {i.get('title','')[:110]!r} | link={(i.get('link') or '')[:90]}")
        if items:
            print(f"      keys={sorted(items[0].keys())}")
    except Exception as e:
        print(f"ERR {sid:14} {url} -> {type(e).__name__}: {str(e)[:150]}")

print("=== APIS ===")
for url in APIS:
    try:
        st, ct, body, dt, final = get(url)
        print(f"OK  {url}\n    -> {st} {ct} {dt:.1f}s {body[:700].decode('utf-8','replace')}")
    except Exception as e:
        print(f"ERR {url} -> {type(e).__name__}: {str(e)[:200]}")


print("=== OG:IMAGE ===")
for url in ["https://www1.folha.uol.com.br/mercado/", "https://exame.com/economia/", "https://www.cnbc.com/world/"]:
    try:
        st, ct, body, dt, final = get(url)
        html = body.decode("utf-8", "replace")
        links = re.findall(r'href="(https://[^"]+/20\d\d/\d{2}/[^"]+\.shtml)"', html)[:1] or re.findall(r'href="(https://exame.com/[a-z-]+/[a-z0-9-]{30,}/?)"', html)[:1] or re.findall(r'href="(https://www.cnbc.com/20\d\d/[^"]+\.html)"', html)[:1]
        print(f"home {url} {st} {len(body)}B sample_article={links}")
        if links:
            st, ct, body, dt, final = get(links[0])
            h = body.decode("utf-8", "replace")
            m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', h)
            d = re.search(r'<meta[^>]+(?:name|property)=["\'](?:og:)?description["\'][^>]+content=["\']([^"\']+)', h)
            ab = re.search(r'"articleBody"\s*:\s*"(.{0,300})', h)
            print(f"    og:image={m.group(1) if m else None}\n    desc={d.group(1)[:200] if d else None}\n    articleBody={ab.group(1)[:200] if ab else None}")
    except Exception as e:
        print(f"ERR {url} -> {type(e).__name__}: {str(e)[:200]}")
