"""GET /api/v1/files -- the History page's server-side .alog chooser. Uses a
throwaway folder tree so nothing depends on the machine running the tests."""
from __future__ import annotations

import os

from backend.app.api import files as files_api


def _tree(root):
    (root / "beta").mkdir()
    (root / "Alpha").mkdir()
    (root / ".hidden").mkdir()
    (root / "z-roast.alog").write_text("{}")
    (root / "a-roast.ALOG").write_text("{}")  # extension match is case-insensitive
    (root / "notes.txt").write_text("not a roast")
    (root / "roasts.db").write_text("not a roast")
    (root / ".secret.alog").write_text("{}")  # dotfiles are skipped even when they end in .alog
    return root


def test_lists_folders_first_then_alog_files_only(client, tmp_path):
    _tree(tmp_path)
    resp = client.get("/api/v1/files", params={"path": str(tmp_path)})
    assert resp.status_code == 200
    body = resp.json()
    assert [(e["name"], e["kind"]) for e in body["entries"]] == [
        ("Alpha", "dir"),
        ("beta", "dir"),
        ("a-roast.ALOG", "file"),
        ("z-roast.alog", "file"),
    ]
    assert body["path"] == str(tmp_path)
    assert body["truncated"] is False


def test_entries_carry_full_paths_and_file_sizes(client, tmp_path):
    _tree(tmp_path)
    body = client.get("/api/v1/files", params={"path": str(tmp_path)}).json()
    by_name = {e["name"]: e for e in body["entries"]}
    assert by_name["Alpha"]["path"] == str(tmp_path / "Alpha")
    assert by_name["Alpha"]["size"] is None
    assert by_name["z-roast.alog"]["path"] == str(tmp_path / "z-roast.alog")
    assert by_name["z-roast.alog"]["size"] == 2


def test_parent_points_up_and_is_none_at_the_root(client, tmp_path):
    body = client.get("/api/v1/files", params={"path": str(tmp_path)}).json()
    assert body["parent"] == str(tmp_path.parent)

    root = os.path.abspath(os.sep)
    assert client.get("/api/v1/files", params={"path": root}).json()["parent"] is None


def test_defaults_to_the_home_folder_when_no_path_is_given(client, tmp_path, monkeypatch):
    monkeypatch.setattr(os.path, "expanduser", lambda p: str(tmp_path) if p == "~" else p)
    for params in ({}, {"path": ""}, {"path": "   "}):
        body = client.get("/api/v1/files", params=params).json()
        assert body["path"] == str(tmp_path)


def test_shortcuts_are_home_plus_the_filesystem_roots(client, tmp_path):
    shortcuts = client.get("/api/v1/files", params={"path": str(tmp_path)}).json()["shortcuts"]
    assert shortcuts[0]["label"] == "Home"
    assert len(shortcuts) >= 2  # a root ("/") or the drive letters
    # The app's own data folders are deliberately not offered: saved roasts
    # have UUID names, and the samples folder holds one demo file.
    assert not any("roasts" in s["label"].lower() for s in shortcuts)


def test_missing_folder_is_404_and_a_file_path_is_400(client, tmp_path):
    (tmp_path / "x.alog").write_text("{}")
    assert client.get("/api/v1/files", params={"path": str(tmp_path / "nope")}).status_code == 404
    resp = client.get("/api/v1/files", params={"path": str(tmp_path / "x.alog")})
    assert resp.status_code == 400
    assert "Not a folder" in resp.json()["detail"]


def test_unreadable_folder_is_403(client, tmp_path, monkeypatch):
    def deny(_path):
        raise PermissionError

    monkeypatch.setattr(files_api.os, "scandir", deny)
    resp = client.get("/api/v1/files", params={"path": str(tmp_path)})
    assert resp.status_code == 403
    assert "No permission" in resp.json()["detail"]


def test_a_huge_folder_is_capped_and_flagged(client, tmp_path, monkeypatch):
    monkeypatch.setattr(files_api, "_MAX_ENTRIES", 3)
    for i in range(6):
        (tmp_path / f"r{i}.alog").write_text("{}")
    body = client.get("/api/v1/files", params={"path": str(tmp_path)}).json()
    assert len(body["entries"]) == 3
    assert body["truncated"] is True


def test_requires_login(anon_client, tmp_path):
    assert anon_client.get("/api/v1/files", params={"path": str(tmp_path)}).status_code == 401
