#!/usr/bin/env python3
"""power_config.py — loader/normalizer for the Siemens power one-line config.

Siemens panels have no module_db power source, so the 'Alimentación' power
one-line folio is driven by an EXPLICIT JSON config the user passes (never
derived, never invented). This module reads + validates that JSON and hands
the renderer a normalized dict.

Schema (all values are strings; optional fields may be absent or null → omitted
from the drawing, NEVER drawn blank or invented):

    {
      "system_voltage": "120 VAC",
      "input_breaker":  {"label": "Q1", "rating": "2 A"},
      "power_supply":   {"label": "PS1", "rating": "10 A"},
      "output_breaker": {"label": "Q2", "rating": "10 A"},
      "loads": "Control / PLC",
      "transformer": null,
      "ups": null
    }

Validation policy: tolerate missing optional keys (loads, transformer, ups,
and any device label/rating) — never raise on absent data. Only the presence
of the returned dict drives the folio. Language-agnostic (no English/Spanish
assumptions baked into the values).

STANDARD LIBRARY ONLY.
"""

from __future__ import annotations

import json
from pathlib import Path

# The optional device "boxes" in the vertical stack, in draw order. Each entry
# is (config-key, default-label). A box is rendered only when its key is present
# in the config; absent keys are simply skipped (never invented).
POWER_DEVICE_KEYS = (
    "input_breaker",
    "transformer",
    "power_supply",
    "ups",
    "output_breaker",
)


def _clean_str(value) -> str:
    """A config scalar → a clean string, or "" when None/absent. Never raises:
    non-string scalars (e.g. a number) are stringified, blanks stay blank."""
    if value is None:
        return ""
    return str(value).strip()


def _normalize_device(raw) -> dict | None:
    """Normalize one device sub-dict ({'label':..,'rating':..}) → a dict with
    cleaned 'label'/'rating' strings, or None when the device is absent/empty.
    A device dict with both fields blank collapses to None (nothing to draw)."""
    if not isinstance(raw, dict):
        # tolerate a bare string (treated as a label) but never invent structure
        label = _clean_str(raw)
        return {"label": label, "rating": ""} if label else None
    label = _clean_str(raw.get("label"))
    rating = _clean_str(raw.get("rating"))
    if not label and not rating:
        return None
    return {"label": label, "rating": rating}


def normalize_power_config(raw) -> dict | None:
    """Normalize a parsed config dict into the shape the folio builder expects:

        {
          "system_voltage": str,            # may be ""
          "loads": str,                     # may be ""
          "devices": {key: {"label","rating"}}  # only present, non-empty devices
        }

    Returns None when `raw` is falsy or carries nothing renderable. Never raises
    on absent/optional data; absent device keys are simply omitted."""
    if not raw or not isinstance(raw, dict):
        return None
    devices: dict[str, dict] = {}
    for key in POWER_DEVICE_KEYS:
        dev = _normalize_device(raw.get(key))
        if dev is not None:
            devices[key] = dev
    return {
        "system_voltage": _clean_str(raw.get("system_voltage")),
        "loads": _clean_str(raw.get("loads")),
        "devices": devices,
    }


def load_power_config(path) -> dict | None:
    """Read + validate a power-config JSON file → a normalized config dict, or
    None when `path` is falsy. Raises FileNotFoundError when the path is given
    but missing, and json.JSONDecodeError on malformed JSON — but NEVER on
    merely-absent optional fields. The presence of the returned dict is what
    drives the 'Alimentación' folio."""
    if not path:
        return None
    text = Path(path).read_text(encoding="utf-8")
    raw = json.loads(text)
    return normalize_power_config(raw)
