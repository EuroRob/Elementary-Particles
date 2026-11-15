"""als_reader.py — read an Ableton Live set (.als) into a plain summary, and say
what changed between two saves. Pure functions; studio_partner.py does the I/O.

A Live set from Live 8 on is gzipped XML (verified on DATAGRID 2026-10-24: the
12 Live 9.x sets parse; 65 older sets are Live <= 7 binary, readable only as a
file). Element paths were taken from those real sets; the Live 11/12 forms
(FileRef/Path + RelativePath as Value, Scenes/Scene/Name, ScaleInformation/Root)
are handled alongside the Live 9 ones.

  summary   tempo, time signature, scale (only when one is set), tracks with
            their devices and clips (session + arrangement, loop on/off and
            length), scenes, locators, the set's annotation, arrangement
            length, and the audio files it uses (own vs. Live pack library).
  diff      "Added track 'Bass' (Operator)", "Tempo 92 → 96", "Arrangement
            32 → 48 bars", "New loop 'Drums 1' (4 bars) on Drums" -- the lines
            the studio chronicle is made of.
"""
import gzip
import xml.etree.ElementTree as ET

NOTE_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
TRACK_KINDS = {"AudioTrack": "audio", "MidiTrack": "midi", "GroupTrack": "group", "ReturnTrack": "return"}
# Device tags -> what a musician calls them (the rest: tag as is, minus a trailing "Device").
DEVICE_NAMES = {"OriginalSimpler": "Simpler", "MultiSampler": "Sampler", "InstrumentGroupDevice": "Instrument Rack",
                "DrumGroupDevice": "Drum Rack", "AudioEffectGroupDevice": "Audio Effect Rack",
                "MidiEffectGroupDevice": "MIDI Effect Rack", "InstrumentVector": "Wavetable", "Drift": "Drift",
                "UltraAnalog": "Analog", "LoungeLizard": "Electric", "StringStudio": "Tension",
                "Collision": "Collision", "Operator": "Operator", "Compressor2": "Compressor", "Eq8": "EQ Eight",
                "AutoFilter": "Auto Filter", "CrossDelay": "Simple Delay", "StereoGain": "Utility",
                "GlueCompressor": "Glue Compressor", "MxDeviceAudioEffect": "Max Audio Effect",
                "MxDeviceInstrument": "Max Instrument", "MxDeviceMidiEffect": "Max MIDI Effect"}


def _v(e, path, default=None):
    x = e.find(path) if e is not None else None
    return x.get("Value", default) if x is not None else default


def _f(e, path, default=0.0):
    try:
        return float(_v(e, path, default))
    except (TypeError, ValueError):
        return default


def is_xml_set(raw: bytes) -> bool:
    return raw[:2] == b"\x1f\x8b"


def device_name(d) -> str:
    if d.tag in ("PluginDevice", "AuPluginDevice"):
        n = _v(d, "PluginDesc/VstPluginInfo/PlugName") or _v(d, "PluginDesc/Vst3PluginInfo/Name") \
            or _v(d, "PluginDesc/AuPluginInfo/Name")
        if n:
            return n
    user = _v(d, "UserName")
    base = DEVICE_NAMES.get(d.tag, d.tag[:-6] if d.tag.endswith("Device") else d.tag)
    return f"{user} ({base})" if user and user != base else base


def _clip(c, where: str) -> dict:
    start, end = _f(c, "CurrentStart"), _f(c, "CurrentEnd")
    out = {"name": _v(c, "Name") or "", "kind": "midi" if c.tag == "MidiClip" else "audio", "where": where,
           "start": start, "end": end, "length": round(end - start, 3), "looping": _v(c, "Loop/LoopOn") == "true",
           "loop_start": _f(c, "Loop/LoopStart"), "loop_end": _f(c, "Loop/LoopEnd")}
    if c.tag == "MidiClip":
        out["notes"] = len(c.findall(".//KeyTracks/KeyTrack/Notes/MidiNoteEvent")) + \
            len(c.findall("Notes/Events/MidiNoteEvent"))
    else:
        ref = sample_ref(c.find("SampleRef/FileRef"))
        if ref:
            out["sample"] = ref
    return out


def sample_ref(fr) -> dict:
    """{name, relative, absolute, pack} -- pack set means Ableton library, not your audio."""
    if fr is None:
        return {}
    rel = fr.find("RelativePath")
    if rel is not None and rel.get("Value") is not None:              # Live 11/12
        relative = rel.get("Value")
        name = relative.rsplit("/", 1)[-1]
    else:                                                             # Live 9
        name = _v(fr, "Name") or ""
        dirs = [x.get("Dir") for x in (rel if rel is not None else []) if x.get("Dir")]
        relative = "/".join(dirs + [name]) if name else ""
    if not name and not relative:
        return {}
    return {"name": name, "relative": relative, "absolute": _v(fr, "Path") or "",
            "pack": _v(fr, "LivePackName") or ""}


def _time_signature(ls) -> str:
    ts = ls.find(".//RemoteableTimeSignature")
    if ts is None:
        return "4/4"
    return f"{_v(ts, 'Numerator', '4')}/{_v(ts, 'Denominator', '4')}"


def _scale(ls):
    si = ls.find("ScaleInformation")
    if si is None:
        return None
    name = _v(si, "Name") or ""
    root = _v(si, "Root") or _v(si, "RootNote")
    in_key = _v(ls, "InKey")
    if not name or (in_key is not None and in_key != "true"):         # Live 9 always has RootNote 0, Name ""
        return None
    try:
        return f"{NOTE_NAMES[int(root) % 12]} {name.lower()}"
    except (TypeError, ValueError):
        return None


def summarise(root) -> dict:
    ls = root.find("LiveSet")
    tempo = _f(ls, "MasterTrack/DeviceChain/Mixer/Tempo/Manual", None) or _f(ls, ".//MasterTrack//Tempo/Manual", None)
    tracks, samples, end = [], {}, 0.0
    for t in (ls.find("Tracks") if ls.find("Tracks") is not None else []):
        name = _v(t, "Name/UserName") or _v(t, "Name/EffectiveName") or ""
        devices = [device_name(d) for d in t.findall("DeviceChain/DeviceChain/Devices/*")]
        session = [_clip(c, "session") for c in t.findall("DeviceChain/MainSequencer/ClipSlotList/ClipSlot/ClipSlot/Value/*")
                   if c.tag in ("AudioClip", "MidiClip")]
        arrangement = [_clip(c, "arrangement") for c in t.findall("DeviceChain/MainSequencer//ArrangerAutomation/Events/*")
                       if c.tag in ("AudioClip", "MidiClip")]
        for c in arrangement:
            end = max(end, c["end"])
        for d in t.findall("DeviceChain/DeviceChain/Devices//SampleRef/FileRef"):
            ref = sample_ref(d)
            if ref:
                samples.setdefault(ref["relative"] or ref["name"], ref)
        for c in session + arrangement:
            if c.get("sample"):
                samples.setdefault(c["sample"]["relative"] or c["sample"]["name"], c["sample"])
        tracks.append({"kind": TRACK_KINDS.get(t.tag, t.tag), "name": name, "devices": devices,
                       "session_clips": session, "arrangement_clips": arrangement})
    scenes = [s.get("Value", "").strip() for s in ls.findall("SceneNames/Scene")] or \
             [(_v(s, "Name") or "").strip() for s in ls.findall("Scenes/Scene")]
    locators = sorted(({"name": _v(l, "Name") or "", "beat": _f(l, "Time")}
                       for l in ls.findall("Locators/Locators/Locator")), key=lambda x: x["beat"])
    sig = _time_signature(ls)
    beats_per_bar = int(sig.split("/")[0]) * 4 / int(sig.split("/")[1])
    return {"format": "xml", "live": root.get("Creator") or "Ableton Live", "tempo": tempo, "time_signature": sig,
            "scale": _scale(ls), "annotation": _v(ls, "Annotation") or "", "tracks": tracks,
            "scenes": [s for s in scenes if s], "locators": locators,
            "bars": round(end / beats_per_bar, 1) if end else 0,
            "samples": sorted(samples.values(), key=lambda s: (bool(s["pack"]), s["relative"]))}


def read_set(raw: bytes) -> dict:
    if not is_xml_set(raw):
        return {"format": "binary", "live": "Ableton Live 7 or older (binary set: only the file is known)"}
    return summarise(ET.fromstring(gzip.decompress(raw)))


# ── what changed between two saves ───────────────────────────────────────────

def _bars(beats: float, sig: str) -> str:
    n, d = (int(x) for x in sig.split("/"))
    b = beats / (n * 4 / d)
    return f"{b:g} bar{'s' if b != 1 else ''}"


def describe(s: dict) -> str:
    """One line for a set: what's in it."""
    if s.get("format") != "xml":
        return s.get("live", "")
    kinds = {}
    for t in s["tracks"]:
        kinds[t["kind"]] = kinds.get(t["kind"], 0) + 1
    parts = [f"{s['tempo']:g} BPM" if s.get("tempo") else None, s.get("time_signature"), s.get("scale"),
             ", ".join(f"{n} {k}" for k, n in kinds.items() if k != "return") + " tracks",
             f"{s['bars']:g} bars arranged" if s.get("bars") else "session view only"]
    return " · ".join(p for p in parts if p)


def diff(old: dict, new: dict) -> list:
    """Human lines for what changed from one save to the next."""
    if not old:
        return [f"First seen: {describe(new)}."]
    if new.get("format") != "xml" or old.get("format") != "xml":
        return ["Saved again."] if old.get("format") == new.get("format") else ["Re-saved in a newer Live version."]
    out = []
    if old.get("tempo") != new.get("tempo") and new.get("tempo"):
        out.append(f"Tempo {old['tempo']:g} → {new['tempo']:g} BPM" if old.get("tempo")
                   else f"Tempo {new['tempo']:g} BPM")
    if old.get("time_signature") != new.get("time_signature"):
        out.append(f"Time signature {old.get('time_signature')} → {new.get('time_signature')}")
    if old.get("scale") != new.get("scale") and new.get("scale"):
        out.append(f"Scale set to {new['scale']}")
    if old.get("bars") != new.get("bars"):
        out.append(f"Arrangement {old.get('bars') or 0:g} → {new.get('bars') or 0:g} bars")
    before = {t["name"]: t for t in old["tracks"]}
    after = {t["name"]: t for t in new["tracks"]}
    for name in [n for n in after if n not in before]:
        t = after[name]
        out.append(f"Added {t['kind']} track '{name}'" + (f" ({', '.join(t['devices'])})" if t["devices"] else ""))
        for c in t["session_clips"]:
            if c["looping"]:
                out.append(f"New loop '{c['name'] or 'untitled'}' "
                           f"({_bars(c['loop_end'] - c['loop_start'], new['time_signature'])}) on '{name}'")
    for name in [n for n in before if n not in after]:
        out.append(f"Removed track '{name}'")
    for name in [n for n in after if n in before]:
        a, b = before[name], after[name]
        added = [d for d in b["devices"] if d not in a["devices"]]
        removed = [d for d in a["devices"] if d not in b["devices"]]
        if added:
            out.append(f"'{name}': added {', '.join(added)}")
        if removed:
            out.append(f"'{name}': removed {', '.join(removed)}")
        old_clips = {(c["where"], c["name"], c["start"]) for c in a["session_clips"] + a["arrangement_clips"]}
        for c in b["session_clips"]:
            if (c["where"], c["name"], c["start"]) not in old_clips:
                what = f"loop '{c['name'] or 'untitled'}' ({_bars(c['loop_end'] - c['loop_start'], new['time_signature'])})" \
                    if c["looping"] else f"clip '{c['name'] or 'untitled'}'"
                out.append(f"New {what} on '{name}'")
        new_arr = [c for c in b["arrangement_clips"] if (c["where"], c["name"], c["start"]) not in old_clips]
        if new_arr:
            out.append(f"'{name}': {len(new_arr)} new clip{'s' if len(new_arr) > 1 else ''} in the arrangement")
    n, d = (int(x) for x in new["time_signature"].split("/"))
    old_loc = {l["name"] for l in old.get("locators", [])}
    for l in new.get("locators", []):
        if l["name"] not in old_loc:
            out.append(f"Locator '{l['name']}' at bar {l['beat'] / (n * 4 / d) + 1:g}")
    if old.get("annotation") != new.get("annotation") and new.get("annotation"):
        out.append(f"Set note: {new['annotation'][:200]}")
    return out or ["Saved (no structural change: mixing, automation or note edits)."]
