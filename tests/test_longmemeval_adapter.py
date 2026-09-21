from scripts.evaluate_longmemeval_retrieval import evaluate


def test_longmemeval_adapter_reports_flat_and_graph_metrics():
    dataset = [
        {
            "question_id": "sample_1",
            "question_type": "single-session-user",
            "question": "Which telescope did I buy?",
            "answer": "Orion StarBlast telescope",
            "question_date": "2026/02/01",
            "haystack_session_ids": ["session-filler", "session-answer"],
            "haystack_dates": ["2026/01/01", "2026/01/15"],
            "haystack_sessions": [
                [
                    {"role": "user", "content": "I made pasta tonight."},
                    {"role": "assistant", "content": "That sounds good."},
                ],
                [
                    {"role": "user", "content": "I bought an Orion StarBlast telescope."},
                    {"role": "assistant", "content": "That is a portable reflector."},
                ],
            ],
            "answer_session_ids": ["session-answer"],
        },
        {
            "question_id": "sample_2_abs",
            "question_type": "single-session-user",
            "question": "Which violin do I own?",
            "answer": "I don't know",
            "question_date": "2026/02/01",
            "haystack_session_ids": ["session-filler"],
            "haystack_dates": ["2026/01/01"],
            "haystack_sessions": [
                [{"role": "user", "content": "I made pasta tonight."}]
            ],
            "answer_session_ids": [],
        },
    ]

    report = evaluate(
        dataset,
        granularity="round",
        mode="both",
        cutoffs=[1, 3],
    )

    assert report["skipped_abstention_questions"] == 1
    assert report["results"]["flat"]["evaluated_questions"] == 1
    assert report["results"]["flat"]["metrics"]["1"]["recall_all"] == 1.0
    assert report["results"]["graph"]["metrics"]["1"]["recall_all"] == 1.0
