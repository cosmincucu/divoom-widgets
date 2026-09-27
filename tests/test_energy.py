from divoom_widgets.widgets import energy


def _fake_states(states):
    """Return a _get_state stand-in backed by an {entity: (value, unit)} map."""
    return lambda entity_id, token: states.get(entity_id, (None, ""))


def _configure(monkeypatch, **entities):
    monkeypatch.setattr(energy.config, "HA_ENTITY_LOAD", entities.get("load", "sensor.load"))
    monkeypatch.setattr(energy.config, "HA_ENTITY_SOLAR", entities.get("solar", "sensor.solar"))
    monkeypatch.setattr(energy.config, "HA_ENTITY_SOC", entities.get("soc", "sensor.soc"))
    monkeypatch.setattr(energy, "_token", lambda: "tok")


def test_to_kw_conversions(monkeypatch):
    assert energy._to_kw(0.561, "kW") == 0.561
    assert energy._to_kw(561.0, "W") == 0.561
    assert energy._to_kw(None, "kW") is None
    # Missing unit falls back to HA_POWER_UNIT.
    monkeypatch.setattr(energy.config, "HA_POWER_UNIT", "W")
    assert energy._to_kw(250.0, "") == 0.25
    monkeypatch.setattr(energy.config, "HA_POWER_UNIT", "kW")
    assert energy._to_kw(0.25, "") == 0.25


def test_fetch_happy_path(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(energy, "_get_state", _fake_states({
        "sensor.load": (0.561, "kW"),
        "sensor.solar": (295.0, "W"),
        "sensor.soc": (86.0, "%"),
    }))
    out = energy.fetch()
    assert out["ok"] is True
    assert out["load_kw"] == 0.561
    assert out["solar_kw"] == 0.295
    assert out["soc_pct"] == 86.0


def test_fetch_partial_failure_keeps_ok(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(energy, "_get_state", _fake_states({
        "sensor.soc": (86.0, "%"),
    }))
    out = energy.fetch()
    assert out["ok"] is True
    assert out["load_kw"] is None
    assert out["soc_pct"] == 86.0


def test_fetch_all_failed_is_error(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(energy, "_get_state", _fake_states({}))
    out = energy.fetch()
    assert out["ok"] is False
    assert "sensors" in out["error"]


def test_fetch_without_token_is_error(monkeypatch):
    monkeypatch.setattr(energy, "_token", lambda: "")
    out = energy.fetch()
    assert out["ok"] is False
    assert "token" in out["error"]


def test_render_full_and_partial_and_error():
    full = energy.render({"ok": True, "load_kw": 0.56, "solar_kw": 3.2, "soc_pct": 86.0})
    assert full.size == (128, 128) and full.mode == "RGB"
    partial = energy.render({"ok": True, "load_kw": None, "solar_kw": None, "soc_pct": 12.0})
    assert partial.size == (128, 128)
    error = energy.render({"ok": False, "error": "all sensors failed"})
    assert error.size == (128, 128)


def test_summary_formats():
    assert energy.summary(
        {"ok": True, "load_kw": 0.561, "solar_kw": None, "soc_pct": 86.0}
    ) == "NRG home 0.56kW / solar --kW / batt 86%"
    assert energy.summary({"ok": False, "error": "no HA token"}).startswith("NRG err")
