"""Identity scope for logically isolated canonical memory."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Optional


_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")


def normalize_scope_id(value: Optional[str], name: str, *, required: bool) -> str:
    """Normalize an identity value while rejecting ambiguous or unsafe IDs."""

    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    clean = value.strip()
    if required and not clean:
        raise ValueError(f"{name} is required")
    if len(clean) > 128:
        raise ValueError(f"{name} must be at most 128 characters")
    if _CONTROL_CHARACTERS.search(clean):
        raise ValueError(f"{name} contains a control character")
    return clean


@dataclass(frozen=True)
class MemoryScope:
    """Workspace/owner boundary with optional agent and session narrowing."""

    workspace_id: str
    owner_id: str
    agent_id: str = ""
    session_id: str = ""

    @classmethod
    def create(
        cls,
        workspace_id: str,
        owner_id: str,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> "MemoryScope":
        return cls(
            workspace_id=normalize_scope_id(workspace_id, "workspace_id", required=True),
            owner_id=normalize_scope_id(owner_id, "owner_id", required=True),
            agent_id=normalize_scope_id(agent_id, "agent_id", required=False),
            session_id=normalize_scope_id(session_id, "session_id", required=False),
        )


__all__ = ["MemoryScope", "normalize_scope_id"]
