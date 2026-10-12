"""Termômetro de Viés Editorial: implementação de referência do cálculo, v1.1.0.

preparar(paragrafos, lexico)                   -> falas diretas e candidatos do dicionário (vão no prompt)
calcular(saida, paragrafos, lexico, meta=None) -> ResultadoAnalise (termômetro + qualidade da apuração)

'paragrafos' é um dict ordenado: {"P0": título, "P1": linha fina, "P2": ..., legendas ao final}.
'saida' é o JSON do modelo, já validado contra config/saida_modelo.schema.json.
Posições (inicio, fim) são índices em code points do parágrafo normalizado.
Tudo aqui roda em milissegundos; o único passo lento do pipeline é a chamada ao modelo.
"""
import math
import re
import unicodedata

PARAMETROS = {
    "versao": "1.1.0",
    # a ordem das chaves é a ordem fixa de soma
    "pesos": {"VERBOS": 1.0, "ROTULOS": 1.0, "FONTES": 1.2, "AGENCIA": 0.8, "HIERARQUIA": 1.0,
              "ADJETIVOS": 0.8, "LEXICO": 1.0, "NUMEROS": 0.8, "OMISSAO": 0.6,
              "ENQUADRAMENTO": 1.2, "HUMANIZACAO": 0.8},
    "familias": {"A": ["VERBOS", "ROTULOS", "ADJETIVOS", "LEXICO"],
                 "B": ["FONTES", "AGENCIA", "HIERARQUIA", "NUMEROS", "OMISSAO"],
                 "C": ["ENQUADRAMENTO", "HUMANIZACAO"]},
    "certeza_max": {"OMISSAO": 0.7},
    "S": 3.0,                     # soma de w que leva um critério ao escore máximo
    "limiar_sinal": 0.15,         # |escore| mínimo para um critério contar como sinal
    "limiar_divergencia": 10.0,   # pontos mínimos de cada parcela para "Misto"
    "limiar_fontes": 0.3,         # desequilíbrio de palavras que aciona a conferência de FONTES
    "sobreposicao": 0.5,          # fração do trecho menor acima da qual dois trechos são o mesmo
    "faixas": (10, 25, 50),
    "min_palavras": 150,
    "palavras_confianca_plena": 300,
    "criterios_voz_redacao": ("LEXICO", "ADJETIVOS", "ROTULOS"),
    "generos_opiniao": ("opiniao", "editorial", "analise"),
    "qualidade_pesos": {"titulo_sustentado": 2.0, "contraponto": 2.0, "fontes_identificadas": 1.5,
                        "opiniao_separada": 1.5, "numeros_contextualizados": 1.0,
                        "documento_primario": 1.0, "incerteza_declarada": 0.5, "autoria_data": 0.5},
    "qualidade_faixas": (50, 80),  # abaixo de 50: Fraca; de 50 a 79: Regular; 80 ou mais: Boa
}

LADO = {"esquerda": -1, "centro_esquerda": -1, "direita": 1, "centro_direita": 1}
DIRECAO = {"esquerda": -1, "equilibrio": 0, "direita": 1}
NOME_DIRECAO = {-1: "esquerda", 0: "equilibrio", 1: "direita"}
ROTULOS = {"Leve": ("Leve à esquerda", "Leve à direita"),
           "Moderada": ("Esquerda moderada", "Direita moderada"),
           "Forte": ("Esquerda forte", "Direita forte")}
NOMES = {"VERBOS": "verbos de atribuição", "ROTULOS": "rótulos e aspas", "FONTES": "fontes e espaço",
         "AGENCIA": "sujeito do verbo", "HIERARQUIA": "título, lead e 'mas'",
         "ADJETIVOS": "carga avaliativa", "LEXICO": "vocabulário codificado",
         "NUMEROS": "números e escala", "OMISSAO": "contexto omitido",
         "ENQUADRAMENTO": "premissas de fundo", "HUMANIZACAO": "empatia desigual"}
CONFIANCA_TXT = {"alta": "alta", "media": "média", "baixa": "baixa"}
ASPAS = str.maketrans({chr(0x201C): '"', chr(0x201D): '"', chr(0x201E): '"', chr(0xAB): '"',
                       chr(0xBB): '"', chr(0x2018): "'", chr(0x2019): "'", chr(0xA0): " "})


def normalizar(texto):
    t = unicodedata.normalize("NFC", texto or "").translate(ASPAS)
    return re.sub(r"\s+", " ", t).strip()


def dobrar(texto):
    """Minúsculas e sem acentos, com o mesmo comprimento do texto de entrada."""
    return "".join(unicodedata.normalize("NFD", c.lower())[0] for c in texto)


def contar_palavras(texto):
    return len(normalizar(texto).split())


def arredondar(x):
    """Inteiro mais próximo; metades para longe de zero (igual em qualquer linguagem)."""
    return int(math.copysign(math.floor(abs(x) + 0.5), x))


def sinal(x):
    return (x > 0) - (x < 0)


def padrao_termo(variante):
    return r"(?<!\w)" + re.escape(dobrar(normalizar(variante))) + r"(?!\w)"


def achar(trecho, texto):
    """(posição, exato): primeiro literal, depois sem diferenciar maiúsculas e acentos."""
    i = texto.find(trecho)
    if i >= 0:
        return i, True
    return dobrar(texto).find(dobrar(trecho)), False


def em_fala(texto, inicio):
    """True quando a posição está dentro de aspas: número ímpar de aspas antes dela."""
    return texto[:inicio].count('"') % 2 == 1


def varrer_lexico(paragrafos, lexico):
    candidatos = []
    for pid, bruto in paragrafos.items():
        texto = dobrar(normalizar(bruto))
        for entrada in lexico:
            for variante in entrada["variantes"]:
                for m in re.finditer(padrao_termo(variante), texto):
                    candidatos.append({"termo": entrada["termo"], "variante": variante,
                                       "direcao": entrada["direcao"],
                                       "alternativa": entrada.get("alternativa", ""),
                                       "paragrafo": pid, "inicio": m.start(), "fim": m.end()})
    ordem = list(paragrafos)
    return sorted(candidatos, key=lambda c: (ordem.index(c["paragrafo"]), c["inicio"]))


def extrair_falas(paragrafos):
    """Falas diretas: trechos entre aspas, numerados Q1, Q2... na ordem do texto."""
    falas = []
    for pid, bruto in paragrafos.items():
        texto = normalizar(bruto)
        marcas = [i for i, c in enumerate(texto) if c == '"']
        if len(marcas) % 2:
            marcas.append(len(texto))  # aspas abertas seguem até o fim do parágrafo
        for a, b in zip(marcas[0::2], marcas[1::2]):
            trecho = texto[a + 1:b].strip()
            if trecho:
                falas.append({"id": f"Q{len(falas) + 1}", "paragrafo": pid, "inicio": a + 1, "fim": b,
                              "texto": trecho, "palavras": contar_palavras(trecho)})
    return falas


def preparar(paragrafos, lexico):
    """Pré-processamento que vai no prompt: falas diretas e candidatos do dicionário."""
    return {"falas": extrair_falas(paragrafos), "candidatos": varrer_lexico(paragrafos, lexico)}


def localizar(citacao, paragrafos, declarado):
    cit = normalizar(citacao)
    if not cit:
        return None
    for pid in [declarado] + [p for p in paragrafos if p != declarado]:
        par = normalizar(paragrafos.get(pid, ""))
        i, exato = achar(cit, par)
        if i >= 0:
            return {"paragrafo": pid, "inicio": i, "fim": i + len(cit), "texto": par, "exato": exato}
    return None


def contar_fontes(fontes, falas):
    """Palavras de fala direta por fonte; cada fala conta para uma fonte só (a primeira que a cita)."""
    por_id = {q["id"]: n for n, q in enumerate(falas)}
    usadas, linhas, posicoes = set(), [], {}
    for f in fontes:
        minhas = [q for q in f.get("falas", []) if q in por_id and q not in usadas]
        usadas.update(minhas)
        posicoes[f["id"]] = [por_id[q] for q in minhas]
        linhas.append({"id": f["id"], "rotulo": f["rotulo_no_texto"], "campo": f["campo"],
                       "anonima": f["anonima"], "vinculo_informado": f["vinculo_informado"],
                       "falas": minhas, "palavras": sum(falas[por_id[q]]["palavras"] for q in minhas),
                       "falas_ignoradas": len(f.get("falas", [])) - len(minhas),
                       "ordem": None, "ultima_palavra": False})
    com_fala = sorted((l for l in linhas if posicoes[l["id"]]), key=lambda l: min(posicoes[l["id"]]))
    for n, l in enumerate(com_fala, 1):
        l["ordem"] = n
    if com_fala:
        max(com_fala, key=lambda l: max(posicoes[l["id"]]))["ultima_palavra"] = True
    esq = sum(l["palavras"] for l in linhas if LADO.get(l["campo"]) == -1)
    dir_ = sum(l["palavras"] for l in linhas if LADO.get(l["campo"]) == 1)
    total = esq + dir_
    return linhas, {"esquerda": esq, "direita": dir_,
                    "desequilibrio": (dir_ - esq) / total if total else 0.0}


def derivar_direcao(ev, atores):
    """(d, origem, fator_de_certeza, alertas). d None = descartar a evidência."""
    if ev["direcao"] == "equilibrio":
        return 0, "sinal de equilíbrio", 1.0, []
    d_modelo = DIRECAO[ev["direcao"]]
    if ev["dimensao"] != "atores":
        return d_modelo, "indicada pelo modelo", 1.0, []
    candidatos, partes = [], []
    for chave, sentido, verbo in (("ator_favorecido", 1, "favorece"),
                                  ("ator_desfavorecido", -1, "desfavorece")):
        ator = atores.get(ev.get(chave) or "")
        if ator and ator["campo"] in LADO:
            candidatos.append(LADO[ator["campo"]] * sentido)
            partes.append(f"{verbo} {ator['nome']} ({ator['campo']})")
    if not candidatos:
        return d_modelo, "indicada pelo modelo (atores sem campo definido)", 1.0, []
    if len(set(candidatos)) > 1:
        return None, "favorecido e desfavorecido do mesmo campo", 1.0, []
    if candidatos[0] != d_modelo:
        return candidatos[0], "; ".join(partes), 0.5, [
            "direção do modelo contradiz o campo dos atores; corrigida"]
    return candidatos[0], "; ".join(partes), 1.0, []


def descarte(ev, motivo):
    return {"id": ev["id"], "criterio": ev["criterio"], "citacao": ev["citacao"], "motivo": motivo}


def calcular_qualidade(saida, meta, selo, tem_numeros, p):
    """Qualidade da apuração, 0 a 100: práticas visíveis no texto, não a veracidade dos fatos."""
    respostas = dict(saida.get("qualidade") or {})
    if not respostas:
        return {"Q": None, "rotulo": "Não avaliada", "itens": []}
    fontes = saida.get("fontes") or []
    respostas["fontes_identificadas"] = ("nao_se_aplica" if not fontes
                                         else "nao" if any(f["anonima"] for f in fontes) else "sim")
    respostas["autoria_data"] = "sim" if meta.get("autor") and meta.get("data") else "nao"
    if not tem_numeros:
        respostas["numeros_contextualizados"] = "nao_se_aplica"
    if selo == "Opinião":
        respostas["opiniao_separada"] = "nao_se_aplica"
    itens, obtido, possivel = [], 0.0, 0.0
    for item, peso in p["qualidade_pesos"].items():
        valor = respostas.get(item, "nao")
        if valor != "nao_se_aplica":
            possivel += peso
            obtido += peso if valor == "sim" else 0.0
        itens.append({"item": item, "valor": valor, "peso": peso})
    if not possivel:
        return {"Q": None, "rotulo": "Não avaliada", "itens": itens}
    Q = arredondar(100 * obtido / possivel)
    fraca, boa = p["qualidade_faixas"]
    return {"Q": Q, "rotulo": "Boa" if Q >= boa else "Regular" if Q >= fraca else "Fraca", "itens": itens}


def calcular(saida, paragrafos, lexico=(), meta=None, p=PARAMETROS):
    meta = meta or {}
    pesos = p["pesos"]
    triagem = saida["triagem"]
    secao = meta.get("secao") or ""
    if triagem["genero"] in p["generos_opiniao"] or re.search(
            r"opini|coluna|editorial|artigo|an[aá]lise", secao, re.I):
        selo = "Opinião"
    else:
        selo = "Entrevista" if triagem["genero"] == "entrevista" else None
    texto_total = " ".join(paragrafos.values())
    palavras = contar_palavras(texto_total)
    tem_numeros = any(c.isdigit() for c in texto_total)
    falas = extrair_falas(paragrafos)
    base = {"versao_calculo": p["versao"], "palavras": palavras, "genero": triagem["genero"],
            "selo": selo, "triagem": triagem, "atores": saida.get("atores", []), "falas": falas,
            "enquadramento": saida.get("enquadramento"), "teste_inversao": saida.get("teste_inversao"),
            "qualidade_apuracao": calcular_qualidade(saida, meta, selo, tem_numeros, p)}
    if triagem["relevancia_politica"] == "nenhuma":
        return {**base, "estado": "nao_se_aplica", "T": None, "rotulo": "Não se aplica",
                "confianca": None,
                "resumo": "Não se aplica: o texto não tem dimensão político-ideológica relevante."}

    atores = {a["id"]: a for a in saida["atores"]}
    fontes, saldo = contar_fontes(saida["fontes"], falas)

    # 1. Validação de cada evidência; ID e dimensão saem da ordem e dos atores informados
    validas, descartadas = [], []
    for n, bruta in enumerate(saida["evidencias"], 1):
        ev = {**bruta, "id": f"E{n}", "dimensao": "atores" if bruta.get("ator_favorecido")
              or bruta.get("ator_desfavorecido") else "premissas"}
        loc = localizar(ev["citacao"], paragrafos, ev["paragrafo"])
        if not loc:
            descartadas.append(descarte(ev, "citação não encontrada no texto"))
            continue
        alertas = []
        if loc["paragrafo"] != ev["paragrafo"]:
            alertas.append(f"citação encontrada em {loc['paragrafo']}, não em {ev['paragrafo']}")
        if not loc["exato"]:
            alertas.append("citação encontrada sem diferenciar maiúsculas e acentos")
        voz = "citacao_fonte" if em_fala(loc["texto"], loc["inicio"]) else "redacao"
        if voz == "citacao_fonte" and ev["criterio"] in p["criterios_voz_redacao"]:
            descartadas.append(descarte(ev, "termo dentro de fala de fonte (regra da voz)"))
            continue
        d, origem, fator, extra = derivar_direcao(ev, atores)
        if d is None:
            descartadas.append(descarte(ev, origem))
            continue
        alertas += extra
        certeza = min(ev["certeza"], p["certeza_max"].get(ev["criterio"], 1.0)) * fator
        if ev["criterio"] == "LEXICO":
            cit = dobrar(normalizar(ev["citacao"]))
            entrada = next((x for x in lexico for v in x["variantes"]
                            if re.search(padrao_termo(v), cit)), None)
            if entrada:
                teto = entrada.get("teto_certeza", 1.0)
                if certeza > teto:
                    certeza = teto
                    alertas.append(f"certeza limitada a {teto} pelo dicionário ('{entrada['termo']}')")
                fixa = DIRECAO.get(entrada["direcao"])  # None para 'contextual'
                if fixa and d and fixa != d:
                    d, certeza = fixa, certeza * 0.5
                    alertas.append(f"direção corrigida pelo dicionário ('{entrada['termo']}')")
        if (ev["criterio"] == "FONTES" and d and abs(saldo["desequilibrio"]) >= p["limiar_fontes"]
                and sinal(saldo["desequilibrio"]) != d):
            certeza *= 0.5
            alertas.append("contagem de palavras das fontes aponta na direção oposta; certeza reduzida")
        validas.append({**ev, "paragrafo": loc["paragrafo"], "inicio": loc["inicio"], "fim": loc["fim"],
                        "voz": voz, "d": d, "direcao_final": NOME_DIRECAO[d],
                        "origem_direcao": origem, "certeza_final": certeza,
                        "w": ev["intensidade"] * certeza, "alertas": alertas,
                        "pontua": True, "contribuicao": 0.0})

    # 2. Um trecho, um critério: entre evidências sobrepostas, pontua a de maior peso
    mantidas = []
    for e in sorted(validas, key=lambda e: (-pesos[e["criterio"]], -e["w"], int(e["id"][1:]))):
        for m in mantidas:
            if m["paragrafo"] != e["paragrafo"]:
                continue
            inter = min(m["fim"], e["fim"]) - max(m["inicio"], e["inicio"])
            menor = min(m["fim"] - m["inicio"], e["fim"] - e["inicio"])
            if menor > 0 and inter / menor > p["sobreposicao"]:
                e["pontua"] = False
                e["alertas"].append(f"mesmo trecho de {m['id']} ({m['criterio']}); não pontua")
                break
        if e["pontua"]:
            mantidas.append(e)

    # 3. Dicionário: o que aconteceu com cada termo encontrado na varredura
    lexico_status = []
    for c in varrer_lexico(paragrafos, lexico):
        reg = next((e for e in validas if e["criterio"] == "LEXICO" and e["paragrafo"] == c["paragrafo"]
                    and e["inicio"] <= c["inicio"] < e["fim"]), None)
        if reg:
            status = f"registrado ({reg['id']})"
        elif em_fala(normalizar(paragrafos[c["paragrafo"]]), c["inicio"]):
            status = "fala de fonte"
        else:
            status = "não marcado pelo modelo"
        lexico_status.append({**c, "status": status})

    # 4. Critérios aplicáveis: todos, menos VERBOS e FONTES sem fontes e NUMEROS sem números
    aplic = {k: True for k in pesos}
    aplic["VERBOS"] = aplic["FONTES"] = bool(saida["fontes"])
    aplic["NUMEROS"] = tem_numeros
    for e in validas:
        aplic[e["criterio"]] = True
    n_aplic = sum(aplic.values())
    comum = {"evidencias": validas, "descartadas": descartadas, "fontes": fontes,
             "saldo_fontes": saldo, "lexico": lexico_status,
             "aplicaveis": [k for k in pesos if aplic[k]]}
    if palavras < p["min_palavras"] or n_aplic < 3:
        motivo = (f"texto com {palavras} palavras (mínimo {p['min_palavras']})"
                  if palavras < p["min_palavras"] else f"só {n_aplic} critérios aplicáveis (mínimo 3)")
        return {**base, **comum, "estado": "inconclusivo", "motivo_estado": motivo, "T": None,
                "rotulo": "Inconclusivo", "confianca": None, "resumo": f"Inconclusivo: {motivo}."}

    # 5. Escore por critério e T
    W = sum(pesos[k] for k in pesos if aplic[k])
    por_criterio = {}
    for k in pesos:
        evs = [e for e in validas if e["criterio"] == k and e["pontua"]]
        denom = max(p["S"], sum(e["w"] for e in evs))
        score = sum(e["d"] * e["w"] for e in evs) / denom
        for e in evs:
            e["contribuicao"] = 100 * pesos[k] * e["d"] * e["w"] / (denom * W)
        por_criterio[k] = {"aplicavel": aplic[k], "peso": pesos[k], "score": score,
                           "contribuicao": 100 * pesos[k] * score / W, "n_evidencias": len(evs)}
    T_bruto = sum(c["contribuicao"] for c in por_criterio.values())
    T = max(-100, min(100, arredondar(T_bruto)))

    # 6. Decomposição
    premissas = sum(e["contribuicao"] for e in validas if e["dimensao"] == "premissas")
    atores_pts = sum(e["contribuicao"] for e in validas if e["dimensao"] == "atores")
    divergente = (premissas * atores_pts < 0
                  and min(abs(premissas), abs(atores_pts)) >= p["limiar_divergencia"])
    por_eixo = {}
    for eixo in sorted({e["eixo"] for e in validas} - {"geral"}):
        evs = [e for e in validas if e["eixo"] == eixo and e["pontua"] and e["d"] != 0]
        if len(evs) >= 2:
            por_eixo[eixo] = sum(e["contribuicao"] for e in evs)

    # 7. Rótulo e confiança
    f1, f2, f3 = p["faixas"]
    a = abs(T)
    faixa = "Equilibrado" if a <= f1 else "Leve" if a <= f2 else "Moderada" if a <= f3 else "Forte"
    st = sinal(T_bruto)
    lado = 0 if st < 0 else 1
    familia = {k: f for f, ks in p["familias"].items() for k in ks}
    fortes = [k for k in pesos if abs(por_criterio[k]["score"]) >= p["limiar_sinal"]]
    concord = [k for k in fortes if sinal(por_criterio[k]["score"]) == st]
    contra = [k for k in fortes if sinal(por_criterio[k]["score"]) == -st]
    motivos, isolado, compensam = [], False, False
    if divergente:
        rotulo = "Misto"
    elif faixa == "Equilibrado":
        rotulo = "Equilibrado"
    elif len(concord) < 2:
        rotulo, isolado = "Equilibrado", True
    else:
        rotulo = ROTULOS[faixa][lado]

    if rotulo == "Misto":
        n_fam = len({familia[k] for k in fortes})
        nivel = 1 if len(fortes) >= 3 and n_fam >= 2 else 0
        motivos.append(f"premissas ({arredondar(premissas):+d}) e atores ({arredondar(atores_pts):+d}) "
                       "apontam em direções opostas")
    elif rotulo == "Equilibrado":
        nivel = 2 if n_aplic >= 7 else 1 if n_aplic >= 5 else 0
        motivos.append(f"{n_aplic} critérios avaliados")
        n_esq = sum(1 for k in fortes if por_criterio[k]["score"] < 0)
        compensam = min(n_esq, len(fortes) - n_esq) >= 2
        if isolado and nivel == 2:
            nivel = 1
            motivos.append("sinal isolado: menos de 2 critérios na direção de T")
        if compensam and nivel == 2:
            nivel = 1
            motivos.append("sinais fortes nas duas direções se compensam")
    else:
        n_fam = len({familia[k] for k in concord})
        nivel = 2 if len(concord) >= 5 and n_fam >= 2 else 1 if len(concord) >= 3 and n_fam >= 2 else 0
        motivos.append(f"{len(concord)} critérios concordantes em {n_fam} famílias")
        if len(contra) >= 2 and nivel > 0:
            nivel -= 1
            motivos.append(f"{len(contra)} critérios apontam na direção oposta")
    if nivel == 2 and palavras < p["palavras_confianca_plena"]:
        nivel = 1
        motivos.append(f"texto com {palavras} palavras (alta exige {p['palavras_confianca_plena']})")
    confianca = ("baixa", "media", "alta")[nivel]
    if confianca == "baixa" and rotulo in ROTULOS["Moderada"] + ROTULOS["Forte"]:
        rotulo = ROTULOS["Leve"][lado]
        motivos.append("rótulo limitado a 'Leve' por confiança baixa")

    # 8. Resumo em frase fixa
    def ids(k):
        return ", ".join(e["id"] for e in validas
                         if e["criterio"] == k and e["pontua"] and e["d"] == st and st != 0)
    ct = CONFIANCA_TXT[confianca]
    if rotulo == "Misto":
        resumo = (f"Misto (confiança {ct}): premissas {'de esquerda' if premissas < 0 else 'de direita'} "
                  f"({arredondar(premissas):+d} pontos) e tratamento dos atores favorável à "
                  f"{'esquerda' if atores_pts < 0 else 'direita'} ({arredondar(atores_pts):+d} pontos).")
    elif isolado:
        k = concord[0] if concord else max(pesos, key=lambda k: abs(por_criterio[k]["contribuicao"]))
        resumo = (f"Equilibrado (confiança {ct}): sinal isolado em {NOMES[k]} ({ids(k)}); "
                  "um critério só não inclina o termômetro.")
    elif rotulo == "Equilibrado":
        resumo = (f"Equilibrado (confiança {ct}): sinais fortes nas duas direções se compensam."
                  if compensam else
                  f"Equilibrado (confiança {ct}): sinais fracos ou ausentes nos {n_aplic} critérios avaliados.")
    else:
        top = sorted((k for k in pesos if sinal(por_criterio[k]["contribuicao"]) == st),
                     key=lambda k: -abs(por_criterio[k]["contribuicao"]))[:3]
        itens = [f"{NOMES[k]} ({ids(k)})" for k in top]
        lista = itens[0] if len(itens) == 1 else ", ".join(itens[:-1]) + " e " + itens[-1]
        resumo = f"{rotulo} (confiança {ct}). Principais sinais: {lista}."

    return {**base, **comum, "estado": "ok", "T": T, "T_bruto": T_bruto, "rotulo": rotulo,
            "confianca": confianca, "motivos_confianca": motivos,
            "decomposicao": {"premissas": premissas, "atores": atores_pts, "divergente": divergente},
            "por_eixo": por_eixo, "por_criterio": por_criterio, "resumo": resumo}
