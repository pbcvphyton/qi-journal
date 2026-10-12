"""Testes do Termômetro de Viés, v1.1 (seção 11 da especificação).

Uso: python3 testes.py, na mesma pasta de referencia.py e caso_referencia.json.
Imprime o detalhamento do caso de referência e termina com FALHAS: nenhuma.
"""
import copy
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from referencia import arredondar, calcular, contar_palavras, preparar, varrer_lexico  # noqa: E402

CASO = json.loads((Path(__file__).parent / "caso_referencia.json").read_text(encoding="utf-8"))
PAR, LEX, SAIDA, META = CASO["paragrafos"], CASO["lexico"], CASO["saida_modelo"], CASO["meta"]
TROCA_CAMPO = {"esquerda": "direita", "direita": "esquerda",
               "centro_esquerda": "centro_direita", "centro_direita": "centro_esquerda"}
TROCA_DIR = {"esquerda": "direita", "direita": "esquerda"}
falhas = []


def confere(nome, cond, detalhe=""):
    print(("OK   " if cond else "FALHA") + f" {nome} {detalhe}")
    if not cond:
        falhas.append(nome)


def saida_com(evidencias, **extra):
    s = copy.deepcopy(SAIDA)
    s["evidencias"] = evidencias
    s.update(extra)
    return s


def ev(n):
    """Evidência n do caso de referência (E1 = primeira da lista)."""
    return copy.deepcopy(SAIDA["evidencias"][n - 1])


# ---------- caso de referência ----------
prep = preparar(PAR, LEX)
print("palavras:", contar_palavras(" ".join(PAR.values())))
print("falas:", [(q["id"], q["paragrafo"], q["palavras"]) for q in prep["falas"]])
print("candidatos:", [(c["termo"], c["paragrafo"]) for c in prep["candidatos"]])
r = calcular(SAIDA, PAR, LEX, META)
print("T", r["T"], "T_bruto", round(r["T_bruto"], 4), "|", r["rotulo"], "|", r["confianca"], r["motivos_confianca"])
print("decomposicao", {k: (round(v, 4) if isinstance(v, float) else v) for k, v in r["decomposicao"].items()})
for k, c in r["por_criterio"].items():
    print(f"  {k:14s} aplic={c['aplicavel']!s:5s} n={c['n_evidencias']} score={c['score']:+.4f} contrib={c['contribuicao']:+.4f}")
print("descartadas", [(d["id"], d["motivo"]) for d in r["descartadas"]])
print("fontes", [(f["id"], f["campo"], f["palavras"], f["ordem"], f["ultima_palavra"]) for f in r["fontes"]],
      r["saldo_fontes"])
print("lexico", [(x["termo"], x["paragrafo"], x["status"]) for x in r["lexico"]])
print("qualidade", r["qualidade_apuracao"]["Q"], r["qualidade_apuracao"]["rotulo"],
      [(i["item"], i["valor"]) for i in r["qualidade_apuracao"]["itens"]])
print("resumo:", r["resumo"])

confere("ref: T", r["T"] == -59, r["T"])
confere("ref: T bruto", abs(r["T_bruto"] + 59.4771) < 1e-4)
confere("ref: rotulo", r["rotulo"] == "Esquerda forte", r["rotulo"])
confere("ref: confianca", r["confianca"] == "alta", r["confianca"])
confere("ref: aplicaveis", len(r["aplicaveis"]) == 11)
confere("ref: falas", [(q["id"], q["paragrafo"], q["palavras"]) for q in prep["falas"]]
        == [("Q1", "P2", 7), ("Q2", "P3", 6), ("Q3", "P4", 2), ("Q4", "P4", 11), ("Q5", "P8", 2),
            ("Q6", "P8", 14), ("Q7", "P9", 5)])
confere("ref: candidatos", [(c["termo"], c["paragrafo"]) for c in prep["candidatos"]]
        == [("desmonte", "P0"), ("rombo", "P3"), ("retrocesso", "P4"), ("desmonte", "P9")])
confere("ref: descartadas", [d["id"] for d in r["descartadas"]] == ["E7", "E12"])
confere("ref: E4 nao pontua", not next(e for e in r["evidencias"] if e["id"] == "E4")["pontua"])
confere("ref: saldo de fontes", (r["saldo_fontes"]["esquerda"], r["saldo_fontes"]["direita"]) == (18, 22)
        and abs(r["saldo_fontes"]["desequilibrio"] - 0.1) < 1e-9)
confere("ref: ultima palavra F7", next(f for f in r["fontes"] if f["ultima_palavra"])["id"] == "F7")
confere("ref: dicionario", [x["status"] for x in r["lexico"]]
        == ["registrado (E1)", "fala de fonte", "fala de fonte", "fala de fonte"])
confere("ref: qualidade", (r["qualidade_apuracao"]["Q"], r["qualidade_apuracao"]["rotulo"]) == (35, "Fraca"))

# ---------- simetria ----------
s2 = copy.deepcopy(SAIDA)
for a in s2["atores"]:
    a["campo"] = TROCA_CAMPO.get(a["campo"], a["campo"])
for f in s2["fontes"]:
    f["campo"] = TROCA_CAMPO.get(f["campo"], f["campo"])
for e in s2["evidencias"]:
    e["direcao"] = TROCA_DIR.get(e["direcao"], e["direcao"])
lx2 = copy.deepcopy(LEX)
for x in lx2:
    x["direcao"] = TROCA_DIR.get(x["direcao"], x["direcao"])
r2 = calcular(s2, PAR, lx2, META)
confere("simetria: T", r2["T"] == -r["T"], (r["T"], r2["T"]))
confere("simetria: T_bruto", abs(r2["T_bruto"] + r["T_bruto"]) < 1e-9)
confere("simetria: rotulo", r2["rotulo"] == "Direita forte" and r2["confianca"] == r["confianca"], r2["rotulo"])

# ---------- unitários ----------
u = calcular(saida_com([], triagem={**SAIDA["triagem"], "relevancia_politica": "nenhuma"}), PAR, LEX, META)
confere("U1 nao se aplica", u["estado"] == "nao_se_aplica" and u["qualidade_apuracao"]["Q"] == 35)

u = calcular(SAIDA, {"P0": PAR["P0"], "P1": PAR["P1"]}, LEX)
confere("U2 inconclusivo (palavras)", u["estado"] == "inconclusivo", u.get("motivo_estado"))

u = calcular(saida_com([ev(10)]), PAR, LEX)
confere("U3 sinal isolado", u["T"] == -11 and u["rotulo"] == "Equilibrado" and u["confianca"] == "media",
        (u["T"], u["rotulo"], u["confianca"], u["resumo"]))

v = {"criterio": "VERBOS", "citacao": "disse", "paragrafo": "P8", "eixo": "economico",
     "ator_favorecido": "A6", "ator_desfavorecido": None, "direcao": "direita", "intensidade": 3,
     "certeza": 0.9, "explicacao": "teste", "ressalva": None}
fo = {"criterio": "FONTES", "citacao": "Ninguém gosta de cortar, mas a cidade não pode gastar o que não tem",
      "paragrafo": "P8", "eixo": "economico", "ator_favorecido": None, "ator_desfavorecido": "A4",
      "direcao": "direita", "intensidade": 3, "certeza": 0.9, "explicacao": "teste", "ressalva": None}
u = calcular(saida_com([ev(10), ev(5), v, fo]), PAR, LEX)
confere("U4 misto", u["rotulo"] == "Misto" and u["T"] == 4 and u["confianca"] == "media",
        (u["T"], round(u["T_bruto"], 4), u["rotulo"], u["confianca"],
         round(u["decomposicao"]["premissas"], 4), round(u["decomposicao"]["atores"], 4), u["resumo"]))

e = ev(14); e["direcao"] = "direita"
x = calcular(saida_com([e]), PAR, LEX)["evidencias"][0]
confere("U5 direcao corrigida pelos atores", x["d"] == -1 and abs(x["certeza_final"] - 0.4) < 1e-9, x["alertas"])

e = ev(1); e["direcao"] = "direita"
x = calcular(saida_com([e]), PAR, LEX)["evidencias"][0]
confere("U6 direcao corrigida pelo dicionario", x["d"] == -1 and abs(x["certeza_final"] - 0.45) < 1e-9, x["alertas"])

s = saida_com([fo])
for f in s["fontes"]:
    if f["campo"] == "direita":
        f["falas"] = []
u = calcular(s, PAR, LEX)
x = u["evidencias"][0]
confere("U7 conferencia de FONTES", abs(x["certeza_final"] - 0.45) < 1e-9, (u["saldo_fontes"], x["alertas"]))

confere("U8 arredondamento", (arredondar(10.5), arredondar(-10.5), arredondar(2.5), arredondar(-0.4))
        == (11, -11, 3, 0))

e = ev(10); e["paragrafo"] = "P2"
x = calcular(saida_com([e]), PAR, LEX)["evidencias"][0]
confere("U9 citacao em outro paragrafo", x["paragrafo"] == "P5", x["alertas"])

c1 = varrer_lexico({"P0": "A GASTANCA voltou; desmontagem não conta; o desmonte, sim."}, LEX)
confere("U10 varredura sem acento e com limite de palavra",
        [(c["termo"], c["paragrafo"]) for c in c1] == [("gastança", "P0"), ("desmonte", "P0")], c1)

e = ev(10); e["citacao"] = "enquanto corta a comida dos pobres"
u = calcular(saida_com([e]), PAR, LEX)
confere("U11 citacao sem diferenciar maiusculas", len(u["evidencias"]) == 1, u["evidencias"][0]["alertas"])

par_sem_numeros = {k: re.sub(r"\d", "", t) for k, t in PAR.items()}
u = calcular(saida_com([ev(1)], fontes=[]), par_sem_numeros, LEX)
confere("U12 aplicabilidade derivada", u["aplicaveis"] == ["ROTULOS", "AGENCIA", "HIERARQUIA", "ADJETIVOS",
                                                            "LEXICO", "OMISSAO", "ENQUADRAMENTO", "HUMANIZACAO"]
        and u["T"] == -8 and u["rotulo"] == "Equilibrado", (u["aplicaveis"], u["T"], u["rotulo"]))

s = copy.deepcopy(SAIDA)
s["qualidade"].update(titulo_sustentado="sim", opiniao_separada="sim", documento_primario="sim")
q = calcular(s, PAR, LEX, META)["qualidade_apuracao"]
q2 = calcular(s, PAR, LEX, {**META, "autor": None})["qualidade_apuracao"]
q3 = calcular(s, PAR, LEX, {**META, "secao": "Opinião"})["qualidade_apuracao"]
confere("U13 qualidade da apuracao",
        (q["Q"], q["rotulo"], q2["Q"], q2["rotulo"], q3["Q"]) == (80, "Boa", 75, "Regular", 76),
        (q["Q"], q["rotulo"], q2["Q"], q2["rotulo"], q3["Q"]))

s = copy.deepcopy(SAIDA)
s["fontes"][0]["falas"] = ["Q7", "Q99"]
f = calcular(s, PAR, LEX, META)["fontes"]
confere("U14 fala atribuida duas vezes conta uma vez",
        (f[0]["palavras"], f[0]["falas_ignoradas"], f[6]["palavras"]) == (5, 1, 0),
        (f[0]["palavras"], f[0]["falas_ignoradas"], f[6]["palavras"]))

print("\nFALHAS:", falhas or "nenhuma")
