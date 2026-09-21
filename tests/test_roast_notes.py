"""Adding, editing and deleting notes -- during a roast and after it, both
while the session is still in memory ("warm") and after a backend restart
("cold": the session is popped so everything goes through the .alog file)."""
from __future__ import annotations

from backend.app.roast_session.session import session_manager


def _finished_roast(client) -> str:
    roast_id = client.post("/api/roasts", json={"title": "Notes Roast", "mode": "simulator"}).json()["id"]
    client.post(f"/api/roasts/{roast_id}/stop")
    return roast_id


def _notes(client, roast_id):
    return client.get(f"/api/roasts/{roast_id}").json()["notes"]


def test_note_can_be_added_edited_and_deleted_after_the_roast_warm(client):
    roast_id = _finished_roast(client)

    created = client.post(f"/api/roasts/{roast_id}/notes", json={"text": "  smells sweet\nnice  "})
    assert created.status_code == 200
    note = created.json()
    assert note["text"] == "smells sweet nice"

    edited = client.patch(f"/api/roasts/{roast_id}/notes/{note['id']}", json={"text": "smells nutty"})
    assert edited.status_code == 200
    assert [n["text"] for n in _notes(client, roast_id)] == ["smells nutty"]
    # An edit gives the note a new id (ids come from the content) -- the old one is gone.
    assert edited.json()["id"] != note["id"]
    assert client.delete(f"/api/roasts/{roast_id}/notes/{note['id']}").status_code == 409

    assert client.delete(f"/api/roasts/{roast_id}/notes/{edited.json()['id']}").status_code == 204
    assert _notes(client, roast_id) == []


def test_notes_survive_a_restart_and_can_be_edited_cold(client):
    roast_id = _finished_roast(client)
    client.post(f"/api/roasts/{roast_id}/notes", json={"text": "first"})
    client.post(f"/api/roasts/{roast_id}/notes", json={"text": "second"})
    session_manager.sessions.pop(roast_id, None)

    notes = _notes(client, roast_id)
    assert [n["text"] for n in notes] == ["first", "second"]

    assert client.patch(f"/api/roasts/{roast_id}/notes/{notes[0]['id']}", json={"text": "first, edited"}).status_code == 200
    added = client.post(f"/api/roasts/{roast_id}/notes", json={"text": "third"})
    assert added.status_code == 200

    assert [n["text"] for n in _notes(client, roast_id)] == ["first, edited", "second", "third"]

    victim = _notes(client, roast_id)[1]
    assert client.delete(f"/api/roasts/{roast_id}/notes/{victim['id']}").status_code == 204
    assert [n["text"] for n in _notes(client, roast_id)] == ["first, edited", "third"]


def test_empty_note_is_rejected(client):
    roast_id = _finished_roast(client)
    assert client.post(f"/api/roasts/{roast_id}/notes", json={"text": "   "}).status_code == 409
    note = client.post(f"/api/roasts/{roast_id}/notes", json={"text": "ok"}).json()
    assert client.patch(f"/api/roasts/{roast_id}/notes/{note['id']}", json={"text": ""}).status_code == 409


def test_unknown_roast_or_note_is_404_or_409(client):
    assert client.post("/api/roasts/nope/notes", json={"text": "x"}).status_code == 404
    roast_id = _finished_roast(client)
    assert client.delete(f"/api/roasts/{roast_id}/notes/missing").status_code == 409


def test_note_during_a_live_roast_still_works(client):
    roast_id = client.post("/api/roasts", json={"title": "Live", "mode": "simulator"}).json()["id"]
    assert client.post(f"/api/roasts/{roast_id}/notes", json={"text": "charged"}).status_code == 200
    assert [n["text"] for n in _notes(client, roast_id)] == ["charged"]
    client.post(f"/api/roasts/{roast_id}/stop")


def test_cold_note_ids_returned_match_what_the_next_read_shows(client):
    # A roast with no live session stores notes as plain lines in its file,
    # so ids are positions. The id handed back on add/edit must be the same
    # one a fresh read shows, or delete/save on it fails "not found".
    roast_id = _finished_roast(client)
    session_manager.sessions.pop(roast_id, None)

    first = client.post(f"/api/roasts/{roast_id}/notes", json={"text": "one"}).json()
    second = client.post(f"/api/roasts/{roast_id}/notes", json={"text": "two"}).json()
    assert [n["id"] for n in _notes(client, roast_id)] == [first["id"], second["id"]]

    edited = client.patch(f"/api/roasts/{roast_id}/notes/{second['id']}", json={"text": "two!"}).json()
    assert edited["id"] == _notes(client, roast_id)[1]["id"]  # the id handed back matches the next read
    assert client.delete(f"/api/roasts/{roast_id}/notes/{edited['id']}").status_code == 204
    assert [n["text"] for n in _notes(client, roast_id)] == ["one"]


def test_free_text_notes_in_an_imported_file_show_up_and_survive_edits(client, tmp_path):
    from alog_playback.alog_io import roast_to_native_alog_dict, save_native_alog

    data = roast_to_native_alog_dict(
        title="Imported", profile=[{"time_s": 0.0, "bt": 100.0, "et": 120.0}, {"time_s": 30.0, "bt": 110.0, "et": 130.0}],
        events=[], notes=[], beans="b", weight_green_g=None, weight_roasted_g=None, roastdate="2026-01-01T00:00:00+00:00",
    )
    data["roastingnotes"] = "Fruity, slow drying.\n[10s] timed note"
    path = tmp_path / "free.alog"
    save_native_alog(str(path), data)
    roast_id = client.post("/api/roasts/import", params={"path": str(path)}).json()["id"]

    assert [n["text"] for n in _notes(client, roast_id)] == ["Fruity, slow drying.", "timed note"]

    added = client.post(f"/api/roasts/{roast_id}/notes", json={"text": "later"})
    assert added.status_code == 200
    assert [n["text"] for n in _notes(client, roast_id)] == ["Fruity, slow drying.", "timed note", "later"]


def test_author_and_tenths_of_a_second_survive_a_restart(client):
    from alog_playback.alog_io import alog_dict_to_points, roast_to_native_alog_dict

    notes = [{"time_s": 12.34, "text": "first crack soon", "author": "alice"}, {"time_s": 30.0, "text": "plain", "author": None}]
    from alog_playback.alog_io import assign_note_ids, round_note_time

    for n in notes:
        n["time_s"] = round_note_time(n["time_s"])
    assign_note_ids(notes)
    d = roast_to_native_alog_dict(
        title="t", profile=[{"time_s": 0.0, "bt": 1.0, "et": 1.0}], events=[], notes=notes,
        beans=None, weight_green_g=None, weight_roasted_g=None, roastdate="2026-01-01T00:00:00+00:00",
    )
    back = alog_dict_to_points(d)["notes"]
    assert [(n["time_s"], n["author"], n["text"]) for n in back] == [(12.3, "alice", "first crack soon"), (30.0, None, "plain")]
    assert [n["id"] for n in back] == [n["id"] for n in notes]  # same id in memory and after reading back


def test_note_is_stamped_with_the_logged_in_user(client):
    roast_id = _finished_roast(client)
    note = client.post(f"/api/roasts/{roast_id}/notes", json={"text": "who wrote me"}).json()
    session_manager.sessions.pop(roast_id, None)
    assert _notes(client, roast_id)[0]["author"] == note["author"]


def test_a_stale_note_id_never_hits_a_different_note(client):
    # Two open pages: one deletes the first note, then the other (still
    # holding the old list) tries to delete/edit by an id that's gone.
    roast_id = _finished_roast(client)
    a = client.post(f"/api/roasts/{roast_id}/notes", json={"text": "alpha"}).json()
    client.post(f"/api/roasts/{roast_id}/notes", json={"text": "beta"})
    b = _notes(client, roast_id)[1]

    assert client.delete(f"/api/roasts/{roast_id}/notes/{a['id']}").status_code == 204
    assert client.delete(f"/api/roasts/{roast_id}/notes/{a['id']}").status_code == 409
    assert client.patch(f"/api/roasts/{roast_id}/notes/{a['id']}", json={"text": "x"}).status_code == 409
    assert [n["id"] for n in _notes(client, roast_id)] == [b["id"]]  # beta untouched and keeps its id


def test_identical_notes_get_separate_ids(client):
    roast_id = _finished_roast(client)
    client.post(f"/api/roasts/{roast_id}/notes", json={"text": "same"})
    client.post(f"/api/roasts/{roast_id}/notes", json={"text": "same"})
    ids = [n["id"] for n in _notes(client, roast_id)]
    assert len(set(ids)) == 2
    assert client.delete(f"/api/roasts/{roast_id}/notes/{ids[1]}").status_code == 204
    assert [n["id"] for n in _notes(client, roast_id)] == [ids[0]]
