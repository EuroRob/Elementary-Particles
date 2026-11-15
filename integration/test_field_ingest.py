"""Unit tests for field_ingest (no NAS, no database, no Whisper).

Run:  pytest -q test_field_ingest.py
"""
import io
import struct
import wave
from datetime import datetime, timedelta

import pytest

import field_ingest as F

TZ = F.TZ


def make_wav(path, seconds=1.0, rate=8000, ch=2, bits=16, originator=None, when=None):
    """A WAV with an optional bext chunk before fmt (as the H2n writes it)."""
    frames = int(rate * seconds)
    data = bytes(frames * ch * bits // 8)
    chunks = b""
    if originator is not None:
        bext = bytearray(602)
        bext[256:256 + len(originator)] = originator.encode()
        if when:
            bext[320:338] = when.encode()
        chunks += b"bext" + struct.pack("<I", len(bext)) + bytes(bext)
    fmt = struct.pack("<HHIIHH", 1, ch, rate, rate * ch * bits // 8, ch * bits // 8, bits)
    chunks += b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(data)) + data
    path.write_bytes(b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WAVE" + chunks)
    return str(path)


def test_wav_info_reads_bext_without_reading_audio(tmp_path):
    p = make_wav(tmp_path / "ZOOM0001.WAV", seconds=2, originator="ZOOM Handy Recorder H2n", when="2025-06-0619:20:19")
    i = F.wav_info(p)
    assert i["originator"] == "ZOOM Handy Recorder H2n" and i["channels"] == 2 and i["seconds"] == 2.0
    assert F.bext_time(i["bext_time"]) == datetime(2025, 6, 6, 19, 20, 19, tzinfo=TZ)


@pytest.mark.parametrize("when, ok", [("2025-06-0619:20:19", True), ("2011-01-0100:02:34", False),   # H2n clock unset
                                      ("2000-01-0100:12:11", False), ("", False)])            # Tascam, none
def test_clock_plausibility(when, ok):
    assert F.plausible(F.bext_time(when)) is ok


def test_take_time_falls_back_to_the_file_time():
    info = {"bext_time": "2011-01-0100:02:34", "seconds": 600}
    end = datetime(2026, 10, 25, 14, 40, tzinfo=TZ)
    start, clock_ok = F.take_time(info, end.timestamp())
    assert start == end - timedelta(minutes=10) and clock_ok is False
    assert F.take_time(info, datetime(2010, 1, 1, tzinfo=TZ).timestamp()) == (None, False)   # R16-style 2010 date


@pytest.mark.parametrize("path, info, want", [
    ("/r/Projects/Axehandle Hound/AHH214/AUDIO/MONO-003.WAV", {}, ("zoom-r16", "multitrack", "track 4")),
    ("/r/Projects/X/PROJ001/AUDIO/STE-000.WAV", {}, ("zoom-r16", "multitrack", "stereo 1")),
    ("/r/Field Recordings/zoom-h2n/b/STEREO/FOLDER01/ZOOM0007.WAV", {"originator": "ZOOM Handy Recorder H2n"},
     ("zoom-h2n", "handheld", None)),
    ("/r/FieldRecordings/Zoom/4CH/FOLDER01/SR001XY.WAV", {"originator": "ZOOM Handy Recorder H2n"},
     ("zoom-h2n", "handheld", "XY")),
    ("/r/FieldRecordings/000116_0029.wav", {"originator": "TASCAM PCM Recoder DR-07MK2    "},
     ("tascam-dr07mk2", "handheld", None)),
])
def test_classify(path, info, want):
    c = F.classify(path, info)
    assert (c["device"], c["kind"], c["track"]) == want


def test_4ch_ms_and_xy_are_one_take():
    a = F.classify("/r/4CH/FOLDER01/SR001MS.WAV", {"originator": "ZOOM Handy Recorder H2n"})
    b = F.classify("/r/4CH/FOLDER01/SR001XY.WAV", {"originator": "ZOOM Handy Recorder H2n"})
    assert a["take"] == b["take"] == "/r/4CH/FOLDER01/SR001"


def row(path, start=None, secs=60, kind="handheld", device="zoom-h2n", take=None, folder="/f", fs=None):
    return {"path": path, "device": device, "kind": kind, "take": take or path, "start": start,
            "seconds": secs, "folder": folder, "field_session": fs}


T0 = datetime(2026, 10, 25, 14, 0, tzinfo=TZ)


def test_new_session_after_a_30_minute_gap():
    rows = [row("a", T0, 600), row("b", T0 + timedelta(minutes=35), 60),                  # 25 min after a ended
            row("c", T0 + timedelta(minutes=36 + 31), 60)]                                # 30+ min after b
    s = F.group_sessions(rows)
    assert [[m["path"] for m in x["members"]] for x in s] == [["a", "b"], ["c"]]


def test_recorders_are_not_mixed_and_untimed_takes_group_by_folder():
    rows = [row("h", T0), row("t", T0 + timedelta(minutes=1), device="tascam-dr07mk2"),
            row("u1", None, folder="/FOLDER01"), row("u2", None, folder="/FOLDER01"), row("u3", None, folder="/FOLDER02")]
    s = F.group_sessions(rows)
    assert sorted(tuple(sorted(m["path"] for m in x["members"])) for x in s) == [("h",), ("t",), ("u1", "u2"), ("u3",)]


def test_r16_project_is_its_own_live_session_and_h2n_attaches_to_it():
    proj = "/r/Projects/Band/PROJ003"
    tracks = [row(f"{proj}/AUDIO/MONO-00{i}.WAV", T0, 1800, kind="multitrack", device="zoom-r16", take=proj) for i in range(8)]
    h2n = [row("room", T0 + timedelta(minutes=10), 900)]
    s = F.group_sessions(tracks + h2n)
    live = next(x for x in s if x["kind"] == "multitrack")
    hand = next(x for x in s if x["kind"] == "handheld")
    assert len(live["members"]) == 8 and live["key"] == f"m:{proj}"
    assert hand["attached_to"] == live["key"] and live["attached_to"] is None
    assert F.session_title(live) == "Live · PROJ003 · Zoom R16 · 8 tracks"


def test_attach_rule_is_one_way():
    hand, live = {"kind": "handheld"}, {"kind": "multitrack"}
    assert F.can_attach(hand, live) is None
    assert "R16 live session stands on its own" in F.can_attach(live, hand)
    assert F.can_attach(hand, hand)


def test_session_keys_survive_new_takes():
    first = F.group_sessions([row("a", T0, fs=None)])[0]["key"]
    again = F.group_sessions([row("z", T0 - timedelta(minutes=5)), row("a", T0, fs=first)])
    assert len(again) == 1 and again[0]["key"] == first          # an earlier take doesn't rename the session


@pytest.mark.parametrize("bits", [16, 24])
def test_first_seconds_become_16bit_mono(tmp_path, bits):
    p = make_wav(tmp_path / "x.wav", seconds=30, rate=8000, ch=2, bits=bits)
    out = F.first_seconds_wav(p, F.wav_info(p), secs=5)
    with wave.open(io.BytesIO(out)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (1, 2, 8000, 40000)


@pytest.mark.parametrize("text, note", [("Take three, chorus idea in D.", "Take three, chorus idea in D."),
                                        ("Thank you.", None), ("you", None), ("  Bridge   version two ", "Bridge version two")])
def test_clean_note(text, note):
    assert F.clean_note(text) == note


def test_verify_batch(tmp_path):
    b = tmp_path / "batch"
    (b / "STEREO" / "FOLDER01").mkdir(parents=True)
    wav = make_wav(b / "STEREO" / "FOLDER01" / "ZOOM0001.WAV")
    good = F.sha256(wav)
    size = (b / "STEREO" / "FOLDER01" / "ZOOM0001.WAV").stat().st_size
    (b / F.MANIFEST).write_text("sha256\tsize\tmtime\tpath\tdevice\n"
                                f"{good}\t{size}\t0\tSTEREO/FOLDER01/ZOOM0001.WAV\tzoom-h2n\n"
                                f"{'0' * 64}\t10\t0\tSTEREO/FOLDER01/ZOOM0002.WAV\tzoom-h2n\n")
    assert F.verify_batch(str(b)) == ("wait", ["OK STEREO/FOLDER01/ZOOM0001.WAV", "WAIT STEREO/FOLDER01/ZOOM0002.WAV"])
    (b / "STEREO" / "FOLDER01" / "ZOOM0002.WAV").write_bytes(b"x" * 10)
    assert F.verify_batch(str(b))[0] == "bad"                     # arrived, but not what the card had
    assert F.verify_batch(str(tmp_path / "nothing"))[0] == "wait"


def test_batch_paths_stay_inside_field_recordings():
    with pytest.raises(F.HTTPException):
        F._batch_dir("../../Projects")
    assert F._batch_dir("zoom-h2n/2026-10-25 1432").endswith("Field Recordings/zoom-h2n/2026-10-25 1432")


def test_untimed_title_names_the_folder_and_its_parent():
    s = F.group_sessions([row("u1", None, folder="/r/FieldRecordings/Zoom/4CH/FOLDER01")])[0]
    assert F.session_title(s) == "Field · 4CH/FOLDER01 (clock not set) · Zoom H2n · 1 take"


def make_m4a(path, seconds=90.0, created=None):
    """ftyp + mdat + moov/mvhd(v0) -- the atoms Voice Memos write (moov after the audio)."""
    def atom(kind, body):
        return struct.pack(">I", 8 + len(body)) + kind + body
    secs_1904 = int((created - F.MP4_EPOCH).total_seconds()) if created else 0
    mvhd = bytes(4) + struct.pack(">IIII", secs_1904, secs_1904, 1000, int(seconds * 1000)) + bytes(80)
    path.write_bytes(atom(b"ftyp", b"M4A \0\0\0\0") + atom(b"mdat", bytes(5000)) + atom(b"moov", atom(b"mvhd", mvhd)))
    return str(path)


def test_m4a_duration_and_recording_time(tmp_path):
    when = datetime(2026, 10, 24, 18, 5, 0, tzinfo=F.timezone.utc)
    p = make_m4a(tmp_path / "x.m4a", 93.5, when)
    i = F.media_info(p)
    assert i["seconds"] == 93.5 and i["created"] == when
    assert F.take_time(i, None) == ((when - timedelta(seconds=93.5)).astimezone(TZ), True)   # mvhd = end of the memo


def test_voice_memos_are_handheld():
    c = F.classify("/r/Field Recordings/iphone-voicememo/b/2026-10-24 2005 Chorus idea.m4a", {})
    assert (c["device"], c["kind"], c["track"]) == ("iphone-voicememo", "handheld", None)
    assert F.can_attach({"kind": "handheld"}, {"kind": "multitrack"}) is None      # may join an R16 session


def test_memo_time_order_manifest_then_file_then_old_name():
    info = {"created": None}
    assert F.take_time(info, None, "2026-10-24T20:05:00+02:00")[0] == datetime(2026, 10, 24, 20, 5, tzinfo=TZ)
    assert F.take_time(info, None, "", "/r/FieldRecordings/20120601 213251.m4a") == \
        (datetime(2012, 6, 1, 21, 32, 51, tzinfo=TZ), True)                     # the 41 old memos


@pytest.mark.parametrize("name, when", [("/r/VoiceRecordings/20200806-181719.m4a", datetime(2020, 8, 6, 18, 17, 19, tzinfo=TZ)),
                                        ("/r/FieldRecordings/20120601 213251.m4a", datetime(2012, 6, 1, 21, 32, 51, tzinfo=TZ)),
                                        ("/r/VoiceRecordings/6. June 2020 214336 Free.m4a", None)])
def test_old_memo_names(name, when):
    assert F._name_time(name) == when
