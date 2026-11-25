"""Tests for the loop editor's pure parts: Ogg header reader, loop numbers, waveform data,
playback rules, decoder wrapper. No Qt and no sound device needed.

The OGG files in tests/data were made with ffmpeg (libvorbis); see tests/data/README.md."""

import importlib.machinery
import json
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "rpg_asset_tools"))

try:
    import numpy as np
except ImportError:                      # numpy is optional (only the loop editor's drawing/playing needs it)
    np = None

import audiodecode  # noqa: E402
import enginetags  # noqa: E402
import loopmath  # noqa: E402
import oggmeta  # noqa: E402

if np is not None:
    import loopplayback  # noqa: E402
    import waveform  # noqa: E402

needs_numpy = unittest.skipIf(np is None, "numpy is not installed")
DATA = HERE / "data"


# --------------------------------------------------------------------------
class TestOggMeta(unittest.TestCase):
    def test_tagged_file(self):
        info = oggmeta.read_ogg_info(DATA / "loop_tagged.ogg")
        self.assertEqual((info.sample_rate, info.channels), (44100, 2))
        tags = oggmeta.loop_tags(info)
        self.assertEqual((tags.start, tags.length), (22050, 44100))
        self.assertEqual(tags.notes, ())

    def test_no_tags(self):
        info = oggmeta.read_ogg_info(DATA / "no_tags.ogg")
        self.assertEqual((info.sample_rate, info.channels), (22050, 1))
        tags = oggmeta.loop_tags(info)
        self.assertEqual((tags.start, tags.length, tags.notes), (None, None, ()))

    def test_lower_case_names_are_read_and_noted(self):
        info = oggmeta.read_ogg_info(DATA / "lower_case.ogg")
        self.assertEqual(info.sample_rate, 48000)
        tags = oggmeta.loop_tags(info)
        self.assertEqual((tags.start, tags.length), (1000, 5000))
        self.assertEqual(len(tags.notes), 2)
        self.assertIn("upper case", tags.notes[0])

    def test_start_only(self):
        tags = oggmeta.loop_tags(oggmeta.read_ogg_info(DATA / "start_only.ogg"))
        self.assertEqual((tags.start, tags.length), (30000, None))

    def test_comment_block_spanning_many_pages(self):
        info = oggmeta.read_ogg_info(DATA / "big_comment.ogg")
        sizes = {n: len(v) for n, v in info.comments}
        self.assertEqual(sizes["DESCRIPTION"], 70000)       # ffmpeg renames COMMENT to DESCRIPTION
        tags = oggmeta.loop_tags(info)
        self.assertEqual((tags.start, tags.length), (100, 2000))

    def test_opus_is_refused_in_plain_words(self):
        with self.assertRaises(oggmeta.OggError) as cm:
            oggmeta.read_ogg_info(DATA / "opus.ogg")
        self.assertIn("Opus", str(cm.exception))
        self.assertIn("Vorbis", str(cm.exception))

    def test_bad_files(self):
        good = (DATA / "loop_tagged.ogg").read_bytes()
        cases = {
            "empty": b"",
            "text": b"hello, this is not audio at all\n" * 5,
            "cut in the first page": good[:20],
            "cut in the comments": good[:130],
        }
        with tempfile.TemporaryDirectory() as d:
            for name, blob in cases.items():
                p = Path(d) / "x.ogg"
                p.write_bytes(blob)
                with self.subTest(name), self.assertRaises(oggmeta.OggError):
                    oggmeta.read_ogg_info(p)

    def test_missing_file(self):
        with self.assertRaises(oggmeta.OggError) as cm:
            oggmeta.read_ogg_info(DATA / "does_not_exist.ogg")
        self.assertIn("could not be read", str(cm.exception))

    def test_header_agrees_with_ffprobe(self):
        probe = shutil.which("ffprobe")
        if not probe:
            self.skipTest("ffprobe not installed")
        for f in sorted(DATA.glob("*.ogg")):
            if f.name == "opus.ogg":
                continue
            out = subprocess.run(
                [probe, "-v", "error", "-select_streams", "a:0", "-show_entries",
                 "stream=sample_rate,channels", "-of", "json", str(f)],
                capture_output=True, text=True, check=True).stdout
            s = json.loads(out)["streams"][0]
            info = oggmeta.read_ogg_info(f)
            with self.subTest(f.name):
                self.assertEqual((info.sample_rate, info.channels),
                                 (int(s["sample_rate"]), int(s["channels"])))

    def test_loop_tags_odd_values(self):
        def tags(*pairs):
            return oggmeta.loop_tags(oggmeta.OggInfo(44100, 2, "v", tuple(pairs)))
        t = tags(("LOOPSTART", "9" * 5000))                 # beyond Python's digit limit: no crash
        self.assertIsNone(t.start)
        self.assertTrue(t.notes)
        t = tags(("LOOPSTART", "abc"), ("LOOPLENGTH", "-5"))
        self.assertEqual((t.start, t.length), (None, None))
        self.assertEqual(len(t.notes), 2)
        t = tags(("LOOPSTART", " 120 "), ("LOOPSTART", "999"), ("LOOPLENGTH", "7"))
        self.assertEqual((t.start, t.length), (120, 7))
        self.assertIn("more than once", t.notes[0])
        t = tags(("LOOPSTART", "٣"))            # a non-ASCII digit is not a number here
        self.assertIsNone(t.start)
        t = tags(("TITLE", "x"), ("loopstart", "5"))
        self.assertEqual(t.start, 5)


# --------------------------------------------------------------------------
def ogg_header(comments, rate=44100, split_after=None):
    """A minimal Ogg Vorbis header (identification + comment pages) built by hand, for the
    engine-reader quirks. `comments` is a list of byte strings (each 'NAME=value')."""
    import struct

    def page(body, seq, header_type=0):
        segs, n = [], len(body)
        while n >= 255:
            segs.append(255)
            n -= 255
        segs.append(n)                     # a body that is an exact multiple of 255 ends with a 0
        return (b"OggS\x00" + bytes([header_type]) + b"\0" * 8 + b"\1\0\0\0"
                + struct.pack("<I", seq) + b"\0\0\0\0" + bytes([len(segs)]) + bytes(segs) + body)
    ident = b"\x01vorbis" + struct.pack("<IBIiii", 0, 2, rate, 0, 0, 0) + b"\xb8\x01"
    vendor = b"test"
    body = b"\x03vorbis" + struct.pack("<I", len(vendor)) + vendor + struct.pack("<I", len(comments))
    for c in comments:
        body += struct.pack("<I", len(c)) + c
    return page(ident, 0, 2) + page(body + b"\x01", 1)


class TestEngineTags(unittest.TestCase):
    def fixture(self, name):
        return enginetags.read_engine_tags_from_file(DATA / name)

    def test_normal_file(self):
        t = self.fixture("loop_tagged.ogg")
        self.assertEqual((t.sample_rate, t.loop_start, t.loop_length), (44100, 22050, 44100))

    def test_lower_case_names_are_not_found(self):
        t = self.fixture("lower_case.ogg")
        self.assertEqual((t.loop_start, t.loop_length), (0, 0))

    def test_start_only(self):
        t = self.fixture("start_only.ogg")
        self.assertEqual((t.loop_start, t.loop_length), (30000, 0))
        self.assertFalse(enginetags.engine_loop(t, 100000).from_tags)

    def test_tags_after_a_page_spanning_comment_block_are_lost(self):
        t = self.fixture("big_comment.ogg")
        self.assertEqual((t.loop_start, t.loop_length), (0, 0))

    def test_opus_has_no_rate(self):
        self.assertEqual(self.fixture("opus.ogg").sample_rate, 0)

    def test_hand_built_header(self):
        t = enginetags.read_engine_tags(ogg_header([b"LOOPSTART=100", b"LOOPLENGTH=2000"], rate=48000))
        self.assertEqual((t.sample_rate, t.loop_start, t.loop_length), (48000, 100, 2000))

    def test_digit_after_the_number_is_swallowed(self):
        # the next comment's length byte is '0'..'9' (48..57 bytes) and ends up in the number
        nxt = b"TITLE=" + b"x" * 42                       # 48 bytes long -> length byte 0x30 = '0'
        self.assertEqual(len(nxt), 48)
        t = enginetags.read_engine_tags(ogg_header([b"LOOPSTART=100", nxt, b"LOOPLENGTH=2000"]))
        self.assertEqual(t.loop_start, 1000)

    def test_no_such_digit_means_no_problem(self):
        nxt = b"TITLE=" + b"x" * 20
        t = enginetags.read_engine_tags(ogg_header([b"LOOPSTART=100", nxt, b"LOOPLENGTH=2000"]))
        self.assertEqual((t.loop_start, t.loop_length), (100, 2000))

    def test_loop_length_zero_means_whole_file(self):
        t = enginetags.read_engine_tags(ogg_header([b"LOOPSTART=100", b"LOOPLENGTH=0"]))
        el = enginetags.engine_loop(t, 5000)
        self.assertEqual((el.start, el.length, el.from_tags), (0, 5000, False))

    def test_length_only_starts_at_zero(self):
        t = enginetags.read_engine_tags(ogg_header([b"LOOPLENGTH=700"]))
        el = enginetags.engine_loop(t, 5000)
        self.assertEqual((el.start, el.length, el.from_tags), (0, 700, True))

    def test_truncated_data_does_not_crash(self):
        data = ogg_header([b"LOOPSTART=100", b"LOOPLENGTH=2000"])
        for cut in range(0, len(data), 7):
            enginetags.read_engine_tags(data[:cut])
        enginetags.read_engine_tags(b"")


# --------------------------------------------------------------------------
class TestLoopMath(unittest.TestCase):
    def view(self, start, length, total=1000):
        """A file whose tags are read the same way by the game and by the Vorbis rules."""
        eng = enginetags.EngineTags(44100, start or 0, length or 0)
        return loopmath.resolve_loop(eng, total, oggmeta.LoopTags(start, length, ()))

    def test_complete(self):
        v = self.view(100, 300)
        self.assertEqual((v.start, v.end, v.length, v.status), (100, 400, 300, loopmath.COMPLETE))
        self.assertEqual(v.notes, ())

    def test_end_exactly_at_file_end_is_fine(self):
        self.assertEqual(self.view(200, 800).status, loopmath.COMPLETE)

    def test_no_tags_loops_the_whole_file(self):
        v = self.view(None, None)
        self.assertEqual((v.start, v.end, v.status), (0, 1000, loopmath.NONE))

    def test_start_only_is_ignored_by_the_game(self):
        v = self.view(250, None)
        self.assertEqual((v.start, v.end, v.status), (0, 1000, loopmath.START_ONLY))
        self.assertTrue(v.notes)

    def test_length_only_starts_at_zero(self):
        v = self.view(None, 400)
        self.assertEqual((v.start, v.end, v.status), (0, 400, loopmath.LENGTH_ONLY))

    def test_values_that_do_not_fit(self):
        v = self.view(5000, 10)
        self.assertEqual((v.start, v.end, v.status), (0, 1000, loopmath.INVALID))
        v = self.view(900, 500)                      # ends after the file: shown until the end
        self.assertEqual((v.start, v.end, v.status), (900, 1000, loopmath.INVALID))
        v = self.view(1000, 5)                       # start exactly at the end: nothing to loop
        self.assertEqual(v.status, loopmath.INVALID)

    def test_game_and_standard_disagree(self):
        eng = enginetags.EngineTags(44100, 0, 0)     # the game finds nothing...
        v = loopmath.resolve_loop(eng, 1000, oggmeta.LoopTags(100, 300, ()))   # ...the file has tags
        self.assertEqual((v.start, v.end, v.status), (0, 1000, loopmath.NONE))
        self.assertIn("does not find LOOPSTART", " ".join(v.notes))
        eng = enginetags.EngineTags(44100, 1000, 300)
        v = loopmath.resolve_loop(eng, 5000, oggmeta.LoopTags(100, 300, ()))
        self.assertEqual(v.start, 1000)
        self.assertIn("reads LOOPSTART as 1000", " ".join(v.notes))

    def test_empty_audio(self):
        v = self.view(0, 10, total=0)
        self.assertEqual((v.start, v.end), (0, 0))

    def test_format_time(self):
        self.assertEqual(loopmath.format_time(66150, 44100), "0:01.500")
        self.assertEqual(loopmath.format_time(0, 44100), "0:00.000")
        self.assertEqual(loopmath.format_time(44100 * 125, 44100), "2:05.000")
        self.assertEqual(loopmath.format_time(-22050, 44100), "-0:00.500")
        self.assertEqual(loopmath.format_time(5, 0), "0:00.000")

    def test_format_time_with_a_huge_value_does_not_overflow(self):
        self.assertIn(":", loopmath.format_time(10 ** 400, 44100))

    def test_seconds(self):
        self.assertAlmostEqual(loopmath.frames_to_seconds(132300, 44100), 3.0)
        self.assertEqual(loopmath.frames_to_seconds(5, 0), 0.0)

    def test_seam_start(self):
        v = self.view(100, 300)
        self.assertEqual(loopmath.seam_start(v, 100, 1.0), 300)
        self.assertEqual(loopmath.seam_start(v, 44100), 0)           # never before the start

    def test_tick_step(self):
        last = 0
        for span in (0.001, 0.05, 1, 10, 100, 1000, 3600):      # up to an hour of audio
            step = loopmath.nice_tick_seconds(span, 900)
            self.assertGreaterEqual(step * 900 / span, 90 - 1e-9)
            self.assertGreaterEqual(step, last)
            last = step
        self.assertEqual(loopmath.nice_tick_seconds(0, 900), 1.0)


# --------------------------------------------------------------------------
@needs_numpy
class TestWaveform(unittest.TestCase):
    def brute(self, s, start, end, width):
        n = s.shape[0]
        spp = (end - start) / width
        f, g = s.min(axis=1), s.max(axis=1)
        lo = np.zeros(width, np.float32)
        hi = np.zeros(width, np.float32)
        edges = start + spp * np.arange(width + 1)
        for i in range(width):
            if spp < 1:
                a = int(np.floor(edges[i]))
                if 0 <= a < n:
                    lo[i], hi[i] = f[a], g[a]
            else:
                a = max(0, int(np.floor(edges[i])))
                b = min(n, int(np.floor(edges[i + 1])))
                if b > a:
                    lo[i], hi[i] = f[a:b].min(), g[a:b].max()
        return lo, hi

    def test_fine_zoom_matches_brute_force_exactly(self):
        rng = np.random.default_rng(1)
        for n, ch in [(1000, 1), (50000, 2), (7, 1), (64, 2), (65, 1)]:
            s = rng.standard_normal((n, ch)).astype(np.float32)
            pyr = waveform.build_pyramid(s)
            for start, end, width in [(0, n, 800), (0, n, 3), (n * .25, n * .5, 500), (-100, n + 100, 400),
                                      (n - 50, n + 50, 300), (10, 30, 200), (n * .5, n * .5 + 40, 640),
                                      (-500, -10, 100), (n + 5, n + 500, 100), (0, n, 1)]:
                if (end - start) / width >= waveform.BASE:
                    continue                      # coarse levels are checked below
                lo, hi = waveform.peaks(pyr, start, end, width)
                bl, bh = self.brute(s, start, end, width)
                with self.subTest(n=n, ch=ch, start=start, end=end, width=width):
                    np.testing.assert_array_equal(lo, bl)
                    np.testing.assert_array_equal(hi, bh)

    def test_coarse_levels_keep_peaks(self):
        n = 400_000
        s = np.zeros((n, 2), np.float32)
        s[123_456] = (1.0, 0.5)
        s[300_001] = (-0.9, -1.0)
        pyr = waveform.build_pyramid(s)
        self.assertGreater(len(pyr.sizes), 3)
        lo, hi = waveform.peaks(pyr, 0, n, 500)
        self.assertEqual(hi.max(), 1.0)
        self.assertEqual(lo.min(), -1.0)
        self.assertFalse(np.isnan(lo).any() or np.isnan(hi).any())
        col = int(np.argmax(hi))                       # the spike is in (or next to) the right column
        self.assertLessEqual(abs(col - 123_456 * 500 // n), 1)

    def test_spike_in_the_last_partial_bucket_is_drawn(self):
        s = np.zeros((1000, 1), np.float32)
        s[999] = 1.0
        pyr = waveform.build_pyramid(s)
        lo, hi = waveform.peaks(pyr, 0, 1000, 10)            # 100 samples per column: level of 64
        self.assertEqual(hi[-1], 1.0)
        self.assertEqual(hi[:-1].max(), 0.0)

    def test_repaint_only_folds_what_is_on_screen(self):
        s = np.random.default_rng(2).standard_normal((200_000, 2)).astype(np.float32)
        pyr = waveform.build_pyramid(s)
        calls = []
        real = waveform._folded
        waveform._folded = lambda p, a=0, b=None: (calls.append((a, b)), real(p, a, b))[1]
        try:
            waveform.peaks(pyr, 100_000, 100_400, 100)         # 4 samples per column
            waveform.peaks(pyr, 150_000, 150_050, 100)         # deep zoom
        finally:
            waveform._folded = real
        for a, b in calls:
            self.assertLess(b - a, 1000)

    def test_peaks_outside_audio_are_zero(self):
        s = np.ones((1000, 1), np.float32)
        pyr = waveform.build_pyramid(s)
        lo, hi = waveform.peaks(pyr, -1000, 0, 50)
        self.assertEqual((lo.max(), hi.max()), (0.0, 0.0))
        lo, hi = waveform.peaks(pyr, 0, 2000, 100)
        self.assertEqual(hi[0], 1.0)
        self.assertEqual(hi[-1], 0.0)

    def test_empty_and_mono_1d(self):
        pyr = waveform.build_pyramid(np.zeros((0, 2), np.float32))
        lo, hi = waveform.peaks(pyr, 0, 10, 5)
        self.assertEqual(lo.tolist(), [0.0] * 5)
        pyr = waveform.build_pyramid(np.arange(100, dtype=np.float32))
        self.assertEqual(pyr.frames, 100)

    def test_samples_in_view(self):
        s = np.arange(20, dtype=np.float32).reshape(10, 2)
        pyr = waveform.build_pyramid(s)
        frames, vals = waveform.samples_in_view(pyr, 2.5, 5.2)
        self.assertEqual(frames.tolist(), [2, 3, 4, 5, 6])
        self.assertEqual(vals.tolist(), [4.5, 6.5, 8.5, 10.5, 12.5])
        frames, vals = waveform.samples_in_view(pyr, 50, 60)
        self.assertEqual(len(frames), 0)


# --------------------------------------------------------------------------
@needs_numpy
class TestLoopPlayback(unittest.TestCase):
    ramp = np.arange(10, dtype=np.float32).reshape(10, 1) if np is not None else None

    def test_jump_is_exact_and_can_fall_in_the_middle_of_a_chunk(self):
        out, pos = loopplayback.render(self.ramp, 0, 25, 3, 7)
        self.assertEqual(out[:, 0].astype(int).tolist(),
                         [0, 1, 2, 3, 4, 5, 6, 3, 4, 5, 6, 3, 4, 5, 6, 3, 4, 5, 6, 3, 4, 5, 6, 3, 4])
        self.assertEqual(pos, 5)

    def test_chunks_join_without_a_gap(self):
        whole, _ = loopplayback.render(self.ramp, 0, 40, 2, 9)
        pos, parts = 0, []
        for n in (1, 6, 2, 11, 7, 13):
            chunk, pos = loopplayback.render(self.ramp, pos, n, 2, 9)
            parts.append(chunk)
        np.testing.assert_array_equal(np.concatenate(parts), whole)

    def test_play_once_stops_at_the_end(self):
        out, pos = loopplayback.render(self.ramp, 6, 10, 0, None)
        self.assertEqual(out[:, 0].astype(int).tolist(), [6, 7, 8, 9])
        self.assertEqual(pos, 10)
        out, pos = loopplayback.render(self.ramp, pos, 10, 0, None)
        self.assertEqual(len(out), 0)

    def test_start_after_the_loop_end_goes_back_to_the_loop(self):
        out, pos = loopplayback.render(self.ramp, 8, 3, 2, 6)
        self.assertEqual(out[:, 0].astype(int).tolist(), [2, 3, 4])

    def test_whole_file_loop(self):
        out, pos = loopplayback.render(self.ramp, 8, 5, 0, 10)
        self.assertEqual(out[:, 0].astype(int).tolist(), [8, 9, 0, 1, 2])

    def test_advance_matches_render(self):
        rng = np.random.default_rng(3)
        for _ in range(200):
            total = int(rng.integers(5, 40))
            s = np.arange(total, dtype=np.float32).reshape(total, 1)
            ls = int(rng.integers(0, total - 1))
            le = int(rng.integers(ls + 1, total + 1))
            pos = int(rng.integers(0, total))
            k = int(rng.integers(0, 200))
            _, new_pos = loopplayback.render(s, pos, k, ls, le)
            self.assertEqual(loopplayback.advance(pos, k, total, ls, le), new_pos,
                             (total, ls, le, pos, k))
        self.assertEqual(loopplayback.advance(3, 100, 10, 0, None), 10)

    def test_bad_loop_is_refused(self):
        for bad in [(5, 5), (6, 5), (0, 11), (-1, 4)]:
            with self.subTest(bad), self.assertRaises(ValueError):
                loopplayback.render(self.ramp, 0, 4, *bad)

    def test_pcm16(self):
        data = np.array([[0.0, 1.0], [-1.0, 2.0], [0.5, -2.0]], dtype=np.float32)
        out = np.frombuffer(loopplayback.to_pcm16(data), dtype="<i2")
        self.assertEqual(out.tolist(), [0, 32767, -32767, 32767, 16383, -32768])


# --------------------------------------------------------------------------
class TestAudioDecode(unittest.TestCase):
    def setUp(self):
        self._saved = sys.modules.get("soundfile", "absent")

    def tearDown(self):
        if self._saved == "absent":
            sys.modules.pop("soundfile", None)
        else:
            sys.modules["soundfile"] = self._saved

    def fake_soundfile(self, reader):
        mod = types.ModuleType("soundfile")
        mod.__spec__ = importlib.machinery.ModuleSpec("soundfile", None)
        mod.read = reader
        sys.modules["soundfile"] = mod

    def test_missing_package_gives_install_instructions(self):
        sys.modules["soundfile"] = None                     # makes `import soundfile` fail
        self.assertIn("soundfile", audiodecode.missing_dependencies())
        with self.assertRaises(audiodecode.DecodeError) as cm:
            audiodecode.decode("x.ogg")
        self.assertIn(audiodecode.INSTALL_COMMAND, str(cm.exception))

    @needs_numpy
    def test_decode_shapes_and_extra_channels(self):
        seen = {}

        def reader(path, dtype, always_2d):
            seen.update(path=path, dtype=dtype, always_2d=always_2d)
            return np.zeros((50, 6), dtype=np.float32)[:, ::1], 48000
        self.fake_soundfile(reader)
        self.assertEqual(audiodecode.missing_dependencies(), [])
        got = audiodecode.decode(Path("a.ogg"))
        self.assertEqual((got.frames, got.channels, got.sample_rate, got.dropped_channels),
                         (50, 2, 48000, 4))
        self.assertEqual(got.samples.dtype, np.float32)
        self.assertTrue(got.samples.flags["C_CONTIGUOUS"])
        self.assertEqual((seen["dtype"], seen["always_2d"]), ("float32", True))

    @needs_numpy
    def test_decoder_errors_become_plain_messages(self):
        def reader(path, dtype, always_2d):
            raise RuntimeError("Error opening 'a.ogg': Format not recognised.")
        self.fake_soundfile(reader)
        with self.assertRaises(audiodecode.DecodeError) as cm:
            audiodecode.decode("a.ogg")
        self.assertIn("could not be read", str(cm.exception))

    @needs_numpy
    def test_real_decoder_agrees_with_ffmpeg(self):
        """Only runs where soundfile and ffmpeg are both installed."""
        sys.modules.pop("soundfile", None)
        if audiodecode.missing_dependencies() or not shutil.which("ffmpeg"):
            self.skipTest("soundfile or ffmpeg not installed")
        f = DATA / "loop_tagged.ogg"
        got = audiodecode.decode(f)
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(f), "-f", "f32le", "-"],
                             capture_output=True, check=True).stdout
        ref = np.frombuffer(raw, dtype="<f4").reshape(-1, got.channels)
        self.assertEqual(got.sample_rate, 44100)
        self.assertEqual(got.frames, ref.shape[0])
        self.assertLess(float(np.abs(got.samples - ref).max()), 1e-3)


if __name__ == "__main__":
    unittest.main()
