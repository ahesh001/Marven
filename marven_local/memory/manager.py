"""Marven's canonical memory store and deterministic evidence graph.

The ``mem`` table is the source of truth. Embeddings and graph tables are
disposable projections that can be rebuilt from canonical records at any
time. The graph is deliberately deterministic: it proposes related evidence
through typed, bounded traversal and never edits canonical memory.
"""

from __future__ import annotations

from collections import defaultdict, deque
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import struct
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
import uuid

from .retrieval import EmbeddingProvider, embedding_provider_from_env
from .scope import MemoryScope, normalize_scope_id


GRAPH_PROJECTION_VERSION = "1"
EMBEDDING_PROJECTION_VERSION = "3"
LEXICAL_PROJECTION_VERSION = "1"
RRF_K = 60
MAX_GRAPH_VISITS_PER_SEED = 5000
DEFAULT_GRAPH_EDGE_TYPES = frozenset(
    {
        "about",
        "subject_of",
        "tagged_with",
        "tag_for",
        "in_episode",
        "episode_contains",
        "mentions",
        "mentioned_by",
        "from_source",
        "source_contains",
        "derived_from",
        "source_for",
        "supersedes",
        "superseded_by",
    }
)
TRUST_STATES = frozenset({"confirmed", "unverified", "disputed", "rejected"})
VISIBILITY_STATES = frozenset({"private", "shared", "public"})
PROPOSAL_STATES = frozenset({"pending", "approved", "rejected"})
RETRIEVAL_QUERY_STORAGE_MODES = frozenset({"plaintext", "hash-only"})
RETRIEVAL_LABEL_SOURCES = frozenset({"human", "benchmark", "researcher"})
RETRIEVAL_RELEVANCE_GRADES = frozenset({0, 1, 2, 3})


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: Optional[str]) -> Optional[datetime.datetime]:
    if not value:
        return None
    raw = str(value).strip()
    candidates = [raw]
    if raw.endswith("Z"):
        candidates.insert(0, raw[:-1] + "+00:00")
    for candidate in candidates:
        try:
            parsed = datetime.datetime.fromisoformat(candidate)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=datetime.timezone.utc)
            return parsed.astimezone(datetime.timezone.utc)
        except ValueError:
            pass
    for fmt in ("%Y/%m/%d (%a) %H:%M", "%Y/%m/%d", "%Y-%m-%d"):
        try:
            parsed = datetime.datetime.strptime(raw, fmt)
            return parsed.replace(tzinfo=datetime.timezone.utc)
        except ValueError:
            pass
    return None


def _json_list(value: Any) -> List[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            value = [value]
    if not isinstance(value, (list, tuple, set)):
        value = [value]
    out: List[str] = []
    for item in value:
        text = str(item).strip()
        if text and text not in out:
            out.append(text)
    return out


def _json_object(value: Any) -> Dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return dict(decoded) if isinstance(decoded, Mapping) else {}
        except (TypeError, ValueError):
            return {}
    return {}


class MemoryManager:
    """SQLite-backed canonical memory with disposable search projections."""

    def __init__(
        self,
        root: Path,
        *,
        workspace_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        embedding_provider: Optional[EmbeddingProvider] = None,
    ):
        self.root = Path(root)
        self.mem_dir = self.root / "memory"
        self.mem_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.mem_dir / "marven_mem.db"
        self.cache_path = self.mem_dir / "prompt_cache.json"
        self.default_scope = MemoryScope.create(
            workspace_id
            if workspace_id is not None
            else os.environ.get("MARVEN_WORKSPACE_ID", "local"),
            owner_id
            if owner_id is not None
            else os.environ.get("MARVEN_OWNER_ID", "primary"),
            agent_id
            if agent_id is not None
            else os.environ.get("MARVEN_AGENT_ID", "marven"),
        )
        self.embedding_provider = embedding_provider or embedding_provider_from_env()
        self.dim = int(self.embedding_provider.dimension)
        if self.dim <= 0:
            raise ValueError("embedding provider dimension must be positive")
        self._fts_enabled = False
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        self._ensure_schema()
        self._ensure_embeddings()
        self._ensure_lexical_projection()
        self.rebuild_graph_projection()
        try:
            cached = json.loads(self.cache_path.read_text(encoding="utf-8"))
            self._cache = cached if isinstance(cached, dict) else {}
        except Exception:
            self._cache = {}

    def close(self) -> None:
        self._conn.close()

    def _ensure_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS mem (
              id TEXT PRIMARY KEY,
              workspace_id TEXT NOT NULL DEFAULT 'local',
              owner_id TEXT NOT NULL DEFAULT 'primary',
              agent_id TEXT NOT NULL DEFAULT 'marven',
              session_id TEXT NOT NULL DEFAULT '',
              text TEXT NOT NULL,
              tags TEXT DEFAULT '',
              ts TEXT NOT NULL,
              score REAL DEFAULT 0,
              subject TEXT DEFAULT '',
              memory_type TEXT DEFAULT 'episodic',
              source TEXT DEFAULT 'user',
              source_locator TEXT DEFAULT '',
              valid_from TEXT,
              valid_to TEXT,
              confidence REAL DEFAULT 1.0,
              trust_status TEXT DEFAULT 'unverified',
              consent_scope TEXT DEFAULT 'local',
              visibility TEXT DEFAULT 'private',
              episode_id TEXT DEFAULT '',
              lineage TEXT DEFAULT '[]',
              supersedes TEXT DEFAULT '[]',
              superseded_by TEXT DEFAULT '',
              deleted_at TEXT,
              metadata TEXT DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS emb (
              id TEXT PRIMARY KEY,
              vector BLOB NOT NULL,
              dim INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS episodes (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              workspace_id TEXT NOT NULL DEFAULT 'local',
              owner_id TEXT NOT NULL DEFAULT 'primary',
              agent_id TEXT NOT NULL DEFAULT 'marven',
              session_id TEXT NOT NULL DEFAULT '',
              ts TEXT NOT NULL,
              role TEXT NOT NULL,
              content TEXT NOT NULL,
              meta TEXT
            );

            CREATE TABLE IF NOT EXISTS hot (
              id TEXT PRIMARY KEY,
              ts TEXT NOT NULL,
              hits INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS memory_graph_nodes (
              node_id TEXT PRIMARY KEY,
              node_type TEXT NOT NULL,
              label TEXT NOT NULL,
              canonical_id TEXT,
              attrs TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS memory_graph_edges (
              src_id TEXT NOT NULL,
              dst_id TEXT NOT NULL,
              edge_type TEXT NOT NULL,
              weight REAL NOT NULL DEFAULT 1.0,
              evidence TEXT NOT NULL DEFAULT '{}',
              PRIMARY KEY (src_id, dst_id, edge_type)
            );

            CREATE TABLE IF NOT EXISTS memory_projection_meta (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS memory_proposals (
              id TEXT PRIMARY KEY,
              workspace_id TEXT NOT NULL,
              owner_id TEXT NOT NULL,
              agent_id TEXT NOT NULL DEFAULT '',
              session_id TEXT NOT NULL DEFAULT '',
              text TEXT NOT NULL,
              tags TEXT DEFAULT '',
              proposed_at TEXT NOT NULL,
              subject TEXT DEFAULT '',
              memory_type TEXT DEFAULT 'episodic',
              source TEXT DEFAULT 'user',
              source_locator TEXT DEFAULT '',
              valid_from TEXT,
              valid_to TEXT,
              confidence REAL DEFAULT 1.0,
              trust_status TEXT DEFAULT 'unverified',
              consent_scope TEXT DEFAULT 'local',
              visibility TEXT DEFAULT 'private',
              episode_id TEXT DEFAULT '',
              lineage TEXT DEFAULT '[]',
              supersedes TEXT DEFAULT '[]',
              metadata TEXT DEFAULT '{}',
              status TEXT NOT NULL DEFAULT 'pending',
              decided_at TEXT,
              decision_reason TEXT DEFAULT '',
              canonical_id TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS retrieval_runs (
              id TEXT PRIMARY KEY,
              workspace_id TEXT NOT NULL,
              owner_id TEXT NOT NULL,
              agent_id TEXT NOT NULL DEFAULT '',
              session_id TEXT NOT NULL DEFAULT '',
              query_text TEXT NOT NULL DEFAULT '',
              query_hash TEXT NOT NULL,
              query_storage TEXT NOT NULL DEFAULT 'hash-only',
              created_at TEXT NOT NULL,
              top_k INTEGER NOT NULL,
              result_ids TEXT NOT NULL DEFAULT '[]',
              result_snapshot TEXT NOT NULL DEFAULT '[]',
              retrieval_config TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS retrieval_labels (
              id TEXT PRIMARY KEY,
              run_id TEXT NOT NULL,
              memory_id TEXT NOT NULL,
              relevance INTEGER NOT NULL CHECK(relevance BETWEEN 0 AND 3),
              label_source TEXT NOT NULL DEFAULT 'human',
              labeler_id TEXT NOT NULL DEFAULT '',
              note TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              FOREIGN KEY(run_id) REFERENCES retrieval_runs(id) ON DELETE CASCADE,
              UNIQUE(run_id, memory_id, label_source, labeler_id)
            );
            """
        )

        # Upgrade databases created by Persistent Memory v1 without replacing
        # or copying their canonical rows.
        existing = {
            row["name"] for row in self._conn.execute("PRAGMA table_info(mem)").fetchall()
        }
        additions = {
            "workspace_id": "TEXT NOT NULL DEFAULT 'local'",
            "owner_id": "TEXT NOT NULL DEFAULT 'primary'",
            "agent_id": "TEXT NOT NULL DEFAULT 'marven'",
            "session_id": "TEXT NOT NULL DEFAULT ''",
            "subject": "TEXT DEFAULT ''",
            "memory_type": "TEXT DEFAULT 'episodic'",
            "source": "TEXT DEFAULT 'user'",
            "source_locator": "TEXT DEFAULT ''",
            "valid_from": "TEXT",
            "valid_to": "TEXT",
            "confidence": "REAL DEFAULT 1.0",
            "trust_status": "TEXT DEFAULT 'unverified'",
            "consent_scope": "TEXT DEFAULT 'local'",
            "visibility": "TEXT DEFAULT 'private'",
            "episode_id": "TEXT DEFAULT ''",
            "lineage": "TEXT DEFAULT '[]'",
            "supersedes": "TEXT DEFAULT '[]'",
            "superseded_by": "TEXT DEFAULT ''",
            "deleted_at": "TEXT",
            "metadata": "TEXT DEFAULT '{}'",
        }
        missing_scope_columns = [
            column
            for column in ("workspace_id", "owner_id", "agent_id", "session_id")
            if column not in existing
        ]
        for column, declaration in additions.items():
            if column not in existing:
                self._conn.execute(f"ALTER TABLE mem ADD COLUMN {column} {declaration}")

        if missing_scope_columns:
            scope_defaults = {
                "workspace_id": self.default_scope.workspace_id,
                "owner_id": self.default_scope.owner_id,
                "agent_id": self.default_scope.agent_id,
                "session_id": "",
            }
            self._conn.execute(
                "UPDATE mem SET "
                + ", ".join(f"{column} = ?" for column in missing_scope_columns),
                tuple(scope_defaults[column] for column in missing_scope_columns),
            )

        episode_existing = {
            row["name"] for row in self._conn.execute("PRAGMA table_info(episodes)").fetchall()
        }
        episode_scope_additions = {
            "workspace_id": "TEXT NOT NULL DEFAULT 'local'",
            "owner_id": "TEXT NOT NULL DEFAULT 'primary'",
            "agent_id": "TEXT NOT NULL DEFAULT 'marven'",
            "session_id": "TEXT NOT NULL DEFAULT ''",
        }
        missing_episode_scope = [
            column for column in episode_scope_additions if column not in episode_existing
        ]
        for column, declaration in episode_scope_additions.items():
            if column not in episode_existing:
                self._conn.execute(f"ALTER TABLE episodes ADD COLUMN {column} {declaration}")
        if missing_episode_scope:
            scope_defaults = {
                "workspace_id": self.default_scope.workspace_id,
                "owner_id": self.default_scope.owner_id,
                "agent_id": self.default_scope.agent_id,
                "session_id": "",
            }
            self._conn.execute(
                "UPDATE episodes SET "
                + ", ".join(f"{column} = ?" for column in missing_episode_scope),
                tuple(scope_defaults[column] for column in missing_episode_scope),
            )

        self._conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_mem_ts ON mem(ts);
            CREATE INDEX IF NOT EXISTS idx_mem_scope
              ON mem(workspace_id, owner_id, agent_id, session_id, deleted_at);
            CREATE INDEX IF NOT EXISTS idx_mem_tags ON mem(tags);
            CREATE INDEX IF NOT EXISTS idx_mem_subject ON mem(subject);
            CREATE INDEX IF NOT EXISTS idx_mem_episode ON mem(episode_id);
            CREATE INDEX IF NOT EXISTS idx_mem_superseded ON mem(superseded_by);
            CREATE INDEX IF NOT EXISTS idx_mem_deleted ON mem(deleted_at);
            CREATE INDEX IF NOT EXISTS idx_ep_ts ON episodes(ts);
            CREATE INDEX IF NOT EXISTS idx_ep_scope
              ON episodes(workspace_id, owner_id, agent_id, session_id, ts);
            CREATE INDEX IF NOT EXISTS idx_graph_edges_src ON memory_graph_edges(src_id);
            CREATE INDEX IF NOT EXISTS idx_graph_edges_dst ON memory_graph_edges(dst_id);
            CREATE INDEX IF NOT EXISTS idx_graph_nodes_canonical ON memory_graph_nodes(canonical_id);
            CREATE INDEX IF NOT EXISTS idx_memory_proposal_scope
              ON memory_proposals(workspace_id, owner_id, status, proposed_at);
            CREATE INDEX IF NOT EXISTS idx_retrieval_run_scope
              ON retrieval_runs(workspace_id, owner_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_retrieval_label_run
              ON retrieval_labels(run_id, relevance, memory_id);
            """
        )
        fts_schema = """
            CREATE VIRTUAL TABLE IF NOT EXISTS mem_fts USING fts5(
              id UNINDEXED,
              workspace_id UNINDEXED,
              owner_id UNINDEXED,
              agent_id UNINDEXED,
              session_id UNINDEXED,
              content,
              tokenize='unicode61 remove_diacritics 2'
            )
        """
        try:
            self._conn.execute(fts_schema)
            fts_columns = {
                row["name"]
                for row in self._conn.execute("PRAGMA table_info(mem_fts)").fetchall()
            }
            expected_fts_columns = {
                "id",
                "workspace_id",
                "owner_id",
                "agent_id",
                "session_id",
                "content",
            }
            if not expected_fts_columns.issubset(fts_columns):
                self._conn.execute("DROP TABLE mem_fts")
                self._conn.execute(fts_schema)
                self._conn.execute(
                    "DELETE FROM memory_projection_meta WHERE key = 'lexical_version'"
                )
            self._fts_enabled = True
        except sqlite3.OperationalError:
            # Some minimal Python builds omit FTS5. Vector and graph retrieval
            # remain available, and the API reports lexical search as disabled.
            self._fts_enabled = False
        self._conn.commit()

    def _embed(self, text: str, *, purpose: str) -> bytes:
        vector = [
            float(value)
            for value in self.embedding_provider.embed(str(text or ""), purpose=purpose)
        ]
        if len(vector) != self.dim:
            raise ValueError(
                f"embedding provider returned {len(vector)} values; expected {self.dim}"
            )
        if not all(math.isfinite(value) for value in vector):
            raise ValueError("embedding provider returned a non-finite value")
        return struct.pack(f"{self.dim}f", *vector)

    def _unpack(self, blob: bytes) -> List[float]:
        return list(struct.unpack(f"{self.dim}f", blob))

    def _row_to_record(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": row["id"],
            "workspace_id": row["workspace_id"] or "local",
            "owner_id": row["owner_id"] or "primary",
            "agent_id": row["agent_id"] or "",
            "session_id": row["session_id"] or "",
            "text": row["text"],
            "tags": [tag.strip() for tag in (row["tags"] or "").split(",") if tag.strip()],
            "created_at": row["ts"],
            "score": float(row["score"] or 0.0),
            "subject": row["subject"] or "",
            "memory_type": row["memory_type"] or "episodic",
            "source": row["source"] or "user",
            "source_locator": row["source_locator"] or "",
            "valid_from": row["valid_from"],
            "valid_to": row["valid_to"],
            "confidence": float(row["confidence"] if row["confidence"] is not None else 1.0),
            "trust_status": row["trust_status"] or "unverified",
            "consent_scope": row["consent_scope"] or "local",
            "visibility": row["visibility"] or "private",
            "episode_id": row["episode_id"] or "",
            "lineage": _json_list(row["lineage"]),
            "supersedes": _json_list(row["supersedes"]),
            "superseded_by": row["superseded_by"] or "",
            "deleted_at": row["deleted_at"],
            "metadata": _json_object(row["metadata"]),
        }

    def _row_to_proposal(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": row["id"],
            "workspace_id": row["workspace_id"],
            "owner_id": row["owner_id"],
            "agent_id": row["agent_id"] or "",
            "session_id": row["session_id"] or "",
            "text": row["text"],
            "tags": [tag.strip() for tag in (row["tags"] or "").split(",") if tag.strip()],
            "proposed_at": row["proposed_at"],
            "subject": row["subject"] or "",
            "memory_type": row["memory_type"] or "episodic",
            "source": row["source"] or "user",
            "source_locator": row["source_locator"] or "",
            "valid_from": row["valid_from"],
            "valid_to": row["valid_to"],
            "confidence": float(row["confidence"] if row["confidence"] is not None else 1.0),
            "trust_status": row["trust_status"] or "unverified",
            "consent_scope": row["consent_scope"] or "local",
            "visibility": row["visibility"] or "private",
            "episode_id": row["episode_id"] or "",
            "lineage": _json_list(row["lineage"]),
            "supersedes": _json_list(row["supersedes"]),
            "metadata": _json_object(row["metadata"]),
            "status": row["status"],
            "decided_at": row["decided_at"],
            "decision_reason": row["decision_reason"] or "",
            "canonical_id": row["canonical_id"] or "",
        }

    def _row_to_retrieval_run(self, row: sqlite3.Row) -> Dict[str, Any]:
        try:
            snapshot_value = json.loads(row["result_snapshot"] or "[]")
        except (TypeError, ValueError):
            snapshot_value = []
        snapshot = [
            dict(item) for item in snapshot_value if isinstance(item, Mapping)
        ] if isinstance(snapshot_value, list) else []
        return {
            "id": row["id"],
            "workspace_id": row["workspace_id"],
            "owner_id": row["owner_id"],
            "agent_id": row["agent_id"] or "",
            "session_id": row["session_id"] or "",
            "query": row["query_text"] or "",
            "query_hash": row["query_hash"],
            "query_storage": row["query_storage"],
            "created_at": row["created_at"],
            "top_k": int(row["top_k"]),
            "result_ids": _json_list(row["result_ids"]),
            "results": snapshot,
            "retrieval_config": _json_object(row["retrieval_config"]),
        }

    @staticmethod
    def _row_to_retrieval_label(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": row["id"],
            "run_id": row["run_id"],
            "memory_id": row["memory_id"],
            "relevance": int(row["relevance"]),
            "label_source": row["label_source"],
            "labeler_id": row["labeler_id"] or "",
            "note": row["note"] or "",
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _index_text(self, record: Mapping[str, Any]) -> str:
        parts = [str(record.get("text") or "")]
        subject = str(record.get("subject") or "").strip()
        if subject:
            parts.append(f"subject: {subject}")
        tags = _json_list(record.get("tags"))
        if tags:
            parts.append("tags: " + " ".join(tags))
        metadata = _json_object(record.get("metadata"))
        for key in ("facts", "keyphrases", "timestamped_events", "aliases"):
            values = metadata.get(key)
            if isinstance(values, str):
                values = [values]
            if isinstance(values, (list, tuple)):
                clean = [str(value).strip() for value in values if str(value).strip()]
                if clean:
                    parts.append(f"{key}: " + " ".join(clean))
        return "\n".join(parts)

    def _write_embedding(self, memory_id: str, index_text: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO emb(id, vector, dim) VALUES (?,?,?)",
            (
                memory_id,
                sqlite3.Binary(self._embed(index_text, purpose="document")),
                self.dim,
            ),
        )

    @property
    def _embedding_projection_version(self) -> str:
        return (
            f"{EMBEDDING_PROJECTION_VERSION}:"
            f"{self.embedding_provider.identity}:{self.dim}"
        )

    def _ensure_embeddings(self) -> None:
        version_row = self._conn.execute(
            "SELECT value FROM memory_projection_meta WHERE key = 'embedding_version'"
        ).fetchone()
        rebuild_all = (
            version_row is None
            or version_row["value"] != self._embedding_projection_version
        )
        if rebuild_all:
            self._conn.execute("DELETE FROM emb")
            rows = self._conn.execute(
                "SELECT * FROM mem WHERE deleted_at IS NULL"
            ).fetchall()
        else:
            rows = self._conn.execute(
                """
                SELECT mem.*
                FROM mem
                LEFT JOIN emb ON mem.id = emb.id
                WHERE mem.deleted_at IS NULL
                  AND (emb.id IS NULL OR emb.dim != ?)
                """,
                (self.dim,),
            ).fetchall()
        for row in rows:
            record = self._row_to_record(row)
            self._write_embedding(record["id"], self._index_text(record))
        self._conn.execute(
            "INSERT OR REPLACE INTO memory_projection_meta(key, value) VALUES (?,?)",
            ("embedding_version", self._embedding_projection_version),
        )
        self._conn.commit()

    def _write_lexical(self, record: Mapping[str, Any]) -> None:
        if not self._fts_enabled:
            return
        self._conn.execute("DELETE FROM mem_fts WHERE id = ?", (record["id"],))
        self._conn.execute(
            """
            INSERT INTO mem_fts(
              id, workspace_id, owner_id, agent_id, session_id, content
            ) VALUES (?,?,?,?,?,?)
            """,
            (
                record["id"],
                record["workspace_id"],
                record["owner_id"],
                record["agent_id"],
                record["session_id"],
                self._index_text(record),
            ),
        )

    def _ensure_lexical_projection(self) -> None:
        if not self._fts_enabled:
            return
        version_row = self._conn.execute(
            "SELECT value FROM memory_projection_meta WHERE key = 'lexical_version'"
        ).fetchone()
        if version_row is None or version_row["value"] != LEXICAL_PROJECTION_VERSION:
            with self._conn:
                self._conn.execute("DELETE FROM mem_fts")
                for row in self._conn.execute(
                    "SELECT * FROM mem WHERE deleted_at IS NULL"
                ).fetchall():
                    self._write_lexical(self._row_to_record(row))
                self._conn.execute(
                    "INSERT OR REPLACE INTO memory_projection_meta(key, value) VALUES (?,?)",
                    ("lexical_version", LEXICAL_PROJECTION_VERSION),
                )

    def _lexical_search(
        self,
        query: str,
        limit: int,
        *,
        workspace_id: str,
        owner_id: str,
        agent_id: Optional[str],
        session_id: Optional[str],
    ) -> List[Tuple[str, float]]:
        if not self._fts_enabled:
            return []
        tokens = re.findall(r"[\w'-]+", str(query or ""), flags=re.UNICODE)[:32]
        if not tokens:
            return []
        match_query = " OR ".join(
            f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens
        )
        conditions = [
            "mem_fts MATCH ?",
            "workspace_id = ?",
            "owner_id = ?",
        ]
        params: List[Any] = [match_query, workspace_id, owner_id]
        if agent_id is not None:
            conditions.append("agent_id = ?")
            params.append(agent_id)
        if session_id is not None:
            conditions.append("session_id = ?")
            params.append(session_id)
        params.append(int(limit))
        try:
            rows = self._conn.execute(
                f"""
                SELECT id, bm25(mem_fts) AS lexical_rank
                FROM mem_fts
                WHERE {' AND '.join(conditions)}
                ORDER BY lexical_rank ASC, id ASC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [(str(row["id"]), float(row["lexical_rank"])) for row in rows]

    def _resolve_boundary(
        self,
        workspace_id: Optional[str],
        owner_id: Optional[str],
    ) -> Tuple[str, str]:
        return (
            normalize_scope_id(
                self.default_scope.workspace_id if workspace_id is None else workspace_id,
                "workspace_id",
                required=True,
            ),
            normalize_scope_id(
                self.default_scope.owner_id if owner_id is None else owner_id,
                "owner_id",
                required=True,
            ),
        )

    def _resolve_write_scope(
        self,
        workspace_id: Optional[str],
        owner_id: Optional[str],
        agent_id: Optional[str],
        session_id: Optional[str],
    ) -> MemoryScope:
        boundary = self._resolve_boundary(workspace_id, owner_id)
        selected_agent = self.default_scope.agent_id if agent_id is None else agent_id
        return MemoryScope.create(
            boundary[0],
            boundary[1],
            selected_agent,
            session_id,
        )

    def _validate_relation_ids(
        self,
        memory_ids: Sequence[str],
        scope: MemoryScope,
    ) -> None:
        for memory_id in memory_ids:
            row = self._conn.execute(
                """
                SELECT id, workspace_id, owner_id, deleted_at
                FROM mem
                WHERE id = ?
                """,
                (memory_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"unknown related memory: {memory_id}")
            if row["deleted_at"]:
                raise ValueError(f"related memory is deleted: {memory_id}")
            if (
                row["workspace_id"] != scope.workspace_id
                or row["owner_id"] != scope.owner_id
            ):
                raise ValueError("related memories must remain inside one owner scope")

    def log_episode(
        self,
        role: str,
        content: str,
        meta: Optional[dict] = None,
        *,
        workspace_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> int:
        scope = self._resolve_write_scope(
            workspace_id, owner_id, agent_id, session_id
        )
        timestamp = _utc_now()
        cursor = self._conn.execute(
            """
            INSERT INTO episodes(
              workspace_id, owner_id, agent_id, session_id,
              ts, role, content, meta
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                scope.workspace_id,
                scope.owner_id,
                scope.agent_id,
                scope.session_id,
                timestamp,
                role,
                content or "",
                json.dumps(meta or {}, sort_keys=True),
            ),
        )
        self._conn.commit()
        return int(cursor.lastrowid)

    def add_memory(
        self,
        text: str,
        tags: Optional[List[str]] = None,
        ts: Optional[str] = None,
        score: float = 0.0,
        *,
        workspace_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
        subject: str = "",
        memory_type: str = "episodic",
        source: str = "user",
        source_locator: str = "",
        valid_from: Optional[str] = None,
        valid_to: Optional[str] = None,
        confidence: float = 1.0,
        trust_status: str = "unverified",
        consent_scope: str = "local",
        visibility: str = "private",
        episode_id: str = "",
        lineage: Optional[Sequence[str]] = None,
        supersedes: Optional[Sequence[str]] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        rebuild_graph: bool = True,
    ) -> str:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("empty memory text")
        if trust_status not in TRUST_STATES:
            raise ValueError(f"invalid trust status: {trust_status}")
        if visibility not in VISIBILITY_STATES:
            raise ValueError(f"invalid visibility: {visibility}")
        if not consent_scope.strip():
            raise ValueError("consent scope is required")
        if not 0.0 <= float(confidence) <= 1.0:
            raise ValueError("confidence must be between 0 and 1")

        scope = self._resolve_write_scope(
            workspace_id, owner_id, agent_id, session_id
        )
        clean_tags = _json_list(tags or [])
        lineage_ids = _json_list(lineage or [])
        superseded_ids = _json_list(supersedes or [])
        self._validate_relation_ids(lineage_ids + superseded_ids, scope)
        for old_id in superseded_ids:
            old = self._conn.execute(
                "SELECT superseded_by FROM mem WHERE id = ?", (old_id,)
            ).fetchone()
            if old and old["superseded_by"]:
                raise ValueError(f"memory is already superseded: {old_id}")

        memory_id = f"mem_{uuid.uuid4().hex[:12]}"
        timestamp = ts or _utc_now()
        metadata_obj = dict(metadata or {})
        record = {
            "id": memory_id,
            "workspace_id": scope.workspace_id,
            "owner_id": scope.owner_id,
            "agent_id": scope.agent_id,
            "session_id": scope.session_id,
            "text": text.strip(),
            "tags": clean_tags,
            "created_at": timestamp,
            "score": float(score),
            "subject": str(subject or "").strip(),
            "memory_type": str(memory_type or "episodic").strip(),
            "source": str(source or "user").strip(),
            "source_locator": str(source_locator or "").strip(),
            "valid_from": valid_from,
            "valid_to": valid_to,
            "confidence": float(confidence),
            "trust_status": trust_status,
            "consent_scope": consent_scope.strip(),
            "visibility": visibility,
            "episode_id": str(episode_id or "").strip(),
            "lineage": lineage_ids,
            "supersedes": superseded_ids,
            "superseded_by": "",
            "deleted_at": None,
            "metadata": metadata_obj,
        }

        with self._conn:
            self._conn.execute(
                """
                INSERT INTO mem(
                  id, workspace_id, owner_id, agent_id, session_id,
                  text, tags, ts, score, subject, memory_type, source,
                  source_locator, valid_from, valid_to, confidence, trust_status,
                  consent_scope, visibility, episode_id, lineage, supersedes,
                  superseded_by, deleted_at, metadata
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record["id"],
                    record["workspace_id"],
                    record["owner_id"],
                    record["agent_id"],
                    record["session_id"],
                    record["text"],
                    ",".join(record["tags"]),
                    record["created_at"],
                    record["score"],
                    record["subject"],
                    record["memory_type"],
                    record["source"],
                    record["source_locator"],
                    record["valid_from"],
                    record["valid_to"],
                    record["confidence"],
                    record["trust_status"],
                    record["consent_scope"],
                    record["visibility"],
                    record["episode_id"],
                    json.dumps(record["lineage"], sort_keys=True),
                    json.dumps(record["supersedes"], sort_keys=True),
                    record["superseded_by"],
                    record["deleted_at"],
                    json.dumps(record["metadata"], sort_keys=True),
                ),
            )
            self._write_embedding(memory_id, self._index_text(record))
            self._write_lexical(record)
            for old_id in superseded_ids:
                self._conn.execute(
                    """
                    UPDATE mem
                    SET superseded_by = ?,
                        valid_to = CASE
                          WHEN valid_to IS NULL OR valid_to = '' THEN ?
                          ELSE valid_to
                        END
                    WHERE id = ? AND workspace_id = ? AND owner_id = ?
                    """,
                    (
                        memory_id,
                        timestamp,
                        old_id,
                        scope.workspace_id,
                        scope.owner_id,
                    ),
                )
        if rebuild_graph:
            self.rebuild_graph_projection()
        return memory_id

    def propose_memory(
        self,
        text: str,
        tags: Optional[List[str]] = None,
        *,
        workspace_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
        subject: str = "",
        memory_type: str = "episodic",
        source: str = "user",
        source_locator: str = "",
        valid_from: Optional[str] = None,
        valid_to: Optional[str] = None,
        confidence: float = 1.0,
        trust_status: str = "unverified",
        consent_scope: str = "local",
        visibility: str = "private",
        episode_id: str = "",
        lineage: Optional[Sequence[str]] = None,
        supersedes: Optional[Sequence[str]] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> str:
        """Store a reviewable candidate without changing canonical memory."""

        if not isinstance(text, str) or not text.strip():
            raise ValueError("empty memory proposal text")
        if trust_status not in TRUST_STATES:
            raise ValueError(f"invalid trust status: {trust_status}")
        if visibility not in VISIBILITY_STATES:
            raise ValueError(f"invalid visibility: {visibility}")
        if not isinstance(consent_scope, str) or not consent_scope.strip():
            raise ValueError("consent scope is required")
        if not 0.0 <= float(confidence) <= 1.0:
            raise ValueError("confidence must be between 0 and 1")

        scope = self._resolve_write_scope(
            workspace_id, owner_id, agent_id, session_id
        )
        clean_tags = _json_list(tags or [])
        lineage_ids = _json_list(lineage or [])
        superseded_ids = _json_list(supersedes or [])
        self._validate_relation_ids(lineage_ids + superseded_ids, scope)
        proposal_id = f"proposal_{uuid.uuid4().hex[:12]}"
        proposed_at = _utc_now()
        self._conn.execute(
            """
            INSERT INTO memory_proposals(
              id, workspace_id, owner_id, agent_id, session_id,
              text, tags, proposed_at, subject, memory_type, source,
              source_locator, valid_from, valid_to, confidence, trust_status,
              consent_scope, visibility, episode_id, lineage, supersedes, metadata
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                proposal_id,
                scope.workspace_id,
                scope.owner_id,
                scope.agent_id,
                scope.session_id,
                text.strip(),
                ",".join(clean_tags),
                proposed_at,
                str(subject or "").strip(),
                str(memory_type or "episodic").strip(),
                str(source or "user").strip(),
                str(source_locator or "").strip(),
                valid_from,
                valid_to,
                float(confidence),
                trust_status,
                consent_scope.strip(),
                visibility,
                str(episode_id or "").strip(),
                json.dumps(lineage_ids, sort_keys=True),
                json.dumps(superseded_ids, sort_keys=True),
                json.dumps(dict(metadata or {}), sort_keys=True),
            ),
        )
        self._conn.commit()
        return proposal_id

    def get_memory_proposal(
        self,
        proposal_id: str,
        *,
        workspace_id: Optional[str] = None,
        owner_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        boundary = self._resolve_boundary(workspace_id, owner_id)
        row = self._conn.execute(
            """
            SELECT * FROM memory_proposals
            WHERE id = ? AND workspace_id = ? AND owner_id = ?
            """,
            (proposal_id, boundary[0], boundary[1]),
        ).fetchone()
        return self._row_to_proposal(row) if row else None

    def list_memory_proposals(
        self,
        *,
        status: Optional[str] = "pending",
        workspace_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        if status is not None and status not in PROPOSAL_STATES:
            raise ValueError(f"invalid proposal status: {status}")
        boundary = self._resolve_boundary(workspace_id, owner_id)
        conditions = ["workspace_id = ?", "owner_id = ?"]
        params: List[Any] = [boundary[0], boundary[1]]
        if status is not None:
            conditions.append("status = ?")
            params.append(status)
        if agent_id is not None:
            conditions.append("agent_id = ?")
            params.append(normalize_scope_id(agent_id, "agent_id", required=False))
        if session_id is not None:
            conditions.append("session_id = ?")
            params.append(normalize_scope_id(session_id, "session_id", required=False))
        params.append(max(1, min(int(limit), 1000)))
        rows = self._conn.execute(
            f"""
            SELECT * FROM memory_proposals
            WHERE {' AND '.join(conditions)}
            ORDER BY proposed_at DESC, id DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()
        return [self._row_to_proposal(row) for row in rows]

    def approve_memory_proposal(
        self,
        proposal_id: str,
        *,
        workspace_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        decision_reason: str = "",
    ) -> str:
        proposal = self.get_memory_proposal(
            proposal_id,
            workspace_id=workspace_id,
            owner_id=owner_id,
        )
        if proposal is None:
            raise ValueError(f"unknown memory proposal: {proposal_id}")
        if proposal["status"] != "pending":
            raise ValueError(f"memory proposal is already {proposal['status']}")

        canonical_id = self.add_memory(
            proposal["text"],
            tags=proposal["tags"],
            workspace_id=proposal["workspace_id"],
            owner_id=proposal["owner_id"],
            agent_id=proposal["agent_id"],
            session_id=proposal["session_id"],
            subject=proposal["subject"],
            memory_type=proposal["memory_type"],
            source=proposal["source"],
            source_locator=proposal["source_locator"],
            valid_from=proposal["valid_from"],
            valid_to=proposal["valid_to"],
            confidence=proposal["confidence"],
            trust_status=proposal["trust_status"],
            consent_scope=proposal["consent_scope"],
            visibility=proposal["visibility"],
            episode_id=proposal["episode_id"],
            lineage=proposal["lineage"],
            supersedes=proposal["supersedes"],
            metadata=proposal["metadata"],
        )
        with self._conn:
            cursor = self._conn.execute(
                """
                UPDATE memory_proposals
                SET status = 'approved', decided_at = ?, decision_reason = ?,
                    canonical_id = ?
                WHERE id = ? AND status = 'pending'
                  AND workspace_id = ? AND owner_id = ?
                """,
                (
                    _utc_now(),
                    str(decision_reason or "").strip(),
                    canonical_id,
                    proposal_id,
                    proposal["workspace_id"],
                    proposal["owner_id"],
                ),
            )
        if cursor.rowcount != 1:
            raise RuntimeError("memory proposal decision changed during approval")
        return canonical_id

    def reject_memory_proposal(
        self,
        proposal_id: str,
        *,
        workspace_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        decision_reason: str = "",
    ) -> bool:
        boundary = self._resolve_boundary(workspace_id, owner_id)
        with self._conn:
            cursor = self._conn.execute(
                """
                UPDATE memory_proposals
                SET status = 'rejected', decided_at = ?, decision_reason = ?
                WHERE id = ? AND status = 'pending'
                  AND workspace_id = ? AND owner_id = ?
                """,
                (
                    _utc_now(),
                    str(decision_reason or "").strip(),
                    proposal_id,
                    boundary[0],
                    boundary[1],
                ),
            )
        return cursor.rowcount == 1

    def supersede_memory(
        self,
        memory_id: str,
        text: str,
        *,
        workspace_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        **overrides: Any,
    ) -> str:
        boundary = self._resolve_boundary(workspace_id, owner_id)
        current = self.get_memory(
            memory_id,
            include_deleted=False,
            workspace_id=boundary[0],
            owner_id=boundary[1],
        )
        if current is None:
            raise ValueError(f"unknown memory: {memory_id}")
        values = {
            "workspace_id": current["workspace_id"],
            "owner_id": current["owner_id"],
            "agent_id": current["agent_id"],
            "session_id": current["session_id"],
            "tags": current["tags"],
            "subject": current["subject"],
            "memory_type": current["memory_type"],
            "source": current["source"],
            "source_locator": current["source_locator"],
            "confidence": current["confidence"],
            "trust_status": current["trust_status"],
            "consent_scope": current["consent_scope"],
            "visibility": current["visibility"],
            "episode_id": current["episode_id"],
            "lineage": current["lineage"],
            "metadata": current["metadata"],
            "supersedes": [memory_id],
        }
        values.update(overrides)
        if (
            values["workspace_id"] != current["workspace_id"]
            or values["owner_id"] != current["owner_id"]
        ):
            raise ValueError("a superseding memory cannot change owner scope")
        return self.add_memory(text, **values)

    def _scrub_retrieval_memory_reference(
        self,
        memory_id: str,
        workspace_id: str,
        owner_id: str,
    ) -> None:
        """Remove a deleted canonical ID from retained retrieval evidence."""

        run_rows = self._conn.execute(
            """
            SELECT id, result_ids, result_snapshot
            FROM retrieval_runs
            WHERE workspace_id = ? AND owner_id = ?
            """,
            (workspace_id, owner_id),
        ).fetchall()
        for row in run_rows:
            result_ids = _json_list(row["result_ids"])
            try:
                snapshot_value = json.loads(row["result_snapshot"] or "[]")
            except (TypeError, ValueError):
                snapshot_value = []
            snapshot = snapshot_value if isinstance(snapshot_value, list) else []
            if memory_id not in result_ids and not any(
                isinstance(item, Mapping) and item.get("memory_id") == memory_id
                for item in snapshot
            ):
                continue
            kept_ids = [item for item in result_ids if item != memory_id]
            kept_snapshot = [
                item
                for item in snapshot
                if not isinstance(item, Mapping) or item.get("memory_id") != memory_id
            ]
            for rank, item in enumerate(kept_snapshot, start=1):
                if isinstance(item, dict):
                    item["rank"] = rank
            self._conn.execute(
                """
                UPDATE retrieval_runs
                SET result_ids = ?, result_snapshot = ?
                WHERE id = ?
                """,
                (
                    json.dumps(kept_ids),
                    json.dumps(kept_snapshot, sort_keys=True),
                    row["id"],
                ),
            )
        self._conn.execute(
            """
            DELETE FROM retrieval_labels
            WHERE memory_id = ?
              AND run_id IN (
                SELECT id FROM retrieval_runs
                WHERE workspace_id = ? AND owner_id = ?
              )
            """,
            (memory_id, workspace_id, owner_id),
        )

    def delete_memory(
        self,
        memory_id: str,
        deleted_at: Optional[str] = None,
        *,
        workspace_id: Optional[str] = None,
        owner_id: Optional[str] = None,
    ) -> bool:
        boundary = self._resolve_boundary(workspace_id, owner_id)
        timestamp = deleted_at or _utc_now()
        with self._conn:
            cursor = self._conn.execute(
                """
                UPDATE mem
                SET deleted_at = ?
                WHERE id = ? AND workspace_id = ? AND owner_id = ?
                  AND deleted_at IS NULL
                """,
                (timestamp, memory_id, boundary[0], boundary[1]),
            )
            if cursor.rowcount > 0:
                self._conn.execute("DELETE FROM emb WHERE id = ?", (memory_id,))
                if self._fts_enabled:
                    self._conn.execute("DELETE FROM mem_fts WHERE id = ?", (memory_id,))
                self._conn.execute("DELETE FROM hot WHERE id = ?", (memory_id,))
                self._scrub_retrieval_memory_reference(
                    memory_id,
                    boundary[0],
                    boundary[1],
                )
        self.rebuild_graph_projection()
        return cursor.rowcount > 0

    def get_memory(
        self,
        memory_id: str,
        *,
        include_deleted: bool = False,
        workspace_id: Optional[str] = None,
        owner_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        boundary = self._resolve_boundary(workspace_id, owner_id)
        query = "SELECT * FROM mem WHERE id = ? AND workspace_id = ? AND owner_id = ?"
        params: Tuple[Any, ...] = (memory_id, boundary[0], boundary[1])
        if not include_deleted:
            query += " AND deleted_at IS NULL"
        row = self._conn.execute(query, params).fetchone()
        return self._row_to_record(row) if row else None

    def list_memories(
        self,
        *,
        include_deleted: bool = False,
        workspace_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        boundary = self._resolve_boundary(workspace_id, owner_id)
        query = "SELECT * FROM mem WHERE workspace_id = ? AND owner_id = ?"
        params: List[Any] = [boundary[0], boundary[1]]
        if not include_deleted:
            query += " AND deleted_at IS NULL"
        if agent_id is not None:
            query += " AND agent_id = ?"
            params.append(normalize_scope_id(agent_id, "agent_id", required=False))
        if session_id is not None:
            query += " AND session_id = ?"
            params.append(normalize_scope_id(session_id, "session_id", required=False))
        query += " ORDER BY ts ASC, id ASC"
        return [
            self._row_to_record(row)
            for row in self._conn.execute(query, tuple(params)).fetchall()
        ]

    def list_eligible_memories(
        self,
        *,
        workspace_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
        as_of: Optional[str] = None,
        time_start: Optional[str] = None,
        time_end: Optional[str] = None,
        consent_scope: Optional[str] = None,
        visibility: Optional[str] = None,
        include_superseded: bool = False,
    ) -> List[Dict[str, Any]]:
        """Return governed canonical records for offline, scoped evaluation."""

        records = self.list_memories(
            workspace_id=workspace_id,
            owner_id=owner_id,
            agent_id=agent_id,
            session_id=session_id,
        )
        return [
            record
            for record in records
            if self._eligible(
                record,
                as_of=as_of,
                time_start=time_start,
                time_end=time_end,
                consent_scope=consent_scope,
                visibility=visibility,
                include_superseded=include_superseded,
            )
        ]

    def _all_memories(self, *, include_deleted: bool = False) -> List[Dict[str, Any]]:
        query = "SELECT * FROM mem"
        if not include_deleted:
            query += " WHERE deleted_at IS NULL"
        query += " ORDER BY workspace_id, owner_id, ts ASC, id ASC"
        return [self._row_to_record(row) for row in self._conn.execute(query).fetchall()]

    @staticmethod
    def _concept_id(
        node_type: str,
        value: str,
        workspace_id: str,
        owner_id: str,
    ) -> str:
        normalized = " ".join(value.lower().split())
        scoped_value = f"{workspace_id}\x00{owner_id}\x00{normalized}"
        digest = hashlib.sha256(scoped_value.encode("utf-8")).hexdigest()[:16]
        return f"{node_type}:{digest}"

    def rebuild_graph_projection(self) -> Dict[str, Any]:
        """Rebuild graph tables exclusively from non-deleted canonical rows."""
        records = self._all_memories(include_deleted=False)
        with self._conn:
            self._conn.execute("DELETE FROM memory_graph_edges")
            self._conn.execute("DELETE FROM memory_graph_nodes")

            def put_node(
                node_id: str,
                node_type: str,
                label: str,
                canonical_id: Optional[str] = None,
                attrs: Optional[Mapping[str, Any]] = None,
            ) -> None:
                self._conn.execute(
                    """
                    INSERT OR REPLACE INTO memory_graph_nodes(
                      node_id, node_type, label, canonical_id, attrs
                    ) VALUES (?,?,?,?,?)
                    """,
                    (
                        node_id,
                        node_type,
                        label,
                        canonical_id,
                        json.dumps(dict(attrs or {}), sort_keys=True),
                    ),
                )

            def put_edge(
                src_id: str,
                dst_id: str,
                edge_type: str,
                weight: float,
                evidence: Mapping[str, Any],
            ) -> None:
                self._conn.execute(
                    """
                    INSERT OR REPLACE INTO memory_graph_edges(
                      src_id, dst_id, edge_type, weight, evidence
                    ) VALUES (?,?,?,?,?)
                    """,
                    (
                        src_id,
                        dst_id,
                        edge_type,
                        float(weight),
                        json.dumps(dict(evidence), sort_keys=True),
                    ),
                )

            active_ids = {record["id"] for record in records}
            for record in records:
                memory_id = record["id"]
                memory_node = f"memory:{memory_id}"
                label = record["subject"] or f"{record['memory_type']} memory"
                put_node(
                    memory_node,
                    "memory",
                    label,
                    memory_id,
                    {
                        "workspace_id": record["workspace_id"],
                        "owner_id": record["owner_id"],
                        "agent_id": record["agent_id"],
                        "session_id": record["session_id"],
                        "created_at": record["created_at"],
                        "memory_type": record["memory_type"],
                        "trust_status": record["trust_status"],
                        "valid_from": record["valid_from"],
                        "valid_to": record["valid_to"],
                        "visibility": record["visibility"],
                    },
                )

                def connect_concept(
                    node_type: str,
                    value: str,
                    forward_type: str,
                    reverse_type: str,
                    weight: float,
                    field: str,
                ) -> None:
                    clean = " ".join(str(value).split())
                    if not clean:
                        return
                    concept_node = self._concept_id(
                        node_type,
                        clean,
                        record["workspace_id"],
                        record["owner_id"],
                    )
                    put_node(
                        concept_node,
                        node_type,
                        clean,
                        attrs={
                            "value": clean,
                            "workspace_id": record["workspace_id"],
                            "owner_id": record["owner_id"],
                        },
                    )
                    evidence = {"canonical_id": memory_id, "field": field}
                    put_edge(memory_node, concept_node, forward_type, weight, evidence)
                    put_edge(concept_node, memory_node, reverse_type, weight, evidence)

                connect_concept(
                    "subject", record["subject"], "about", "subject_of", 1.0, "subject"
                )
                for tag in record["tags"]:
                    connect_concept("tag", tag, "tagged_with", "tag_for", 0.9, "tags")
                connect_concept(
                    "episode",
                    record["episode_id"],
                    "in_episode",
                    "episode_contains",
                    1.0,
                    "episode_id",
                )
                for entity in _json_list(record["metadata"].get("entities")):
                    connect_concept(
                        "entity", entity, "mentions", "mentioned_by", 0.95, "metadata.entities"
                    )
                source_value = record["source_locator"]
                if not source_value and record["source"] not in {"user", "assistant", "system"}:
                    source_value = record["source"]
                connect_concept(
                    "source",
                    source_value,
                    "from_source",
                    "source_contains",
                    0.8,
                    "source_locator" if record["source_locator"] else "source",
                )

                for parent_id in record["lineage"]:
                    if parent_id not in active_ids:
                        continue
                    parent_node = f"memory:{parent_id}"
                    evidence = {"canonical_id": memory_id, "field": "lineage"}
                    put_edge(memory_node, parent_node, "derived_from", 1.0, evidence)
                    put_edge(parent_node, memory_node, "source_for", 1.0, evidence)
                for old_id in record["supersedes"]:
                    if old_id not in active_ids:
                        continue
                    old_node = f"memory:{old_id}"
                    evidence = {"canonical_id": memory_id, "field": "supersedes"}
                    put_edge(memory_node, old_node, "supersedes", 1.0, evidence)
                    put_edge(old_node, memory_node, "superseded_by", 1.0, evidence)

            rebuilt_at = _utc_now()
            meta = {
                "version": GRAPH_PROJECTION_VERSION,
                "rebuilt_at": rebuilt_at,
                "canonical_count": str(len(records)),
            }
            for key, value in meta.items():
                self._conn.execute(
                    "INSERT OR REPLACE INTO memory_projection_meta(key, value) VALUES (?,?)",
                    (key, value),
                )

        node_count = self._conn.execute("SELECT COUNT(*) FROM memory_graph_nodes").fetchone()[0]
        edge_count = self._conn.execute("SELECT COUNT(*) FROM memory_graph_edges").fetchone()[0]
        return {
            "version": GRAPH_PROJECTION_VERSION,
            "rebuilt_at": rebuilt_at,
            "canonical_count": len(records),
            "node_count": int(node_count),
            "edge_count": int(edge_count),
        }

    def rebuild_projections(self) -> Dict[str, Any]:
        """Rebuild vector, lexical, and graph projections from canonical memory."""
        records = self._all_memories(include_deleted=False)
        with self._conn:
            self._conn.execute("DELETE FROM emb")
            if self._fts_enabled:
                self._conn.execute("DELETE FROM mem_fts")
            for record in records:
                self._write_embedding(record["id"], self._index_text(record))
                self._write_lexical(record)
            self._conn.execute(
                "INSERT OR REPLACE INTO memory_projection_meta(key, value) VALUES (?,?)",
                ("embedding_version", self._embedding_projection_version),
            )
            if self._fts_enabled:
                self._conn.execute(
                    "INSERT OR REPLACE INTO memory_projection_meta(key, value) VALUES (?,?)",
                    ("lexical_version", LEXICAL_PROJECTION_VERSION),
                )
        graph_status = self.rebuild_graph_projection()
        graph_status.update(
            {
                "embedding_provider": self.embedding_provider.identity,
                "embedding_dimension": self.dim,
                "lexical_enabled": self._fts_enabled,
            }
        )
        return graph_status

    def _eligible(
        self,
        record: Mapping[str, Any],
        *,
        as_of: Optional[str],
        time_start: Optional[str],
        time_end: Optional[str],
        consent_scope: Optional[str],
        visibility: Optional[str],
        include_superseded: bool,
    ) -> bool:
        if record.get("deleted_at"):
            return False
        if record.get("trust_status") == "rejected":
            return False
        if record.get("superseded_by") and not include_superseded:
            return False
        if consent_scope and record.get("consent_scope") != consent_scope:
            return False
        if visibility and record.get("visibility") != visibility:
            return False

        at = _parse_timestamp(as_of) or datetime.datetime.now(datetime.timezone.utc)
        valid_from = _parse_timestamp(record.get("valid_from"))
        valid_to = _parse_timestamp(record.get("valid_to"))
        if valid_from and at < valid_from:
            return False
        if valid_to and at > valid_to:
            return False

        created = _parse_timestamp(record.get("created_at"))
        start = _parse_timestamp(time_start)
        end = _parse_timestamp(time_end)
        if created and start and created < start:
            return False
        if created and end and created > end:
            return False
        return True

    def _graph_affinity(
        self,
        seeds: Sequence[Tuple[str, float]],
        eligible_ids: Iterable[str],
        *,
        max_hops: int,
        max_neighbors: int,
        allowed_edge_types: Sequence[str],
    ) -> Tuple[Dict[str, float], Dict[str, List[Dict[str, str]]]]:
        eligible = set(eligible_ids)
        allowed = set(allowed_edge_types)
        rows = self._conn.execute(
            """
            SELECT src_id, dst_id, edge_type, weight
            FROM memory_graph_edges
            ORDER BY src_id, weight DESC, edge_type, dst_id
            """
        ).fetchall()
        adjacency: Dict[str, List[sqlite3.Row]] = defaultdict(list)
        for row in rows:
            if row["edge_type"] in allowed and len(adjacency[row["src_id"]]) < max_neighbors:
                adjacency[row["src_id"]].append(row)

        affinity: Dict[str, float] = {}
        paths: Dict[str, List[Dict[str, str]]] = {}
        for seed_id, seed_score in seeds:
            start = f"memory:{seed_id}"
            seed_strength = max(0.0, min(1.0, (seed_score + 1.0) / 2.0))
            queue = deque([(start, 0, seed_strength, [])])
            best: Dict[str, float] = {start: seed_strength}
            while queue:
                if len(best) >= MAX_GRAPH_VISITS_PER_SEED:
                    break
                node_id, depth, strength, path = queue.popleft()
                if depth >= max_hops:
                    continue
                for edge in adjacency.get(node_id, []):
                    next_id = edge["dst_id"]
                    next_strength = strength * float(edge["weight"]) * 0.85
                    if next_strength <= best.get(next_id, -1.0):
                        continue
                    if len(best) >= MAX_GRAPH_VISITS_PER_SEED:
                        break
                    next_path = path + [
                        {
                            "from": node_id,
                            "edge": edge["edge_type"],
                            "to": next_id,
                        }
                    ]
                    best[next_id] = next_strength
                    if next_id.startswith("memory:"):
                        candidate_id = next_id[len("memory:") :]
                        if candidate_id != seed_id and candidate_id not in eligible:
                            # Ineligible memories remain visible in audit snapshots but
                            # cannot bridge retrieval into otherwise eligible evidence.
                            continue
                        if candidate_id != seed_id and candidate_id in eligible:
                            if next_strength > affinity.get(candidate_id, -1.0):
                                affinity[candidate_id] = next_strength
                                paths[candidate_id] = next_path
                    queue.append((next_id, depth + 1, next_strength, next_path))
        return affinity, paths

    def search_evidence(
        self,
        query: str,
        top_k: int = 5,
        boost_tags: Optional[List[str]] = None,
        *,
        workspace_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
        use_graph: bool = True,
        max_hops: int = 2,
        max_neighbors: int = 50,
        graph_weight: float = 0.25,
        allowed_edge_types: Optional[Sequence[str]] = None,
        as_of: Optional[str] = None,
        time_start: Optional[str] = None,
        time_end: Optional[str] = None,
        consent_scope: Optional[str] = None,
        visibility: Optional[str] = None,
        include_superseded: bool = False,
    ) -> List[Dict[str, Any]]:
        top_k = max(1, min(int(top_k), 1000))
        max_hops = max(0, min(int(max_hops), 4))
        max_neighbors = max(1, min(int(max_neighbors), 250))
        graph_weight = max(0.0, min(float(graph_weight), 1.0))
        boundary = self._resolve_boundary(workspace_id, owner_id)
        agent_filter = (
            normalize_scope_id(agent_id, "agent_id", required=False)
            if agent_id is not None
            else None
        )
        session_filter = (
            normalize_scope_id(session_id, "session_id", required=False)
            if session_id is not None
            else None
        )
        query_vector = self._unpack(
            self._embed(query or "", purpose="query")
        )
        boost_set = {tag.strip() for tag in (boost_tags or []) if tag.strip()}
        conditions = [
            "mem.deleted_at IS NULL",
            "mem.workspace_id = ?",
            "mem.owner_id = ?",
        ]
        params: List[Any] = [boundary[0], boundary[1]]
        if agent_filter is not None:
            conditions.append("mem.agent_id = ?")
            params.append(agent_filter)
        if session_filter is not None:
            conditions.append("mem.session_id = ?")
            params.append(session_filter)
        rows = self._conn.execute(
            f"""
            SELECT mem.*, emb.vector
            FROM mem
            JOIN emb ON mem.id = emb.id
            WHERE {' AND '.join(conditions)}
            """,
            tuple(params),
        ).fetchall()

        eligible_records: Dict[str, Dict[str, Any]] = {}
        semantic_scores: Dict[str, float] = {}
        for row in rows:
            record = self._row_to_record(row)
            if not self._eligible(
                record,
                as_of=as_of,
                time_start=time_start,
                time_end=time_end,
                consent_scope=consent_scope,
                visibility=visibility,
                include_superseded=include_superseded,
            ):
                continue
            vector = self._unpack(row["vector"])
            semantic_score = sum(left * right for left, right in zip(query_vector, vector))
            eligible_records[record["id"]] = record
            semantic_scores[record["id"]] = float(semantic_score)

        if not eligible_records:
            return []

        candidate_limit = min(1000, max(60, top_k * 8))
        semantic_order = sorted(
            eligible_records,
            key=lambda memory_id: (
                semantic_scores[memory_id],
                eligible_records[memory_id]["created_at"],
                memory_id,
            ),
            reverse=True,
        )[:candidate_limit]
        semantic_ranks = {
            memory_id: index
            for index, memory_id in enumerate(semantic_order, start=1)
        }

        lexical_rows = self._lexical_search(
            query,
            candidate_limit,
            workspace_id=boundary[0],
            owner_id=boundary[1],
            agent_id=agent_filter,
            session_id=session_filter,
        )
        lexical_rows = [
            item for item in lexical_rows if item[0] in eligible_records
        ]
        lexical_ranks = {
            memory_id: index
            for index, (memory_id, _) in enumerate(lexical_rows, start=1)
        }
        lexical_bm25 = dict(lexical_rows)
        candidate_ids = set(semantic_ranks).union(lexical_ranks)

        active_signal_count = 1 + int(bool(lexical_ranks))
        maximum_rrf = active_signal_count / float(RRF_K + 1)

        def score_record(memory_id: str) -> Dict[str, Any]:
            record = dict(eligible_records[memory_id])
            semantic_rank = semantic_ranks.get(memory_id)
            lexical_rank = lexical_ranks.get(memory_id)
            semantic_rrf = (
                1.0 / (RRF_K + semantic_rank) if semantic_rank is not None else 0.0
            )
            lexical_rrf = (
                1.0 / (RRF_K + lexical_rank) if lexical_rank is not None else 0.0
            )
            fusion_score = (semantic_rrf + lexical_rrf) / maximum_rrf
            tag_boost = 0.15 * len(boost_set.intersection(record["tags"]))
            base_score = fusion_score + tag_boost + record["score"]
            record.update(
                {
                    "semantic_score": semantic_scores[memory_id],
                    "semantic_rank": semantic_rank,
                    "lexical_score": (
                        (RRF_K + 1) / float(RRF_K + lexical_rank)
                        if lexical_rank is not None
                        else 0.0
                    ),
                    "lexical_rank": lexical_rank,
                    "lexical_bm25": lexical_bm25.get(memory_id),
                    "fusion_score": float(fusion_score),
                    "tag_boost": float(tag_boost),
                    "base_score": float(base_score),
                    "graph_score": 0.0,
                    "score": float(base_score),
                    "graph_path": [],
                    "retrieval": {
                        "embedding_provider": self.embedding_provider.identity,
                        "lexical_enabled": self._fts_enabled,
                        "rrf_k": RRF_K,
                    },
                }
            )
            return record

        scored = {
            memory_id: score_record(memory_id)
            for memory_id in candidate_ids
        }

        ordered_base = sorted(
            scored.values(), key=lambda item: (item["base_score"], item["created_at"]), reverse=True
        )
        if use_graph and max_hops > 0 and ordered_base:
            seed_count = min(len(ordered_base), max(5, top_k))
            seeds = [
                (
                    item["id"],
                    max(-1.0, min(1.0, (2.0 * item["fusion_score"]) - 1.0)),
                )
                for item in ordered_base[:seed_count]
            ]
            affinity, paths = self._graph_affinity(
                seeds,
                eligible_records.keys(),
                max_hops=max_hops,
                max_neighbors=max_neighbors,
                allowed_edge_types=allowed_edge_types or tuple(DEFAULT_GRAPH_EDGE_TYPES),
            )
            for memory_id, strength in affinity.items():
                if memory_id not in scored:
                    scored[memory_id] = score_record(memory_id)
                contribution = graph_weight * strength
                scored[memory_id]["graph_score"] = float(contribution)
                scored[memory_id]["score"] = float(scored[memory_id]["base_score"] + contribution)
                scored[memory_id]["graph_path"] = paths.get(memory_id, [])

        return sorted(
            scored.values(), key=lambda item: (item["score"], item["created_at"]), reverse=True
        )[:top_k]

    def record_retrieval_run(
        self,
        query: str,
        results: Sequence[Mapping[str, Any]],
        *,
        workspace_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
        query_storage: str = "hash-only",
        top_k: Optional[int] = None,
        retrieval_config: Optional[Mapping[str, Any]] = None,
    ) -> str:
        """Persist a scoped, labelable retrieval event without copying memory text.

        Query storage is explicit. ``hash-only`` supports audit and deletion while
        avoiding raw-query retention; ``plaintext`` is required for a reusable
        supervised evaluation dataset.
        """

        query_value = str(query or "")
        if not query_value.strip():
            raise ValueError("retrieval query must not be empty")
        if len(query_value) > 8192:
            raise ValueError("retrieval query must be at most 8192 characters")
        storage_mode = str(query_storage or "").strip().lower()
        if storage_mode not in RETRIEVAL_QUERY_STORAGE_MODES:
            raise ValueError(f"invalid retrieval query storage mode: {query_storage}")

        boundary = self._resolve_boundary(workspace_id, owner_id)
        agent_filter = normalize_scope_id(agent_id, "agent_id", required=False)
        session_filter = normalize_scope_id(session_id, "session_id", required=False)
        config = dict(retrieval_config or {})
        # Fail before writing rather than storing a partially serializable run.
        config_json = json.dumps(config, sort_keys=True)
        result_ids: List[str] = []
        snapshot: List[Dict[str, Any]] = []
        snapshot_fields = (
            "score",
            "semantic_score",
            "semantic_rank",
            "lexical_score",
            "lexical_rank",
            "lexical_bm25",
            "fusion_score",
            "tag_boost",
            "base_score",
            "graph_score",
            "graph_path",
        )
        for result in results:
            memory_id = str(result.get("id") or "").strip()
            if not memory_id or memory_id in result_ids:
                continue
            memory = self.get_memory(
                memory_id,
                workspace_id=boundary[0],
                owner_id=boundary[1],
            )
            if memory is None:
                raise ValueError("retrieval results must remain inside one owner scope")
            if agent_filter and memory["agent_id"] != agent_filter:
                raise ValueError("retrieval results must remain inside the captured agent scope")
            if session_filter and memory["session_id"] != session_filter:
                raise ValueError("retrieval results must remain inside the captured session scope")
            if not self._eligible(
                memory,
                as_of=config.get("as_of"),
                time_start=config.get("time_start"),
                time_end=config.get("time_end"),
                consent_scope=config.get("consent_scope"),
                visibility=config.get("visibility"),
                include_superseded=bool(config.get("include_superseded", False)),
            ):
                raise ValueError("retrieval results must be eligible in the captured policy scope")
            result_ids.append(memory_id)
            item: Dict[str, Any] = {"memory_id": memory_id, "rank": len(result_ids)}
            for field in snapshot_fields:
                if field in result:
                    item[field] = result[field]
            snapshot.append(item)

        requested_top_k = len(result_ids) if top_k is None else int(top_k)
        requested_top_k = max(1, min(requested_top_k, 1000))
        run_id = f"retrieval_{uuid.uuid4().hex[:12]}"
        timestamp = _utc_now()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO retrieval_runs(
                  id, workspace_id, owner_id, agent_id, session_id,
                  query_text, query_hash, query_storage, created_at, top_k,
                  result_ids, result_snapshot, retrieval_config
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    run_id,
                    boundary[0],
                    boundary[1],
                    agent_filter,
                    session_filter,
                    query_value if storage_mode == "plaintext" else "",
                    hashlib.sha256(query_value.encode("utf-8")).hexdigest(),
                    storage_mode,
                    timestamp,
                    requested_top_k,
                    json.dumps(result_ids),
                    json.dumps(snapshot, sort_keys=True),
                    config_json,
                ),
            )
        return run_id

    def get_retrieval_run(
        self,
        run_id: str,
        *,
        workspace_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        include_labels: bool = True,
    ) -> Optional[Dict[str, Any]]:
        boundary = self._resolve_boundary(workspace_id, owner_id)
        row = self._conn.execute(
            """
            SELECT * FROM retrieval_runs
            WHERE id = ? AND workspace_id = ? AND owner_id = ?
            """,
            (run_id, boundary[0], boundary[1]),
        ).fetchone()
        if row is None:
            return None
        run = self._row_to_retrieval_run(row)
        if include_labels:
            label_rows = self._conn.execute(
                """
                SELECT * FROM retrieval_labels
                WHERE run_id = ?
                ORDER BY updated_at ASC, id ASC
                """,
                (run_id,),
            ).fetchall()
            run["labels"] = [self._row_to_retrieval_label(item) for item in label_rows]
        return run

    def label_retrieval_result(
        self,
        run_id: str,
        memory_id: str,
        relevance: int,
        *,
        workspace_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        label_source: str = "human",
        labeler_id: str = "",
        note: str = "",
    ) -> Dict[str, Any]:
        """Create or correct a graded relevance judgment for one canonical ID."""

        boundary = self._resolve_boundary(workspace_id, owner_id)
        run = self.get_retrieval_run(
            run_id,
            workspace_id=boundary[0],
            owner_id=boundary[1],
            include_labels=False,
        )
        if run is None:
            raise ValueError(f"unknown retrieval run: {run_id}")
        try:
            grade = int(relevance)
            exact_grade = float(relevance) == grade
        except (TypeError, ValueError):
            raise ValueError("relevance must be an integer from 0 to 3") from None
        if (
            isinstance(relevance, bool)
            or not exact_grade
            or grade not in RETRIEVAL_RELEVANCE_GRADES
        ):
            raise ValueError("relevance must be an integer from 0 to 3")

        memory_key = str(memory_id or "").strip()
        memory = self.get_memory(
            memory_key,
            workspace_id=boundary[0],
            owner_id=boundary[1],
        )
        if memory is None:
            raise ValueError("retrieval labels must reference an active memory in the run scope")
        if run["agent_id"] and memory["agent_id"] != run["agent_id"]:
            raise ValueError("retrieval labels must remain inside the run's agent scope")
        if run["session_id"] and memory["session_id"] != run["session_id"]:
            raise ValueError("retrieval labels must remain inside the run's session scope")
        config = run["retrieval_config"]
        if not self._eligible(
            memory,
            as_of=config.get("as_of"),
            time_start=config.get("time_start"),
            time_end=config.get("time_end"),
            consent_scope=config.get("consent_scope"),
            visibility=config.get("visibility"),
            include_superseded=bool(config.get("include_superseded", False)),
        ):
            raise ValueError("retrieval labels must reference memory eligible in the run scope")
        if memory_key not in run["result_ids"] and grade < 2:
            raise ValueError("an unreturned memory may only be labeled relevant or essential")

        source = str(label_source or "").strip().lower()
        if source not in RETRIEVAL_LABEL_SOURCES:
            raise ValueError(f"invalid retrieval label source: {label_source}")
        labeler = normalize_scope_id(labeler_id, "labeler_id", required=False)
        note_value = str(note or "").strip()
        if len(note_value) > 2000:
            raise ValueError("retrieval label note must be at most 2000 characters")

        label_id = f"label_{uuid.uuid4().hex[:12]}"
        timestamp = _utc_now()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO retrieval_labels(
                  id, run_id, memory_id, relevance, label_source, labeler_id,
                  note, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(run_id, memory_id, label_source, labeler_id)
                DO UPDATE SET relevance = excluded.relevance,
                              note = excluded.note,
                              updated_at = excluded.updated_at
                """,
                (
                    label_id,
                    run_id,
                    memory_key,
                    grade,
                    source,
                    labeler,
                    note_value,
                    timestamp,
                    timestamp,
                ),
            )
        row = self._conn.execute(
            """
            SELECT * FROM retrieval_labels
            WHERE run_id = ? AND memory_id = ? AND label_source = ? AND labeler_id = ?
            """,
            (run_id, memory_key, source, labeler),
        ).fetchone()
        return self._row_to_retrieval_label(row)

    def list_retrieval_runs(
        self,
        *,
        workspace_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        labeled_only: bool = False,
        include_hash_only: bool = True,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        boundary = self._resolve_boundary(workspace_id, owner_id)
        conditions = ["workspace_id = ?", "owner_id = ?"]
        params: List[Any] = [boundary[0], boundary[1]]
        if labeled_only:
            conditions.append(
                "EXISTS (SELECT 1 FROM retrieval_labels "
                "WHERE retrieval_labels.run_id = retrieval_runs.id)"
            )
        if not include_hash_only:
            conditions.append("query_storage = 'plaintext'")
        params.append(max(1, min(int(limit), 10000)))
        rows = self._conn.execute(
            f"""
            SELECT * FROM retrieval_runs
            WHERE {' AND '.join(conditions)}
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()
        runs: List[Dict[str, Any]] = []
        for row in rows:
            run = self._row_to_retrieval_run(row)
            label_rows = self._conn.execute(
                """
                SELECT * FROM retrieval_labels
                WHERE run_id = ?
                ORDER BY updated_at ASC, id ASC
                """,
                (run["id"],),
            ).fetchall()
            run["labels"] = [self._row_to_retrieval_label(item) for item in label_rows]
            runs.append(run)
        return runs

    def export_retrieval_labels(
        self,
        *,
        workspace_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        include_hash_only: bool = False,
    ) -> Dict[str, Any]:
        """Export only labeled runs; memory text remains in the canonical database."""

        boundary = self._resolve_boundary(workspace_id, owner_id)
        runs = self.list_retrieval_runs(
            workspace_id=boundary[0],
            owner_id=boundary[1],
            labeled_only=True,
            include_hash_only=include_hash_only,
            limit=10000,
        )
        return {
            "schema": "marven.retrieval-labels.v1",
            "exported_at": _utc_now(),
            "scope": {"workspace_id": boundary[0], "owner_id": boundary[1]},
            "run_count": len(runs),
            "runs": runs,
        }

    def delete_retrieval_run(
        self,
        run_id: str,
        *,
        workspace_id: Optional[str] = None,
        owner_id: Optional[str] = None,
    ) -> bool:
        boundary = self._resolve_boundary(workspace_id, owner_id)
        with self._conn:
            cursor = self._conn.execute(
                """
                DELETE FROM retrieval_runs
                WHERE id = ? AND workspace_id = ? AND owner_id = ?
                """,
                (run_id, boundary[0], boundary[1]),
            )
        return cursor.rowcount == 1

    def search(
        self,
        query: str,
        top_k: int = 5,
        boost_tags: Optional[List[str]] = None,
        **options: Any,
    ) -> List[Tuple[str, str, List[str], float]]:
        results = self.search_evidence(query, top_k, boost_tags, **options)
        return [
            (item["id"], item["text"], item["tags"], float(item["score"]))
            for item in results
        ]

    def graph_snapshot(
        self,
        memory_id: Optional[str] = None,
        *,
        workspace_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        max_hops: int = 2,
        limit: int = 200,
    ) -> Dict[str, Any]:
        boundary = self._resolve_boundary(workspace_id, owner_id)
        limit = max(1, min(int(limit), 1000))
        max_hops = max(0, min(int(max_hops), 4))
        selected: Optional[set] = None
        if memory_id:
            scoped_memory = self.get_memory(
                memory_id,
                workspace_id=boundary[0],
                owner_id=boundary[1],
            )
            if scoped_memory is None:
                raise ValueError(f"unknown memory graph node: {memory_id}")
            start = f"memory:{memory_id}"
            exists = self._conn.execute(
                "SELECT 1 FROM memory_graph_nodes WHERE node_id = ?", (start,)
            ).fetchone()
            if exists is None:
                raise ValueError(f"unknown memory graph node: {memory_id}")
            selected = {start}
            frontier = {start}
            for _ in range(max_hops):
                if not frontier or len(selected) >= limit:
                    break
                placeholders = ",".join("?" for _ in frontier)
                edge_rows = self._conn.execute(
                    f"""
                    SELECT src_id, dst_id FROM memory_graph_edges
                    WHERE src_id IN ({placeholders}) OR dst_id IN ({placeholders})
                    """,
                    tuple(frontier) + tuple(frontier),
                ).fetchall()
                next_frontier = set()
                for edge in edge_rows:
                    next_frontier.add(edge["src_id"])
                    next_frontier.add(edge["dst_id"])
                next_frontier -= selected
                remaining = max(0, limit - len(selected))
                frontier = set(sorted(next_frontier)[:remaining])
                selected.update(frontier)

        if selected is None:
            all_node_rows = self._conn.execute(
                "SELECT * FROM memory_graph_nodes ORDER BY node_type, node_id"
            ).fetchall()
            node_rows = [
                row
                for row in all_node_rows
                if _json_object(row["attrs"]).get("workspace_id") == boundary[0]
                and _json_object(row["attrs"]).get("owner_id") == boundary[1]
            ][:limit]
            selected = {row["node_id"] for row in node_rows}
        else:
            placeholders = ",".join("?" for _ in selected)
            node_rows = self._conn.execute(
                f"SELECT * FROM memory_graph_nodes WHERE node_id IN ({placeholders}) ORDER BY node_type, node_id",
                tuple(selected),
            ).fetchall()

        if selected:
            placeholders = ",".join("?" for _ in selected)
            edge_rows = self._conn.execute(
                f"""
                SELECT * FROM memory_graph_edges
                WHERE src_id IN ({placeholders}) AND dst_id IN ({placeholders})
                ORDER BY src_id, edge_type, dst_id
                """,
                tuple(selected) + tuple(selected),
            ).fetchall()
        else:
            edge_rows = []

        meta_rows = self._conn.execute("SELECT key, value FROM memory_projection_meta").fetchall()
        metadata = {row["key"]: row["value"] for row in meta_rows}
        for numeric in ("canonical_count",):
            if numeric in metadata:
                metadata[numeric] = int(metadata[numeric])
        metadata["canonical_count"] = int(
            self._conn.execute(
                """
                SELECT COUNT(*) FROM mem
                WHERE workspace_id = ? AND owner_id = ? AND deleted_at IS NULL
                """,
                boundary,
            ).fetchone()[0]
        )
        return {
            "projection": metadata,
            "scope": {"workspace_id": boundary[0], "owner_id": boundary[1]},
            "nodes": [
                {
                    "id": row["node_id"],
                    "type": row["node_type"],
                    "label": row["label"],
                    "canonical_id": row["canonical_id"],
                    "attrs": _json_object(row["attrs"]),
                }
                for row in node_rows
            ],
            "edges": [
                {
                    "source": row["src_id"],
                    "target": row["dst_id"],
                    "type": row["edge_type"],
                    "weight": float(row["weight"]),
                    "evidence": _json_object(row["evidence"]),
                }
                for row in edge_rows
            ],
        }

    def record_hit(
        self,
        mem_id: str,
        *,
        workspace_id: Optional[str] = None,
        owner_id: Optional[str] = None,
    ) -> None:
        boundary = self._resolve_boundary(workspace_id, owner_id)
        if self.get_memory(
            mem_id,
            workspace_id=boundary[0],
            owner_id=boundary[1],
        ) is None:
            raise ValueError(f"unknown memory in owner scope: {mem_id}")
        now = _utc_now()
        self._conn.execute(
            """
            INSERT INTO hot(id, ts, hits) VALUES (?,?,1)
            ON CONFLICT(id) DO UPDATE SET hits = hits + 1, ts = excluded.ts
            """,
            (mem_id, now),
        )
        self._conn.commit()
        self._trim_hot(50)

    def _trim_hot(self, max_items: int) -> None:
        rows = self._conn.execute(
            "SELECT id FROM hot ORDER BY hits DESC, ts DESC"
        ).fetchall()
        for row in rows[max_items:]:
            self._conn.execute("DELETE FROM hot WHERE id = ?", (row["id"],))
        self._conn.commit()

    def list_hot(
        self,
        limit: int = 50,
        *,
        workspace_id: Optional[str] = None,
        owner_id: Optional[str] = None,
    ) -> List[dict]:
        boundary = self._resolve_boundary(workspace_id, owner_id)
        rows = self._conn.execute(
            """
            SELECT h.id, h.ts, h.hits, m.text, m.tags
            FROM hot h
            LEFT JOIN mem m ON h.id = m.id
            WHERE m.deleted_at IS NULL
              AND m.workspace_id = ?
              AND m.owner_id = ?
            ORDER BY h.hits DESC, h.ts DESC
            LIMIT ?
            """,
            (boundary[0], boundary[1], int(limit)),
        ).fetchall()
        return [
            {
                "id": row["id"],
                "ts": row["ts"],
                "hits": row["hits"],
                "text": row["text"] or "",
                "tags": [tag for tag in (row["tags"] or "").split(",") if tag],
            }
            for row in rows
        ]

    def get_cached(self, prompt: str, ttl_sec: int = 600) -> Optional[str]:
        key = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        entry = self._cache.get(key)
        if not entry:
            return None
        try:
            if datetime.datetime.now().timestamp() - float(entry.get("t", 0)) <= ttl_sec:
                return entry.get("out")
        except (TypeError, ValueError):
            return None
        return None

    def set_cached(self, prompt: str, output: str) -> None:
        key = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        self._cache[key] = {"t": datetime.datetime.now().timestamp(), "out": output}
        try:
            self.cache_path.write_text(json.dumps(self._cache), encoding="utf-8")
        except OSError:
            pass

    def compress_old(self) -> str:
        return "ok"
