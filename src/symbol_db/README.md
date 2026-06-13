# Symbol database

Plain-JSON database of common control field devices, used by `logix_to_qet.py`
to draw a real symbol (limit switch, push button, solenoid valve, ...) at the
end of each digital I/O row — wired to the point's terminal — instead of
leaving every point generic.

One JSON file per device type plus its QElectroTech element in `elements/`
(copied verbatim from the official QET collection — author: the QElectroTech
team, license: <http://qelectrotech.org/wiki/doc/elements_license>).

## Schema

```json
{
  "id":          "limit_switch",
  "description": "Limit switch / position switch, NO contact",
  "element":     "limit_switch.elmt",
  "qet_source":  "10_electric/10_allpole/390_sensors_instruments/41_limit_switches/fin_de_course_no.elmt",
  "direction":   "I",
  "priority":    0,
  "suffixes":    ["LS", "FC", "FDC", "ZS", "SQ"],
  "keywords":    ["limit switch", "fin de carrera", "final de carrera", "puerta"]
}
```

- `direction` — `"I"`, `"O"` or `"any"`: which side of the PLC the device can
  live on. Inputs never match output devices and vice versa.
- `suffixes` — tag-token conventions, matched against the raw tag only.
  `LS` hits the tokens `LS`, `LS2`, `2LS` and `LS08A` (suffix bounded by a
  digit) but not `LSH` or `FLASH`. Whole words of 4+ characters
  (`PARO`, `EMERGENCIA`) outrank 2-letter codes when both hit the same tag.
- `keywords` — phrases fuzzy-matched (English/Spanish, accent-insensitive)
  against the *humanized* tag (abbreviations expanded through the
  `logix_to_eplan_csv.py` dictionary) plus the tag description. A multi-word
  phrase found verbatim in the engineer's own words (raw tag tokens or
  description) is the strongest evidence of all and beats any suffix —
  that is how `HU_OIL_LEVEL_LOW_LS` becomes a level switch, not a limit
  switch.
- `priority` — tie-breaker only; leave 0 unless two entries keep colliding.

Points with no confident match keep the generic terminal — a wrong symbol in
an electrical drawing is worse than a plain one.

## Adding a device type

1. Pick an element in the QET collection (`C:\Program Files\QElectroTech\elements\`).
   Prefer definitions whose `width` is ≤ 40 px — symbols are rotated 90° into
   the row, so the definition width becomes the vertical footprint and the row
   pitch is 35 px.
2. Copy the `.elmt` into `elements/` under a semantic name.
3. Write the JSON next to it; run on a project and check the `symbols :`
   summary line on stderr.
