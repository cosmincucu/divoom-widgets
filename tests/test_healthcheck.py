import time

from divoom_widgets import healthcheck


class FakeResp:
    def __init__(self, status):
        self.status_code = status


def test_heartbeat_age_missing_file(monkeypatch, tmp_path):
    monkeypatch.setattr(healthcheck.config, "HEARTBEAT_FILE", str(tmp_path / "nope"))
    assert healthcheck.heartbeat_age() is None


def test_heartbeat_age_disabled(monkeypatch):
    monkeypatch.setattr(healthcheck.config, "HEARTBEAT_FILE", "")
    assert healthcheck.heartbeat_age() is None


def test_heartbeat_age_fresh(monkeypatch, tmp_path):
    beat = tmp_path / "heartbeat"
    beat.write_text("now")
    monkeypatch.setattr(healthcheck.config, "HEARTBEAT_FILE", str(beat))
    assert healthcheck.heartbeat_age() < 5


def test_device_responds(monkeypatch):
    monkeypatch.setattr(healthcheck.requests, "get", lambda url, timeout: FakeResp(200))
    assert healthcheck.device_responds("1.2.3.4") is True

    def boom(url, timeout):
        raise healthcheck.requests.RequestException("no route")

    monkeypatch.setattr(healthcheck.requests, "get", boom)
    assert healthcheck.device_responds("1.2.3.4") is False


def _setup(monkeypatch, tmp_path, age_s, device_ok):
    beat = tmp_path / "heartbeat"
    beat.write_text("x")
    import os
    os.utime(beat, (time.time() - age_s, time.time() - age_s))
    monkeypatch.setattr(healthcheck.config, "HEARTBEAT_FILE", str(beat))
    monkeypatch.setattr(healthcheck.config, "HEARTBEAT_MAX_AGE_SECONDS", 100)
    monkeypatch.setattr(healthcheck, "device_responds", lambda host, timeout=5: device_ok)


def test_main_healthy(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, age_s=10, device_ok=True)
    assert healthcheck.main() == 0


def test_main_unhealthy_when_heartbeat_stale(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, age_s=500, device_ok=True)
    assert healthcheck.main() == 1


def test_main_unhealthy_when_device_silent(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, age_s=10, device_ok=False)
    assert healthcheck.main() == 1


def test_main_unhealthy_without_heartbeat(monkeypatch, tmp_path):
    monkeypatch.setattr(healthcheck.config, "HEARTBEAT_FILE", str(tmp_path / "absent"))
    monkeypatch.setattr(healthcheck, "device_responds", lambda host, timeout=5: True)
    assert healthcheck.main() == 1


def test_max_interval_uses_slowest_widget(monkeypatch):
    monkeypatch.setattr(healthcheck.config, "WIDGETS", "claude_usage:3,energy:2")
    monkeypatch.setattr(healthcheck.config, "UPDATE_INTERVAL_SECONDS", 150)
    monkeypatch.setattr(healthcheck.config, "ENERGY_INTERVAL_SECONDS", 30)
    assert healthcheck._max_interval() == 150


def test_max_interval_ignores_unknown_widget(monkeypatch):
    monkeypatch.setattr(healthcheck.config, "WIDGETS", "bogus:1,energy:2")
    monkeypatch.setattr(healthcheck.config, "ENERGY_INTERVAL_SECONDS", 45)
    assert healthcheck._max_interval() == 45
