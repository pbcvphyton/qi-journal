"""Testes da especificação do Termômetro de Viés (v1.1), rodados como vieram.

``tests/termometro/testes.py`` (seção 11) e ``tests/termometro/testes_ranking.py``
(seção 14.7) são os scripts da especificação, sem alteração: importam
``referencia`` e ``agregacao`` como módulos soltos e leem os casos JSON da
própria pasta. Aqui eles rodam num processo à parte, com
``qijournal/termometro`` no caminho de importação, e precisam terminar com
``FALHAS: nenhuma``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PASTA = ROOT / "tests" / "termometro"
MODULOS = ROOT / "qijournal" / "termometro"


@pytest.mark.parametrize("script", ["testes.py", "testes_ranking.py"])
def test_script_da_especificacao(script: str) -> None:
    env = {**os.environ, "PYTHONPATH": str(MODULOS), "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run(
        [sys.executable, str(PASTA / script)],
        cwd=PASTA,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    saida = proc.stdout + proc.stderr
    assert proc.returncode == 0, saida
    assert "FALHAS: nenhuma" in proc.stdout, saida
    assert "\nFALHA " not in proc.stdout, saida


def test_referencia_e_a_copia_literal_usada_pelo_pacote() -> None:
    """O pacote importa a referência sem alteração (o cálculo é o pilar)."""
    from qijournal.termometro import referencia

    assert Path(referencia.__file__).resolve() == MODULOS / "referencia.py"
    assert referencia.PARAMETROS["versao"] == "1.1.0"
