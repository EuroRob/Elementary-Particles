"""Unit tests for DOMO's studio partner: als_reader, music_theory and the pure
parts of studio_partner (no NAS, no database, no model).

Run:  pytest -q test_studio_partner.py
"""
import gzip

import pytest

import als_reader as A
import music_theory as T
import studio_partner as S

LIVE12 = """<?xml version="1.0" encoding="UTF-8"?>
<Ableton MajorVersion="5" MinorVersion="12.0_12049" Creator="Ableton Live 12.1.5">
<LiveSet>
 <Tracks>
  <MidiTrack Id="1">
   <Name><EffectiveName Value="Keys"/><UserName Value="Keys"/></Name>
   <DeviceChain>
    <MainSequencer>
     <ClipSlotList>
      <ClipSlot><ClipSlot><Value>
       <MidiClip><CurrentStart Value="0"/><CurrentEnd Value="16"/><Name Value="Chords A"/>
        <Loop><LoopStart Value="0"/><LoopEnd Value="16"/><LoopOn Value="true"/></Loop>
        <Notes><KeyTracks><KeyTrack><Notes><MidiNoteEvent Time="0"/><MidiNoteEvent Time="4"/></Notes></KeyTrack></KeyTracks></Notes>
       </MidiClip>
      </Value></ClipSlot></ClipSlot>
     </ClipSlotList>
     <ClipTimeable><ArrangerAutomation><Events>
      <MidiClip><CurrentStart Value="0"/><CurrentEnd Value="64"/><Name Value="Chords A"/><Loop><LoopOn Value="false"/></Loop></MidiClip>
     </Events></ArrangerAutomation></ClipTimeable>
    </MainSequencer>
    <DeviceChain><Devices>
     <Drift><UserName Value=""/></Drift>
     <PluginDevice><PluginDesc><Vst3PluginInfo><Name Value="Vital"/></Vst3PluginInfo></PluginDesc></PluginDevice>
     <Reverb/>
    </Devices></DeviceChain>
   </DeviceChain>
  </MidiTrack>
  <AudioTrack Id="2">
   <Name><EffectiveName Value="Guitar"/><UserName Value=""/></Name>
   <DeviceChain>
    <MainSequencer>
     <Sample><ArrangerAutomation><Events>
      <AudioClip><CurrentStart Value="16"/><CurrentEnd Value="80"/><Name Value="Take 3"/>
       <SampleRef><FileRef><RelativePath Value="Samples/Recorded/Take 3.wav"/>
        <Path Value="/Users/owner/Music/Ableton/My Song Project/Samples/Recorded/Take 3.wav"/></FileRef></SampleRef>
      </AudioClip>
     </Events></ArrangerAutomation></Sample>
    </MainSequencer>
    <DeviceChain><Devices><Eq8/></Devices></DeviceChain>
   </DeviceChain>
  </AudioTrack>
 </Tracks>
 <MasterTrack><DeviceChain><Mixer><Tempo><Manual Value="96"/></Tempo></Mixer></DeviceChain></MasterTrack>
 <Scenes><Scene><Name Value="Verse"/></Scene><Scene><Name Value="Chorus"/></Scene></Scenes>
 <Locators><Locators>
  <Locator><Time Value="32"/><Name Value="Chorus"/></Locator><Locator><Time Value="0"/><Name Value="Intro"/></Locator>
 </Locators></Locators>
 <ScaleInformation><Root Value="2"/><Name Value="Minor"/></ScaleInformation>
 <InKey Value="true"/>
 <Annotation Value="try a half-time bridge"/>
</LiveSet>
</Ableton>"""


def live12(tempo="96", extra_track=""):
    xml = LIVE12.replace('<Manual Value="96"/>', f'<Manual Value="{tempo}"/>')
    if extra_track:
        xml = xml.replace("</Tracks>", extra_track + "</Tracks>")
    return gzip.compress(xml.encode())


def test_reads_a_live12_set():
    s = A.read_set(live12())
    assert s["format"] == "xml" and s["live"] == "Ableton Live 12.1.5"
    assert s["tempo"] == 96 and s["scale"] == "D minor" and s["time_signature"] == "4/4"
    keys, guitar = s["tracks"]
    assert keys["name"] == "Keys" and keys["devices"] == ["Drift", "Vital", "Reverb"]
    assert keys["session_clips"][0] == {**keys["session_clips"][0], "name": "Chords A", "looping": True, "notes": 2}
    assert guitar["name"] == "Guitar" and guitar["arrangement_clips"][0]["sample"]["relative"] == "Samples/Recorded/Take 3.wav"
    assert s["bars"] == 20 and [l["name"] for l in s["locators"]] == ["Intro", "Chorus"]
    assert s["scenes"] == ["Verse", "Chorus"] and s["annotation"] == "try a half-time bridge"
    assert A.describe(s) == "96 BPM · 4/4 · D minor · 1 midi, 1 audio tracks · 20 bars arranged"


def test_old_binary_set_is_known_as_a_file_only():
    assert A.read_set(b"\xab\x1e\x56\x78rest")["format"] == "binary"


def test_diff_says_what_changed():
    old = A.read_set(live12())
    bass = """<MidiTrack><Name><EffectiveName Value="Bass"/><UserName Value="Bass"/></Name><DeviceChain>
      <MainSequencer><ClipSlotList><ClipSlot><ClipSlot><Value><MidiClip><CurrentStart Value="0"/><CurrentEnd Value="8"/>
      <Name Value="Sub"/><Loop><LoopStart Value="0"/><LoopEnd Value="8"/><LoopOn Value="true"/></Loop></MidiClip>
      </Value></ClipSlot></ClipSlot></ClipSlotList></MainSequencer>
      <DeviceChain><Devices><Operator/></Devices></DeviceChain></DeviceChain></MidiTrack>"""
    new = A.read_set(live12("100", bass))
    lines = A.diff(old, new)
    assert "Tempo 96 → 100 BPM" in lines
    assert "Added midi track 'Bass' (Operator)" in lines and "New loop 'Sub' (2 bars) on 'Bass'" in lines
    assert A.diff(old, old) == ["Saved (no structural change: mixing, automation or note edits)."]
    assert A.diff({}, old)[0].startswith("First seen: 96 BPM")


@pytest.mark.parametrize("key, chords", [
    ((0, "major"), ["C", "Dm", "Em", "F", "G", "Am", "Bdim"]),
    ((9, "minor"), ["Am", "Bdim", "C", "Dm", "Em", "F", "G"]),
    ((6, "major", "F#"), ["F#", "G#m", "A#m", "B", "C#", "D#m", "E#dim"]),     # spelled by letter, not Ab/Bb
    ((10, "major"), ["Bb", "Cm", "Dm", "Eb", "F", "Gm", "Adim"]),
])
def test_diatonic_chords_are_spelled_right(key, chords):
    assert [c["chord"] for c in T.diatonic(key)] == chords


def test_borrowed_secondary_and_related():
    c = (0, "major")
    assert [x["chord"] for x in T.borrowed(c)] == ["Fm", "Ab", "Bb", "Eb"]
    assert {x["chord"]: x["resolves_to"] for x in T.secondary_dominants(c)}["D7"] == "G"
    assert T.related_keys((6, "major", "F#"))["relative"] == "D# minor"
    assert T.chord_tones("vii°", "dim", (6, "major", "F#")) == ["E#", "G#", "B"]


@pytest.mark.parametrize("text, key", [("chords in D minor", (2, "minor", "D")), ("what about F#m?", (6, "minor", "F#")),
                                       ("in Bb please", (10, "major", "Bb")), ("A good idea", None), ("e moll", (4, "minor", "E"))])
def test_key_from_words(text, key):
    assert T.parse_key_text(text) == key


def test_own_progression_in_a_key_and_bar_maths():
    assert T.realise_pattern("i–bVI–bIII–bVII", (4, "minor")) == "Em C G D"
    plan = T.arrangement_plan(96)
    assert plan["bar_seconds"] == 2.5 and plan["sections"][0] == {"section": "Intro", "bars": 8, "starts_bar": 1, "starts_at": 0.0}
    assert plan["length"] == "3:40"


@pytest.mark.parametrize("text, kind, body", [("idea: bridge in the relative major", "idea", "bridge in the relative major"),
                                              ("Studio note: bass too loud at bar 33", "note", "bass too loud at bar 33"),
                                              ("todo: re-record the vocal", "todo", "re-record the vocal"),
                                              ("Idee: halbes Tempo im Refrain", "idea", "halbes Tempo im Refrain")])
def test_note_phrases(text, kind, body):
    m = S._NOTE.match(text)
    assert m and S.note_kind(m.group(1)) == kind and m.group(2) == body


@pytest.mark.parametrize("text, end", [("end studio session", True), ("That's it for today", True), ("studio off", True),
                                       ("end of the chorus is weak", False)])
def test_talk_end(text, end):
    assert bool(S._TALK_END.match(text)) is end


def test_paths_and_project_audio():
    S.ROOT, S.DATAGRID_PATH = "/import/nasty", "/DATAGRID/Multimedia/Music/My Recordings"
    assert S.nas_path("/import/nasty/Ableton Live/x.als") == "/DATAGRID/Multimedia/Music/My Recordings/Ableton Live/x.als"
    assert S.project_audio_prefixes("/import/nasty/Ableton Live/Recordings/Sets/bloated.als") == [
        "/import/nasty/Ableton Live/Recordings/Sets/bloated Sounds/", "/import/nasty/Ableton Live/Recordings/Sets/Samples/Recorded/"]
    assert S.project_audio_prefixes("/import/nasty/Sets/Demo9 Project/Demo9.als") == ["/import/nasty/Sets/Demo9 Project/"]
    s = A.read_set(live12())
    # Saved on the Mac (absolute /Users path) but mirrored to DATAGRID: found by the project-relative path.
    assert S.own_sample_paths("/import/nasty/Ableton Live/My Song Project/My Song.als", s) == [
        "/import/nasty/Ableton Live/My Song Project/Samples/Recorded/Take 3.wav"]


def test_chord_checker_catches_the_live_slip():
    am = (9, "minor")
    # the reply from the first live session test (2026-10-24)
    assert T.check_chords("try bVII–V, so Am to G then back to V Em", am) == ["V is E in A minor, not Em"]
    assert T.check_chords("bVII G then iv Dm, or the borrowed IV (D) for a lift", am) == []
    assert T.check_chords("end on a C#m7 for colour", am) == ["C#m7 isn't one of the chords listed for A minor"]
    assert T.check_chords("end on a C#m7", am, allowed={"C#m"}) == []        # named in the facts or by owner
    assert T.check_chords("A pad under the chorus, then E7 back to Am", am) == []   # "A" as a word; E7 = V/i


@pytest.mark.parametrize("text, minutes", [("arrange it to about three and a half minutes", 3.5), ("make it 3:30", 3.5),
                                           ("around 4 min", 4.0), ("so vier Minuten", 4), ("how long is the intro", None)])
def test_minutes_from_words(text, minutes):
    assert T.parse_minutes(text) == minutes


def test_plan_is_the_closest_form_in_phrases():
    plan = T.arrangement_plan(130, 3.5)                       # 3:30 at 130 = 113.75 bars -> the 120-bar form
    assert plan["total_bars"] == 120 and plan["length"] == "3:41"
    assert all(x["bars"] % 8 == 0 for x in plan["sections"])
    assert T.arrangement_plan(130, 3.0)["total_bars"] == 104
    assert T.plan_line(plan).startswith("Closest common form, 3:41 at 130 BPM (1.85 s a bar, 120 bars): Intro 8 (bar 1)")


def test_inside_and_outside_references():
    assert S.ref_inside({"relative": "Samples/Recorded/Take 3.wav"})
    assert not S.ref_inside({"relative": "../../Downloads/loop.wav"})
    assert not S.ref_inside({"relative": "", "absolute": "/Users/owner/Desktop/vox.wav"})
    summary = {"samples": [{"relative": "Samples/Recorded/a.wav", "pack": ""},
                           {"relative": "../../Desktop/b.wav", "absolute": "/Users/owner/Desktop/b.wav", "pack": ""},
                           {"relative": "Samples/Drums/kick.wav", "pack": "Core Library"}]}
    assert S.outside_refs(summary) == ["/Users/owner/Desktop/b.wav"]      # library samples are fine


def test_check_endpoint(tmp_path, monkeypatch):
    import asyncio, os
    monkeypatch.setattr(S, "ROOT", str(tmp_path))
    proj = tmp_path / "Ableton Live" / "Projects" / "My Song Project"
    (proj / "Samples" / "Recorded").mkdir(parents=True)
    (proj / "My Song.als").write_bytes(live12())
    run = lambda **kw: asyncio.run(S.st_check(**kw))
    rel = "Ableton Live/Projects/My Song Project/My Song.als"
    assert run(als=rel) == "STATE=incomplete\nMISSING Ableton Live/Projects/My Song Project/Samples/Recorded/Take 3.wav\n"
    (proj / "Samples" / "Recorded" / "Take 3.wav").write_bytes(b"x")
    assert run(als=rel) == "STATE=ok\n"
    assert run(als=rel, mtime=os.stat(proj / "My Song.als").st_mtime + 60) == "STATE=stale\n"   # the Mac saved later
    assert run(als="Ableton Live/Projects/Nope/x.als") == "STATE=missing\n"
    with pytest.raises(S.HTTPException):
        run(als="../../etc/passwd")
