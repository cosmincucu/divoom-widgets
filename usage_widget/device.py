"""Times Gate device client.

The five LCD panels are addressed with the local Draw/SendHttpGif command and
a 5-element LcdArray (one flag per panel). Images are sent as 128x128 base64
JPEG, matching the proven approach in divoom_keeper.

Note: current Times Gate firmware replies `{"error_code": "DeviceToken is err"}`
to local commands, yet the draw is still applied. We therefore treat that
specific error_code as success and only fail on transport-level errors.
"""
from __future__ import annotations

import base64
import io
import time

import requests
from PIL import Image

IMG_SIZE = 128
# error_code values the device returns that do NOT prevent the draw.
_BENIGN = {0, "0", "DeviceToken is err"}


class TimesGate:
    def __init__(self, host: str):
        self.host = host
        self.url = f"http://{host}/post"

    def _post(self, payload: dict, timeout: int = 10) -> dict:
        r = requests.post(self.url, json=payload, timeout=timeout)
        r.raise_for_status()
        try:
            return r.json()
        except ValueError:
            return {"error_code": "non-json"}

    @staticmethod
    def _lcd_array(panel: int) -> list[int]:
        arr = [0, 0, 0, 0, 0]
        arr[max(1, min(5, panel)) - 1] = 1
        return arr

    @staticmethod
    def _encode(image: Image.Image) -> str:
        img = image.convert("RGB")
        if img.size != (IMG_SIZE, IMG_SIZE):
            img = img.resize((IMG_SIZE, IMG_SIZE), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        return base64.b64encode(buf.getvalue()).decode()

    def push_image(self, image: Image.Image, panel: int) -> tuple[bool, str]:
        """Draw a single still image on one panel. Returns (ok, detail)."""
        # Best-effort: put the panel in custom mode first (ignored if it errors).
        try:
            self._post({"Command": "Channel/SetCustom", "LcdIndex": panel - 1}, timeout=6)
        except requests.RequestException:
            pass

        payload = {
            "Command": "Draw/SendHttpGif",
            "LcdArray": self._lcd_array(panel),
            "PicNum": 1,
            "PicOffset": 0,
            "PicID": int(time.time() * 1000) % 2_000_000_000,
            "PicSpeed": 1000,
            "PicWidth": IMG_SIZE,
            "PicData": self._encode(image),
        }
        try:
            body = self._post(payload)
        except requests.RequestException as exc:
            return False, f"transport: {exc}"

        code = body.get("error_code", 0)
        if code in _BENIGN:
            return True, str(code)
        return False, f"error_code={code}"
