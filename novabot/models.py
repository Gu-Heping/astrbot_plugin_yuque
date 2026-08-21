"""Domain models for NovaBot knowledge retrieval."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any


DEFAULT_TEAM_ID = "default"
DEFAULT_TEAM_NAME = "NOVA"


def scoped_document_id(team_id: str, raw_document_id: object) -> str:
    """Return the storage identity for a document within a team.

    The default team keeps the legacy bare Yuque ID so existing single-team
    indexes remain addressable. Non-default teams are namespaced to prevent
    same-ID documents from overwriting one another.
    """

    raw = str(raw_document_id or "").strip()
    team = str(team_id or DEFAULT_TEAM_ID).strip() or DEFAULT_TEAM_ID
    if not raw:
        return ""
    return raw if team == DEFAULT_TEAM_ID else f"{team}:{raw}"


@dataclass(frozen=True)
class Team:
    """A first-class Yuque team scope.

    The legacy single-token setup is represented as the ``default`` team, so old
    installs can migrate without a data rewrite.
    """

    team_id: str = DEFAULT_TEAM_ID
    name: str = DEFAULT_TEAM_NAME
    description: str = ""
    yuque_token: str = ""
    yuque_base_url: str = "https://www.yuque.com/api/v2"
    enabled: bool = True

    @classmethod
    def default(
        cls,
        *,
        yuque_token: str = "",
        yuque_base_url: str = "https://www.yuque.com/api/v2",
        enabled: bool = True,
    ) -> "Team":
        return cls(
            team_id=DEFAULT_TEAM_ID,
            name=DEFAULT_TEAM_NAME,
            description="默认语雀团队（兼容旧版单团队配置）",
            yuque_token=yuque_token,
            yuque_base_url=yuque_base_url,
            enabled=enabled,
        )


@dataclass(frozen=True)
class RepositoryRef:
    team_id: str
    namespace: str
    name: str = ""
    slug: str = ""
    description: str = ""


@dataclass(frozen=True)
class RetrievalScope:
    """Composable retrieval filters chosen by Agent or command tools."""

    team_ids: tuple[str, ...] = ()
    repositories: tuple[str, ...] = ()
    path_prefix: str = ""
    author: str = ""
    updated_after: str = ""
    updated_before: str = ""
    keywords: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "RetrievalScope":
        if not data:
            return cls()
        return cls(
            team_ids=_as_tuple(data.get("team_ids") or data.get("team_id")),
            repositories=_as_tuple(data.get("repositories") or data.get("repository")),
            path_prefix=_normalize_path(data.get("path_prefix") or data.get("path") or ""),
            author=str(data.get("author") or ""),
            updated_after=str(data.get("updated_after") or ""),
            updated_before=str(data.get("updated_before") or ""),
            keywords=_as_tuple(data.get("keywords") or data.get("keyword")),
        )

    def matches_doc(self, doc: dict[str, Any]) -> bool:
        if self.team_ids and str(doc.get("team_id") or DEFAULT_TEAM_ID) not in self.team_ids:
            return False
        if self.repositories:
            repo_text = " ".join(
                str(doc.get(k) or "")
                for k in ("book_name", "book_namespace", "repository", "namespace")
            ).casefold()
            if not any(repo.casefold() in repo_text for repo in self.repositories):
                return False
        if self.path_prefix:
            path = _normalize_path(str(doc.get("file_path") or doc.get("path") or ""))
            if not _path_matches_prefix(path, self.path_prefix):
                return False
        if self.author and self.author.casefold() not in str(doc.get("author") or "").casefold():
            return False
        updated = str(doc.get("updated_at") or "")
        if (self.updated_after or self.updated_before) and not updated:
            return False
        if updated:
            if self.updated_after and updated < self.updated_after:
                return False
            if self.updated_before and updated > self.updated_before:
                return False
        if self.keywords:
            haystack = " ".join(
                str(doc.get(k) or "") for k in ("title", "content", "body", "file_path")
            ).casefold()
            if not all(keyword.casefold() in haystack for keyword in self.keywords):
                return False
        return True


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    document_id: str
    chunk_index: int
    content: str
    content_hash: str
    title: str = ""
    team_id: str = DEFAULT_TEAM_ID
    team_name: str = DEFAULT_TEAM_NAME
    repository: str = ""
    namespace: str = ""
    slug: str = ""
    file_path: str = ""
    source_url: str = ""
    author: str = ""
    updated_at: str = ""

    @property
    def embedding_text(self) -> str:
        return "\n".join(part for part in (self.title.strip(), self.content.strip()) if part)

    def as_document(self) -> dict[str, Any]:
        return {
            "yuque_id": self.document_id,
            "title": self.title,
            "team_id": self.team_id,
            "team_name": self.team_name,
            "book_name": self.repository,
            "book_namespace": self.namespace,
            "slug": self.slug,
            "file_path": self.file_path,
            "author": self.author,
            "updated_at": self.updated_at,
            "content": self.content,
        }


@dataclass(frozen=True)
class RetrievalResult:
    chunk: Chunk
    score: float
    keyword_score: float = 0.0
    vector_score: float = 0.0
    methods: tuple[str, ...] = field(default_factory=tuple)
    reliable: bool = False


def _as_tuple(value: Any) -> tuple[str, ...]:
    if value is None or value == "":
        return ()
    if isinstance(value, str):
        raw = value.replace("，", ",").split(",")
        return tuple(v.strip() for v in raw if v.strip())
    if isinstance(value, (list, tuple, set)):
        return tuple(str(v).strip() for v in value if str(v).strip())
    return (str(value).strip(),)


def _normalize_path(path: str) -> str:
    if not path:
        return ""
    normalized = str(PurePosixPath(path.replace("\\", "/"))).strip("/")
    return "" if normalized == "." else normalized


def _path_matches_prefix(path: str, prefix: str) -> bool:
    path_parts = tuple(p for p in _normalize_path(path).split("/") if p)
    prefix_parts = tuple(p for p in _normalize_path(prefix).split("/") if p)
    if not prefix_parts:
        return True
    for start in range(0, max(len(path_parts) - len(prefix_parts) + 1, 1)):
        if path_parts[start : start + len(prefix_parts)] == prefix_parts:
            return True
    return False
