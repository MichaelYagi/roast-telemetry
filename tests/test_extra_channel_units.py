"""An imported log's extra channels aren't all temperatures ("Drum Speed",
"Fan Speed" are percentages), and its saved file can be a Fahrenheit one --
what the API reports and what the downloads contain."""
from __future__ import annotations

import csv
import io
import json

from alog_playback.alog_io import save_native_alog


def _import_fahrenheit_log(client, tmp_path) -> str:
    n = 5
    path = tmp_path / "third_party.alog"
    save_native_alog(str(path), {
        "mode": "F", "title": "Third party", "timex": [0.0, 1.0, 2.0, 3.0, 4.0],
        "temp1": [400.0] * n, "temp2": [300.0, 301.0, 302.0, 303.0, 304.0], "timeindex": [1, 0, 0, 0, 0, 0, 3, 0],
        "extraname1": ["Drum Heat", "Fan Speed"], "extratemp1": [[340.0] * n, [65.0] * n],
        "extraname2": ["Drum Speed", "SV"], "extratemp2": [[55.0] * n, [470.0] * n],
        "extratimex": [[0.0, 1.0, 2.0, 3.0, 4.0]] * 2,
    })
    return client.post("/api/v1/roasts/import", params={"path": str(path)}).json()["id"]


def _use_fahrenheit(client) -> None:
    client.put("/api/v1/settings", json={"ollama_url": None, "ollama_model": None, "temperature_unit": "f"})


def test_roast_says_what_each_extra_channel_measures(client, tmp_path):
    roast = client.get(f"/api/v1/roasts/{_import_fahrenheit_log(client, tmp_path)}").json()

    assert roast["extra_units"] == {"Drum Heat": "temp", "Fan Speed": "percent", "Drum Speed": "percent", "SV": "temp"}
    extra = roast["profile"][2]["extra"]
    assert extra["Fan Speed"] == 65.0 and extra["Drum Speed"] == 55.0
    assert round(extra["Drum Heat"], 2) == 171.11  # 340 F, stored as Celsius like every temperature


def test_csv_does_not_convert_or_mislabel_percent_channels(client, tmp_path):
    roast_id = _import_fahrenheit_log(client, tmp_path)
    _use_fahrenheit(client)

    rows = list(csv.DictReader(io.StringIO(client.get(f"/api/v1/roasts/{roast_id}/csv").text)))

    assert float(rows[2]["extra_Fan Speed_pct"]) == 65.0
    assert float(rows[2]["extra_Drum Speed_pct"]) == 55.0
    assert round(float(rows[2]["extra_Drum Heat_f"]), 1) == 340.0
    assert round(float(rows[2]["extra_SV_f"]), 1) == 470.0


def test_downloading_a_fahrenheit_log_in_fahrenheit_does_not_convert_it_twice(client, tmp_path):
    roast_id = _import_fahrenheit_log(client, tmp_path)
    _use_fahrenheit(client)

    data = json.loads(client.get(f"/api/v1/roasts/{roast_id}/json").text)

    assert data["mode"] == "F"
    assert data["temp2"][:2] == [300.0, 301.0]  # not 572
    assert data["extratemp1"][1][0] == 65.0


def test_editing_an_imported_log_keeps_all_its_extra_channels(client, tmp_path):
    roast_id = _import_fahrenheit_log(client, tmp_path)
    before = client.get(f"/api/v1/roasts/{roast_id}").json()

    # Any edit rewrites the saved file from what was read out of it.
    assert client.post(f"/api/v1/roasts/{roast_id}/weight", params={"grams": 250}).status_code == 200

    after = client.get(f"/api/v1/roasts/{roast_id}").json()
    assert after["extra_units"] == before["extra_units"]
    assert after["profile"][2]["extra"].keys() == before["profile"][2]["extra"].keys()
    for label, value in before["profile"][2]["extra"].items():
        assert round(after["profile"][2]["extra"][label], 6) == round(value, 6), label


# -- every chart color is saved in one place --------------------------------


def _save_colors(client, colors: dict) -> dict:
    settings = client.get("/api/v1/settings").json()
    return client.put("/api/v1/settings", json={**settings, "breakout_panel_colors": colors}).json()


def test_settings_keep_a_color_for_every_part_of_the_chart(client):
    wanted = {
        "bt": "#112233",                 # a readout item, as before
        "damper": "#223344",             # the Damper line
        "bg_bt": "#334455",              # a background roast's BT
        "event:FC_START": "#445566",     # a milestone marker
        "phase:maillard": "#556677",     # a phase band
        "extra:Drum Heat": "#667788",    # an extra channel, by name
    }
    saved = _save_colors(client, {**wanted, "nonsense": "#000000", "event:NOPE": "#000000", "extra:": "#000000",
                                  "phase:dry": "red"})

    assert saved["breakout_panel_colors"] == wanted
    assert client.get("/api/v1/settings").json()["breakout_panel_colors"] == wanted


def test_settings_list_every_extra_channel_seen(client, tmp_path):
    assert client.get("/api/v1/settings").json()["extra_channels"] == []

    _import_fahrenheit_log(client, tmp_path)

    names = ["Drum Heat", "Drum Speed", "Fan Speed", "SV"]
    assert client.get("/api/v1/settings").json()["extra_channels"] == names
    # Saving settings neither clears the list nor lets a client overwrite it.
    saved = client.put("/api/v1/settings", json={**client.get("/api/v1/settings").json(), "extra_channels": ["x"]}).json()
    assert saved["extra_channels"] == names
    assert client.get("/api/v1/settings").json()["extra_channels"] == names
