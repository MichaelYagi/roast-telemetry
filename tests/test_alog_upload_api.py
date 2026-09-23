"""POST /api/v1/roasts/import-upload -- an .alog sent from the browser's computer
(the raw file as the request body), the counterpart of POST /import, which reads
a path on the server."""
from __future__ import annotations

import os
import tempfile

import pytest

from alog_playback.alog_io import roast_to_native_alog_dict


def _alog_bytes(title="Uploaded roast", n=40) -> bytes:
    profile = [{"time_s": float(i), "bt": 90.0 + i * 0.5, "et": 150.0 + i * 0.8, "heater_pct": 70.0, "fan_pct": 20.0, "drum_speed_pct": 50.0} for i in range(n)]
    events = [{"type": "CHARGE", "time_s": 0.0}]
    d = roast_to_native_alog_dict(title=title, profile=profile, events=events, notes=[], beans="Test beans")
    return repr(d).encode("utf-8")  # the format's Python-literal syntax, as save_native_alog writes it


def _upload(client, data, **params):
    return client.post("/api/v1/roasts/import-upload", params=params, content=data, headers={"Content-Type": "application/octet-stream"})


def test_upload_imports_the_roast_into_history(client):
    resp = _upload(client, _alog_bytes(), filename="roast.alog")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["title"] == "Uploaded roast" and body["mode"] == "alog_playback" and body["status"] == "complete"

    listed = client.get("/api/v1/roasts").json()
    assert [r["id"] for r in listed] == [body["id"]]
    # It is a real, readable roast: the detail page and the .alog download both work.
    detail = client.get(f"/api/v1/roasts/{body['id']}").json()
    assert len(detail["profile"]) >= 40 and detail["beans"] == "Test beans"
    assert client.get(f"/api/v1/roasts/{body['id']}/alog").status_code == 200


def test_a_title_given_wins_over_the_files_own(client):
    resp = _upload(client, _alog_bytes(), filename="roast.alog", title="My title")
    assert resp.json()["title"] == "My title"


def test_the_upload_is_recorded_against_the_signed_in_user(client):
    resp = _upload(client, _alog_bytes(), filename="roast.alog")
    assert resp.json().get("created_by_username")


def test_the_same_file_can_be_uploaded_twice_as_two_roasts(client):
    a = _upload(client, _alog_bytes(), filename="roast.alog").json()["id"]
    b = _upload(client, _alog_bytes(), filename="roast.alog").json()["id"]
    assert a != b and len(client.get("/api/v1/roasts").json()) == 2


@pytest.mark.parametrize("data", [b"", b"   \n  "], ids=["empty", "blank"])
def test_an_empty_file_is_refused(client, data):
    resp = _upload(client, data, filename="roast.alog")
    assert resp.status_code == 400
    assert "roast.alog is empty" in resp.json()["detail"]


@pytest.mark.parametrize("data", [b"not an alog at all", b"{'title': ", b"\\xff\\xfe\\x00 binary"], ids=["text", "truncated", "binary"])
def test_something_that_is_not_an_alog_is_refused_and_nothing_is_stored(client, data):
    resp = _upload(client, data, filename="notes.txt")
    assert resp.status_code == 400
    assert "notes.txt doesn't look like a valid .alog or .json file" in resp.json()["detail"]
    assert client.get("/api/v1/roasts").json() == []


def test_a_huge_upload_is_refused(client, monkeypatch):
    from backend.app.api import roasts as roasts_api

    monkeypatch.setattr(roasts_api, "MAX_ALOG_UPLOAD_BYTES", 1000)
    resp = _upload(client, b"x" * 5000, filename="big.alog")
    assert resp.status_code == 413
    assert client.get("/api/v1/roasts").json() == []


def test_the_filename_is_only_a_label_never_a_path(client):
    resp = _upload(client, b"garbage", filename="../../etc/passwd")
    assert resp.status_code == 400
    assert "passwd" in resp.json()["detail"] and "../" not in resp.json()["detail"]


def test_the_throwaway_file_is_always_removed(client, monkeypatch):
    created = []
    real = tempfile.mkstemp

    def spy(*a, **kw):
        fd, path = real(*a, **kw)
        created.append(path)
        return fd, path

    monkeypatch.setattr(tempfile, "mkstemp", spy)
    _upload(client, _alog_bytes(), filename="ok.alog")
    _upload(client, b"garbage", filename="bad.alog")
    assert len(created) == 2 and not any(os.path.exists(p) for p in created)


def test_requires_login(anon_client):
    assert _upload(anon_client, _alog_bytes(), filename="roast.alog").status_code == 401


def test_the_shipped_sample_roast_uploads_too(client):
    """The sample .alog is a different shape (this app's older JSON form) from the
    Python-literal ones it writes now -- both must import."""
    from pathlib import Path

    sample = Path(__file__).resolve().parent.parent / "backend" / "data" / "sample_roasts" / "demo_roast.alog"
    resp = _upload(client, sample.read_bytes(), filename=sample.name)
    assert resp.status_code == 201, resp.text
    assert client.get(f"/api/v1/roasts/{resp.json()['id']}").json()["profile"]


def test_the_error_for_a_bad_file_is_plain_english(client):
    detail = _upload(client, b"hello there", filename="notes.txt").json()["detail"]
    assert detail == "notes.txt doesn't look like a valid .alog or .json file."
    assert "ast" not in detail.lower() and "0x" not in detail


def test_a_file_missing_a_required_field_says_which_and_hides_the_temp_path(client):
    resp = _upload(client, b"{'timex': [0.0, 1.0], 'temp1': [1.0, 2.0]}", filename="partial.alog")
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert detail.startswith("partial.alog doesn't look like a valid .alog file (it is missing the ")
    assert "temp2" in detail and "tmp" not in detail
