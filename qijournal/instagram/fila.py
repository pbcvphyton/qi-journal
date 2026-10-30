"""Fila de publicações do Instagram (``fila.json`` na branch ``instagram``).

Cada edição gera as publicações do dia com um horário-alvo (``due``) e um prazo
(``expires``). O publicador roda de hora em hora (e logo depois da edição) e
publica o que já venceu e ainda não expirou. O estado fica no repositório, na
branch ``instagram`` junto com as imagens, então uma execução atrasada,
repetida ou refeita nunca publica a mesma coisa duas vezes: o id da mídia é
gravado assim que a API confirma.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

log = logging.getLogger(__name__)

FILA_FILE = "fila.json"
KINDS = ("carousel", "image", "story")
PENDING, PUBLISHED, FAILED, EXPIRED = "pending", "published", "failed", "expired"
MAX_ATTEMPTS = 4  # tentativas por publicação (erros permanentes desistem na hora)
KEEP_DAYS = 45  # registros mais antigos saem da fila
FEED_GAP = timedelta(hours=3)  # intervalo mínimo entre dois posts de feed
SERIES = ("em1min", "numero", "raiox", "placar", "semana", "mercado")
SEM_NUMERO = {"mercado"}  # o Mercado em linha não leva número de série
NUMERO_REPETICAO_DIAS = 7  # chaves do Número publicadas nesse período não voltam
RAIOX_REPETICAO_DIAS = 3  # entidades dos Raio-X desse período penalizam a repetição


@dataclass
class Post:
    id: str  # único: "<data>-<slot>", ex.: "2026-10-20-carrossel"
    date: str  # data da edição (AAAA-MM-DD)
    kind: str  # "carousel" | "image" | "story"
    due: str  # ISO 8601 com fuso: a partir de quando pode sair
    expires: str  # ISO 8601 com fuso: depois disso, não sai mais (notícia velha)
    images: list[str]  # caminhos relativos à pasta de mídia (branch instagram)
    caption: str = ""
    alt_texts: list[str] = field(default_factory=list)
    title: str = ""  # rótulo curto para logs e para o e-mail de resumo
    status: str = PENDING
    attempts: int = 0
    last_error: str | None = None
    container_id: str | None = None  # container criado e ainda não publicado (recuperação)
    permanent: bool = False  # nunca sai da fila (o "Comece aqui": sai uma vez só, para sempre)
    media_id: str | None = None
    permalink: str | None = None
    published_at: str | None = None
    serie: str = ""  # "em1min" | "numero" | "raiox" (o Placar do dia também) | "placar" | "semana" | "mercado"
    numero_serie: int = 0  # "RAIO-X Nº 015"; 0 = sem número (Mercado em linha, stories)
    story_ids: list[str] = field(default_factory=list)  # matérias usadas
    chave: str = ""  # do Número do dia: não repete em 7 dias
    entidades: list[str] = field(default_factory=list)  # do Raio-X: penaliza a repetição em 3 dias
    formula: str = ""  # molde do gancho ("G2", "G3", "G0", "compacto", "sem_rotulo")
    redacao: dict[str, Any] = field(default_factory=dict)  # Onda 2: {modo, formula, falhas, tentativas}
    metricas: dict[str, Any] = field(default_factory=dict)  # Onda 2: insights de D+1 e D+7

    @property
    def feed(self) -> bool:
        """Vai para o feed (carrossel, imagem); story não conta no intervalo entre posts."""
        return self.kind != "story"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Post":
        """Lê um registro do ``fila.json``; campos que ainda não existiam ficam no padrão."""
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known and not (v is None and k in _COLECOES)})

    def due_at(self) -> datetime:
        return datetime.fromisoformat(self.due)

    def expires_at(self) -> datetime:
        return datetime.fromisoformat(self.expires)


_COLECOES = {"alt_texts", "story_ids", "entidades", "redacao", "metricas"}  # null no JSON = padrão


@dataclass
class Fila:
    posts: list[Post] = field(default_factory=list)
    token: dict[str, Any] = field(default_factory=dict)  # {"refreshed_at", "expires_at"} (sem o token!)
    path: Path | None = None
    series: dict[str, int] = field(default_factory=dict)  # último número PUBLICADO de cada série

    @classmethod
    def load(cls, media_dir: Path) -> "Fila":
        path = Path(media_dir) / FILA_FILE
        if not path.is_file():
            return cls(path=path)
        data = json.loads(path.read_text(encoding="utf-8"))
        posts = [Post.from_dict(p) for p in data.get("posts") or [] if isinstance(p, dict)]
        series = {str(k): int(v) for k, v in (data.get("series") or {}).items() if isinstance(v, int)}
        return cls(posts=posts, token=dict(data.get("token") or {}), path=path, series=series)

    def save(self) -> None:
        assert self.path is not None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"posts": [p.to_dict() for p in self.posts], "token": self.token, "series": self.series}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    def get(self, post_id: str) -> Post | None:
        return next((p for p in self.posts if p.id == post_id), None)

    def upsert(self, posts: Iterable[Post]) -> list[str]:
        """Adiciona as publicações do dia; refazer a edição atualiza só as que ainda não saíram.

        Devolve os ids adicionados ou atualizados. Publicação já feita nunca é
        trocada; uma que expirou só volta com um prazo mais longo (o "Comece
        aqui", renovado a cada edição até sair).
        """
        changed: list[str] = []
        for post in posts:
            if post.kind not in KINDS:
                raise ValueError(f"tipo de publicação desconhecido: {post.kind}")
            current = self.get(post.id)
            if current is None:
                self.posts.append(post)
                changed.append(post.id)
            elif current.status in (PENDING, FAILED) or (
                current.status == EXPIRED and post.expires_at() > current.expires_at()
            ):
                if current.container_id and current.status in (PENDING, FAILED):
                    # Um container criado (talvez já publicado) não pode ser esquecido:
                    # a próxima execução confere o status dele antes de criar outro.
                    post.container_id, post.attempts = current.container_id, current.attempts
                self.posts[self.posts.index(current)] = post
                changed.append(post.id)
        self.posts.sort(key=lambda p: (p.due_at(), p.id))
        return changed

    def replace_day(self, day: date | str, posts: Iterable[Post]) -> list[str]:
        """Refaz as publicações de um dia: o que não veio na geração nova sai da fila.

        Só saem posts daquela data ainda não publicados, que não são permanentes e não
        têm container criado (que talvez já tenha ido ao ar). O resto segue :meth:`upsert`.
        """
        posts = list(posts)
        day = day.isoformat() if isinstance(day, date) else day
        novos = {p.id for p in posts}
        gone = [
            p
            for p in self.posts
            if p.date == day and p.id not in novos and p.status != PUBLISHED and not p.permanent and not p.container_id
        ]
        for p in gone:
            log.info("Instagram: %s saiu da fila (a geração nova do dia não tem mais esse post)", p.id)
        self.posts = [p for p in self.posts if p not in gone]
        return self.upsert(posts)

    # ── Séries numeradas ──────────────────────────────────────────────────

    def numero_para(self, serie: str, post_id: str | None = None) -> int:
        """Número de série do post: o último publicado + 1 (0 sem série ou no Mercado em linha).

        Refazer o mesmo post (mesmo id, mesma série) mantém o número; um post que expirou
        não consome número, porque a série só avança quando publica (:meth:`registrar_publicacao`).
        """
        if not serie or serie in SEM_NUMERO:
            return 0
        current = self.get(post_id) if post_id else None
        if current is not None and current.serie == serie and current.numero_serie > 0:
            return current.numero_serie
        return self.series.get(serie, 0) + 1

    def registrar_publicacao(self, post: Post) -> None:
        """A série do post avança até o número dele (chamado depois do ``media_publish``)."""
        if post.serie and post.numero_serie > 0:
            self.series[post.serie] = max(self.series.get(post.serie, 0), post.numero_serie)

    def _recentes(self, serie: str, day: date | str, dias: int, statuses: tuple[str, ...]) -> list[Post]:
        day = date.fromisoformat(day) if isinstance(day, str) else day
        start = (day - timedelta(days=dias)).isoformat()
        return [p for p in self.posts if p.serie == serie and p.status in statuses and start <= p.date < day.isoformat()]

    def chaves_numero(self, day: date | str, dias: int = NUMERO_REPETICAO_DIAS) -> set[str] | None:
        """Chaves dos Números publicados nos ``dias`` anteriores a ``day`` (o ``usados`` de
        :func:`.estatistica.numero_do_dia`); ``None`` enquanto a fila não tem nenhum Número
        publicado (vale a regra antiga)."""
        if not any(p.serie == "numero" and p.status == PUBLISHED for p in self.posts):
            return None
        return {p.chave for p in self._recentes("numero", day, dias, (PUBLISHED,)) if p.chave}

    def entidades_raiox(self, day: date | str, dias: int = RAIOX_REPETICAO_DIAS) -> list[list[str]]:
        """Entidades dos Raio-X dos ``dias`` anteriores a ``day`` (publicados ou ainda na fila),
        uma lista por post: o ``entidades_recentes`` de :func:`.selecao.escolher_raiox`."""
        return [list(p.entidades) for p in self._recentes("raiox", day, dias, (PUBLISHED, PENDING)) if p.entidades]

    def due(self, now: datetime) -> list[Post]:
        """Publicações pendentes cujo horário chegou; marca como expiradas as que passaram do prazo."""
        ready: list[Post] = []
        for post in self.posts:
            if post.status not in (PENDING, FAILED):
                continue
            if post.status == FAILED and post.attempts >= MAX_ATTEMPTS:
                continue
            if now >= post.expires_at():
                log.info("Instagram: %s expirou sem sair (%s)", post.id, post.last_error or "não chegou a vez")
                post.status = EXPIRED
                continue
            if now >= post.due_at():
                ready.append(post)
        return sorted(ready, key=lambda p: (p.due_at(), p.id))

    def last_feed_publication(self) -> datetime | None:
        times = [datetime.fromisoformat(p.published_at) for p in self.posts if p.feed and p.status == PUBLISHED and p.published_at]
        return max(times) if times else None

    def next_due(self, now: datetime) -> datetime | None:
        """Próximo horário-alvo ainda no futuro entre as publicações pendentes."""
        upcoming = [p.due_at() for p in self.posts if p.status == PENDING and p.due_at() > now and p.expires_at() > now]
        return min(upcoming) if upcoming else None

    def prune(self, today: date, keep_days: int = KEEP_DAYS) -> list[Post]:
        """Remove da fila registros com mais de ``keep_days`` dias; devolve os removidos."""
        cutoff = (today - timedelta(days=keep_days)).isoformat()
        old = [p for p in self.posts if p.date < cutoff and not p.permanent]
        self.posts = [p for p in self.posts if p.date >= cutoff or p.permanent]
        return old

    def retry_soon(self, now: datetime) -> bool:
        """Há publicação vencida, ainda no prazo, esperando nova tentativa?"""
        return any(
            p.status in (PENDING, FAILED) and p.attempts < MAX_ATTEMPTS and p.due_at() <= now < p.expires_at()
            for p in self.posts
        )

    def has_published(self, post_id: str) -> bool:
        post = self.get(post_id)
        return post is not None and post.status == PUBLISHED


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
