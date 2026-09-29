"""Testes dos utilitários de texto (qijournal.text)."""

from __future__ import annotations

import pytest

from qijournal import text
from qijournal.config import load_config


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            "Five British men who were detained near R.A.F. Fairford, a base used by U.S. bombers, were released. "
            "Police said.",
            [
                "Five British men who were detained near R.A.F. Fairford, a base used by U.S. bombers, were released.",
                "Police said.",
            ],
        ),
        ("John F. Kennedy disse. O mundo.", ["John F. Kennedy disse.", "O mundo."]),
        ("O Dr. Daniel e a Petrobras S.A. falaram. O mercado subiu.", ["O Dr. Daniel e a Petrobras S.A. falaram.", "O mercado subiu."]),
        ("Os EUA. O mercado", ["Os EUA.", "O mercado"]),
    ],
)
def test_split_sentences_respects_abbreviations(value, expected):
    assert text.split_sentences(value) == expected


def test_first_sentences_does_not_cut_at_abbreviations():
    value = "A base da R.A.F. em Fairford é usada pelos EUA há décadas e fica no sul da Inglaterra. " + "Outra frase " * 20
    assert text.first_sentences(value, 120).endswith("sul da Inglaterra.")


def test_strip_paywall_keeps_the_news():
    value = (
        "A dívida subiu 0,04% em agosto. Matéria exclusiva para assinantes. Para ter acesso completo, acesse o link "
        "da matéria e faça o seu cadastro.\nOutro parágrafo com notícia."
    )
    assert text.strip_paywall(value) == "A dívida subiu 0,04% em agosto.\nOutro parágrafo com notícia."
    assert text.strip_paywall("Matéria exclusiva para assinantes.") == ""
    assert text.is_paywall("Você tem 7 acessos por dia para dar de presente.")
    assert not text.is_paywall("Os assinantes da operadora cresceram 12%.")


def test_ai_token_budget_leaves_room_for_reasoning():
    llm = load_config(env={}).llm
    assert llm.max_tokens_select == llm.max_tokens_write == 64_000
