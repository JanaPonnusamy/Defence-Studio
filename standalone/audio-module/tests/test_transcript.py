from __future__ import annotations

from app.pipeline.transcript import load_transcript, parse_plain_text, parse_srt_or_vtt

SRT_SAMPLE = """1
00:00:00,000 --> 00:00:02,500
Hello world.

2
00:00:02,500 --> 00:00:05,000
This is a test.
"""

VTT_SAMPLE = """WEBVTT

00:00:00.000 --> 00:00:02.500
Hello world.

00:00:02.500 --> 00:00:05.000
This is a test.
"""


def test_parse_srt():
    segs = parse_srt_or_vtt(SRT_SAMPLE)
    assert len(segs) == 2
    assert segs[0].text == "Hello world."
    assert segs[0].start == 0.0
    assert segs[0].end == 2.5
    assert segs[1].end == 5.0


def test_parse_vtt():
    segs = parse_srt_or_vtt(VTT_SAMPLE)
    assert len(segs) == 2
    assert segs[1].text == "This is a test."


def test_parse_plain_text_sentences():
    segs = parse_plain_text("First sentence. Second sentence! Third one?")
    assert len(segs) == 3
    assert segs[0].text == "First sentence."
    assert all(s.start == 0.0 and s.end == 0.0 for s in segs)


def test_load_transcript_dispatch():
    assert len(load_transcript(SRT_SAMPLE, "srt")) == 2
    assert len(load_transcript("One. Two.", "plain")) == 2
