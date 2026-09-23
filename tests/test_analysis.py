"""Cross-roast analysis: per-roast metrics, grouped summaries, bulk export,
outcomes, saved beans and saved views."""
from __future__ import annotations

import io
import zipfile

from alog_playback.alog_io import roast_to_native_alog_dict, save_native_alog


def _ev(kind, t):
    return {"id": kind, "type": kind, "time_s": float(t), "label": kind, "value": None}


def make_alog(path, *, title="Roast", drop_at=600, dry_at=240, fc_at=480, charge_bt=100.0, slope=0.3):
    profile = [{"time_s": float(t), "bt": charge_bt + slope * t, "et": 150.0 + slope * t} for t in range(0, 661)]
    events = [
        _ev("CHARGE", 0), _ev("TURNING_POINT", 60), _ev("DRY_END", dry_at), _ev("FC_START", fc_at), _ev("DROP", drop_at),
    ]
    save_native_alog(
        str(path),
        roast_to_native_alog_dict(
            title=title, profile=profile, events=events, notes=[], beans=None,
            weight_green_g=None, weight_roasted_g=None, roastdate="2026-03-01T10:00:00+00:00",
        ),
    )


def import_roast(client, tmp_path, name="a", *, green=None, roasted=None, tags=None, **kwargs):
    path = tmp_path / f"{name}.alog"
    make_alog(path, title=name, **kwargs)
    roast_id = client.post("/api/roasts/import", params={"path": str(path), "title": name}).json()["id"]
    if green is not None:
        client.post(f"/api/roasts/{roast_id}/weight-green", params={"grams": green})
    if roasted is not None:
        client.post(f"/api/roasts/{roast_id}/weight", params={"grams": roasted})
    if tags is not None:
        client.put(f"/api/roasts/{roast_id}/tags", json={"tags": tags})
    return roast_id


def rows(client, **params):
    return client.get("/api/analysis/table", params=params).json()["rows"]


def row_for(client, roast_id, **params):
    return next(r for r in rows(client, **params) if r["id"] == roast_id)


# -- metrics ---------------------------------------------------------------------------


def test_metrics_are_worked_out_from_the_curve_and_milestones(client, tmp_path):
    roast_id = import_roast(client, tmp_path, green=500, roasted=430)
    m = row_for(client, roast_id)["metrics"]

    assert m["charge_temp_c"] == 100.0
    # Milestones in an .alog file sit on whole sample positions, so times can land a second or two off.
    near = lambda value, expected: abs(value - expected) <= 2
    assert near(m["dry_end_time_s"], 240) and near(m["fc_start_time_s"], 480) and near(m["drop_time_s"], 600)
    assert near(m["duration_s"], 600)
    assert near(m["dry_time_s"], 240) and near(m["maillard_time_s"], 240) and near(m["development_time_s"], 120)
    assert abs(m["dry_pct"] - 40) < 1 and abs(m["maillard_pct"] - 40) < 1 and abs(m["dtr_pct"] - 20) < 1
    assert abs(m["fc_start_temp_c"] - (100 + 0.3 * 480)) < 1.0
    assert abs(m["drop_temp_c"] - (100 + 0.3 * 600)) < 1.0
    assert m["weight_green_g"] == 500 and m["weight_roasted_g"] == 430 and m["weight_loss_pct"] == 14.0
    assert m["max_ror"] is not None


def test_a_roast_without_milestones_still_gets_a_row_with_gaps(client, tmp_path):
    from alog_playback.alog_io import roast_to_native_alog_dict, save_native_alog

    path = tmp_path / "bare.alog"
    save_native_alog(
        str(path),
        roast_to_native_alog_dict(
            title="bare", profile=[{"time_s": float(t), "bt": 100.0 + t, "et": 120.0 + t} for t in range(30)],
            events=[], notes=[], beans=None, weight_green_g=None, weight_roasted_g=None,
            roastdate="2026-03-01T10:00:00+00:00",
        ),
    )
    roast_id = client.post("/api/roasts/import", params={"path": str(path)}).json()["id"]
    m = row_for(client, roast_id)["metrics"]
    assert m["drop_time_s"] is None and m["dtr_pct"] is None and m["weight_loss_pct"] is None


def test_metric_list_has_labels_and_units(client):
    metrics = client.get("/api/analysis/metrics").json()
    keys = [m["key"] for m in metrics]
    assert "charge_temp_c" in keys and "dtr_pct" in keys and "cupping_score" in keys
    assert all({"key", "label", "unit", "group"} <= set(m) for m in metrics)


def test_editing_a_milestone_refreshes_the_metrics(client, tmp_path):
    roast_id = import_roast(client, tmp_path)
    before = row_for(client, roast_id)["metrics"]["duration_s"]
    events = client.get(f"/api/roasts/{roast_id}").json()["events"]
    drop = next(e for e in events if e["type"] == "DROP")
    client.patch(f"/api/roasts/{roast_id}/events/{drop['id']}", json={"time_s": 640})
    assert abs(row_for(client, roast_id)["metrics"]["duration_s"] - before - 40) <= 2


# -- filters ---------------------------------------------------------------------------


def test_simulated_roasts_are_left_out_unless_asked_for(client, tmp_path):
    real = import_roast(client, tmp_path, "real")
    fake = import_roast(client, tmp_path, "fake", tags=["simulated"])
    ids = [r["id"] for r in rows(client)]
    assert real in ids and fake not in ids
    body = client.get("/api/analysis/table").json()
    assert body["simulated_excluded"] == 1
    assert fake in [r["id"] for r in rows(client, include_simulated="true")]


def test_search_tag_and_date_filters(client, tmp_path):
    a = import_roast(client, tmp_path, "ethiopia one", tags=["light"])
    b = import_roast(client, tmp_path, "brazil two", tags=["dark"])
    assert [r["id"] for r in rows(client, q="ethiopia")] == [a]
    assert [r["id"] for r in rows(client, tag="dark")] == [b]
    assert rows(client, created_from="2999-01-01") == []
    assert len(rows(client, created_to="2999-01-01")) == 2


# -- summaries -------------------------------------------------------------------------


def test_summary_gives_average_and_spread_per_group(client, tmp_path):
    import_roast(client, tmp_path, "a", drop_at=580, tags=["light"])
    import_roast(client, tmp_path, "b", drop_at=620, tags=["light"])
    import_roast(client, tmp_path, "c", drop_at=600, tags=["dark"])

    overall = client.get("/api/analysis/summary").json()
    assert overall["groups"][0]["key"] == "All roasts" and overall["groups"][0]["count"] == 3

    by_tag = {g["key"]: g for g in client.get("/api/analysis/summary", params={"group_by": "tag"}).json()["groups"]}
    light = by_tag["light"]["metrics"]["duration_s"]
    assert by_tag["light"]["count"] == 2
    assert abs(light["mean"] - 600) <= 2 and abs(light["max"] - light["min"] - 40) <= 2
    assert abs(light["sd"] - 28.28) < 2  # sample standard deviation of two roasts 40 s apart
    assert by_tag["dark"]["metrics"]["duration_s"]["sd"] is None  # one roast: no spread


def test_summary_can_group_by_month_and_rejects_unknown_groupings(client, tmp_path):
    import_roast(client, tmp_path, "a")
    groups = client.get("/api/analysis/summary", params={"group_by": "month"}).json()["groups"]
    assert len(groups) == 1 and len(groups[0]["key"]) == 7
    assert client.get("/api/analysis/summary", params={"group_by": "nonsense"}).status_code == 422


# -- export ----------------------------------------------------------------------------


def test_zip_export_holds_every_alog(client, tmp_path):
    import_roast(client, tmp_path, "same")
    import_roast(client, tmp_path, "same")  # identical titles must not overwrite each other
    resp = client.get("/api/analysis/export.zip")
    assert resp.status_code == 200 and resp.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(resp.content)).namelist()
    assert "summary.csv" not in names
    assert len([n for n in names if n.startswith("alog/") and n.endswith(".alog")]) == 2


def test_zip_export_respects_filters_and_404s_when_empty(client, tmp_path):
    import_roast(client, tmp_path, "keep", tags=["x"])
    import_roast(client, tmp_path, "skip", tags=["y"])
    names = zipfile.ZipFile(io.BytesIO(client.get("/api/analysis/export.zip", params={"tag": "x"}).content)).namelist()
    assert len([n for n in names if n.startswith("alog/")]) == 1
    assert client.get("/api/analysis/export.zip", params={"tag": "nothing"}).status_code == 404


def test_zip_export_by_ids_ignores_other_filters_and_includes_simulated(client, tmp_path):
    # An explicit selection (History's row checkboxes) -- picking exactly
    # these two, one of them simulated, must not silently drop the
    # simulated one or apply the (irrelevant, unset) tag filter.
    keep = import_roast(client, tmp_path, "keep", tags=["x"])
    sim = import_roast(client, tmp_path, "sim", tags=["simulated"])
    import_roast(client, tmp_path, "left_out", tags=["x"])
    names = zipfile.ZipFile(
        io.BytesIO(client.get("/api/analysis/export.zip", params={"ids": f"{keep},{sim}"}).content)
    ).namelist()
    assert len([n for n in names if n.startswith("alog/")]) == 2

    assert client.get("/api/analysis/export.zip", params={"ids": "not-a-real-id"}).status_code == 404


def test_table_by_ids_ignores_other_filters_and_includes_simulated(client, tmp_path):
    a = import_roast(client, tmp_path, "a")
    sim = import_roast(client, tmp_path, "b", tags=["simulated"])
    import_roast(client, tmp_path, "c")
    result_ids = {r["id"] for r in rows(client, ids=f"{a},{sim},not-a-real-id")}
    assert result_ids == {a, sim}


# -- outcomes ----------------------------------------------------------------------------


def test_outcomes_are_saved_cleared_and_validated(client, tmp_path):
    roast_id = import_roast(client, tmp_path)
    saved = client.put(
        f"/api/roasts/{roast_id}/outcome",
        json={"color_agtron": 62.5, "cupping_score": 86, "rating": 4, "tasting_notes": "  jammy, clean  "},
    )
    assert saved.status_code == 200
    assert saved.json() == {"color_agtron": 62.5, "cupping_score": 86, "rating": 4, "tasting_notes": "jammy, clean"}

    detail = client.get(f"/api/roasts/{roast_id}").json()
    assert detail["cupping_score"] == 86 and detail["rating"] == 4
    listed = next(r for r in client.get("/api/roasts").json() if r["id"] == roast_id)
    assert listed["color_agtron"] == 62.5
    m = row_for(client, roast_id)["metrics"]
    assert m["color_agtron"] == 62.5 and m["cupping_score"] == 86 and m["rating"] == 4

    # A field left out is unchanged; null clears just that field.
    client.put(f"/api/roasts/{roast_id}/outcome", json={"rating": None})
    after = client.get(f"/api/roasts/{roast_id}").json()
    assert after["rating"] is None and after["cupping_score"] == 86

    assert client.put(f"/api/roasts/{roast_id}/outcome", json={"rating": 9}).status_code == 422
    assert client.put(f"/api/roasts/{roast_id}/outcome", json={"cupping_score": 120}).status_code == 422
    assert client.put("/api/roasts/nope/outcome", json={"rating": 3}).status_code == 404


# -- saved beans ---------------------------------------------------------------------------


def test_bean_records_can_be_created_edited_and_deleted(client):
    created = client.post("/api/beans", json={"name": "Yirgacheffe", "origin": "Ethiopia", "process": "Washed", "density_g_l": 720})
    assert created.status_code == 201
    bean = created.json()
    assert bean["roast_count"] == 0

    edited = client.put(f"/api/beans/{bean['id']}", json={"name": "Yirgacheffe G1", "origin": "Ethiopia", "moisture_pct": 10.5})
    assert edited.json()["name"] == "Yirgacheffe G1" and edited.json()["moisture_pct"] == 10.5 and edited.json()["density_g_l"] is None
    assert [b["name"] for b in client.get("/api/beans").json()] == ["Yirgacheffe G1"]

    assert client.post("/api/beans", json={"name": ""}).status_code == 422
    assert client.delete(f"/api/beans/{bean['id']}").status_code == 204
    assert client.get("/api/beans").json() == []
    assert client.delete(f"/api/beans/{bean['id']}").status_code == 404


def test_linking_a_roast_to_beans_groups_and_annotates_it(client, tmp_path):
    bean = client.post("/api/beans", json={"name": "Huila", "origin": "Colombia", "process": "Washed", "density_g_l": 700, "moisture_pct": 10.8}).json()
    a = import_roast(client, tmp_path, "a")
    b = import_roast(client, tmp_path, "b")
    assert client.put(f"/api/roasts/{a}/beans", json={"name": "Huila"}).json() == {"beans": "Huila", "bean_id": bean["id"]}
    client.put(f"/api/roasts/{b}/beans", json={"name": "Huila"})

    row = row_for(client, a)
    assert row["beans"] == "Huila" and row["origin"] == "Colombia" and row["process"] == "Washed"
    assert row["metrics"]["bean_density_g_l"] == 700
    assert [r["id"] for r in rows(client, bean_id=bean["id"])] == [b, a] or {r["id"] for r in rows(client, bean_id=bean["id"])} == {a, b}

    by_beans = client.get("/api/analysis/summary", params={"group_by": "beans"}).json()["groups"]
    assert by_beans[0]["key"] == "Huila" and by_beans[0]["count"] == 2
    assert client.get("/api/beans").json()[0]["roast_count"] == 2

    # Deleting the record keeps the roasts' own beans name and just drops the link.
    client.delete(f"/api/beans/{bean['id']}")
    after = row_for(client, a)
    assert after["beans"] == "Huila" and after["metrics"]["bean_density_g_l"] is None
    assert client.get(f"/api/roasts/{a}").json()["bean_id"] is None


def test_the_beans_field_links_by_name_and_adds_new_names_to_the_list(client, tmp_path):
    bean = client.post("/api/beans", json={"name": "Huila Supremo"}).json()
    roast_id = import_roast(client, tmp_path)

    linked = client.put(f"/api/roasts/{roast_id}/beans", json={"name": "  huila supremo "}).json()
    assert linked == {"beans": "Huila Supremo", "bean_id": bean["id"]}  # the saved spelling wins
    assert client.get(f"/api/roasts/{roast_id}").json()["bean_id"] == bean["id"]

    # A name that isn't in the list yet is added to it, and the roast links to the new record.
    typed = client.put(f"/api/roasts/{roast_id}/beans", json={"name": "Something new"}).json()
    assert typed["beans"] == "Something new" and typed["bean_id"] not in (None, bean["id"])
    assert sorted(b["name"] for b in client.get("/api/beans").json()) == ["Huila Supremo", "Something new"]
    assert client.get(f"/api/roasts/{roast_id}").json()["beans"] == "Something new"

    # Typing it again in another case finds the same record instead of adding a second.
    again = client.put(f"/api/roasts/{roast_id}/beans", json={"name": "SOMETHING NEW"}).json()
    assert again["bean_id"] == typed["bean_id"] and len(client.get("/api/beans").json()) == 2

    cleared = client.put(f"/api/roasts/{roast_id}/beans", json={"name": "   "}).json()
    assert cleared == {"beans": None, "bean_id": None}
    assert client.put("/api/roasts/nope/beans", json={"name": "x"}).status_code == 404


def test_a_new_roast_links_or_adds_beans_by_name_or_id(client):
    bean = client.post("/api/beans", json={"name": "Sidamo"}).json()

    by_name = client.post("/api/roasts", json={"title": "Sim", "mode": "simulator", "beans": "sidamo"}).json()
    assert by_name["bean_id"] == bean["id"] and by_name["beans"] == "Sidamo"
    client.post(f"/api/roasts/{by_name['id']}/stop")

    by_id = client.post("/api/roasts", json={"title": "Sim", "mode": "simulator", "bean_id": bean["id"]}).json()
    assert by_id["beans"] == "Sidamo" and by_id["bean_id"] == bean["id"]
    client.post(f"/api/roasts/{by_id['id']}/stop")

    typed = client.post("/api/roasts", json={"title": "Sim", "mode": "simulator", "beans": "Nowhere Estate"}).json()
    assert typed["beans"] == "Nowhere Estate" and typed["bean_id"] is not None
    client.post(f"/api/roasts/{typed['id']}/stop")
    assert "Nowhere Estate" in [b["name"] for b in client.get("/api/beans").json()]


def test_beans_names_in_uploaded_logs_join_the_list(client, tmp_path):
    from alog_playback.alog_io import roast_to_native_alog_dict, save_native_alog

    path = tmp_path / "named.alog"
    save_native_alog(
        str(path),
        roast_to_native_alog_dict(
            title="named", profile=[{"time_s": float(t), "bt": 100.0 + t, "et": 120.0 + t} for t in range(30)],
            events=[], notes=[], beans="Ethiopia Yirgacheffe", weight_green_g=None, weight_roasted_g=None,
            roastdate="2026-03-01T10:00:00+00:00",
        ),
    )
    roast_id = client.post("/api/roasts/import", params={"path": str(path)}).json()["id"]
    beans = client.get("/api/beans").json()
    assert [b["name"] for b in beans] == ["Ethiopia Yirgacheffe"] and beans[0]["roast_count"] == 1
    assert client.get(f"/api/roasts/{roast_id}").json()["bean_id"] == beans[0]["id"]


def test_existing_typed_names_are_added_to_the_list_once(client, tmp_path):
    from backend.app import storage
    from backend.app.roast_session.session import session_manager

    a = import_roast(client, tmp_path, "a")
    b = import_roast(client, tmp_path, "b")
    c = import_roast(client, tmp_path, "c")
    # How roasts looked before beans had a list: just typed names, in mixed case, with one blank.
    storage.update_roast(a, beans="Kenya AA", bean_id=None)
    storage.update_roast(b, beans="kenya aa", bean_id=None)
    storage.update_roast(c, beans="  ", bean_id=None)
    storage.set_schema_version(2)

    session_manager.backfill_beans()
    beans = client.get("/api/beans").json()
    assert [x["name"] for x in beans] == ["Kenya AA"] and beans[0]["roast_count"] == 2
    assert client.get(f"/api/roasts/{b}").json()["beans"] == "Kenya AA"  # the same spelling, so they group together
    assert storage.get_schema_version() == 3
    session_manager.backfill_beans()  # runs once
    assert len(client.get("/api/beans").json()) == 1


# -- saved views -------------------------------------------------------------------------------


def test_saved_views_are_stored_listed_and_deleted(client):
    config = {"x": "charge_temp_c", "y": "dtr_pct", "groupBy": "beans"}
    created = client.post("/api/views", json={"kind": "analysis", "name": "Charge vs DTR", "config": config})
    assert created.status_code == 201
    view = created.json()
    assert view["config"] == config

    # Saving under the same name updates that view rather than adding another.
    again = client.post("/api/views", json={"kind": "analysis", "name": " charge vs dtr ", "config": {"x": "dry_pct"}})
    assert again.status_code == 200 and again.json()["id"] == view["id"] and again.json()["config"] == {"x": "dry_pct"}
    assert len(client.get("/api/views", params={"kind": "analysis"}).json()) == 1
    client.post("/api/views", json={"kind": "analysis", "name": "Charge vs DTR", "config": config})

    client.post("/api/views", json={"kind": "compare", "name": "Ethiopians", "config": {"ids": ["a", "b"]}})
    assert [v["name"] for v in client.get("/api/views", params={"kind": "analysis"}).json()] == ["Charge vs DTR"]
    assert len(client.get("/api/views").json()) == 2

    assert client.post("/api/views", json={"kind": "other", "name": "x", "config": {}}).status_code == 422
    assert client.delete(f"/api/views/{view['id']}").status_code == 204
    assert client.delete(f"/api/views/{view['id']}").status_code == 404


# -- uploaded logs ------------------------------------------------------------------------------


def test_uploaded_logs_are_analysed_but_replays_are_not_double_counted(client, tmp_path):
    path = tmp_path / "up.alog"
    make_alog(path, title="up")
    uploaded = client.post("/api/roasts/import-upload", params={"filename": "up.alog", "title": "Uploaded log"}, content=path.read_bytes())
    assert uploaded.status_code == 201
    uploaded_id = uploaded.json()["id"]

    # A roast made by replaying that log back (it has a source file).
    replay_id = client.post("/api/roasts", json={"title": "Replay", "mode": "alog_playback", "alog_path": str(path)}).json()["id"]
    client.post(f"/api/roasts/{replay_id}/stop")

    ids = [r["id"] for r in rows(client)]
    assert uploaded_id in ids and replay_id not in ids
    assert row_for(client, uploaded_id)["source"] == "uploaded"
    assert replay_id in [r["id"] for r in rows(client, include_replays="true")]


def test_imported_roasts_get_a_real_date(client, tmp_path):
    roast_id = import_roast(client, tmp_path)
    created = row_for(client, roast_id)["created_at"]
    assert created.startswith("2026-03-01")


def test_old_imports_with_text_dates_are_repaired_once(client, tmp_path):
    from backend.app import storage
    from backend.app.roast_session.session import session_manager

    roast_id = import_roast(client, tmp_path)
    storage.update_roast(roast_id, created_at="Sun Mar 01 2026")  # how imports used to be stored
    storage.set_schema_version(1)
    session_manager.backfill_dates()
    assert storage.get_roast_row(roast_id)["created_at"].startswith("2026-03-01")
    assert storage.get_schema_version() == 2


def test_one_roasts_numbers_can_be_fetched_directly(client, tmp_path):
    roast_id = import_roast(client, tmp_path, "solo", tags=["simulated"])  # a simulated one still works here
    body = client.get(f"/api/analysis/roasts/{roast_id}").json()
    assert body["id"] == roast_id and body["simulated"] is True and body["metrics"]["charge_temp_c"] == 100.0
    assert client.get("/api/analysis/roasts/nope").status_code == 404


def test_the_source_filter_shows_only_uploaded_or_only_recorded_roasts(client, tmp_path):
    path = tmp_path / "up.alog"
    make_alog(path, title="up")
    uploaded_id = client.post("/api/roasts/import-upload", params={"filename": "up.alog"}, content=path.read_bytes()).json()["id"]
    assert [r["id"] for r in rows(client, source="uploaded")] == [uploaded_id]
    assert rows(client, source="recorded") == []
    assert client.get("/api/analysis/table", params={"source": "bogus"}).status_code == 422
    groups = client.get("/api/analysis/summary", params={"group_by": "source"}).json()["groups"]
    assert groups[0]["key"] == "uploaded"


# -- AI insights (Ollama) -------------------------------------------------------------------------


def _configure_ollama(client):
    client.put("/api/settings", json={"ollama_url": "http://ollama.test", "ollama_model": "test-model"})


def _wait_for_insight(client, job_id, timeout=5.0):
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/analysis/insights/{job_id}").json()
        if job["status"] != "pending":
            return job
        time.sleep(0.05)
    raise AssertionError("the analysis never finished")


def test_insights_need_ollama_to_be_configured(client, tmp_path):
    import_roast(client, tmp_path)
    resp = client.post("/api/analysis/insights", json={})
    assert resp.status_code == 400 and "Ollama" in resp.json()["detail"]


def test_insights_send_summary_numbers_and_return_the_models_text(client, tmp_path, monkeypatch):
    from backend.app import ollama_client

    seen = {}

    async def fake_generate(url, model, prompt, options=None):
        seen.update(url=url, model=model, prompt=prompt, options=options)
        return "Your drop temperature is steady."

    monkeypatch.setattr(ollama_client, "generate", fake_generate)
    _configure_ollama(client)
    import_roast(client, tmp_path, "ethiopia", tags=["light"], green=500, roasted=430)
    import_roast(client, tmp_path, "brazil", tags=["dark"])

    started = client.post(
        "/api/analysis/insights", json={"group_by": "tag", "question": "Which is more consistent?", "q": ""}
    )
    assert started.status_code == 202
    job = _wait_for_insight(client, started.json()["id"])

    assert job["status"] == "ready" and job["text"] == "Your drop temperature is steady." and job["model"] == "test-model"
    assert seen["url"] == "http://ollama.test" and seen["model"] == "test-model"
    prompt = seen["prompt"]
    assert "Which is more consistent?" in prompt
    assert '"group":"light"' in prompt and '"group":"dark"' in prompt
    assert "ethiopia" in prompt and "drop_temp_c" in prompt
    assert "grouped by tag" in prompt
    assert seen["options"] == {"num_ctx": 8192}
    assert len(prompt) < 12000  # small enough for a local model's window


def test_insights_respect_the_filters_and_say_what_they_were(client, tmp_path, monkeypatch):
    from backend.app import ollama_client

    seen = {}

    async def fake_generate(url, model, prompt, options=None):
        seen["prompt"] = prompt
        return "ok"

    monkeypatch.setattr(ollama_client, "generate", fake_generate)
    _configure_ollama(client)
    import_roast(client, tmp_path, "keep", tags=["x"])
    import_roast(client, tmp_path, "skip", tags=["y"])
    job = _wait_for_insight(client, client.post("/api/analysis/insights", json={"tag": "x"}).json()["id"])
    assert job["status"] == "ready"
    assert "keep" in seen["prompt"] and "skip" not in seen["prompt"]
    assert "tag = x" in seen["prompt"]


def test_insights_report_failures_and_empty_selections(client, tmp_path, monkeypatch):
    from backend.app import ollama_client

    async def broken(url, model, prompt, options=None):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(ollama_client, "generate", broken)
    _configure_ollama(client)
    import_roast(client, tmp_path)
    failed = _wait_for_insight(client, client.post("/api/analysis/insights", json={}).json()["id"])
    assert failed["status"] == "failed" and "connection refused" in failed["error"]

    empty = _wait_for_insight(client, client.post("/api/analysis/insights", json={"tag": "nothing"}).json()["id"])
    assert empty["status"] == "failed" and "no finished roasts" in empty["error"]

    assert client.get("/api/analysis/insights/nope").status_code == 404
    assert client.post("/api/analysis/insights", json={"group_by": "bogus"}).status_code == 422


# -- a roast whose recording file is missing ---------------------------------------------------------


def _delete_recording(roast_id):
    import os
    from backend.app import storage

    os.remove(storage.get_roast_row(roast_id)["alog_path"])


def test_a_roast_with_a_missing_recording_gets_a_clear_message_not_a_crash(client, tmp_path):
    gone = import_roast(client, tmp_path, "gone")
    kept = import_roast(client, tmp_path, "kept")
    _delete_recording(gone)

    detail = client.get(f"/api/roasts/{gone}")
    assert detail.status_code == 404 and "recording file" in detail.json()["detail"] and "missing" in detail.json()["detail"]
    assert client.get(f"/api/roasts/{gone}/stats").status_code == 404  # the backstop covers the other endpoints too

    numbers = client.get(f"/api/analysis/roasts/{gone}")
    assert numbers.status_code == 404 and "missing" in numbers.json()["detail"]

    body = client.get("/api/analysis/table").json()
    assert [r["id"] for r in body["rows"]] == [kept] and body["missing_recording"] == 1
    assert client.get("/api/analysis/summary").json()["missing_recording"] == 1
    assert client.get(f"/api/roasts/{kept}").status_code == 200  # the others are unaffected


def test_an_unreadable_recording_is_reported_not_crashed_on(client, tmp_path):
    from backend.app import storage

    roast_id = import_roast(client, tmp_path)
    with open(storage.get_roast_row(roast_id)["alog_path"], "w") as f:
        f.write("this is not a roast log")
    resp = client.get(f"/api/roasts/{roast_id}")
    assert resp.status_code == 404 and "couldn't be read" in resp.json()["detail"]
    assert client.get("/api/analysis/table").json()["missing_recording"] == 1


# -- recordings recorded on another system ---------------------------------------------------------------


def test_a_roast_recorded_under_another_systems_path_is_still_found(client, tmp_path):
    from backend.app import storage

    roast_id = import_roast(client, tmp_path, "moved")
    # How a roast recorded on Windows looks to WSL (and the other way round): a path that isn't here.
    storage.update_roast(roast_id, alog_path="C:\\Users\\someone\\roast-telemetry\\backend\\data\\roasts\\" + roast_id + ".alog")

    assert client.get(f"/api/roasts/{roast_id}").status_code == 200
    assert [r["id"] for r in rows(client)] == [roast_id]
    assert client.get("/api/analysis/table").json()["missing_recording"] == 0
    assert storage.get_roast_row(roast_id)["alog_path"] == storage.alog_path_for(roast_id)  # found by its ID


def test_a_listed_roast_with_no_recording_says_so(client):
    from backend.app import storage
    from backend.app.roast_session.session import session_manager

    roast_id = client.post("/api/roasts", json={"title": "Interrupted", "mode": "simulator"}).json()["id"]
    client.post(f"/api/roasts/{roast_id}/stop")
    session_manager.sessions.pop(roast_id, None)  # as after a server restart
    storage.update_roast(roast_id, alog_path=None, status="aborted")  # the state after a server stop mid-roast
    resp = client.get(f"/api/roasts/{roast_id}")
    assert resp.status_code == 404 and "no recording" in resp.json()["detail"] and "interrupted" in resp.json()["detail"]
    assert client.get("/api/roasts/never-existed").json()["detail"].endswith("not found")


# -- beans descriptions from uploaded logs -------------------------------------------------------------

DESCRIPTION = (
    "Caturra\\nEthiopia\\nFarm: Finca La Maravilla, Mauricio Rosales\\nProcess Natural / Dry Process"
    "\\nVariety Caturra\\nAltitude: 1850m\\nSCA: 87.0\\nuuid: 94ee0778-f239-4457-91d6-d33153c17e6f"
)


def test_a_beans_description_is_split_into_a_name_and_details():
    from backend.app.beans_text import parse_beans_description

    for text in (DESCRIPTION, DESCRIPTION.replace("\\n", "\n")):  # escaped line breaks, and real ones
        parsed = parse_beans_description(text)
        assert parsed["name"] == "Caturra · Ethiopia"
        assert parsed["origin"] == "Ethiopia" and parsed["process"] == "Natural / Dry Process"
        assert parsed["variety"] == "Caturra" and parsed["altitude_m"] == 1850.0
        assert parsed["supplier"] == "Finca La Maravilla, Mauricio Rosales"
        assert "SCA: 87.0" in parsed["notes"] and "uuid: 94ee0778" in parsed["notes"]  # nothing is lost
        assert parsed["notes"].splitlines()[:3] == ["Caturra", "Ethiopia", "Farm: Finca La Maravilla, Mauricio Rosales"]
        assert "\\n" not in parsed["notes"] and parsed["notes"].count("\n") == 7  # one line each


def test_plain_names_and_escaped_accents_are_handled():
    from backend.app.beans_text import parse_beans_description

    assert parse_beans_description("Fake Beanz") == {"name": "Fake Beanz"}
    assert parse_beans_description("  ") == {"name": ""} and parse_beans_description(None) == {"name": ""}
    assert parse_beans_description("Red & Yellow Catua\\xed")["name"] == "Red & Yellow Catuaí"


def test_uploading_a_log_with_a_described_beans_field_makes_one_clean_record(client, tmp_path):
    from alog_playback.alog_io import roast_to_native_alog_dict, save_native_alog

    path = tmp_path / "described.alog"
    save_native_alog(
        str(path),
        roast_to_native_alog_dict(
            title="described", profile=[{"time_s": float(t), "bt": 100.0 + t, "et": 120.0 + t} for t in range(30)],
            events=[], notes=[], beans=DESCRIPTION, weight_green_g=None, weight_roasted_g=None,
            roastdate="2026-03-01T10:00:00+00:00",
        ),
    )
    first = client.post("/api/roasts/import", params={"path": str(path)}).json()
    second = client.post("/api/roasts/import", params={"path": str(path)}).json()  # the same description again
    beans = client.get("/api/beans").json()
    assert len(beans) == 1 and beans[0]["name"] == "Caturra · Ethiopia" and beans[0]["roast_count"] == 2
    assert beans[0]["altitude_m"] == 1850 and beans[0]["variety"] == "Caturra" and "SCA: 87.0" in beans[0]["notes"]
    assert client.get(f"/api/roasts/{first['id']}").json()["beans"] == "Caturra · Ethiopia"


def test_existing_messy_beans_records_are_cleaned_once(client, tmp_path):
    from backend.app import storage
    from backend.app.roast_session.session import session_manager

    messy = client.post("/api/beans", json={"name": DESCRIPTION}).json()
    duplicate = client.post("/api/beans", json={"name": DESCRIPTION.replace("Caturra\\nEthiopia", "Caturra\\nEthiopia")}).json()  # a second one with the same text
    fine = client.post("/api/beans", json={"name": "Fake Beanz"}).json()
    roast_id = import_roast(client, tmp_path)
    client.put(f"/api/roasts/{roast_id}/beans", json={"name": "Fake Beanz"})
    storage.update_roast(roast_id, bean_id=messy["id"], beans=DESCRIPTION)
    storage.set_schema_version(3)

    session_manager.clean_bean_records()
    names = sorted(b["name"] for b in client.get("/api/beans").json())
    assert names == ["Caturra · Ethiopia", "Fake Beanz"]  # the duplicate merged, the plain one untouched
    assert client.get(f"/api/roasts/{roast_id}").json()["beans"] == "Caturra · Ethiopia"
    kept = next(b for b in client.get("/api/beans").json() if b["name"] == "Caturra · Ethiopia")
    assert kept["origin"] == "Ethiopia" and kept["altitude_m"] == 1850 and kept["roast_count"] == 1
    assert storage.get_schema_version() == 4
    session_manager.clean_bean_records()  # runs once
    assert len(client.get("/api/beans").json()) == 2


def test_deleting_a_roast_removes_its_file_even_when_the_stored_path_is_from_another_system(client, tmp_path):
    import os
    from backend.app import storage

    roast_id = import_roast(client, tmp_path, "to delete")
    real = storage.alog_path_for(roast_id)
    assert os.path.exists(real)
    storage.update_roast(roast_id, alog_path="C:\\Users\\someone\\backend\\data\\roasts\\" + roast_id + ".alog")  # a path from the other system

    assert client.delete(f"/api/roasts/{roast_id}").status_code == 204
    assert not os.path.exists(real)  # no file left behind
    assert storage.get_roast_row(roast_id) is None
