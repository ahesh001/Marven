import sqlite3

import pytest

from marven_local.memory import EmbeddingProvider, MemoryManager


class ZeroEmbeddingProvider(EmbeddingProvider):
    """Makes lexical retrieval observable without a dense-signal advantage."""

    @property
    def identity(self):
        return "test-zero-v1"

    @property
    def dimension(self):
        return 4

    def embed(self, text, *, purpose):
        del text, purpose
        return [0.0, 0.0, 0.0, 0.0]


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
    assert migrated["workspace_id"] == "local"
    assert migrated["owner_id"] == "primary"
    assert migrated["agent_id"] == "marven"
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


def test_owner_scope_isolates_search_get_and_graph_projection(tmp_path):
    manager = MemoryManager(tmp_path)
    alice_id = manager.add_memory(
        "Alice keeps the cobalt release notebook.",
        workspace_id="heshware",
        owner_id="alice",
        subject="release notebook",
    )
    bob_id = manager.add_memory(
        "Bob keeps the amber release notebook.",
        workspace_id="heshware",
        owner_id="bob",
        subject="release notebook",
    )

    alice_results = manager.search_evidence(
        "release notebook",
        workspace_id="heshware",
        owner_id="alice",
        top_k=10,
    )
    alice_snapshot = manager.graph_snapshot(
        workspace_id="heshware",
        owner_id="alice",
    )

    assert [row["id"] for row in alice_results] == [alice_id]
    assert manager.get_memory(
        bob_id,
        workspace_id="heshware",
        owner_id="alice",
    ) is None
    assert alice_snapshot["projection"]["canonical_count"] == 1
    assert f"memory:{alice_id}" in {node["id"] for node in alice_snapshot["nodes"]}
    assert f"memory:{bob_id}" not in {node["id"] for node in alice_snapshot["nodes"]}
    with pytest.raises(ValueError):
        manager.graph_snapshot(
            memory_id=bob_id,
            workspace_id="heshware",
            owner_id="alice",
        )
    manager.close()


def test_cross_owner_lineage_and_supersession_are_rejected(tmp_path):
    manager = MemoryManager(tmp_path)
    bob_id = manager.add_memory(
        "Bob's private preference.",
        workspace_id="heshware",
        owner_id="bob",
    )

    with pytest.raises(ValueError, match="owner scope"):
        manager.add_memory(
            "Alice cannot derive from Bob's memory.",
            workspace_id="heshware",
            owner_id="alice",
            lineage=[bob_id],
        )
    with pytest.raises(ValueError, match="owner scope"):
        manager.add_memory(
            "Alice cannot supersede Bob's memory.",
            workspace_id="heshware",
            owner_id="alice",
            supersedes=[bob_id],
        )
    manager.close()


def test_agent_and_session_filters_narrow_an_owner_scope(tmp_path):
    manager = MemoryManager(tmp_path)
    first_id = manager.add_memory(
        "The first session selected a blue interface.",
        agent_id="design-agent",
        session_id="session-one",
    )
    manager.add_memory(
        "The second session selected a green interface.",
        agent_id="design-agent",
        session_id="session-two",
    )
    manager.add_memory(
        "A research agent evaluated interface accessibility.",
        agent_id="research-agent",
        session_id="session-one",
    )

    results = manager.search_evidence(
        "interface",
        agent_id="design-agent",
        session_id="session-one",
        top_k=10,
    )

    assert [row["id"] for row in results] == [first_id]
    manager.close()


def test_explicit_empty_owner_scope_is_rejected(tmp_path):
    manager = MemoryManager(tmp_path)
    with pytest.raises(ValueError, match="owner_id is required"):
        manager.add_memory("Do not silently fall back scopes.", owner_id="")
    with pytest.raises(ValueError, match="owner_id is required"):
        manager.search_evidence("scope", owner_id="")
    manager.close()


def test_hybrid_retrieval_unions_lexical_candidates_before_graph(tmp_path):
    manager = MemoryManager(tmp_path, embedding_provider=ZeroEmbeddingProvider())
    if not manager._fts_enabled:
        pytest.skip("SQLite was built without FTS5")
    exact_id = manager.add_memory(
        "The incident code is ORCHID-742 and requires a manual review.",
        subject="incident response",
    )
    manager.add_memory(
        "A routine release review completed successfully.",
        subject="release process",
    )

    results = manager.search_evidence("ORCHID-742", top_k=2, use_graph=False)

    assert results[0]["id"] == exact_id
    assert results[0]["semantic_score"] == 0.0
    assert results[0]["lexical_rank"] == 1
    assert results[0]["lexical_score"] > 0
    assert results[0]["retrieval"]["embedding_provider"] == "test-zero-v1"
    manager.close()


def test_lexical_projection_rebuild_and_delete_follow_canonical_state(tmp_path):
    manager = MemoryManager(tmp_path, embedding_provider=ZeroEmbeddingProvider())
    if not manager._fts_enabled:
        pytest.skip("SQLite was built without FTS5")
    memory_id = manager.add_memory("Remember the exact token NEBULA-991.")

    status = manager.rebuild_projections()
    before = manager.search_evidence("NEBULA-991", use_graph=False)
    manager.delete_memory(memory_id)
    after = manager.search_evidence("NEBULA-991", use_graph=False)

    assert status["lexical_enabled"] is True
    assert before[0]["id"] == memory_id
    assert memory_id not in {row["id"] for row in after}
    manager.close()


def test_memory_proposal_requires_approval_before_retrieval(tmp_path):
    manager = MemoryManager(tmp_path)
    proposal_id = manager.propose_memory(
        "Akeem prefers local-first memory processing.",
        workspace_id="heshware",
        owner_id="akeem",
        session_id="session-42",
        subject="memory preference",
        trust_status="confirmed",
        metadata={"entities": ["Akeem", "Marven"]},
    )

    before = manager.search_evidence(
        "local-first memory",
        workspace_id="heshware",
        owner_id="akeem",
    )
    pending = manager.list_memory_proposals(
        workspace_id="heshware",
        owner_id="akeem",
    )
    canonical_id = manager.approve_memory_proposal(
        proposal_id,
        workspace_id="heshware",
        owner_id="akeem",
        decision_reason="User confirmed this preference.",
    )
    after = manager.search_evidence(
        "local-first memory",
        workspace_id="heshware",
        owner_id="akeem",
    )
    decided = manager.get_memory_proposal(
        proposal_id,
        workspace_id="heshware",
        owner_id="akeem",
    )

    assert before == []
    assert [proposal["id"] for proposal in pending] == [proposal_id]
    assert after[0]["id"] == canonical_id
    assert decided["status"] == "approved"
    assert decided["canonical_id"] == canonical_id
    assert manager.list_memory_proposals(
        workspace_id="heshware",
        owner_id="someone-else",
    ) == []
    manager.close()


def test_rejected_memory_proposal_never_reaches_canonical_memory(tmp_path):
    manager = MemoryManager(tmp_path)
    proposal_id = manager.propose_memory("This candidate should be rejected.")

    assert manager.reject_memory_proposal(
        proposal_id,
        decision_reason="Unsupported by the source.",
    )
    assert manager.get_memory_proposal(proposal_id)["status"] == "rejected"
    assert manager.search_evidence("candidate rejected") == []
    with pytest.raises(ValueError, match="already rejected"):
        manager.approve_memory_proposal(proposal_id)
    manager.close()


def test_retrieval_runs_collect_scoped_correctable_labels(tmp_path):
    manager = MemoryManager(tmp_path)
    memory_id = manager.add_memory(
        "Marven keeps canonical memory local by default.",
        workspace_id="heshware",
        owner_id="akeem",
    )
    results = manager.search_evidence(
        "Where does Marven keep memory?",
        workspace_id="heshware",
        owner_id="akeem",
        use_graph=False,
    )
    run_id = manager.record_retrieval_run(
        "Where does Marven keep memory?",
        results,
        workspace_id="heshware",
        owner_id="akeem",
        query_storage="plaintext",
        retrieval_config={"use_graph": False},
    )

    first = manager.label_retrieval_result(
        run_id,
        memory_id,
        3,
        workspace_id="heshware",
        owner_id="akeem",
        labeler_id="akeem",
    )
    corrected = manager.label_retrieval_result(
        run_id,
        memory_id,
        2,
        workspace_id="heshware",
        owner_id="akeem",
        labeler_id="akeem",
        note="Relevant, but not the only supporting record.",
    )
    run = manager.get_retrieval_run(
        run_id,
        workspace_id="heshware",
        owner_id="akeem",
    )
    exported = manager.export_retrieval_labels(
        workspace_id="heshware",
        owner_id="akeem",
    )

    assert first["id"] == corrected["id"]
    assert corrected["relevance"] == 2
    assert run["query"] == "Where does Marven keep memory?"
    assert run["retrieval_config"] == {"use_graph": False}
    assert len(run["labels"]) == 1
    assert "text" not in run["results"][0]
    assert exported["schema"] == "marven.retrieval-labels.v1"
    assert exported["run_count"] == 1
    assert manager.get_retrieval_run(
        run_id,
        workspace_id="heshware",
        owner_id="someone-else",
    ) is None
    with pytest.raises(ValueError, match="unknown retrieval run"):
        manager.label_retrieval_result(
            run_id,
            memory_id,
            2,
            workspace_id="heshware",
            owner_id="someone-else",
        )
    manager.close()


def test_hash_only_retrieval_run_requires_opt_in_for_export(tmp_path):
    manager = MemoryManager(tmp_path)
    manager.add_memory("The release codename is Silver Pine.")
    results = manager.search_evidence("release codename")
    run_id = manager.record_retrieval_run(
        "release codename",
        results,
        query_storage="hash-only",
    )
    manager.label_retrieval_result(run_id, results[0]["id"], 3)

    stored = manager.get_retrieval_run(run_id)
    default_export = manager.export_retrieval_labels()
    audit_export = manager.export_retrieval_labels(include_hash_only=True)

    assert stored["query"] == ""
    assert len(stored["query_hash"]) == 64
    assert default_export["run_count"] == 0
    assert audit_export["run_count"] == 1
    manager.close()


def test_retrieval_labels_can_mark_relevant_evidence_missed_by_top_k(tmp_path):
    manager = MemoryManager(tmp_path)
    first_id = manager.add_memory("The first retrieval candidate.")
    missed_id = manager.add_memory("The essential evidence omitted from a simulated top result.")
    results = manager.search_evidence("retrieval evidence", top_k=2)
    captured_results = [row for row in results if row["id"] == first_id]
    run_id = manager.record_retrieval_run(
        "retrieval evidence",
        captured_results,
        query_storage="plaintext",
        top_k=1,
    )

    missed = manager.label_retrieval_result(run_id, missed_id, 3)

    assert missed["relevance"] == 3
    with pytest.raises(ValueError, match="unreturned memory"):
        manager.label_retrieval_result(run_id, missed_id, 0, labeler_id="second-review")
    manager.close()


def test_deleting_memory_scrubs_retrieval_snapshots_and_labels(tmp_path):
    manager = MemoryManager(tmp_path)
    memory_id = manager.add_memory("Remove this memory and its evaluation reference.")
    results = manager.search_evidence("evaluation reference")
    run_id = manager.record_retrieval_run(
        "evaluation reference",
        results,
        query_storage="plaintext",
    )
    manager.label_retrieval_result(run_id, memory_id, 3)

    assert manager.delete_memory(memory_id)
    run = manager.get_retrieval_run(run_id)

    assert memory_id not in run["result_ids"]
    assert memory_id not in {item["memory_id"] for item in run["results"]}
    assert run["labels"] == []
    manager.close()


def test_retrieval_capture_and_missed_labels_cannot_cross_session_scope(tmp_path):
    manager = MemoryManager(tmp_path)
    allowed_id = manager.add_memory(
        "Evidence in the captured session.",
        session_id="session-a",
    )
    outside_id = manager.add_memory(
        "Evidence in a different session.",
        session_id="session-b",
    )
    allowed_results = manager.search_evidence(
        "captured session",
        session_id="session-a",
        use_graph=False,
    )
    outside_results = manager.search_evidence(
        "different session",
        session_id="session-b",
        use_graph=False,
    )
    run_id = manager.record_retrieval_run(
        "captured session",
        allowed_results,
        session_id="session-a",
        query_storage="plaintext",
    )

    assert allowed_id in manager.get_retrieval_run(run_id)["result_ids"]
    with pytest.raises(ValueError, match="session scope"):
        manager.record_retrieval_run(
            "invalid capture",
            outside_results,
            session_id="session-a",
            query_storage="plaintext",
        )
    with pytest.raises(ValueError, match="session scope"):
        manager.label_retrieval_result(run_id, outside_id, 3)
    with pytest.raises(ValueError, match="must not be empty"):
        manager.record_retrieval_run("   ", [], query_storage="hash-only")
    manager.close()
