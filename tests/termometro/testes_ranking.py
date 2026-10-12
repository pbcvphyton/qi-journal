"""Testes da aba analítica (seção 14.7 da especificação).

Uso: python3 testes_ranking.py, na mesma pasta de agregacao.py e caso_ranking.json.
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from agregacao import (P_ANALITICO, acumular, desvio, janelas_padrao, linha_vazia, puxadores,  # noqa: E402
                       ranking, serie, tendencia)

CASO = json.loads((Path(__file__).parent / "caso_ranking.json").read_text(encoding="utf-8"))
falhas = []


def confere(nome, cond, detalhe=""):
    print(("OK   " if cond else "FALHA") + f" {nome} {detalhe}")
    if not cond:
        falhas.append(nome)


def perto(a, b, tol=1e-3):
    return a is not None and abs(a - b) < tol


def linha(editoria, dia, n, media, q, dp_T=20, dp_Q=10):
    """Uma linha diária só de notícias, com n análises de média 'media' e qualidade 'q'.
    Somas de quadrados = n × (média² + desvio²), o que dá o desvio-padrão pedido dentro do dia."""
    l = {"veiculo": "Jornal Fictício", "editoria": editoria, "dia": dia, "eixo": "todos", "tipo": "noticia",
         "versao": "1.1.0", **linha_vazia()}
    l.update(n_total=n, n=n, soma_T=n * media, soma_T2=n * (media * media + dp_T ** 2),
             n_Q=n, soma_Q=n * q, soma_Q2=n * (q * q + dp_Q ** 2))
    return l


def gerar_historico():
    """365 dias, de 01/09/2025 a 31/08/2026, só notícias. Editoria X: 6 análises por dia, média de T
    −2 − 2k/30 e qualidade 70 − k/30 no dia k. Editoria Y: 4 por dia, média −2 e qualidade 70.
    Desvio-padrão dentro do dia: 20 em T e 10 em Q."""
    linhas = []
    for k in range(365):
        dia = (date(2025, 9, 1) + timedelta(days=k)).isoformat()
        linhas.append(linha("Editoria X", dia, 6, -2 - 2 * k / 30, 70 - k / 30))
        linhas.append(linha("Editoria Y", dia, 4, -2.0, 70.0))
    return linhas


# ---------- R1: análises -> linhas diárias ----------
linhas = acumular(CASO["analises"])
for l in linhas:
    print(f"  {l['editoria']:9s} {l['dia']} {l['eixo']:12s} {l['tipo']:8s} n_total={l['n_total']} n={l['n']} "
          f"soma_T={l['soma_T']:g} soma_T2={l['soma_T2']:g} n_Q={l['n_Q']} soma_Q={l['soma_Q']:g}")
confere("R1 numero de linhas", len(linhas) == 14, len(linhas))
e1 = next(l for l in linhas if l["editoria"] == "Economia" and l["dia"] == "2026-09-01" and l["eixo"] == "todos")
confere("R1 Economia 01/09", (e1["n"], e1["soma_T"], e1["soma_T2"], e1["n_Q"], e1["soma_Q"], e1["soma_Q2"])
        == (2, -40, 1000, 2, 100, 5200))
confere("R1 opiniao em tipo proprio", all(l["tipo"] == "opiniao" for l in linhas if l["editoria"] == "Opinião"))
es = next(l for l in linhas if l["editoria"] == "Esportes")
confere("R1 nao se aplica", (es["n_total"], es["n"], es["n_nao_se_aplica"], es["n_Q"], es["soma_Q"]) == (1, 0, 1, 1, 90))

# ---------- R2: séries do jornal ----------
p1 = {**P_ANALITICO, "min_n_semana": 1, "min_n_mes": 1}
sem = serie(linhas, "2026-08-01", "2026-09-30", "semana", p=p1)
mes = serie(linhas, "2026-08-01", "2026-09-30", "mes", p=p1)
op = serie(linhas, "2026-08-01", "2026-09-30", "semana", incluir_opiniao=True, p=p1)
print("semanas:", [(s["periodo"], s["n"], s["media_T"], s["media_Q"]) for s in sem])
print("mes:", [(s["periodo"], s["n"], s["media_T"], s["media_Q"]) for s in mes])
confere("R2 semanas", [(s["periodo"], s["n"]) for s in sem] == [("2026-08-31", 3), ("2026-09-07", 2)]
        and perto(sem[0]["media_T"], -20 / 3) and perto(sem[0]["media_Q"], 67.5)
        and perto(sem[1]["media_T"], 20) and perto(sem[1]["media_Q"], 50))
confere("R2 mes", [(s["periodo"], s["n"]) for s in mes] == [("2026-09", 5)] and perto(mes[0]["media_T"], 4)
        and perto(mes[0]["media_Q"], 370 / 6))
confere("R2 com opiniao", op[0]["n"] == 4 and perto(op[0]["media_T"], -20))

# ---------- R3 a R5: ranking de editorias ----------
r = ranking(CASO["linhas"], "2026-07-01", "2026-09-30")
for i in r["ranking"]:
    print(f"  {i['nome']}: n={i['n']} media={i['media_T']:.4f} ic=[{i['ic95_T'][0]:.4f}, {i['ic95_T'][1]:.4f}] "
          f"ajust={i['T_ajustado']:.4f} {i['rotulo']} | Q={i['media_Q']:.4f} ajust={i['Q_ajustado']:.4f} {i['rotulo_Q']}")
confere("R3 ordem", [i["nome"] for i in r["ranking"]]
        == ["Editoria A", "Editoria B", "Editoria C", "Editoria D", "Editoria E", "Editoria F"])
confere("R3 fora do ranking", r["fora_do_ranking"] == [{"nome": "Editoria G", "n": 12}])
confere("R3 medias gerais", perto(r["media_geral_T"], -4.1847) and perto(r["media_geral_Q"], 64.5639)
        and (r["n_analises"], r["n_validas"]) == (665, 574))
a, f = r["ranking"][0], r["ranking"][-1]
confere("R3 Editoria A", perto(a["ic95_T"][0], -41.9363) and perto(a["T_ajustado"], -35.3988)
        and a["rotulo"] == "Esquerda moderada")
confere("R3 Editoria F", perto(f["T_ajustado"], 35.2392) and f["rotulo"] == "Direita moderada"
        and perto(f["Q_ajustado"], 44.9273) and f["rotulo_Q"] == "Fraca")
confere("R3 ordem da qualidade", [i["nome"] for i in r["ranking_qualidade"]]
        == ["Editoria C", "Editoria B", "Editoria D", "Editoria E", "Editoria A", "Editoria F"])
r50 = ranking(CASO["linhas"], "2026-07-01", "2026-09-30", p={**P_ANALITICO, "min_n": 50})
confere("R4 min_n 50", [i["nome"] for i in r50["fora_do_ranking"]] == ["Editoria F", "Editoria G"])
rv = ranking(CASO["linhas"], "2026-07-01", "2026-09-30", versao="1.0.0")
confere("R5 outra versao", rv["n_validas"] == 0 and rv["ranking"] == [])

# ---------- R6 a R10: para onde estamos indo ----------
hist = gerar_historico()
ms = serie(hist, "2025-09-01", "2026-08-31", "mes")
print("meses:", [(s["periodo"], s["n"], round(s["media_T"], 4), [round(v, 4) for v in s["ic95_T"]],
                  round(s["media_Q"], 4)) for s in ms])
confere("R6 serie mensal", len(ms) == 12 and (ms[0]["periodo"], ms[0]["n"], ms[-1]["periodo"], ms[-1]["n"])
        == ("2025-09", 300, "2026-08", 310) and perto(ms[0]["media_T"], -2.58) and perto(ms[-1]["media_T"], -15.96)
        and perto(ms[0]["media_Q"], 69.71) and perto(ms[-1]["media_Q"], 63.02))
t = tendencia(hist, "2025-09-01", "2026-08-31")
print("tendencia T:", {k: (round(v, 4) if isinstance(v, float) else v) for k, v in t["T"].items()})
print("tendencia Q:", {k: (round(v, 4) if isinstance(v, float) else v) for k, v in t["Q"].items()})
confere("R6 tendencia de T", t["T"]["leitura"] == "rumo à esquerda" and t["T"]["meses"] == 12
        and perto(t["T"]["por_mes"], -1.2131) and perto(t["T"]["ic95"][0], -1.4113)
        and perto(t["T"]["ic95"][1], -1.0148) and perto(t["T"]["variacao_no_periodo"], -13.3439), t["T"]["por_mes"])
confere("R6 tendencia de Q", t["Q"]["leitura"] == "piorando" and perto(t["Q"]["por_mes"], -0.6065), t["Q"]["por_mes"])
curta = tendencia(hist, "2026-07-01", "2026-08-31")
confere("R6 poucos meses", curta["T"] == {"leitura": "dados insuficientes", "meses": 2})
estavel = tendencia([linha("Editoria Y", f"2026-0{m}-01", 100, -2.0, 70.0) for m in range(1, 7)],
                    "2026-01-01", "2026-06-30")
confere("R6 estavel", estavel["T"]["leitura"] == "estável" and perto(estavel["T"]["por_mes"], 0.0))

recente, base = janelas_padrao("2025-09-01", "2026-08-31")
confere("R7 janelas padrao", (recente, base) == (("2026-06-03", "2026-08-31"), ("2025-09-01", "2026-06-02")))
d = desvio(hist, recente, base)
print("desvio T:", d["T"])
print("desvio Q:", d["Q"])
confere("R7 desvio de T", (d["T"]["n_recente"], d["T"]["n_base"]) == (900, 2750) and perto(d["T"]["recente"], -14.78)
        and perto(d["T"]["base"], -7.48) and perto(d["T"]["diferenca"], -7.3) and perto(d["T"]["ic95"][0], -8.9711)
        and d["T"]["leitura"] == "mudou para a esquerda")
confere("R7 desvio de Q", perto(d["Q"]["diferenca"], -3.65) and d["Q"]["leitura"] == "piorou")

px = puxadores(hist, "editoria", recente, base)
print("puxadores:", [(g["nome"], round(g["contribuicao_T"], 4), round(g["efeito_inclinacao"], 4),
                      round(g["efeito_pauta"], 4), g["T"]["leitura"]) for g in px["grupos"]], round(px["mudanca_T"], 4))
x, y = px["grupos"]
confere("R8 puxadores", (x["nome"], x["T"]["leitura"], y["nome"], y["T"]["leitura"])
        == ("Editoria X", "mudou para a esquerda", "Editoria Y", "dentro do histórico")
        and perto(x["contribuicao_T"], -7.3) and perto(x["efeito_inclinacao"], -7.3) and perto(x["efeito_pauta"], 0)
        and perto(y["contribuicao_T"], 0) and perto(px["mudanca_T"], d["T"]["diferenca"]))

ri = ranking(hist, "2025-09-01", "2026-08-31", por="veiculo")
confere("R9 ranking por veiculo", [i["nome"] for i in ri["ranking"]] == ["Jornal Fictício"]
        and ri["ranking"][0]["n"] == 3650 and perto(ri["ranking"][0]["media_T"], -9.28))

# R10: o jornal muda sem nenhuma editoria mudar, só porque a pauta mudou
mix = [linha("Editoria A", "2026-02-02", 60, -10, 70, dp_T=10), linha("Editoria B", "2026-02-02", 60, 10, 70, dp_T=10),
       linha("Editoria A", "2026-05-04", 90, -10, 70, dp_T=10), linha("Editoria B", "2026-05-04", 30, 10, 70, dp_T=10)]
r10, b10 = ("2026-04-01", "2026-06-30"), ("2026-01-01", "2026-03-31")
d10 = desvio(mix, r10, b10)
p10 = puxadores(mix, "editoria", r10, b10)
print("R10:", round(d10["T"]["diferenca"], 4), d10["T"]["leitura"], [(g["nome"], round(g["contribuicao_T"], 4),
      round(g["efeito_inclinacao"], 4), round(g["efeito_pauta"], 4), g["T"]["leitura"]) for g in p10["grupos"]])
confere("R10 mudanca de pauta", perto(d10["T"]["diferenca"], -5) and d10["T"]["leitura"] == "mudou para a esquerda"
        and [(g["nome"], round(g["contribuicao_T"], 4), round(g["efeito_inclinacao"], 4), round(g["efeito_pauta"], 4),
              g["T"]["leitura"]) for g in p10["grupos"]]
        == [("Editoria A", -2.5, 0.0, -2.5, "dentro do histórico"), ("Editoria B", -2.5, 0.0, -2.5, "dentro do histórico")])

print("\nFALHAS:", falhas or "nenhuma")
