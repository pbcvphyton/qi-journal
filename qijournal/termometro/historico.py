"""Histórico do termômetro: um arquivo por dia em ``data/termometro/AAAA-MM-DD.json``.

Cada arquivo guarda as análises feitas naquele dia (``analisado_em``) e os dados
da execução. Esses arquivos nunca expiram (a limpeza do arquivo de edições só
apaga ``data/AAAA-MM-DD.json``): são a base da aba "Analítico Político", que
agrega todo o histórico a cada edição. Uma análise reaproveitada de outro dia
continua só no arquivo do dia em que foi feita, então nenhum texto conta duas
vezes.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

PASTA = "termometro"
_ARQUIVO = re.compile(r"^(\d{4}-\d{2}-\d{2})\.json$")


def pasta(data_dir: Path) -> Path:
    return Path(data_dir) / PASTA


def caminho_do_dia(data_dir: Path, data: str) -> Path:
    return pasta(data_dir) / f"{data}.json"


def _ler(caminho: Path) -> dict[str, Any] | None:
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("Termômetro: registro ilegível (%s): %s — ignorado", caminho, exc)
        return None
    if not isinstance(dados, dict) or not isinstance(dados.get("analises"), list):
        log.warning("Termômetro: registro em formato inesperado (%s) — ignorado", caminho)
        return None
    return dados


def carregar_dia(data_dir: Path, data: str) -> dict[str, Any] | None:
    caminho = caminho_do_dia(data_dir, data)
    return _ler(caminho) if caminho.is_file() else None


def carregar_registros(data_dir: Path) -> list[dict[str, Any]]:
    """Todos os registros diários, do mais antigo para o mais recente."""
    raiz = pasta(data_dir)
    if not raiz.is_dir():
        return []
    registros = []
    for caminho in sorted(raiz.iterdir()):
        if _ARQUIVO.match(caminho.name) and caminho.is_file():
            dados = _ler(caminho)
            if dados is not None:
                dados.setdefault("data", caminho.stem)
                registros.append(dados)
    return registros


def tem_analises(registro: Mapping[str, Any] | None) -> bool:
    return bool(registro and registro.get("analises"))


EXECUCAO = ("gerado_em", "provedor", "modelo", "candidatos", "textos_curtos", "falhas", "chamadas", "tokens")


def mesclar_dia(existente: Mapping[str, Any] | None, novo: Mapping[str, Any]) -> dict[str, Any] | None:
    """O registro do dia a gravar, ou ``None`` se não há análise nova a guardar.

    Entram só as análises feitas no dia (``analisado_em`` = data do registro) que o
    arquivo ainda não tem, ou que mudaram: as de execuções anteriores do mesmo dia
    continuam, e uma nova análise do mesmo texto substitui a anterior. Uma
    execução sem análise nova (sem IA, por exemplo, que só reaproveita) não cria
    nem reescreve o arquivo. Cada execução que trouxe análise nova fica em
    ``execucoes`` (quem analisou, com que modelo, quantas chamadas e tokens)."""
    data = str(novo.get("data") or "")
    antigas = {
        a["id"]: a for a in (existente or {}).get("analises") or [] if isinstance(a, dict) and a.get("id")
    }
    feitas = [
        a
        for a in novo.get("analises") or []
        if isinstance(a, dict) and a.get("id") and a.get("analisado_em") == data and antigas.get(a["id"]) != a
    ]
    if not feitas:
        return None
    por_id = dict(antigas)
    for analise in feitas:
        por_id[analise["id"]] = analise
    execucoes = list((existente or {}).get("execucoes") or [])
    if existente is not None and not execucoes and antigas:  # registro sem a lista: a execução anterior entra nela
        execucoes.append({**{k: existente.get(k) for k in EXECUCAO}, "analises": list(antigas)})
    execucoes.append({**{k: novo.get(k) for k in EXECUCAO}, "analises": [a["id"] for a in feitas]})
    return {
        "versao_calculo": novo.get("versao_calculo") or (existente or {}).get("versao_calculo"),
        "data": data,
        "analises": list(por_id.values()),
        "execucoes": execucoes,
    }


def todas_as_analises(registros: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Todas as análises do histórico, uma por texto (a mais recente, se um texto foi
    analisado de novo), na ordem em que foram feitas."""
    por_id: dict[str, dict[str, Any]] = {}
    for registro in registros:
        for analise in registro.get("analises") or []:
            if not isinstance(analise, dict) or not analise.get("id"):
                continue
            atual = por_id.get(analise["id"])
            if atual is None or str(analise.get("analisado_em") or "") >= str(atual.get("analisado_em") or ""):
                por_id.pop(analise["id"], None)
                por_id[analise["id"]] = dict(analise)
    return list(por_id.values())
