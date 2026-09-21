import sqlite3

import pytest

from marven_local.memory import MemoryManager


def test_graph_is_rebuildable_projection_over_canonical_memory(tmp_path):
    manager = MemoryManager(tmp_path)
    first_id = manager.add_memory(
        "Marven uses a canonical memory vault.",
        tags=["Marven", "memory"],
        subject="Marven memory architecture",
        source="conversation",
        source_locator="session-1:turn-2",
        episode_id="session-1",
        metadata={"entities": ["Marven"], "facts": ["canonical memory vault"]},
    )
    second_id = manager.add_memory(
        "The graph is disposable and must never become a truth store.",
        tags=["Marven", "graph"],
        subject="Marven memory architecture",
        source="conversation",
        source_locator="session-2:turn-4",
        episode_id="session-2",
    )

    before = manager.list_memories()
    snapshot = manager.graph_snapshot(memory_id=first_id, max_hops=2)
    rebuilt = manager.rebuild_graph_projection()
    after = manager.list_memories()

    assert before == after
    assert rebuilt["canonical_count"] == 2
    assert {node["canonical_id"] for node in snapshot["nodes"] if node["type"] == "memory"} == {
        first_id,
        second_id,
    }
    assert any(edge["type"] == "about" for edge in snapshot["edges"])
    assert all("text" not in node["attrs"] for node in snapshot["nodes"])
    manager.close()


def test_graph_expands_retrieval_with_an_explainable_path(tmp_path):
    manager = MemoryManager(tmp_path)
    anchor_id = manager.add_memory(
        "A graph projection connects evidence without changing it.",
        tags=["graph-projection"],
        subject="Marven retrieval",
    )
    related_id = manager.add_memory(
        "Canonical records retain provenance and consent metadata.",
        tags=["governance"],
        subject="Marven retrieval",
    )
    manager.add_memory(
        "The weather was warm during the picnic.",
        tags=["weather"],
        subject="Weekend plans",
    )

    results = manager.search_evidence(
        "graph projection evidence",
        top_k=3,
        use_graph=True,
        max_hops=2,
    )
    by_id = {row["id"]: row for row in results}

    assert anchor_id in by_id
    assert related_id in by_id
    assert by_id[related_id]["graph_score"] > 0
    assert [step["edge"] for step in by_id[related_id]["graph_path"]] == [
        "about",
        "subject_of",
    ]
    manager.close()


def test_supersession_preserves_lineage_and_hides_stale_memory(tmp_path):
    manager = MemoryManager(tmp_path)
    old_id = manager.add_memory(
        "The preferred local model is Mistral.",
        subject="preferred local model",
        trust_status="confirmed",
        ts="2026-01-01T00:00:00Z",
    )
    new_id = manager.supersede_memory(
        old_id,
        "The preferred local model is Llama 3.",
        ts="2026-02-01T00:00:00Z",
    )

    current = manager.search_evidence("preferred local model", top_k=5, use_graph=True)
    historical = manager.search_evidence(
        "preferred local model",
        top_k=5,
        use_graph=True,
        as_of="2026-02-01T00:00:00Z",
        include_superseded=True,
    )
    snapshot = manager.graph_snapshot(memory_id=new_id, max_hops=1)

    assert [row["id"] for row in current] == [new_id]
    assert {row["id"] for row in historical} == {old_id, new_id}
    assert manager.get_memory(old_id)["superseded_by"] == new_id
    assert any(
        edge["source"] == f"memory:{new_id}"
        and edge["target"] == f"memory:{old_id}"
        and edge["type"] == "supersedes"
        for edge in snapshot["edges"]
    )
    manager.close()


def test_validity_trust_and_deletion_gate_retrieval(tmp_path):
    manager = MemoryManager(tmp_path)
    current_id = manager.add_memory(
        "The current release gate requires tests.",
        subject="release gate",
        valid_from="2026-01-01T00:00:00Z",
        valid_to="2026-12-31T23:59:59Z",
    )
    manager.add_memory(
        "An expired release gate did not require tests.",
        subject="release gate",
        valid_to="2025-12-31T23:59:59Z",
    )
    rejected_id = manager.add_memory(
        "A poisoned memory says tests are optional.",
        subject="release gate",
        trust_status="rejected",
    )

    results = manager.search_evidence(
        "release gate tests",
        top_k=10,
        as_of="2026-06-01T00:00:00Z",
    )
    assert [row["id"] for row in results] == [current_id]

    assert manager.delete_memory(current_id)
    assert manager.get_memory(current_id) is None
    snapshot = manager.graph_snapshot()
    assert f"memory:{current_id}" not in {node["id"] for node in snapshot["nodes"]}
    assert rejected_id not in {row["id"] for row in manager.search_evidence("tests", top_k=10)}
    manager.close()


def test_legacy_v1_database_is_migrated_in_place(tmp_path):
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    connection = sqlite3.connect(str(memory_dir / "marven_mem.db"))
    connection.execute(
        """
        CREATE TABLE mem (
          id TEXT PRIMARY KEY,
          text TEXT NOT NULL,
          tags TEXT DEFAULT '',
          ts TEXT NOT NULL,
          score REAL DEFAULT 0
        )
        """
    )
    connection.execute(
        "INSERT INTO mem(id, text, tags, ts, score) VALUES (?,?,?,?,?)",
        ("legacy-1", "Legacy memory remains canonical.", "legacy", "2025-01-01T00:00:00Z", 0),
    )
    connection.commit()
    connection.close()

    manager = MemoryManager(tmp_path)
    migrated = manager.get_memory("legacy-1")

    assert migrated["memory_type"] == "episodic"
    assert migrated["consent_scope"] == "local"
    assert manager.search("legacy canonical", top_k=1)[0][0] == "legacy-1"
    assert any(
        node["canonical_id"] == "legacy-1" for node in manager.graph_snapshot()["nodes"]
    )
    manager.close()


@pytest.mark.parametrize("trust_status", ["trusted", "unknown", ""])
def test_invalid_trust_state_is_rejected(tmp_path, trust_status):
    manager = MemoryManager(tmp_path)
    with pytest.raises(ValueError):
        manager.add_memory("Do not accept invalid governance state.", trust_status=trust_status)
    manager.close()
