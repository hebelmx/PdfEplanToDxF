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

## Optional `power` block

A card may declare how its I/O points are powered. The block is **optional** —
omit it entirely when the supply structure is uncertain (analog and isolated-relay
cards usually have no single supply/common pair, so they ship without it and
draw no power terminals — never invent one).

```json
{
  "power": {
    "type": "AC",
    "groups": [
      { "points": [0, 1, 2, 3, 4, 5, 6, 7],
        "supply": "L1", "common": "N",
        "supply_pin": "TBD", "common_pin": "TBD" },
      { "points": [8, 9, 10, 11, 12, 13, 14, 15],
        "supply": "L1", "common": "N",
        "supply_pin": "TBD", "common_pin": "TBD" }
    ]
  }
}
```

- `type` — `"AC"` or `"DC"` (documentation/grouping; the rendering is the same).
- `groups` — one entry per independently-supplied point group. A card with one
  shared supply has a single group; the **1756-OA16 has two isolated groups of 8**
  (points 0–7 and 8–15), each with its own `L1`/`N` pair.
- `points` — the zero-based point indices (same numbering as `wiring[].point`)
  this group powers.
- `supply` / `common` — **potential names**, not pins: the rail labels the group
  hangs off. AC cards use `L1` / `N`; DC cards use `L+` / `0V` (or `24V` / `0V`).
  Each terminal carries a compact text annotation referencing the rail folio,
  `→ /Alim <name>` (a label, not a navigable QET cross-reference), and the name is
  drawn as a rail on the `Alimentación` folio. When a card has **more than one
  group** the annotation gets a `(G1)`, `(G2)`… suffix so isolated groups that
  share a potential name (the 1756-OA16's two `L1`/`N` groups) stay distinguishable.
- `supply_pin` / `common_pin` — the **physical RTB pins** for the supply and the
  common. **They follow the exact same rule as `wiring[].pin`: keep them `"TBD"`
  and the folio renders `pin __`.** Never guess a power pin from a manual; fill it
  from the module's installation-instructions wiring diagram.

A missing or malformed `power` block (absent, not a dict, no/empty `groups`, bad
point lists) degrades gracefully: **no power terminals are drawn**, never garbage.
A single group whose `supply` **and** `common` are both blank/non-string is dropped
the same way (nothing to label or reference); a group keeps rendering as long as it
has at least one usable potential name.
