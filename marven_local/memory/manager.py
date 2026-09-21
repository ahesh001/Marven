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
from pathlib import Path
import re
import sqlite3
import struct
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
import uuid


GRAPH_PROJECTION_VERSION = "1"
EMBEDDING_PROJECTION_VERSION = "2"
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

    def __init__(self, root: Path):
        self.root = Path(root)
        self.mem_dir = self.root / "memory"
        self.mem_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.mem_dir / "marven_mem.db"
        self.cache_path = self.mem_dir / "prompt_cache.json"
        self.dim = 256
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        self._ensure_schema()
        self._ensure_embeddings()
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
            """
        )

        # Upgrade databases created by Persistent Memory v1 without replacing
        # or copying their canonical rows.
        existing = {
            row["name"] for row in self._conn.execute("PRAGMA table_info(mem)").fetchall()
        }
        additions = {
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
        for column, declaration in additions.items():
            if column not in existing:
                self._conn.execute(f"ALTER TABLE mem ADD COLUMN {column} {declaration}")

        self._conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_mem_ts ON mem(ts);
            CREATE INDEX IF NOT EXISTS idx_mem_tags ON mem(tags);
            CREATE INDEX IF NOT EXISTS idx_mem_subject ON mem(subject);
            CREATE INDEX IF NOT EXISTS idx_mem_episode ON mem(episode_id);
            CREATE INDEX IF NOT EXISTS idx_mem_superseded ON mem(superseded_by);
            CREATE INDEX IF NOT EXISTS idx_mem_deleted ON mem(deleted_at);
            CREATE INDEX IF NOT EXISTS idx_ep_ts ON episodes(ts);
            CREATE INDEX IF NOT EXISTS idx_graph_edges_src ON memory_graph_edges(src_id);
            CREATE INDEX IF NOT EXISTS idx_graph_edges_dst ON memory_graph_edges(dst_id);
            CREATE INDEX IF NOT EXISTS idx_graph_nodes_canonical ON memory_graph_nodes(canonical_id);
            """
        )
        self._conn.commit()

    def _embed(self, text: str) -> bytes:
        vec = [0.0] * self.dim
        if not text:
            return struct.pack(f"{self.dim}f", *vec)
        for token in re.findall(r"[\w'-]+", text.lower()):
            digest = int(hashlib.sha1(token.encode("utf-8")).hexdigest(), 16)
            index = digest % self.dim
            sign = -1.0 if ((digest >> 8) & 1) else 1.0
            vec[index] += sign
        norm = math.sqrt(sum(value * value for value in vec)) or 1.0
        return struct.pack(f"{self.dim}f", *(value / norm for value in vec))

    def _unpack(self, blob: bytes) -> List[float]:
        return list(struct.unpack(f"{self.dim}f", blob))

    def _row_to_record(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": row["id"],
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
            (memory_id, sqlite3.Binary(self._embed(index_text)), self.dim),
        )

    def _ensure_embeddings(self) -> None:
        version_row = self._conn.execute(
            "SELECT value FROM memory_projection_meta WHERE key = 'embedding_version'"
        ).fetchone()
        rebuild_all = version_row is None or version_row["value"] != EMBEDDING_PROJECTION_VERSION
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
            ("embedding_version", EMBEDDING_PROJECTION_VERSION),
        )
        self._conn.commit()

    def _validate_relation_ids(self, memory_ids: Sequence[str]) -> None:
        for memory_id in memory_ids:
            row = self._conn.execute(
                "SELECT id, deleted_at FROM mem WHERE id = ?", (memory_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"unknown related memory: {memory_id}")
            if row["deleted_at"]:
                raise ValueError(f"related memory is deleted: {memory_id}")

    def log_episode(self, role: str, content: str, meta: Optional[dict] = None) -> int:
        timestamp = _utc_now()
        cursor = self._conn.execute(
            "INSERT INTO episodes(ts, role, content, meta) VALUES (?,?,?,?)",
            (timestamp, role, content or "", json.dumps(meta or {}, sort_keys=True)),
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

        clean_tags = _json_list(tags or [])
        lineage_ids = _json_list(lineage or [])
        superseded_ids = _json_list(supersedes or [])
        self._validate_relation_ids(lineage_ids + superseded_ids)
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
                  id, text, tags, ts, score, subject, memory_type, source,
                  source_locator, valid_from, valid_to, confidence, trust_status,
                  consent_scope, visibility, episode_id, lineage, supersedes,
                  superseded_by, deleted_at, metadata
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record["id"],
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
            for old_id in superseded_ids:
                self._conn.execute(
                    """
                    UPDATE mem
                    SET superseded_by = ?,
                        valid_to = CASE
                          WHEN valid_to IS NULL OR valid_to = '' THEN ?
                          ELSE valid_to
                        END
                    WHERE id = ?
                    """,
                    (memory_id, timestamp, old_id),
                )
        if rebuild_graph:
            self.rebuild_graph_projection()
        return memory_id

    def supersede_memory(self, memory_id: str, text: str, **overrides: Any) -> str:
        current = self.get_memory(memory_id, include_deleted=False)
        if current is None:
            raise ValueError(f"unknown memory: {memory_id}")
        values = {
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
        return self.add_memory(text, **values)

    def delete_memory(self, memory_id: str, deleted_at: Optional[str] = None) -> bool:
        timestamp = deleted_at or _utc_now()
        with self._conn:
            cursor = self._conn.execute(
                "UPDATE mem SET deleted_at = ? WHERE id = ? AND deleted_at IS NULL",
                (timestamp, memory_id),
            )
            self._conn.execute("DELETE FROM emb WHERE id = ?", (memory_id,))
            self._conn.execute("DELETE FROM hot WHERE id = ?", (memory_id,))
        self.rebuild_graph_projection()
        return cursor.rowcount > 0

    def get_memory(self, memory_id: str, *, include_deleted: bool = False) -> Optional[Dict[str, Any]]:
        query = "SELECT * FROM mem WHERE id = ?"
        params: Tuple[Any, ...] = (memory_id,)
        if not include_deleted:
            query += " AND deleted_at IS NULL"
        row = self._conn.execute(query, params).fetchone()
        return self._row_to_record(row) if row else None

    def list_memories(self, *, include_deleted: bool = False) -> List[Dict[str, Any]]:
        query = "SELECT * FROM mem"
        if not include_deleted:
            query += " WHERE deleted_at IS NULL"
        query += " ORDER BY ts ASC, id ASC"
        return [self._row_to_record(row) for row in self._conn.execute(query).fetchall()]

    @staticmethod
    def _concept_id(node_type: str, value: str) -> str:
        normalized = " ".join(value.lower().split())
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
        return f"{node_type}:{digest}"

    def rebuild_graph_projection(self) -> Dict[str, Any]:
        """Rebuild graph tables exclusively from non-deleted canonical rows."""
        records = self.list_memories(include_deleted=False)
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
                    concept_node = self._concept_id(node_type, clean)
                    put_node(concept_node, node_type, clean, attrs={"value": clean})
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
        """Rebuild both vector and graph projections from canonical memory."""
        records = self.list_memories(include_deleted=False)
        with self._conn:
            self._conn.execute("DELETE FROM emb")
            for record in records:
                self._write_embedding(record["id"], self._index_text(record))
            self._conn.execute(
                "INSERT OR REPLACE INTO memory_projection_meta(key, value) VALUES (?,?)",
                ("embedding_version", EMBEDDING_PROJECTION_VERSION),
            )
        return self.rebuild_graph_projection()

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
                node_id, depth, strength, path = queue.popleft()
                if depth >= max_hops:
                    continue
                for edge in adjacency.get(node_id, []):
                    next_id = edge["dst_id"]
                    next_strength = strength * float(edge["weight"]) * 0.85
                    if next_strength <= best.get(next_id, -1.0):
                        continue
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
        top_k = max(1, int(top_k))
        max_hops = max(0, min(int(max_hops), 4))
        max_neighbors = max(1, min(int(max_neighbors), 250))
        graph_weight = max(0.0, min(float(graph_weight), 1.0))
        query_vector = self._unpack(self._embed(query or ""))
        boost_set = {tag.strip() for tag in (boost_tags or []) if tag.strip()}
        rows = self._conn.execute(
            """
            SELECT mem.*, emb.vector
            FROM mem
            JOIN emb ON mem.id = emb.id
            WHERE mem.deleted_at IS NULL
            """
        ).fetchall()

        scored: Dict[str, Dict[str, Any]] = {}
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
            tag_boost = 0.15 * len(boost_set.intersection(record["tags"]))
            base_score = semantic_score + tag_boost + record["score"]
            record.update(
                {
                    "semantic_score": float(semantic_score),
                    "tag_boost": float(tag_boost),
                    "base_score": float(base_score),
                    "graph_score": 0.0,
                    "score": float(base_score),
                    "graph_path": [],
                }
            )
            scored[record["id"]] = record

        ordered_base = sorted(
            scored.values(), key=lambda item: (item["base_score"], item["created_at"]), reverse=True
        )
        if use_graph and max_hops > 0 and ordered_base:
            seed_count = min(len(ordered_base), max(5, top_k))
            seeds = [(item["id"], item["semantic_score"]) for item in ordered_base[:seed_count]]
            affinity, paths = self._graph_affinity(
                seeds,
                scored.keys(),
                max_hops=max_hops,
                max_neighbors=max_neighbors,
                allowed_edge_types=allowed_edge_types or tuple(DEFAULT_GRAPH_EDGE_TYPES),
            )
            for memory_id, strength in affinity.items():
                contribution = graph_weight * strength
                scored[memory_id]["graph_score"] = float(contribution)
                scored[memory_id]["score"] = float(scored[memory_id]["base_score"] + contribution)
                scored[memory_id]["graph_path"] = paths.get(memory_id, [])

        return sorted(
            scored.values(), key=lambda item: (item["score"], item["created_at"]), reverse=True
        )[:top_k]

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
        max_hops: int = 2,
        limit: int = 200,
    ) -> Dict[str, Any]:
        limit = max(1, min(int(limit), 1000))
        max_hops = max(0, min(int(max_hops), 4))
        selected: Optional[set] = None
        if memory_id:
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
            node_rows = self._conn.execute(
                "SELECT * FROM memory_graph_nodes ORDER BY node_type, node_id LIMIT ?", (limit,)
            ).fetchall()
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
        return {
            "projection": metadata,
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

    def record_hit(self, mem_id: str) -> None:
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

    def list_hot(self, limit: int = 50) -> List[dict]:
        rows = self._conn.execute(
            """
            SELECT h.id, h.ts, h.hits, m.text, m.tags
            FROM hot h
            LEFT JOIN mem m ON h.id = m.id
            WHERE m.deleted_at IS NULL
            ORDER BY h.hits DESC, h.ts DESC
            LIMIT ?
            """,
            (int(limit),),
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
