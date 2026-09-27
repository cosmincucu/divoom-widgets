"""Home energy widget: house load, solar generation, battery state of charge.

Data comes from Home Assistant's REST API using a long-lived access token:

    GET <HA_BASE_URL>/api/states/<entity_id>
        Authorization: Bearer <token>

Entity ids are installation-specific (integration renames change them), so all
three come from env (HA_ENTITY_LOAD / HA_ENTITY_SOLAR / HA_ENTITY_SOC — look
yours up under Developer Tools -> States). Power values honor the entity's
unit_of_measurement attribute (kW or W); HA_POWER_UNIT is the fallback.

Fields fail independently: a sensor that is `unavailable` renders as "--"
while the others stay live. Only when *all* fields fail is the fetch treated
as an error (the panel then keeps its previous image).
"""
from __future__ import annotations

from typing import Optional

import requests
from PIL import Image, ImageDraw

from .. import config
from ..render_kit import (
    BG, DIM, GRAY, SIZE, WHITE, Fonts, bar, fit_font, header, level_color,
    right_text_bottom,
)

SUN = (255, 200, 40)
_BAD_STATES = {"unavailable", "unknown", "none", ""}


def _token() -> str:
    if config.HA_TOKEN:
        return config.HA_TOKEN
    if config.HA_TOKEN_FILE:
        try:
            with open(config.HA_TOKEN_FILE, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        return line
        except OSError:
            return ""
    return ""


def _get_state(entity_id: str, token: str) -> tuple[Optional[float], str]:
    """Return (numeric state, unit) for one entity, or (None, '') on failure."""
    if not entity_id:
        return None, ""
    try:
        resp = requests.get(
            f"{config.HA_BASE_URL}/api/states/{entity_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        if resp.status_code != 200:
            return None, ""
        body = resp.json()
    except (requests.RequestException, ValueError):
        return None, ""
    state = str(body.get("state", "")).strip()
    if state.lower() in _BAD_STATES:
        return None, ""
    try:
        value = float(state)
    except ValueError:
        return None, ""
    unit = str((body.get("attributes") or {}).get("unit_of_measurement", "")).strip()
    return value, unit


def _to_kw(value: Optional[float], unit: str) -> Optional[float]:
    if value is None:
        return None
    unit = (unit or config.HA_POWER_UNIT).strip().lower()
    return value / 1000.0 if unit == "w" else value


def fetch() -> dict:
    token = _token()
    if not token:
        return {"ok": False, "error": "no HA token"}

    load_v, load_u = _get_state(config.HA_ENTITY_LOAD, token)
    solar_v, solar_u = _get_state(config.HA_ENTITY_SOLAR, token)
    soc_v, _ = _get_state(config.HA_ENTITY_SOC, token)

    data = {
        "ok": True,
        "load_kw": _to_kw(load_v, load_u),
        "solar_kw": _to_kw(solar_v, solar_u),
        "soc_pct": soc_v,
    }
    if data["load_kw"] is None and data["solar_kw"] is None and data["soc_pct"] is None:
        return {"ok": False, "error": "all sensors failed"}
    return data


def interval_s() -> int:
    return config.ENERGY_INTERVAL_SECONDS


def _fmt_kw(value: Optional[float]) -> str:
    return "--" if value is None else f"{value:.2f}"


def summary(data: dict) -> str:
    if data.get("ok"):
        soc = data.get("soc_pct")
        return (f"NRG home {_fmt_kw(data.get('load_kw'))}kW / "
                f"solar {_fmt_kw(data.get('solar_kw'))}kW / "
                f"batt {'--' if soc is None else f'{soc:.0f}%'}")
    return f"NRG err:{data.get('error')}"


def _power_row(draw, fonts, top, label, kw, color):
    draw.text((5, top), label, font=fonts.row, fill=GRAY)
    value = _fmt_kw(kw)
    gap = 6
    avail = SIZE - 31 - draw.textlength(label, font=fonts.row) - gap
    right_text_bottom(draw, SIZE - 26, top + 15, value,
                      fit_font(draw, value, "bold", avail), color)
    draw.text((SIZE - 23, top + 4), "kW", font=fonts.tiny, fill=DIM)


def render(data: dict) -> Image.Image:
    img = Image.new("RGB", (SIZE, SIZE), BG)
    draw = ImageDraw.Draw(img)
    fonts = Fonts()

    header(draw, fonts, "ENERGY", SUN)

    if not data.get("ok"):
        draw.text((5, 52), "no data", font=fonts.title, fill=GRAY)
        draw.text((5, 70), str(data.get("error", ""))[:20], font=fonts.tiny, fill=DIM)
        return img

    load_kw = data.get("load_kw")
    solar_kw = data.get("solar_kw")
    soc = data.get("soc_pct")

    _power_row(draw, fonts, 28, "HOME", load_kw, WHITE)
    solar_color = SUN if (solar_kw or 0.0) > 0.05 else DIM
    _power_row(draw, fonts, 58, "SOLAR", solar_kw, solar_color)

    # --- Battery: percentage + level bar (green = full, red = low) ---
    draw.text((5, 88), "BATT", font=fonts.row, fill=GRAY)
    text = "--%" if soc is None else f"{int(round(soc))}%"
    color = DIM if soc is None else level_color(soc)
    avail = SIZE - 10 - draw.textlength("BATT", font=fonts.row) - 6
    right_text_bottom(draw, SIZE - 5, 103, text,
                      fit_font(draw, text, "bold", avail), color)
    bar(draw, 5, 106, SIZE - 10, 9, 0 if soc is None else soc, color)

    return img
