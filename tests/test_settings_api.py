"""PUT/GET /api/v1/settings -- exercised through the real FastAPI app (via the
`client` fixture's isolated DB) rather than calling storage directly, since
the interesting behavior here (silently dropping unknown panel keys) lives
in the route handler, not in storage.py."""
from __future__ import annotations


def test_health(client):
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    # Not pinning an exact string -- varies by whatever box the suite runs
    # on (Linux, Linux (WSL2), macOS, Windows) -- just that it's populated.
    assert body["platform"]


def test_get_settings_defaults(client):
    resp = client.get("/api/v1/settings")
    assert resp.status_code == 200
    assert resp.json() == {
        "ollama_url": None, "ollama_model": None, "broken_out_panels": [], "breakout_panel_colors": {},
        "small_readout_panels": ["et", "bt", "dt", "ror_bt", "ror_et"], "temperature_unit": "c",
        "vertical_control_layout": [["drum_speed_pct"], ["fan_pct"], ["heater_pct"]],
        "vertical_control_arrows": {},
        "chart_series_visible": {},
        "history_page_size": 100,
        "max_compare": 20,
        "control": {
            "heater_max_pct": 100, "fan_min_pct": 0, "drum_min_pct": 0,
            "safe_fan_pct": 100, "client_watchdog_s": 0, "safety_disabled": False,
        },
    }


def test_put_settings_drops_unknown_panel_keys(client):
    resp = client.put("/api/v1/settings", json={
        "ollama_url": "http://localhost:11434",
        "ollama_model": "llama3.1",
        "broken_out_panels": ["bt", "not_a_real_panel_key", "fan", "also_bogus"],
    })

    assert resp.status_code == 200
    assert resp.json()["broken_out_panels"] == ["bt", "fan"]

    # And it's what actually got persisted, not just what the PUT echoed back.
    get_resp = client.get("/api/v1/settings")
    assert get_resp.json()["broken_out_panels"] == ["bt", "fan"]


def test_put_settings_drops_invalid_panel_colors(client):
    resp = client.put("/api/v1/settings", json={
        "ollama_url": None,
        "ollama_model": None,
        "broken_out_panels": [],
        "breakout_panel_colors": {
            "bt": "#112233",  # valid
            "not_a_real_panel_key": "#445566",  # unknown key
            "fan": "not-a-hex-color",  # malformed value
            "drum": "#zzzzzz",  # right shape, invalid hex digits
        },
    })

    assert resp.status_code == 200
    assert resp.json()["breakout_panel_colors"] == {"bt": "#112233"}

    get_resp = client.get("/api/v1/settings")
    assert get_resp.json()["breakout_panel_colors"] == {"bt": "#112233"}


def test_put_settings_filters_small_readout_panels_independently(client):
    resp = client.put("/api/v1/settings", json={
        "ollama_url": None,
        "ollama_model": None,
        "broken_out_panels": ["bt"],
        "small_readout_panels": ["heater", "not_a_real_panel_key", "fan"],
    })

    assert resp.status_code == 200
    body = resp.json()
    assert body["small_readout_panels"] == ["heater", "fan"]
    # Untouched by small_readout_panels above -- an independent enabled
    # list, not a variant of broken_out_panels.
    assert body["broken_out_panels"] == ["bt"]

    get_resp = client.get("/api/v1/settings")
    assert get_resp.json()["small_readout_panels"] == ["heater", "fan"]


def test_breakout_panel_colors_apply_to_both_big_and_small_readouts(client):
    # Colors are a property of the item, not the panel -- one shared map
    # (breakout_panel_colors), used regardless of which enabled-list(s) a
    # key appears in.
    resp = client.put("/api/v1/settings", json={
        "ollama_url": None,
        "ollama_model": None,
        "broken_out_panels": ["bt"],
        "small_readout_panels": ["bt"],
        "breakout_panel_colors": {"bt": "#112233"},
    })

    assert resp.status_code == 200
    body = resp.json()
    assert body["breakout_panel_colors"] == {"bt": "#112233"}
    assert "small_readout_colors" not in body


def test_put_settings_preserves_panel_order(client):
    resp = client.put("/api/v1/settings", json={
        "ollama_url": None,
        "ollama_model": None,
        "broken_out_panels": ["drum", "bt", "et"],
    })
    assert resp.json()["broken_out_panels"] == ["drum", "bt", "et"]


def test_put_settings_saves_temperature_unit(client):
    resp = client.put("/api/v1/settings", json={"ollama_url": None, "ollama_model": None, "temperature_unit": "f"})
    assert resp.json()["temperature_unit"] == "f"
    assert client.get("/api/v1/settings").json()["temperature_unit"] == "f"


def test_put_settings_bogus_temperature_unit_falls_back_to_celsius(client):
    # Same tolerant-instead-of-erroring convention as the panel/color
    # filtering above -- an invalid value falls back to the default
    # rather than failing the whole save.
    resp = client.put("/api/v1/settings", json={"ollama_url": None, "ollama_model": None, "temperature_unit": "kelvin"})
    assert resp.json()["temperature_unit"] == "c"


def test_put_settings_saves_history_page_size(client):
    resp = client.put("/api/v1/settings", json={"ollama_url": None, "ollama_model": None, "history_page_size": 250})
    assert resp.json()["history_page_size"] == 250
    assert client.get("/api/v1/settings").json()["history_page_size"] == 250


def test_put_settings_clamps_history_page_size(client):
    resp = client.put("/api/v1/settings", json={"ollama_url": None, "ollama_model": None, "history_page_size": 5})
    assert resp.json()["history_page_size"] == 10

    resp = client.put("/api/v1/settings", json={"ollama_url": None, "ollama_model": None, "history_page_size": 9999})
    assert resp.json()["history_page_size"] == 500


def test_put_settings_saves_max_compare(client):
    resp = client.put("/api/v1/settings", json={"ollama_url": None, "ollama_model": None, "max_compare": 500})
    assert resp.json()["max_compare"] == 500
    assert client.get("/api/v1/settings").json()["max_compare"] == 500


def test_put_settings_clamps_max_compare(client):
    resp = client.put("/api/v1/settings", json={"ollama_url": None, "ollama_model": None, "max_compare": 2})
    assert resp.json()["max_compare"] == 3

    resp = client.put("/api/v1/settings", json={"ollama_url": None, "ollama_model": None, "max_compare": 10**9})
    assert resp.json()["max_compare"] == 100000


def test_put_settings_drops_unknown_vertical_control_keys(client):
    resp = client.put("/api/v1/settings", json={
        "ollama_url": None,
        "ollama_model": None,
        "vertical_control_layout": [["drum_speed_pct", "not_a_real_channel"], ["bogus"], ["burner_sv_c"]],
    })

    assert resp.status_code == 200
    # The bogus-only group disappears entirely (empty after filtering);
    # the mixed group keeps just its valid member.
    assert resp.json()["vertical_control_layout"] == [["drum_speed_pct"], ["burner_sv_c"]]


def test_put_settings_dedupes_vertical_control_layout_across_groups(client):
    # A channel appearing twice would render two drag-independent copies
    # of the same slider -- de-duped across the whole layout, not just
    # within one group.
    resp = client.put("/api/v1/settings", json={
        "ollama_url": None,
        "ollama_model": None,
        "vertical_control_layout": [["heater_pct"], ["heater_pct", "fan_pct"]],
    })

    assert resp.json()["vertical_control_layout"] == [["heater_pct"], ["fan_pct"]]


def test_put_settings_filters_vertical_control_arrows(client):
    resp = client.put("/api/v1/settings", json={
        "ollama_url": None,
        "ollama_model": None,
        "vertical_control_arrows": {"heater_pct": 5, "not_a_real_channel": 5},
    })

    assert resp.json()["vertical_control_arrows"] == {"heater_pct": 5}


def test_put_settings_drops_non_positive_vertical_control_arrows(client):
    # A step of 0 (or negative) means "off", same convention as a missing
    # key -- _filter_arrows used to just flatten every value to bool(v)
    # (which would keep a real step number but silently lose it), now it
    # has to actually drop the key instead.
    resp = client.put("/api/v1/settings", json={
        "ollama_url": None,
        "ollama_model": None,
        "vertical_control_arrows": {"heater_pct": 5, "fan_pct": 0},
    })

    assert resp.json()["vertical_control_arrows"] == {"heater_pct": 5}


def test_put_settings_filters_chart_series_visible(client):
    resp = client.put("/api/v1/settings", json={
        "ollama_url": None,
        "ollama_model": None,
        "chart_series_visible": {"DT": True, "Damper": False, "not_a_real_series": True},
    })

    assert resp.json()["chart_series_visible"] == {"DT": True, "Damper": False}


def test_chart_series_visible_round_trips_through_a_second_get(client):
    client.put("/api/v1/settings", json={
        "ollama_url": None, "ollama_model": None, "chart_series_visible": {"ROR_ET": True},
    })

    resp = client.get("/api/v1/settings")
    assert resp.json()["chart_series_visible"] == {"ROR_ET": True}


# -- safety_disabled: logs only the on/off flip, never a would-be trip ---------------


def test_enabling_safety_disabled_logs_the_toggle(client):
    resp = client.put("/api/v1/settings", json={
        "ollama_url": None, "ollama_model": None,
        "control": {"heater_max_pct": 100, "fan_min_pct": 0, "drum_min_pct": 0, "safe_fan_pct": 100, "client_watchdog_s": 0, "safety_disabled": True},
    })
    assert resp.status_code == 200
    assert resp.json()["control"]["safety_disabled"] is True

    entries = client.get("/api/v1/activity", params={"category": "safety", "action": "safety_disabled"}).json()
    assert len(entries) == 1
    assert "disabled" in entries[0]["message"].lower()
    assert entries[0]["username"] == "test-admin"


def test_saving_settings_again_with_the_same_value_does_not_log_again(client):
    control = {"heater_max_pct": 100, "fan_min_pct": 0, "drum_min_pct": 0, "safe_fan_pct": 100, "client_watchdog_s": 0, "safety_disabled": True}
    client.put("/api/v1/settings", json={"ollama_url": None, "ollama_model": None, "control": control})
    # A second, unrelated save (still safety_disabled=True, nothing changed
    # about it) must not add a second "disabled" entry -- only the flip
    # itself is logged, not every settings save that happens to already
    # have it on.
    client.put("/api/v1/settings", json={"ollama_url": "http://x", "ollama_model": None, "control": control})

    entries = client.get("/api/v1/activity", params={"category": "safety", "action": "safety_disabled"}).json()
    assert len(entries) == 1


def test_disabling_it_again_logs_safety_enabled(client):
    on = {"heater_max_pct": 100, "fan_min_pct": 0, "drum_min_pct": 0, "safe_fan_pct": 100, "client_watchdog_s": 0, "safety_disabled": True}
    off = {**on, "safety_disabled": False}
    client.put("/api/v1/settings", json={"ollama_url": None, "ollama_model": None, "control": on})
    client.put("/api/v1/settings", json={"ollama_url": None, "ollama_model": None, "control": off})

    entries = client.get("/api/v1/activity", params={"category": "safety"}).json()
    actions = sorted(e["action"] for e in entries)
    assert actions == ["safety_disabled", "safety_enabled"]
