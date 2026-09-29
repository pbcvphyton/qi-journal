"""Integração entre as áreas no caminho com IA: bundle real → ``pipeline.run(client=...)``.

Usa o SDK ``anthropic`` de verdade sobre um ``MockTransport`` (sem rede) e um
"modelo" falso que lê os prompts reais (candidatos ``[aN]`` da pauta, blocos
``<materia>`` da redação) e responde JSON no formato dos schemas enviados — com
alguns vícios de propósito (markdown no título, HTML injetado, id inexistente,
seção inválida) para exercitar a validação e o escape até o HTML final.

Também cobre a queda para a edição automática quando a API falha ou recusa.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import anthropic
import httpx2
import pytest

from qijournal import pipeline
from qijournal.collect.enrich import PageInfo
from qijournal.config import ROOT, load_config
from qijournal.models import Edition

BUNDLE = ROOT / "tests" / "fixtures" / "bundle.json"
CANDIDATE_RE = re.compile(r"^\[(a\d+)\] (.+?) \| (pt|en|es) \| (.+?) \| seção-sugerida: (\w+) \| (.+)$", re.M)
MATERIA_RE = re.compile(r'<materia chave="(s\d+)" secao="(\w+) [^"]*" importancia="(\d)">\n(.*?)</materia>', re.S)
INJECTION = "<script>alert(1)</script>"

# Grupos que o "modelo" junta numa matéria só (palavras obrigatórias no título, sem acento/caixa).
GROUPS = [
    ("openai",),
    ("anthropic",),
    ("bets", "bilhao"),
    ("comite", "bets"),
    ("dolar",),
    ("tarif",),
    ("aparecida", "lula"),
    ("renegociacao",),
]


# ── SSE / transporte ────────────────────────────────────────────────────────


def _sse(events: list[tuple[str, dict[str, Any]]]) -> bytes:
    return "".join(f"event: {n}\ndata: {json.dumps(d, ensure_ascii=False)}\n\n" for n, d in events).encode()


def _stream(payload: dict | None, *, stop_reason: str = "end_turn", usage: tuple[int, int] = (1000, 500)) -> bytes:
    text = json.dumps(payload, ensure_ascii=False) if payload is not None else ""
    message = {
        "id": "msg_teste",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": [],
        "stop_reason": None,
        "stop_sequence": None,
        "usage": {"input_tokens": usage[0], "output_tokens": 1},
    }

    def event(name: str, **data: Any) -> tuple[str, dict[str, Any]]:
        return name, {"type": name, **data}

    events = [event("message_start", message=message)]
    if text:
        events += [
            event("content_block_start", index=0, content_block={"type": "text", "text": ""}),
            event("content_block_delta", index=0, delta={"type": "text_delta", "text": text}),
            event("content_block_stop", index=0),
        ]
    delta = {"stop_reason": stop_reason, "stop_sequence": None}
    events += [event("message_delta", delta=delta, usage={"output_tokens": usage[1]}), event("message_stop")]
    return _sse(events)


def _norm(value: str) -> str:
    from qijournal.text import normalize

    return normalize(value)


# ── "modelo" falso ──────────────────────────────────────────────────────────


def fake_selection(prompt: str) -> dict[str, Any]:
    """Pauta plausível: junta grupos conhecidos, uma matéria por candidato no resto."""
    lines = [(m.group(1), m.group(5), m.group(6)) for m in CANDIDATE_RE.finditer(prompt)]
    assert len(lines) >= 60, "a pauta deveria receber os candidatos do bundle"
    grouped: dict[int, list[str]] = {}
    singles: list[tuple[str, str]] = []
    sections: dict[int, str] = {}
    for short_id, section, title in lines:
        norm = _norm(title.split(" — ")[0])
        for gi, words in enumerate(GROUPS):
            if all(w in norm for w in words):
                grouped.setdefault(gi, []).append(short_id)
                sections.setdefault(gi, section)
                break
        else:
            singles.append((short_id, section))
    stories = [
        {"article_ids": ids, "section": sections[gi], "importance": 5 if gi < 2 else 4, "angle": f"Fato {gi}"}
        for gi, ids in sorted(grouped.items())
    ]
    for i, (short_id, section) in enumerate(singles[:14]):
        stories.append({"article_ids": [short_id], "section": section, "importance": 3 - i // 5, "angle": "Isolado"})
    # vícios: id inexistente, id repetido e seção fora do enum
    stories[2]["article_ids"].append("a9999")
    stories[3]["article_ids"].append(stories[0]["article_ids"][0])
    stories[4]["section"] = "esportes"
    return {"stories": stories, "lead": 1}


def fake_writing(prompt: str, keys: list[str]) -> dict[str, Any]:
    """Redação: título a partir do 1º título em pt da matéria, corpo com **negrito**."""
    blocks = {m.group(1): m.group(4) for m in MATERIA_RE.finditer(prompt)}
    assert set(blocks) == set(keys)
    stories = []
    for i, key in enumerate(keys):
        titles = re.findall(r"^Título: (.+)$", blocks[key], re.M)
        texts = re.findall(r"^Texto: (.+)$", blocks[key], re.M)
        headline = titles[0][:100]
        sentences = re.split(r"(?<=[.!?])\s+", texts[0]) if texts else [headline]
        story = {
            "key": key,
            "headline": headline,
            "dek": sentences[0][:190],
            "body": [
                f"Segundo as fontes, **{headline.split()[0]}** aparece no centro do fato. " + " ".join(sentences[1:3]),
                "O segundo parágrafo traz o contexto relatado pelas fontes, sem opinião.",
            ],
            "why_it_matters": "O fato afeta decisões de investimento e o custo de capital no Brasil.",
        }
        if i == 2:  # markdown e HTML onde não deveriam estar
            story["headline"] = f"**{headline}** {INJECTION}"
            story["dek"] = f"# {story['dek']} *itálico*"
            story["body"][1] = f"Parágrafo com {INJECTION} e **negrito quebrado"
        stories.append(story)
    stories.pop()  # a última matéria "esquecida" pelo modelo vira texto automático
    return {
        "editorial": "O dia combina **câmbio** pressionado pela eleição e cautela com a IA. Juros seguem no radar.",
        "briefing": [
            "**Dólar** fecha a R$ 5,22, maior nível desde março.",
            "OpenAI desiste de lançar o modelo Astra.",
            "EUA e China reduzem tarifas sobre US$ 60 bilhões.",
            "Governo prorroga a renegociação de dívidas até 26 de outubro.",
            "AGU pede R$ 1 bilhão a operadoras de apostas.",
        ],
        "stories": stories,
    }


class FakeAPI:
    """Handler do ``MockTransport``: responde as duas chamadas como a API faria."""

    def __init__(self, *, fail_status: int | None = None, refuse: bool = False) -> None:
        self.bodies: list[dict[str, Any]] = []
        self.headers: list[httpx2.Headers] = []
        self.fail_status = fail_status
        self.refuse = refuse

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        self.headers.append(request.headers)
        if self.fail_status:
            return httpx2.Response(
                self.fail_status, json={"type": "error", "error": {"type": "api_error", "message": "indisponível"}}
            )
        schema = body["output_config"]["format"]["schema"]
        prompt = body["messages"][0]["content"]
        if self.refuse:
            content = _stream(None, stop_reason="refusal")
        elif "lead" in schema["properties"]:
            content = _stream(fake_selection(prompt), usage=(12000, 1500))
        else:
            keys = schema["properties"]["stories"]["items"]["properties"]["key"]["enum"]
            content = _stream(fake_writing(prompt, keys), usage=(30000, 9000))
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=content)


def make_client(api: FakeAPI) -> anthropic.Anthropic:
    return anthropic.Anthropic(
        api_key="sk-ant-teste-nao-vaza",
        max_retries=0,
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(api)),
    )


def page_info_for_leads(articles):
    """Enriquecimento falso: texto longo e imagem para os artigos principais."""
    return {
        a.id: PageInfo(image=f"https://img.example.com/{a.id}.jpg", description=None, text=(a.summary + " ") * 3)
        for a in articles
    }


@pytest.fixture
def config(monkeypatch: pytest.MonkeyPatch):
    for key in ("ANTHROPIC_API_KEY", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_TO", "GITHUB_ACTIONS", "QIJ_NO_LLM"):
        monkeypatch.delenv(key, raising=False)
    return load_config(env={})


def _run(config, out: Path, client, **kw) -> pipeline.RunResult:
    return pipeline.run(config, out_dir=out, bundle_path=BUNDLE, send_email=False, client=client, env={}, **kw)


# ── testes ──────────────────────────────────────────────────────────────────


def test_ai_edition_end_to_end(tmp_path: Path, config):
    api = FakeAPI()
    calls: list[int] = []

    def enrich_fn(articles):
        calls.append(len(articles))
        return page_info_for_leads(articles)

    result = _run(config, tmp_path, make_client(api), enrich_fn=enrich_fn)
    edition = result.edition

    # chamadas à API no formato exigido
    assert len(api.bodies) == 2
    for body, headers in zip(api.bodies, api.headers, strict=True):
        assert body["model"] == "claude-opus-5-5" and body["stream"] is True
        assert body["fallbacks"] == "default"
        assert "server-side-fallback-2026-07-01" in headers["anthropic-beta"]
        assert set(body["output_config"]) == {"effort", "format"}
        assert not {"thinking", "temperature", "top_p", "top_k"} & set(body)
    assert calls and calls[0] <= config.edition.enrich_limit

    # edição por IA completa, validada e com tokens somados
    assert edition.mode == "ai" and edition.model == "claude-opus-5-5"
    assert result.warnings == []
    assert (edition.stats.llm_input_tokens, edition.stats.llm_output_tokens) == (42000, 10500)
    assert edition.editorial.startswith("O dia combina **câmbio**")
    assert len(edition.briefing) == 5
    assert len(edition.stories) >= 20 and len(edition.sections) >= 6
    all_ids = [a for s in edition.stories for a in s.article_ids]
    assert len(all_ids) == len(set(all_ids))  # nenhum artigo em duas matérias
    openai = next(s for s in edition.stories if "openai" in _norm(" ".join(x.name for x in s.sources) + s.headline))
    assert len(openai.sources) >= 5  # grupo multilíngue preservado
    assert edition.story(edition.lead).importance == 5
    for story in edition.stories:
        for bad in "*#<>":
            assert bad not in story.headline and bad not in story.dek
        assert story.image and story.image.startswith("https://")  # principal ou enriquecimento
        assert all(p.count("**") % 2 == 0 for p in story.body)
    assert any(s.why_it_matters for s in edition.stories)
    assert any(s.why_it_matters is None for s in edition.stories)  # matéria "esquecida" → texto automático

    # arquivos publicados
    latest = json.loads((tmp_path / "edicoes" / "latest.json").read_text(encoding="utf-8"))
    assert latest["mode"] == "ai" and latest["model"] == "claude-opus-5-5"
    saved = Edition.from_dict(json.loads((tmp_path / "data" / f"{edition.date}.json").read_text(encoding="utf-8")))
    assert saved.to_dict() == edition.to_dict()

    page = (tmp_path / "index.html").read_text(encoding="utf-8")
    email_html = (tmp_path / "edicoes" / "email.html").read_text(encoding="utf-8")
    email_text = (tmp_path / "edicoes" / "email.txt").read_text(encoding="utf-8")
    assert "Edição gerada por IA (claude-opus-5-5)" in page
    assert "Por que importa" in page
    assert "<strong>câmbio</strong>" in page and "<strong>câmbio</strong>" in email_html
    for html in (page, email_html):
        assert INJECTION not in html  # nada de HTML vindo da IA sem escape
        body_html = re.sub(r"<(script|style)\b.*?</\1>", "", html, flags=re.S)
        assert "**" not in body_html and "<h2>:</h2>" not in body_html
    assert "<script>alert" not in page and "Parágrafo com alert(1)" in page  # corpo: tags removidas, texto mantido
    assert "**" not in email_text and "<strong>" not in email_text
    assert "sk-ant-teste-nao-vaza" not in page + email_html + email_text


@pytest.mark.parametrize(
    ("api", "reason"),
    [(FakeAPI(fail_status=500), "erro da API (500)"), (FakeAPI(refuse=True), "recusa")],
    ids=["http-500", "refusal"],
)
def test_ai_failure_falls_back_to_heuristic(tmp_path: Path, config, caplog, api, reason):
    caplog.set_level("WARNING")
    result = _run(config, tmp_path, make_client(api))

    assert result.edition.mode == "heuristic" and result.edition.model is None
    assert len(result.edition.stories) >= 8
    assert any("Edição gerada sem IA" in w for w in result.warnings)
    assert reason in caplog.text
    assert "sk-ant-teste-nao-vaza" not in caplog.text
    latest = json.loads((tmp_path / "edicoes" / "latest.json").read_text(encoding="utf-8"))
    assert latest["mode"] == "heuristic"
    assert (tmp_path / "index.html").is_file()
