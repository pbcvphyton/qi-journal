"""Termômetro de Viés Editorial: agregação da aba analítica (seção 14), v1.1.0.

acumular(analises)                                  -> linhas diárias (somas suficientes, guardáveis para sempre)
resumo(linhas, inicio, fim)                         -> médias de T e Q do período, com IC 95%
serie(linhas, inicio, fim, periodo)                 -> médias por semana, mês ou dia, com IC 95%
ranking(linhas, inicio, fim, por="editoria")        -> ranking por T ajustado e por qualidade da apuração
tendencia(linhas, inicio, fim)                      -> para onde estamos indo: inclinação por mês, com IC 95%
janelas_padrao(inicio, fim)                         -> (janela recente, janela de base)
desvio(linhas, recente, base)                       -> a janela recente saiu do histórico?
puxadores(linhas, por, recente, base)               -> quem puxou a mudança: inclinação × pauta

Cada linha diária guarda, para um veículo, editoria, dia, eixo, tipo (notícia ou opinião) e versão do
cálculo, as contagens e as somas de T, T², Q e Q². Médias, desvios, intervalos e regressões saem só
dessas somas, então o histórico inteiro cabe em poucas linhas por dia e nunca precisa ser recalculado.
Análises de outra versão do cálculo não se misturam: toda consulta filtra por versão.
"""
import math
from datetime import date, timedelta

try:
    from .referencia import PARAMETROS, ROTULOS, arredondar
except ImportError:  # executado fora do pacote, ao lado de referencia.py (testes da especificação)
    from referencia import PARAMETROS, ROTULOS, arredondar

P_ANALITICO = {
    "versao": PARAMETROS["versao"],
    "min_n": 30,                   # análises válidas mínimas para um grupo entrar no ranking ou no desvio
    "min_n_semana": 10,            # análises válidas mínimas para uma semana entrar na série
    "min_n_mes": 30,               # idem para um mês (e para o mês contar na tendência)
    "min_n_dia": 1,
    "peso_media_geral": 10,        # T e Q ajustados: a média do grupo puxada para a média geral com este peso
    "z": 1.96,                     # intervalos de 95%
    "faixas": PARAMETROS["faixas"],
    "qualidade_faixas": PARAMETROS["qualidade_faixas"],
    "janela_recente_dias": 90,
    "min_meses_tendencia": 6,
}

ROTULOS_LINHA = ("Esquerda forte", "Esquerda moderada", "Leve à esquerda", "Equilibrado",
                 "Leve à direita", "Direita moderada", "Direita forte", "Misto")
TIPOS = ("noticia", "opiniao")


def linha_vazia():
    """Contadores de uma linha diária, zerados."""
    return {"n_total": 0, "n": 0, "soma_T": 0, "soma_T2": 0, "rotulos": {r: 0 for r in ROTULOS_LINHA},
            "n_conf_alta": 0, "n_nao_se_aplica": 0, "n_inconclusivo": 0, "n_Q": 0, "soma_Q": 0, "soma_Q2": 0}


def tipo_de(analise):
    return "opiniao" if analise.get("selo") == "Opinião" else "noticia"


def acumular(analises, linhas=None):
    """Soma cada análise na linha do seu dia, em 'todos' e em cada eixo da triagem.

    'linhas' (opcional) é um histórico já acumulado, que recebe as novas análises."""
    por_chave = {}
    for l in linhas or []:
        por_chave[(l["veiculo"], l["editoria"], l["dia"], l["eixo"], l["tipo"], l["versao"])] = l
    for a in analises:
        eixos = ["todos"] + [e for e in dict.fromkeys((a.get("triagem") or {}).get("eixos") or []) if e != "todos"]
        for eixo in eixos:
            chave = (a["veiculo"], a["editoria"], str(a["data_publicacao"])[:10], eixo, tipo_de(a),
                     a["versao_calculo"])
            l = por_chave.get(chave)
            if l is None:
                l = por_chave[chave] = dict(zip(("veiculo", "editoria", "dia", "eixo", "tipo", "versao"), chave),
                                            **linha_vazia())
            l["n_total"] += 1
            if a.get("estado") == "ok" and a.get("T") is not None:
                T = a["T"]
                l["n"] += 1
                l["soma_T"] += T
                l["soma_T2"] += T * T
                if a.get("rotulo") in l["rotulos"]:
                    l["rotulos"][a["rotulo"]] += 1
                if a.get("confianca") == "alta":
                    l["n_conf_alta"] += 1
            elif a.get("estado") == "nao_se_aplica":
                l["n_nao_se_aplica"] += 1
            elif a.get("estado") == "inconclusivo":
                l["n_inconclusivo"] += 1
            Q = (a.get("qualidade_apuracao") or {}).get("Q")
            if Q is not None:
                l["n_Q"] += 1
                l["soma_Q"] += Q
                l["soma_Q2"] += Q * Q
    return sorted(por_chave.values(), key=lambda l: (l["dia"], l["veiculo"], l["editoria"], l["tipo"],
                                                     l["eixo"] != "todos", l["eixo"], l["versao"]))


# ---------- auxiliares ----------

def _filtrar(linhas, inicio, fim, eixo, incluir_opiniao, versao, p):
    versao = versao or p["versao"]
    tipos = TIPOS if incluir_opiniao else ("noticia",)
    return [l for l in linhas if inicio <= l["dia"] <= fim and l["eixo"] == eixo
            and l["tipo"] in tipos and l["versao"] == versao]


def _somar(linhas):
    s = linha_vazia()
    for l in linhas:
        for k, v in l.items():
            if k == "rotulos":
                for r, c in v.items():
                    s["rotulos"][r] = s["rotulos"].get(r, 0) + c
            elif k in s:
                s[k] += v
    return s


def _media_ic(n, soma, soma2, p):
    """(média, [IC 95%], variância amostral); com n < 2 o intervalo fica None."""
    if not n:
        return None, None, None
    media = soma / n
    if n < 2:
        return media, None, None
    var = max(0.0, (soma2 - n * media * media) / (n - 1))
    meia = p["z"] * math.sqrt(var / n)
    return media, [media - meia, media + meia], var


def rotulo_T(T, p=P_ANALITICO):
    if T is None:
        return None
    f1, f2, f3 = p["faixas"]
    t = arredondar(T)
    a = abs(t)
    if a <= f1:
        return "Equilibrado"
    faixa = "Leve" if a <= f2 else "Moderada" if a <= f3 else "Forte"
    return ROTULOS[faixa][0 if t < 0 else 1]


def rotulo_Q(Q, p=P_ANALITICO):
    if Q is None:
        return None
    fraca, boa = p["qualidade_faixas"]
    q = arredondar(Q)
    return "Boa" if q >= boa else "Regular" if q >= fraca else "Fraca"


def _resumo(s, p):
    media_T, ic_T, _ = _media_ic(s["n"], s["soma_T"], s["soma_T2"], p)
    media_Q, ic_Q, _ = _media_ic(s["n_Q"], s["soma_Q"], s["soma_Q2"], p)
    return {"n_total": s["n_total"], "n": s["n"], "media_T": media_T, "ic95_T": ic_T, "rotulo": rotulo_T(media_T, p),
            "n_Q": s["n_Q"], "media_Q": media_Q, "ic95_Q": ic_Q, "rotulo_Q": rotulo_Q(media_Q, p),
            "rotulos": dict(s["rotulos"]), "n_conf_alta": s["n_conf_alta"],
            "n_nao_se_aplica": s["n_nao_se_aplica"], "n_inconclusivo": s["n_inconclusivo"]}


def chave_periodo(dia, periodo):
    d = date.fromisoformat(dia[:10])
    if periodo == "semana":
        return (d - timedelta(days=d.weekday())).isoformat()  # segunda-feira da semana
    if periodo == "mes":
        return dia[:7]
    if periodo == "dia":
        return d.isoformat()
    raise ValueError(f"período inválido: {periodo!r} (use 'semana', 'mes' ou 'dia')")


def _indice_mes(mes):
    ano, m = mes.split("-")
    return int(ano) * 12 + int(m) - 1


def resumo(linhas, inicio=None, fim=None, incluir_opiniao=False, eixo="todos", versao=None, p=P_ANALITICO):
    """Médias de T e Q, com IC 95%, de todas as linhas do período (sem período: todas)."""
    sel = _filtrar(linhas, inicio or "0000-00-00", fim or "9999-99-99", eixo, incluir_opiniao, versao, p)
    return _resumo(_somar(sel), p)


# ---------- séries ----------

def serie(linhas, inicio, fim, periodo="semana", incluir_opiniao=False, eixo="todos", versao=None, p=P_ANALITICO):
    """Médias de T e Q por período; períodos com menos de min_n_<periodo> análises válidas ficam de fora."""
    grupos = {}
    for l in _filtrar(linhas, inicio, fim, eixo, incluir_opiniao, versao, p):
        grupos.setdefault(chave_periodo(l["dia"], periodo), []).append(l)
    minimo = p.get(f"min_n_{periodo}", 1)
    saida = []
    for chave in sorted(grupos):
        s = _somar(grupos[chave])
        if s["n"] >= minimo:
            saida.append({"periodo": chave, **_resumo(s, p)})
    return saida


# ---------- ranking ----------

def ranking(linhas, inicio, fim, por="editoria", incluir_opiniao=False, eixo="todos", versao=None, p=P_ANALITICO):
    """Ranking por T ajustado (da esquerda para a direita) e por qualidade ajustada (da melhor para a pior).

    Ajuste: a média do grupo é puxada para a média geral do período com peso 'peso_media_geral', o que
    segura grupos pequenos perto do centro. Grupos com menos de min_n análises válidas ficam fora."""
    sel = _filtrar(linhas, inicio, fim, eixo, incluir_opiniao, versao, p)
    total = _somar(sel)
    media_T = total["soma_T"] / total["n"] if total["n"] else None
    media_Q = total["soma_Q"] / total["n_Q"] if total["n_Q"] else None
    k = p["peso_media_geral"]
    grupos = {}
    for l in sel:
        grupos.setdefault(l[por], []).append(l)
    dentro, fora = [], []
    for nome, ls in grupos.items():
        s = _somar(ls)
        if s["n"] < p["min_n"]:
            fora.append({"nome": nome, "n": s["n"]})
            continue
        item = {"nome": nome, **_resumo(s, p)}
        item["T_ajustado"] = (s["soma_T"] + k * media_T) / (s["n"] + k)
        item["rotulo"] = rotulo_T(item["T_ajustado"], p)
        item["Q_ajustado"] = ((s["soma_Q"] + k * media_Q) / (s["n_Q"] + k)) if media_Q is not None else None
        item["rotulo_Q"] = rotulo_Q(item["Q_ajustado"], p)
        dentro.append(item)
    return {"por": por, "inicio": inicio, "fim": fim,
            "ranking": sorted(dentro, key=lambda i: (i["T_ajustado"], i["nome"])),
            "ranking_qualidade": sorted((i for i in dentro if i["Q_ajustado"] is not None),
                                        key=lambda i: (-i["Q_ajustado"], i["nome"])),
            "fora_do_ranking": sorted(fora, key=lambda i: (-i["n"], i["nome"])),
            "media_geral_T": media_T, "media_geral_Q": media_Q,
            "n_analises": total["n_total"], "n_validas": total["n"]}


# ---------- para onde estamos indo ----------

def _regressao(meses, chave_n, chave_s, chave_s2, p):
    """Mínimos quadrados de todas as análises sobre o índice do mês, a partir das somas de cada mês.
    Devolve (inclinação por mês, [IC 95%], meses de distância entre o primeiro e o último)."""
    xs = [_indice_mes(m) - _indice_mes(meses[0][0]) for m, _ in meses]
    ns = [s[chave_n] for _, s in meses]
    somas = [s[chave_s] for _, s in meses]
    somas2 = [s[chave_s2] for _, s in meses]
    N = sum(ns)
    xb = sum(n * x for n, x in zip(ns, xs)) / N
    yb = sum(somas) / N
    sxx = sum(n * (x - xb) ** 2 for n, x in zip(ns, xs))
    sxy = sum((x - xb) * (s - n * yb) for n, x, s in zip(ns, xs, somas))
    b = sxy / sxx
    a = yb - b * xb
    rss = sum(s2 - 2 * (a + b * x) * s + n * (a + b * x) ** 2 for n, x, s, s2 in zip(ns, xs, somas, somas2))
    meia = p["z"] * math.sqrt(max(0.0, rss) / (N - 2) / sxx) if N > 2 else math.inf
    return b, [b - meia, b + meia], xs[-1] - xs[0]


def _leitura(ic, negativo, positivo, neutro):
    if ic[1] < 0:
        return negativo
    if ic[0] > 0:
        return positivo
    return neutro


def tendencia(linhas, inicio, fim, incluir_opiniao=False, eixo="todos", versao=None, p=P_ANALITICO):
    """Inclinação mensal de T e de Q no período (só meses com min_n_mes análises válidas)."""
    grupos = {}
    for l in _filtrar(linhas, inicio, fim, eixo, incluir_opiniao, versao, p):
        grupos.setdefault(l["dia"][:7], []).append(l)
    somas = sorted((m, _somar(ls)) for m, ls in grupos.items())
    saida = {}
    for nome, cn, cs, cs2, leituras in (
            ("T", "n", "soma_T", "soma_T2", ("rumo à esquerda", "rumo à direita", "estável")),
            ("Q", "n_Q", "soma_Q", "soma_Q2", ("piorando", "melhorando", "estável"))):
        meses = [(m, s) for m, s in somas if s[cn] >= p["min_n_mes"]]
        if len(meses) < max(2, p["min_meses_tendencia"]) or len({m for m, _ in meses}) < 2:
            saida[nome] = {"leitura": "dados insuficientes", "meses": len(meses)}
            continue
        b, ic, dist = _regressao(meses, cn, cs, cs2, p)
        saida[nome] = {"leitura": _leitura(ic, *leituras), "meses": len(meses), "por_mes": b, "ic95": ic,
                       "variacao_no_periodo": b * dist, "de": meses[0][0], "ate": meses[-1][0]}
    return saida


def janelas_padrao(inicio, fim, p=P_ANALITICO):
    """Janela recente: os últimos 'janela_recente_dias' (no máximo metade do período); base: o resto."""
    d0, d1 = date.fromisoformat(inicio), date.fromisoformat(fim)
    dias = (d1 - d0).days + 1
    recente = max(1, min(p["janela_recente_dias"], dias // 2))
    r0 = d1 - timedelta(days=recente - 1)
    return (r0.isoformat(), d1.isoformat()), (d0.isoformat(), (r0 - timedelta(days=1)).isoformat())


def _desvio_de(sr, sb, cn, cs, cs2, leituras, p):
    nr, nb = sr[cn], sb[cn]
    if nr < p["min_n"] or nb < p["min_n"]:
        return {"leitura": "dados insuficientes", "n_recente": nr, "n_base": nb}
    mr, _, vr = _media_ic(nr, sr[cs], sr[cs2], p)
    mb, _, vb = _media_ic(nb, sb[cs], sb[cs2], p)
    dif = mr - mb
    meia = p["z"] * math.sqrt(vr / nr + vb / nb)
    ic = [dif - meia, dif + meia]
    return {"leitura": _leitura(ic, *leituras), "n_recente": nr, "n_base": nb, "recente": mr, "base": mb,
            "diferenca": dif, "ic95": ic}


LEITURAS_DESVIO_T = ("mudou para a esquerda", "mudou para a direita", "dentro do histórico")
LEITURAS_DESVIO_Q = ("piorou", "melhorou", "dentro do histórico")


def desvio(linhas, recente, base, incluir_opiniao=False, eixo="todos", versao=None, p=P_ANALITICO):
    """A janela recente comparada à base: diferença das médias de T e de Q, com IC 95%."""
    sr = _somar(_filtrar(linhas, recente[0], recente[1], eixo, incluir_opiniao, versao, p))
    sb = _somar(_filtrar(linhas, base[0], base[1], eixo, incluir_opiniao, versao, p))
    return {"recente": list(recente), "base": list(base),
            "T": _desvio_de(sr, sb, "n", "soma_T", "soma_T2", LEITURAS_DESVIO_T, p),
            "Q": _desvio_de(sr, sb, "n_Q", "soma_Q", "soma_Q2", LEITURAS_DESVIO_Q, p)}


def puxadores(linhas, por, recente, base, incluir_opiniao=False, eixo="todos", versao=None, p=P_ANALITICO):
    """Quem puxou a mudança de T entre a base e a janela recente.

    A mudança do jornal é a soma, por grupo, de peso × média; cada parcela se divide em
    efeito_inclinacao (o grupo mudou: peso médio × variação da média) e efeito_pauta (o grupo ganhou ou
    perdeu espaço: média média × variação do peso). Grupo ausente numa janela tem peso 0 nela."""
    sel_r = _filtrar(linhas, recente[0], recente[1], eixo, incluir_opiniao, versao, p)
    sel_b = _filtrar(linhas, base[0], base[1], eixo, incluir_opiniao, versao, p)
    nr = sum(l["n"] for l in sel_r)
    nb = sum(l["n"] for l in sel_b)
    grupos = []
    if nr and nb:
        nomes = sorted({l[por] for l in sel_r} | {l[por] for l in sel_b})
        for nome in nomes:
            sr = _somar([l for l in sel_r if l[por] == nome])
            sb = _somar([l for l in sel_b if l[por] == nome])
            if not sr["n"] and not sb["n"]:
                continue
            wr, wb = sr["n"] / nr, sb["n"] / nb
            mr = sr["soma_T"] / sr["n"] if sr["n"] else sb["soma_T"] / sb["n"]
            mb = sb["soma_T"] / sb["n"] if sb["n"] else mr
            inclinacao = (wr + wb) / 2 * (mr - mb)
            pauta = (mr + mb) / 2 * (wr - wb)
            grupos.append({"nome": nome, "peso_recente": wr, "peso_base": wb,
                           "media_recente": mr if sr["n"] else None, "media_base": mb if sb["n"] else None,
                           "contribuicao_T": inclinacao + pauta, "efeito_inclinacao": inclinacao,
                           "efeito_pauta": pauta,
                           "T": _desvio_de(sr, sb, "n", "soma_T", "soma_T2", LEITURAS_DESVIO_T, p),
                           "Q": _desvio_de(sr, sb, "n_Q", "soma_Q", "soma_Q2", LEITURAS_DESVIO_Q, p)})
    grupos.sort(key=lambda g: (-abs(g["contribuicao_T"]), g["nome"]))
    mudanca = (sum(l["soma_T"] for l in sel_r) / nr - sum(l["soma_T"] for l in sel_b) / nb) if nr and nb else None
    return {"por": por, "recente": list(recente), "base": list(base), "grupos": grupos, "mudanca_T": mudanca}
