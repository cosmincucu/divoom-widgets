import base64

from PIL import Image

from divoom_widgets.device import TimesGate


def test_lcd_array_targets_and_clamps():
    assert TimesGate._lcd_array(1) == [1, 0, 0, 0, 0]
    assert TimesGate._lcd_array(3) == [0, 0, 1, 0, 0]
    assert TimesGate._lcd_array(5) == [0, 0, 0, 0, 1]
    assert TimesGate._lcd_array(0) == [1, 0, 0, 0, 0]   # clamped low
    assert TimesGate._lcd_array(9) == [0, 0, 0, 0, 1]   # clamped high


def test_encode_is_base64_jpeg():
    b64 = TimesGate._encode(Image.new("RGB", (128, 128), (10, 20, 30)))
    raw = base64.b64decode(b64)
    assert raw[:3] == b"\xff\xd8\xff"  # JPEG SOI marker


def test_push_image_treats_devicetoken_err_as_success(monkeypatch):
    tg = TimesGate("127.0.0.1")
    monkeypatch.setattr(tg, "_post", lambda payload, timeout=10: {"error_code": "DeviceToken is err"})
    ok, detail = tg.push_image(Image.new("RGB", (128, 128)), 3)
    assert ok is True
    assert "DeviceToken" in detail


def test_push_image_reports_real_error(monkeypatch):
    tg = TimesGate("127.0.0.1")
    monkeypatch.setattr(tg, "_post", lambda payload, timeout=10: {"error_code": "Request data illegal json"})
    ok, detail = tg.push_image(Image.new("RGB", (128, 128)), 1)
    assert ok is False
    assert "error_code=" in detail


def test_push_image_zero_is_success(monkeypatch):
    tg = TimesGate("127.0.0.1")
    monkeypatch.setattr(tg, "_post", lambda payload, timeout=10: {"error_code": 0})
    ok, _ = tg.push_image(Image.new("RGB", (128, 128)), 2)
    assert ok is True
