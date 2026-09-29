"""Sonda temporária: testa feeds RSS e APIs a partir do runner do GitHub Actions."""
import json, re, sys, time, urllib.request, urllib.error
import xml.etree.ElementTree as ET

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36 QIJournalBot/1.0"
FEEDS = """
valor|https://valor.globo.com/rss/valor/
valor|https://pox.globo.com/rss/valor/
valor|https://valor.globo.com/rss/valor/financas/
valor|https://valor.globo.com/rss/valor/brasil/
valor|https://valor.globo.com/rss/valor/mundo/
valor|https://valor.globo.com/rss/valor/empresas/
valor|https://valor.globo.com/rss/valor/legislacao/
folha|https://feeds.folha.uol.com.br/mercado/rss091.xml
folha|https://feeds.folha.uol.com.br/mundo/rss091.xml
folha|https://feeds.folha.uol.com.br/poder/rss091.xml
folha|https://feeds.folha.uol.com.br/emcimadahora/rss091.xml
folha|https://feeds.folha.uol.com.br/tec/rss091.xml
infomoney|https://www.infomoney.com.br/feed/
infomoney|https://www.infomoney.com.br/mercados/feed/
infomoney|https://www.infomoney.com.br/economia/feed/
infomoney|https://www.infomoney.com.br/onde-investir/fundos-imobiliarios/feed/
infomoney|https://www.infomoney.com.br/negocios/feed/
conjur|https://www.conjur.com.br/rss.xml
conjur|https://www.conjur.com.br/feed/
jota|https://www.jota.info/feed
jota|https://www.jota.info/rss
jota|https://www.jota.info/feed/
agbrasil|https://agenciabrasil.ebc.com.br/rss/ultimasnoticias/feed.xml
agbrasil|https://agenciabrasil.ebc.com.br/rss/economia/feed.xml
agbrasil|https://agenciabrasil.ebc.com.br/rss/internacional/feed.xml
agbrasil|https://agenciabrasil.ebc.com.br/rss/politica/feed.xml
agbrasil|https://agenciabrasil.ebc.com.br/rss/justica/feed.xml
poder360|https://www.poder360.com.br/feed/
neofeed|https://neofeed.com.br/feed/
monitor|https://monitormercantil.com.br/feed/
braziljournal|https://braziljournal.com/feed/
exame|https://exame.com/feed/
exame|https://exame.com/mercado-imobiliario/feed/
g1|https://g1.globo.com/rss/g1/economia/
g1|https://g1.globo.com/rss/g1/mundo/
g1|https://g1.globo.com/rss/g1/politica/
estadao|https://www.estadao.com.br/arc/outboundfeeds/feeds/rss/sections/economia/
cnnbr|https://www.cnnbrasil.com.br/economia/feed/
cnnbr|https://www.cnnbrasil.com.br/feed/
bloomberglinea|https://www.bloomberglinea.com.br/arc/outboundfeeds/rss/?outputType=xml
migalhas|https://www.migalhas.com.br/rss/quentes
migalhas|https://www.migalhas.com.br/rss/migalhas
stf|https://portal.stf.jus.br/rss/noticias.asp
stj|https://www.stj.jus.br/sites/portalp/Paginas/Comunicacao/Noticias/RSS.aspx
nyt|https://rss.nytimes.com/services/xml/rss/nyt/Business.xml
nyt|https://rss.nytimes.com/services/xml/rss/nyt/World.xml
nyt|https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml
nyt|https://rss.nytimes.com/services/xml/rss/nyt/Economy.xml
guardian|https://www.theguardian.com/world/rss
guardian|https://www.theguardian.com/business/rss
guardian|https://www.theguardian.com/technology/rss
cnbc|https://www.cnbc.com/id/100003114/device/rss/rss.html
cnbc|https://www.cnbc.com/id/10000664/device/rss/rss.html
cnbc|https://www.cnbc.com/id/100727362/device/rss/rss.html
cnbc|https://www.cnbc.com/id/19854910/device/rss/rss.html
marketwatch|https://feeds.content.dowjones.io/public/rss/mw_topstories
marketwatch|https://feeds.content.dowjones.io/public/rss/mw_marketpulse
marketwatch|https://www.marketwatch.com/rss/topstories
scmp|https://www.scmp.com/rss/91/feed
scmp|https://www.scmp.com/rss/4/feed
scmp|https://www.scmp.com/rss/92/feed
scmp|https://www.scmp.com/rss/36/feed
scmp|https://www.scmp.com/rss/2/feed
bbc|https://feeds.bbci.co.uk/news/business/rss.xml
bbc|https://feeds.bbci.co.uk/news/world/rss.xml
bbc|https://feeds.bbci.co.uk/news/technology/rss.xml
ft|https://www.ft.com/rss/home
ft|https://www.ft.com/world?format=rss
aljazeera|https://www.aljazeera.com/xml/rss/all.xml
techcrunch|https://techcrunch.com/feed/
verge|https://www.theverge.com/rss/index.xml
arstechnica|https://feeds.arstechnica.com/arstechnica/index
ap|https://apnews.com/hub/business.rss
reuters|https://www.reutersagency.com/feed/?best-topics=business-finance
""".strip().splitlines()

APIS = [
    "https://economia.awesomeapi.com.br/json/last/USD-BRL,EUR-BRL,CNY-BRL,BTC-USD,XAU-USD",
    "https://economia.awesomeapi.com.br/json/last/BTC-BRL,XAU-BRL,GBP-BRL,ARS-BRL,UYU-BRL",
    "https://api.bcb.gov.br/dados/serie/bcdata.sgs.13522/dados/ultimos/3?formato=json",
    "https://api.bcb.gov.br/dados/serie/bcdata.sgs.433/dados/ultimos/13?formato=json",
    "https://api.bcb.gov.br/dados/serie/bcdata.sgs.189/dados/ultimos/13?formato=json",
    "https://api.bcb.gov.br/dados/serie/bcdata.sgs.192/dados/ultimos/13?formato=json",
    "https://api.bcb.gov.br/dados/serie/bcdata.sgs.7456/dados/ultimos/13?formato=json",
    "https://api.bcb.gov.br/dados/serie/bcdata.sgs.432/dados/ultimos/2?formato=json",
    "https://api.bcb.gov.br/dados/serie/bcdata.sgs.4389/dados/ultimos/2?formato=json",
    "https://api.bcb.gov.br/dados/serie/bcdata.sgs.1/dados/ultimos/2?formato=json",
    "https://query1.finance.yahoo.com/v8/finance/chart/%5EBVSP?range=5d&interval=1d",
    "https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC?range=5d&interval=1d",
    "https://query1.finance.yahoo.com/v8/finance/chart/BZ%3DF?range=5d&interval=1d",
    "https://query1.finance.yahoo.com/v8/finance/chart/GC%3DF?range=5d&interval=1d",
    "https://query2.finance.yahoo.com/v8/finance/chart/%5EBVSP?range=5d&interval=1d",
    "https://stooq.com/q/l/?s=%5Ebvp&f=sd2t2ohlcv&h&e=csv",
    "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd,brl&include_24hr_change=true",
    "https://api.open-meteo.com/v1/forecast?latitude=-23.55&longitude=-46.63&current=temperature_2m,weather_code&daily=weather_code,temperature_2m_max,temperature_2m_min&timezone=America%2FSao_Paulo&forecast_days=2",
    "https://api.open-meteo.com/v1/forecast?latitude=-34.90&longitude=-56.16&current=temperature_2m,weather_code&daily=weather_code,temperature_2m_max,temperature_2m_min&timezone=America%2FMontevideo&forecast_days=2",
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
for url in ["https://valor.globo.com/", "https://www.conjur.com.br/", "https://www.infomoney.com.br/", "https://www.jota.info/"]:
    try:
        st, ct, body, dt, final = get(url)
        html = body.decode("utf-8", "replace")
        links = re.findall(r'href="(https://[^"]+/\d{4}/\d{2}/\d{2}/[^"]+)"', html)[:1] or re.findall(r'href="(https://[^"]+\.ghtml)"', html)[:1]
        print(f"home {url} {st} {len(body)}B sample_article={links}")
        if links:
            st, ct, body, dt, final = get(links[0])
            m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', body.decode("utf-8", "replace"))
            print(f"    og:image={m.group(1) if m else None}")
    except Exception as e:
        print(f"ERR {url} -> {type(e).__name__}: {str(e)[:200]}")
