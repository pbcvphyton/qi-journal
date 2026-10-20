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
    media_id: str | None = None
    permalink: str | None = None
    published_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Post":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    def due_at(self) -> datetime:
        return datetime.fromisoformat(self.due)

    def expires_at(self) -> datetime:
        return datetime.fromisoformat(self.expires)


@dataclass
class Fila:
    posts: list[Post] = field(default_factory=list)
    token: dict[str, Any] = field(default_factory=dict)  # {"refreshed_at", "expires_at"} (sem o token!)
    path: Path | None = None

    @classmethod
    def load(cls, media_dir: Path) -> "Fila":
        path = Path(media_dir) / FILA_FILE
        if not path.is_file():
            return cls(path=path)
        data = json.loads(path.read_text(encoding="utf-8"))
        posts = [Post.from_dict(p) for p in data.get("posts") or [] if isinstance(p, dict)]
        return cls(posts=posts, token=dict(data.get("token") or {}), path=path)

    def save(self) -> None:
        assert self.path is not None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"posts": [p.to_dict() for p in self.posts], "token": self.token}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    def get(self, post_id: str) -> Post | None:
        return next((p for p in self.posts if p.id == post_id), None)

    def upsert(self, posts: Iterable[Post]) -> list[str]:
        """Adiciona as publicações do dia; refazer a edição atualiza só as que ainda não saíram.

        Devolve os ids adicionados ou atualizados. Publicação já feita (ou que
        expirou) nunca é trocada.
        """
        changed: list[str] = []
        for post in posts:
            if post.kind not in KINDS:
                raise ValueError(f"tipo de publicação desconhecido: {post.kind}")
            current = self.get(post.id)
            if current is None:
                self.posts.append(post)
                changed.append(post.id)
            elif current.status in (PENDING, FAILED):
                self.posts[self.posts.index(current)] = post
                changed.append(post.id)
        self.posts.sort(key=lambda p: (p.due_at(), p.id))
        return changed

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

    def prune(self, today: date, keep_days: int = KEEP_DAYS) -> list[Post]:
        """Remove da fila registros com mais de ``keep_days`` dias; devolve os removidos."""
        cutoff = (today - timedelta(days=keep_days)).isoformat()
        old = [p for p in self.posts if p.date < cutoff]
        self.posts = [p for p in self.posts if p.date >= cutoff]
        return old


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
