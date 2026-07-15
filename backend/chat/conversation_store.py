"""
Conversation persistence.

In-memory with JSON snapshot-to-disk so history survives a restart during the
POC (production swaps in Redis/DynamoDB behind the same interface). Supports the
edit + regenerate semantics the UI needs: editing a user message truncates every
turn after it; regenerating drops the last assistant turn.
"""
from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from config.settings import settings


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Message:
    id: str
    role: Literal["user", "assistant"]
    content: str
    created_at: str = field(default_factory=_now)
    citations: list[dict[str, Any]] = field(default_factory=list)
    confidence: float | None = None
    attachments: list[dict[str, Any]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Conversation:
    id: str
    title: str
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    messages: list[Message] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "title": self.title,
            "created_at": self.created_at, "updated_at": self.updated_at,
            "messages": [asdict(m) for m in self.messages],
        }


class ConversationStore:
    def __init__(self, directory: str | None = None) -> None:
        base = Path(__file__).resolve().parent.parent
        self.dir = base / (directory or settings.CONVERSATION_DIR)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._cache: dict[str, Conversation] = {}
        self._load_all()

    # -- persistence ---------------------------------------------------- #
    def _path(self, conv_id: str) -> Path:
        return self.dir / f"{conv_id}.json"

    def _load_all(self) -> None:
        for path in self.dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                conv = Conversation(
                    id=data["id"], title=data["title"],
                    created_at=data.get("created_at", _now()),
                    updated_at=data.get("updated_at", _now()),
                    messages=[Message(**m) for m in data.get("messages", [])],
                )
                self._cache[conv.id] = conv
            except Exception:
                continue  # skip corrupt snapshots rather than crash on boot

    def _persist(self, conv: Conversation) -> None:
        # Ensure the directory exists on every write: it may have been removed
        # after startup (cleanup script, tmp reaper), which would otherwise turn
        # every save into a 500. Self-healing keeps the store robust.
        self.dir.mkdir(parents=True, exist_ok=True)
        self._path(conv.id).write_text(
            json.dumps(conv.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # -- CRUD ----------------------------------------------------------- #
    def create(self, title: str = "New chat") -> Conversation:
        conv = Conversation(id="conv_" + uuid.uuid4().hex[:12], title=title)
        with self._lock:
            self._cache[conv.id] = conv
            self._persist(conv)
        return conv

    def get(self, conv_id: str) -> Conversation | None:
        with self._lock:
            return self._cache.get(conv_id)

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            convs = sorted(self._cache.values(), key=lambda c: c.updated_at, reverse=True)
            return [
                {
                    "id": c.id, "title": c.title, "updated_at": c.updated_at,
                    "message_count": len(c.messages),
                    "preview": c.messages[0].content[:80] if c.messages else "",
                }
                for c in convs
            ]

    def delete(self, conv_id: str) -> bool:
        with self._lock:
            if conv_id in self._cache:
                del self._cache[conv_id]
                self._path(conv_id).unlink(missing_ok=True)
                return True
        return False

    # -- message operations -------------------------------------------- #
    def add_message(self, conv_id: str, message: Message) -> None:
        with self._lock:
            conv = self._cache[conv_id]
            conv.messages.append(message)
            conv.updated_at = _now()
            if len([m for m in conv.messages if m.role == "user"]) == 1 and message.role == "user":
                conv.title = _derive_title(message.content)
            self._persist(conv)

    def truncate_after(self, conv_id: str, message_id: str, *, inclusive: bool) -> None:
        """Drop messages after ``message_id`` (edit) or including it (regenerate)."""
        with self._lock:
            conv = self._cache[conv_id]
            idx = next((i for i, m in enumerate(conv.messages) if m.id == message_id), None)
            if idx is None:
                return
            conv.messages = conv.messages[:idx] if inclusive else conv.messages[: idx + 1]
            conv.updated_at = _now()
            self._persist(conv)

    def history(self, conv_id: str) -> list[Message]:
        with self._lock:
            conv = self._cache.get(conv_id)
            return list(conv.messages) if conv else []


def _derive_title(text: str) -> str:
    words = text.strip().split()
    title = " ".join(words[:7])
    return (title[:48] + "…") if len(title) > 48 else (title or "New chat")


def new_message(role: str, content: str, **kw: Any) -> Message:
    return Message(id="msg_" + uuid.uuid4().hex[:12], role=role, content=content, **kw)
