from app.services.audit import AuditStore


def test_audit_store_persists_run_and_events(tmp_path):
    store = AuditStore(str(tmp_path / "test.sqlite3"))
    store.start_run("run-1", "What is the research question?")
    store.log("run-1", "test_event", {"ok": True})
    store.finish_run("run-1", "completed", "# report")
    run = store.get_run("run-1")
    assert run is not None
    assert run["status"] == "completed"
    assert run["report_markdown"] == "# report"
    assert any(e["event_type"] == "test_event" for e in run["audit_events"])
