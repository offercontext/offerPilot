import json

from fastapi.testclient import TestClient

from offerpilot import diagnostics
from offerpilot.api import create_app
from offerpilot.diagnostics import append_log_entry


def test_get_logs_returns_newest_relative_chronological_pages(tmp_path):
    for index in range(1, 6):
        append_log_entry(tmp_path, "INFO", f"entry-{index}")
    with (tmp_path / "logs" / "offerpilot.log").open("a", encoding="utf-8") as handle:
        handle.write("not-json\n[]\n")
    client = TestClient(create_app(data_dir=tmp_path))

    first_page = client.get("/api/logs?limit=2&offset=0")
    second_page = client.get("/api/logs?limit=2&offset=2")
    final_page = client.get("/api/logs?limit=2&offset=4")
    out_of_range_page = client.get("/api/logs?limit=2&offset=20")

    assert first_page.status_code == 200
    assert first_page.json() == {
        "entries": [
            {"level": "INFO", "message": "entry-4"},
            {"level": "INFO", "message": "entry-5"},
        ],
        "total": 5,
        "limit": 2,
        "offset": 0,
        "has_more": True,
    }
    assert second_page.json()["entries"] == [
        {"level": "INFO", "message": "entry-2"},
        {"level": "INFO", "message": "entry-3"},
    ]
    assert final_page.json() == {
        "entries": [{"level": "INFO", "message": "entry-1"}],
        "total": 5,
        "limit": 2,
        "offset": 4,
        "has_more": False,
    }
    assert out_of_range_page.json() == {
        "entries": [],
        "total": 5,
        "limit": 2,
        "offset": 20,
        "has_more": False,
    }


def test_get_logs_rejects_invalid_pagination_parameters(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))

    for query in ("limit=0", "limit=101", "offset=-1"):
        response = client.get(f"/api/logs?{query}")

        assert response.status_code == 422


def test_get_logs_returns_empty_page_for_huge_out_of_range_offset(tmp_path):
    huge_offset = 9_223_372_036_854_775_807
    append_log_entry(tmp_path, "INFO", "entry-1")
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.get(f"/api/logs?offset={huge_offset}")

    assert response.status_code == 200
    assert response.json() == {
        "entries": [],
        "total": 1,
        "limit": 20,
        "offset": huge_offset,
        "has_more": False,
    }


def test_get_logs_filters_by_level_before_limit(tmp_path):
    append_log_entry(tmp_path, "INFO", "server started")
    append_log_entry(tmp_path, "ERROR", "provider failed")
    append_log_entry(tmp_path, "ERROR", "second failure")
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.get("/api/logs?level=ERROR&limit=1")

    assert response.status_code == 200
    assert response.json() == {
        "entries": [{"level": "ERROR", "message": "second failure"}],
        "total": 2,
        "limit": 1,
        "offset": 0,
        "has_more": True,
    }


def test_get_logs_rejects_unknown_level(tmp_path):
    response = TestClient(create_app(data_dir=tmp_path)).get("/api/logs?level=TRACE")

    assert response.status_code == 422
    assert response.json() == {"error": "invalid log level"}


def test_read_log_page_excludes_entries_appended_after_snapshot_count(tmp_path, monkeypatch):
    append_log_entry(tmp_path, "INFO", "entry-1")
    append_log_entry(tmp_path, "INFO", "entry-2")
    appended = {"value": False}

    def count_then_append(_handle, _boundary):
        appended["value"] = True
        append_log_entry(tmp_path, "INFO", "entry-3")
        return 2

    monkeypatch.setattr(diagnostics, "_count_valid_log_rows", count_then_append)

    page = diagnostics.read_recent_log_page(tmp_path, limit=20, offset=0)

    assert appended["value"] is True
    assert page == {
        "entries": [
            {"level": "INFO", "message": "entry-1"},
            {"level": "INFO", "message": "entry-2"},
        ],
        "total": 2,
        "limit": 20,
        "offset": 0,
        "has_more": False,
    }


def test_read_log_page_does_not_mix_a_rotated_file_after_snapshot_count(tmp_path, monkeypatch):
    log_path = tmp_path / "logs" / "offerpilot.log"
    append_log_entry(tmp_path, "INFO", "entry-1")
    append_log_entry(tmp_path, "INFO", "entry-2")
    replacement_path = tmp_path / "replacement.log"
    replacement_path.write_text(
        "\n".join(
            json.dumps({"level": "INFO", "message": message})
            for message in ("replacement-1", "replacement-2")
        )
        + "\n",
        encoding="utf-8",
    )
    rotated = {"value": False}
    opened_paths = []

    def count_then_rotate(_handle, _boundary):
        rotated["value"] = True
        return 2

    def open_rotating_log(_path):
        source = replacement_path if rotated["value"] else log_path
        opened_paths.append(source)
        return source.open("rb")

    monkeypatch.setattr(diagnostics, "_count_valid_log_rows", count_then_rotate)
    monkeypatch.setattr(diagnostics, "_open_log_file", open_rotating_log)

    page = diagnostics.read_recent_log_page(tmp_path, limit=20, offset=0)

    assert rotated["value"] is True
    assert opened_paths == [log_path]
    assert page == {
        "entries": [
            {"level": "INFO", "message": "entry-1"},
            {"level": "INFO", "message": "entry-2"},
        ],
        "total": 2,
        "limit": 20,
        "offset": 0,
        "has_more": False,
    }


def test_read_log_page_retains_only_the_requested_page(tmp_path, monkeypatch):
    for index in range(1, 201):
        append_log_entry(tmp_path, "INFO", f"entry-{index}")
    retained_counts = []

    def track_retained_entry(entries, entry, limit):
        entries.append(entry)
        retained_counts.append(len(entries))

    monkeypatch.setattr(diagnostics, "_append_page_entry", track_retained_entry)

    page = diagnostics.read_recent_log_page(tmp_path, limit=1, offset=199)

    assert page["entries"] == [{"level": "INFO", "message": "entry-1"}]
    assert retained_counts == [1]
