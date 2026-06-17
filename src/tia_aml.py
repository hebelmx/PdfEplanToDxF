#!/usr/bin/env python3
"""tia_aml.py — TIA Portal CAx/AML hardware-map parser (TIA-3, Story 4.1).

The TIA `IO_Channels.xml` (parsed by tia_front_end) is the I/O POINT source; it
does NOT carry the Siemens order number or the PROFINET network address. Those
live in the CAx/AML export (`<project>.aml`, AutomationML == XML). This module
reads that `.aml` with the standard library only (`xml.etree`) and produces a
per-module hardware map so the vendor-neutral IR can carry real Siemens order
numbers and addresses in the BOM.

AML structure (the bits we use — INSPECTED from the real IMV1 export, NOT
assumed; the brief's "CPU 1214C + 8x ET200SP" was inaccurate — see below):

    <InstanceHierarchy>
      <InternalElement Name="<project>">
        <InternalElement Name="Ungrouped devices">
          <InternalElement Name="Q100-Cooling1/UV">       <- a DEVICE (a station)
            <Attribute Name="TypeIdentifier"><Value>System:Device.ET200SP</Value>
            <InternalElement Name="Rack_0">                <- a RACK
              <Attribute Name="TypeIdentifier"><Value>System:Rack.ET200SP</Value>
              <InternalElement Name="Q100_QUERETARO1">     <- head/CPU module
                <Attribute Name="TypeName"><Value>CPU 1512SP F-1 PN</Value>
                <Attribute Name="DeviceItemType"><Value>CPU</Value>
                <Attribute Name="TypeIdentifier"><Value>OrderNumber:6ES7 512-1SK01-0AB0</Value>
                ... <Attribute Name="NetworkAddress"><Value>192.168.10.10</Value> ...
              <InternalElement Name="F-DI150">             <- an I/O module
                <Attribute Name="TypeName"><Value>F-DI 8x24VDC HF</Value>
                <Attribute Name="TypeIdentifier"><Value>OrderNumber:6ES7 136-6BA00-0CA0</Value>
                <InternalElement ...>
                  ... ExternalInterface Name="Channel_DI_0" ... (one per channel)

The join key into the IR is the rack-child module Name (e.g. "F-DI150",
"F-DQ1500", "DI10_11") — these match the IO_Channels <Module Name="..."> values
exactly. The PROFINET NetworkAddress is per ET200SP station (the head module's
interface); every I/O module on that station inherits it (ET200SP modules sit
behind the IM/CPU's single PROFINET node).

Surprises vs the brief (reported, not silently handled):
  * The `.aml` is NOT "CPU 1214C + 8 ET200SP". It contains the FULL plant: many
    ET200SP DEVICES (Q100..Q600 ...), each a distinct station. The floor station
    is "Q100-Cooling1/UV" whose CPU is a `CPU 1512SP F-1 PN`
    (OrderNumber 6ES7 512-1SK01-0AB0), an S7-1500-class F-CPU — so the 1500
    hardware IS present here, contradicting "the 1500 hardware is NOT in it".
  * No masked `?` digits appear in this fixture's order numbers, but the parser
    keeps any `?` verbatim (never normalized) per the never-invent rule.

Hard rules: standard library only; NEVER invent — a module with no OrderNumber
yields catalog "" and a station with no NetworkAddress yields network_address
None; masked `?` digits in order numbers are preserved verbatim.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET


_ORDER_PREFIX = "OrderNumber:"


def _attr_value(el: ET.Element, name: str) -> str | None:
    """Return the <Value> text of the DIRECT-CHILD <Attribute Name="name">, or
    None when absent/empty. Only direct children so a module's own attributes are
    not confused with a nested sub-element's (channels carry their own Type/Number
    attributes)."""
    for a in el.findall("Attribute"):
        if a.get("Name") == name:
            v = a.find("Value")
            if v is not None and v.text is not None and v.text.strip():
                return v.text.strip()
    return None


def _order_number(type_identifier: str | None) -> str:
    """Extract the Siemens order number from a TypeIdentifier value.

    `OrderNumber:6ES7 136-6BA00-0CA0` -> `6ES7 136-6BA00-0CA0` (verbatim, masked
    `?` digits kept). A TypeIdentifier that is not an OrderNumber (e.g.
    `System:Device.ET200SP`) yields "" — NEVER invented.
    """
    if not type_identifier:
        return ""
    s = type_identifier.strip()
    if s.startswith(_ORDER_PREFIX):
        return s[len(_ORDER_PREFIX):].strip()
    return ""


def _first_network_address(el: ET.Element) -> str | None:
    """First NetworkAddress <Value> anywhere in this element's subtree, or None.

    The PROFINET address lives several levels down (DeviceItem > PROFINET
    interface > node). For an ET200SP station the head module owns the single
    PROFINET node; we take the first NetworkAddress in the device subtree as the
    station address. NEVER invented — None when no NetworkAddress exists.
    """
    for a in el.iter("Attribute"):
        if a.get("Name") == "NetworkAddress":
            v = a.find("Value")
            if v is not None and v.text is not None and v.text.strip():
                return v.text.strip()
    return None


def _count_channels(module_el: ET.Element) -> int:
    """Count physical I/O channels declared under a module via the
    `Channel_*` ExternalInterface elements. 0 when none are declared (e.g. a
    head module / server module) — never invented."""
    n = 0
    for ext in module_el.iter("ExternalInterface"):
        name = ext.get("Name") or ""
        if name.startswith("Channel_"):
            n += 1
    return n


def _is_io_module(module_el: ET.Element) -> bool:
    """A rack-child <InternalElement> is treated as a hardware module when it
    declares either a TypeName or an OrderNumber TypeIdentifier. Pure structural
    (rack/port/tag-table children that aren't modules are skipped)."""
    if _attr_value(module_el, "TypeName"):
        return True
    return bool(_order_number(_attr_value(module_el, "TypeIdentifier")))


def parse_aml(aml_path: str) -> dict[tuple[str, str], dict]:
    """Parse a TIA CAx/AML export into a per-module hardware map.

    Returns dict keyed by (station_name, module_name) -> {
        order_number:   str   (the 6ES7… catalog, "" when absent; `?` kept)
        type_name:      str   (e.g. "F-DI 8x24VDC HF", "" when absent)
        network_address:str|None  (the station's PROFINET address, None when absent)
        channels:       int   (declared Channel_* count, 0 when none)
        device_item_type:str  ("CPU"/"HeadModule"/"" — provenance only)
    }

    NEVER raises into the caller for a malformed/missing file other than a true
    parse error surfaced by ET.parse (the front-end guards the optional path).
    NEVER invents: missing fields degrade to ""/None.
    """
    tree = ET.parse(aml_path)
    root = tree.getroot()

    hw: dict[tuple[str, str], dict] = {}

    # A DEVICE is any InternalElement whose direct TypeIdentifier is System:Device.*
    # A RACK is its child whose TypeIdentifier is System:Rack.*; the rack's
    # children are the hardware modules (head/CPU + I/O + server module).
    for dev in root.iter("InternalElement"):
        ti = _attr_value(dev, "TypeIdentifier") or ""
        if not ti.startswith("System:Device."):
            continue
        station = dev.get("Name") or ""
        station_addr = _first_network_address(dev)  # shared by the station's modules

        for rack in dev.findall("InternalElement"):
            rti = _attr_value(rack, "TypeIdentifier") or ""
            if not rti.startswith("System:Rack."):
                continue
            for mod in rack.findall("InternalElement"):
                if not _is_io_module(mod):
                    continue
                name = mod.get("Name") or ""
                order = _order_number(_attr_value(mod, "TypeIdentifier"))
                hw[(station, name)] = {
                    "order_number": order,
                    "type_name": _attr_value(mod, "TypeName") or "",
                    "network_address": station_addr,
                    "channels": _count_channels(mod),
                    "device_item_type": _attr_value(mod, "DeviceItemType") or "",
                }

    return hw


def _tn(el: ET.Element) -> str:
    """Local tag name without the AML namespace prefix."""
    return el.tag.split("}")[-1]


def _ip_sort_key(ip: str) -> tuple:
    """Numeric sort key for a dotted IPv4 string; a non-numeric/odd address sorts
    last (deterministic) rather than raising — never invented."""
    try:
        return (0,) + tuple(int(x) for x in ip.split("."))
    except (ValueError, AttributeError):
        return (1, ip)


def profinet_nodes(aml_path: str) -> list[tuple[str, str, str]]:
    """Parse the CAx/AML for the PROFINET subnet node list.

    Returns a list of (ip, name, type_name) tuples, one per `NetworkAddress`
    attribute in the file, NUMERICALLY IP-sorted. The IP comes from the
    `<Attribute Name="NetworkAddress"><Value>` itself; the device name + type are
    resolved by climbing the parent chain (<=6 hops) from that attribute to the
    first ancestor `<InternalElement>` carrying a `TypeName` attribute — that is
    the owning device/module (the address sits several levels down on its PROFINET
    interface node, which has only a Name). When no ancestor carries a TypeName,
    the FIRST named `<InternalElement>` ancestor's Name is kept and the type is ""
    — NEVER invented (a node with neither name nor type degrades to ("", "")).

    Verified against the IMV1 export: 35 nodes on 192.168.10.x, e.g.
    `192.168.10.10 Q100_QUERETARO1 / CPU 1512SP F-1 PN`.
    """
    tree = ET.parse(aml_path)
    root = tree.getroot()
    parent = {c: p for p in root.iter() for c in p}

    nodes: list[tuple[str, str, str]] = []
    for el in root.iter():
        if _tn(el) != "Attribute" or el.get("Name") != "NetworkAddress":
            continue
        v = el.find("{*}Value")
        ip = v.text.strip() if (v is not None and v.text and v.text.strip()) else None
        if not ip:
            continue
        name = ""
        type_name = ""
        first_named = ""
        cur = el
        for _ in range(6):
            cur = parent.get(cur)
            if cur is None:
                break
            if _tn(cur) != "InternalElement":
                continue
            if not first_named and cur.get("Name"):
                first_named = cur.get("Name")
            tnm = _attr_value(cur, "TypeName")
            if tnm:
                name = cur.get("Name") or ""
                type_name = tnm
                break
        if not name:
            name = first_named  # device type unknown — keep the interface/device name
        nodes.append((ip, name, type_name))

    nodes.sort(key=lambda r: _ip_sort_key(r[0]))
    return nodes


def hardware_for_station(
    hw: dict[tuple[str, str], dict], station_name: str
) -> dict[str, dict]:
    """Project the (station, module) map down to {module_name -> info} for one
    station. Falls back to merging ALL stations when `station_name` is unknown
    (so a name mismatch still lets the unique module names join) — last write
    wins on a name collision across stations, which does not occur for the IMV1
    floor (module names are globally unique there)."""
    exact = {m: info for (st, m), info in hw.items() if st == station_name}
    if exact:
        return exact
    merged: dict[str, dict] = {}
    for (_st, m), info in hw.items():
        merged[m] = info
    return merged
