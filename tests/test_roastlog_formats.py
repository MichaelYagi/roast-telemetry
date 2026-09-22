"""Export/import in the three formats besides .alog: JSON (the same fields,
valid-JSON syntax -- see alog_playback/alog_io.py), and the tab-separated
"roast log" table and its Excel twin (alog_playback/roastlog.py)."""
from __future__ import annotations

import io
import json
import zipfile

from alog_playback.roastlog import parse_roastlog_csv, parse_roastlog_xlsx, save_roastlog_csv, save_roastlog_xlsx


def _ev(kind, t):
    return {"id": kind, "type": kind, "time_s": float(t), "label": kind, "value": None}


def make_roast(client, tmp_path, title="Roast", drop_at=600):
    from alog_playback.alog_io import roast_to_native_alog_dict, save_native_alog

    profile = [{"time_s": float(t), "bt": 100 + 0.2 * t, "et": 150 + 0.15 * t} for t in range(0, drop_at + 30)]
    events = [_ev("CHARGE", 0), _ev("TURNING_POINT", 60), _ev("DRY_END", 240), _ev("FC_START", 480), _ev("DROP", drop_at)]
    path = tmp_path / "r.alog"
    save_native_alog(str(path), roast_to_native_alog_dict(title=title, profile=profile, events=events, notes=[], beans="Beans", weight_green_g=500, weight_roasted_g=430, roastdate="2026-03-01T10:00:00+00:00"))
    return client.post("/api/roasts/import", params={"path": str(path), "title": title}).json()["id"]


# -- JSON ---------------------------------------------------------------------------


def test_json_download_is_the_same_data_as_alog_as_valid_json(client, tmp_path):
    roast_id = make_roast(client, tmp_path)
    resp = client.get(f"/api/roasts/{roast_id}/json")
    assert resp.status_code == 200 and resp.headers["content-type"] == "application/json"
    data = json.loads(resp.content)  # must be strict JSON, not the .alog Python-literal syntax
    assert data["title"] == "Roast" and data["temp2"][0] == 100.0 and len(data["timex"]) > 500
    assert "attachment" in resp.headers["content-disposition"] and ".json" in resp.headers["content-disposition"]


def test_a_downloaded_json_file_imports_back_to_the_same_roast(client, tmp_path):
    roast_id = make_roast(client, tmp_path, title="Round trip")
    body = client.get(f"/api/roasts/{roast_id}/json").content
    reimported = client.post("/api/roasts/import-upload", params={"filename": "roast.json"}, content=body)
    assert reimported.status_code == 201, reimported.text
    detail = client.get(f"/api/roasts/{reimported.json()['id']}").json()
    original = client.get(f"/api/roasts/{roast_id}").json()
    assert detail["title"] == "Round trip" and detail["beans"] == "Beans"
    assert len(detail["profile"]) == len(original["profile"])  # the raw stored file is re-served, not rebuilt
    assert [e["type"] for e in detail["events"]] == [e["type"] for e in original["events"]]


def test_the_users_own_real_json_export_imports_correctly(client):
    """A real example file (the user's own roast), not a fixture built for this
    test -- confirms the shape actually round-trips through another program's
    export, not just this app's own."""
    import pathlib

    path = pathlib.Path("/mnt/c/Users/Michael/Downloads/alog/test.json")
    if not path.exists():
        import pytest

        pytest.skip("the real example file isn't present in this environment")
    resp = client.post("/api/roasts/import-upload", params={"filename": "test.json"}, content=path.read_bytes())
    assert resp.status_code == 201, resp.text
    detail = client.get(f"/api/roasts/{resp.json()['id']}").json()
    assert len(detail["profile"]) == 1037
    types = [e["type"] for e in detail["events"]]
    for milestone in ("CHARGE", "TURNING_POINT", "DRY_END", "FC_START", "DROP"):
        assert milestone in types
    assert "Catua" in detail["beans"]  # the accented beans description, unescaped


# -- roast-log CSV/TSV ----------------------------------------------------------------


def test_csv_download_is_tab_separated_with_a_metadata_line(client, tmp_path):
    roast_id = make_roast(client, tmp_path)
    resp = client.get(f"/api/roasts/{roast_id}/roastlog.csv")
    assert resp.status_code == 200
    lines = resp.content.decode("utf-8").splitlines()
    assert lines[0].startswith("Date:") and "CHARGE:" in lines[0] and "\t" in lines[0]
    assert lines[1].split("\t")[:4] == ["Time1", "Time2", "ET", "BT"]
    assert "_log.csv" in resp.headers["content-disposition"]


def test_a_downloaded_csv_imports_back_with_milestones_and_readings_intact(client, tmp_path):
    roast_id = make_roast(client, tmp_path, title="CSV roast", drop_at=500)
    body = client.get(f"/api/roasts/{roast_id}/roastlog.csv").content
    reimported = client.post("/api/roasts/import-upload", params={"filename": "roast.csv"}, content=body)
    assert reimported.status_code == 201, reimported.text
    detail = client.get(f"/api/roasts/{reimported.json()['id']}").json()
    assert len(detail["profile"]) > 500
    types = {e["type"] for e in detail["events"]}
    assert {"CHARGE", "TURNING_POINT", "DRY_END", "FC_START", "DROP"} <= types
    charge = next(e for e in detail["events"] if e["type"] == "CHARGE")
    assert abs(charge["time_s"]) <= 1.0
    # No beans/weights in this shape -- an honest gap, not a crash.
    assert detail["beans"] is None and detail["weight_green_g"] is None


def test_a_tsv_extension_is_accepted_the_same_way(client):
    text = "Date:01.01.2026\tUnit:C\tCHARGE:00:00\tTP:\tDRYe:\tFCs:\tFCe:\tSCs:\tSCe:\tDROP:\tCOOL:\tTime:00:10\nTime1\tTime2\tET\tBT\tEvent\nName" "\n00:00\t00:00\t150\t100\tCHARGE\n00:05\t00:05\t152\t110\t\n00:10\t00:10\t154\t120\t\n"
    resp = client.post("/api/roasts/import-upload", params={"filename": "roast.tsv"}, content=text.encode())
    assert resp.status_code == 201, resp.text
    assert len(client.get(f"/api/roasts/{resp.json()['id']}").json()["profile"]) in (3, 4)


def test_a_comma_csv_is_also_accepted(client):
    text = "Date:01.01.2026,Unit:C,CHARGE:00:00,Time:00:10\nTime1,Time2,ET,BT,Event\n00:00,00:00,150,100,CHARGE\n00:05,00:05,152,110,\n"
    resp = client.post("/api/roasts/import-upload", params={"filename": "roast.csv"}, content=text.encode())
    assert resp.status_code == 201, resp.text


def test_the_users_own_real_csv_export_imports_correctly(client):
    import pathlib

    path = pathlib.Path("/mnt/c/Users/Michael/Downloads/alog/test.csv")
    if not path.exists():
        import pytest

        pytest.skip("the real example file isn't present in this environment")
    resp = client.post("/api/roasts/import-upload", params={"filename": "test.csv"}, content=path.read_bytes())
    assert resp.status_code == 201, resp.text
    detail = client.get(f"/api/roasts/{resp.json()['id']}").json()
    # Trimmed to Charge (this app's own convention -- see import_table's own
    # docstring): the file's ~786 s of real pre-charge history isn't kept.
    assert 500 <= len(detail["profile"]) <= 600
    types = [e["type"] for e in detail["events"]]
    for milestone in ("CHARGE", "TURNING_POINT", "DRY_END", "FC_START", "DROP"):
        assert milestone in types
    charge = next(e for e in detail["events"] if e["type"] == "CHARGE")
    assert abs(charge["time_s"]) < 2.0  # Charge is (approximately) the new zero
    assert abs(charge["value"] - 199.6) < 0.5  # Fahrenheit converted to Celsius correctly


def test_a_file_with_no_recognizable_columns_is_refused_plainly(client):
    resp = client.post("/api/roasts/import-upload", params={"filename": "spreadsheet.csv"}, content=b"a,b,c\n1,2,3\n")
    assert resp.status_code == 400
    assert "roast log table" in resp.json()["detail"]


# -- roast-log Excel --------------------------------------------------------------------


def test_xlsx_download_and_round_trip(client, tmp_path):
    roast_id = make_roast(client, tmp_path, title="Excel roast", drop_at=400)
    resp = client.get(f"/api/roasts/{roast_id}/xlsx")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert ".xlsx" in resp.headers["content-disposition"]

    reimported = client.post("/api/roasts/import-upload", params={"filename": "roast.xlsx"}, content=resp.content)
    assert reimported.status_code == 201, reimported.text
    detail = client.get(f"/api/roasts/{reimported.json()['id']}").json()
    assert len(detail["profile"]) > 400
    assert {"CHARGE", "TURNING_POINT", "DRY_END", "FC_START", "DROP"} <= {e["type"] for e in detail["events"]}


def test_an_xlsx_with_no_recognizable_sheet_is_refused_plainly(client):
    from openpyxl import Workbook

    wb = Workbook()
    wb.active.append(["some", "other", "spreadsheet"])
    buf = io.BytesIO()
    wb.save(buf)
    resp = client.post("/api/roasts/import-upload", params={"filename": "unrelated.xlsx"}, content=buf.getvalue())
    assert resp.status_code == 400
    assert "spreadsheet" in resp.json()["detail"]


def test_the_users_own_real_xlsx_export_imports_correctly(client):
    import pathlib

    path = pathlib.Path("/mnt/c/Users/Michael/Downloads/alog/test.xlsx")
    if not path.exists():
        import pytest

        pytest.skip("the real example file isn't present in this environment")
    resp = client.post("/api/roasts/import-upload", params={"filename": "test.xlsx"}, content=path.read_bytes())
    assert resp.status_code == 201, resp.text
    detail = client.get(f"/api/roasts/{resp.json()['id']}").json()
    assert len(detail["profile"]) > 100


# -- server-path /import for all three ------------------------------------------------


def test_server_path_import_dispatches_by_extension(client, tmp_path):
    roast_id = make_roast(client, tmp_path)
    for suffix, getter in ((".json", "json"), ("_log.csv", "roastlog.csv"), (".xlsx", "xlsx")):
        body = client.get(f"/api/roasts/{roast_id}/{getter}").content
        path = tmp_path / f"exported{suffix}"
        path.write_bytes(body)
        resp = client.post("/api/roasts/import", params={"path": str(path)})
        assert resp.status_code == 201, (suffix, resp.text)
        assert len(client.get(f"/api/roasts/{resp.json()['id']}").json()["profile"]) > 500


# -- roast_formats module directly (no server needed) -----------------------------------


def test_module_round_trip_preserves_every_reading():
    profile = [{"time_s": float(t), "bt": 100 + 0.3 * t, "et": 150 + 0.2 * t, "ror_bt": 12.5, "heater_pct": 60.0, "fan_pct": 40.0, "drum_speed_pct": 70.0, "burner_sv_c": 210.0} for t in range(-5, 60)]
    events = [_ev("CHARGE", 0), _ev("TURNING_POINT", 10), _ev("DROP", 50)]
    for unit in ("c", "f"):
        text = save_roastlog_csv(profile=profile, events=events, temperature_unit=unit)
        back = parse_roastlog_csv(text)
        assert back["profile"][0]["time_s"] == -5.0 and back["profile"][-1]["time_s"] == 59.0
        for orig, got in zip(profile, back["profile"]):
            assert abs(orig["bt"] - got["bt"]) < 0.05
            assert abs(orig["heater_pct"] - got["heater_pct"]) < 0.05
            assert abs(orig["fan_pct"] - got["fan_pct"]) < 0.05
            assert abs(orig["drum_speed_pct"] - got["drum_speed_pct"]) < 0.05

        xbytes = save_roastlog_xlsx(profile=profile, events=events, temperature_unit=unit)
        backx = parse_roastlog_xlsx(xbytes)
        assert backx["profile"][0]["time_s"] == -5.0 and backx["profile"][-1]["time_s"] == 59.0


# -- bulk zip export with extra formats -------------------------------------------------------


def test_zip_export_can_include_extra_formats(client, tmp_path):
    make_roast(client, tmp_path, title="one")
    make_roast(client, tmp_path, title="two")
    resp = client.get("/api/analysis/export.zip", params={"formats": "json,roastlog_csv,xlsx"})
    assert resp.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(resp.content)).namelist()
    assert "summary.csv" not in names
    for folder, suffix, count in (("alog", ".alog", 2), ("json", ".json", 2), ("roastlog_csv", "_log.csv", 2), ("xlsx", ".xlsx", 2)):
        matched = [n for n in names if n.startswith(f"{folder}/") and n.endswith(suffix)]
        assert len(matched) == count, (folder, names)


def test_zip_export_without_formats_is_unchanged(client, tmp_path):
    make_roast(client, tmp_path, title="one")
    names = zipfile.ZipFile(io.BytesIO(client.get("/api/analysis/export.zip").content)).namelist()
    assert "summary.csv" not in names
    assert any(n.startswith("alog/") for n in names)
    assert not any(n.startswith(("json/", "roastlog_csv/", "xlsx/")) for n in names)


def test_zip_export_rejects_an_unknown_format(client, tmp_path):
    make_roast(client, tmp_path)
    resp = client.get("/api/analysis/export.zip", params={"formats": "pdf"})
    assert resp.status_code == 422 and "pdf" in resp.json()["detail"]


def test_zip_export_extra_formats_open_correctly(client, tmp_path):
    make_roast(client, tmp_path, title="round trip", drop_at=400)
    resp = client.get("/api/analysis/export.zip", params={"formats": "xlsx"})
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    xlsx_name = next(n for n in zf.namelist() if n.startswith("xlsx/"))
    reimported = client.post("/api/roasts/import-upload", params={"filename": "r.xlsx"}, content=zf.read(xlsx_name))
    assert reimported.status_code == 201, reimported.text
    assert len(client.get(f"/api/roasts/{reimported.json()['id']}").json()["profile"]) > 400
