# Module database

One JSON file per I/O module catalog number (file name = catalog base, without
the series suffix: `1756-IB32.json` covers `1756-IB32/B`).

Used by `logix_to_qet.py` to enrich the generated folios: vendor/description
in the card header and the physical RTB terminal (pin) next to each point.

## Schema

```json
{
  "catalog":     "1756-OA16",
  "vendor":      "Allen-Bradley (Rockwell Automation)",
  "family":      "ControlLogix 1756",
  "description": "16-point 74...265 V AC digital output module",
  "kind":        "DO",
  "points":      16,
  "rtb":         "20-position RTB (1756-TBNH)",
  "wiring": [
    { "point": 0, "name": "OUT-0", "pin": "TBD" },
    { "point": 1, "name": "OUT-1", "pin": "TBD" }
  ],
  "notes": "free text"
}
```

- `point` — zero-based logical point/channel index (matches the PLC address bit).
- `name` — the point name printed on the module front (`IN-0`, `OUT-7`, `Ch3`).
- `pin` — the physical RTB terminal number. **`"TBD"` renders as a `__`
  placeholder in the folio**; replace it with the real terminal from the
  Rockwell installation instructions (wiring diagram) of the module — do not
  guess. Strings are allowed (`"2"`, `"L1-0"`, `"34/36"`).

Add files for new catalogs as they appear in your projects; unknown catalogs
simply render without vendor info and with `__` pin placeholders.
