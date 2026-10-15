import struct
from pathlib import Path
import pytest
from defence_studio.integrations.media_qc import font_codepoints, missing_glyphs
from defence_studio.integrations.subtitles import resolve_font, SubtitleError


def font_fixture(collection=False):
    # Minimal cmap format 12: A maps to glyph zero (.notdef), B to glyph one.
    sub=struct.pack('>HHIII',12,0,28,0,1)+struct.pack('>III',65,66,0)
    cmap=struct.pack('>HHHHI',0,1,3,10,12)+sub
    start=16 if collection else 0
    sfnt=struct.pack('>IHHHH',0x10000,1,0,0,0)+struct.pack('>4sIII',b'cmap',0,start+28,len(cmap))+cmap
    return (struct.pack('>4sIII',b'ttcf',0x10000,1,16) if collection else b'')+sfnt


@pytest.mark.parametrize('collection',[False,True])
def test_glyph_zero_is_missing_in_ttf_and_collection(tmp_path,collection):
    font=tmp_path/('font.ttc' if collection else 'font.ttf')
    font.write_bytes(font_fixture(collection))
    assert font_codepoints(font)=={66}
    assert missing_glyphs('AB',font)==['A']


def test_resolved_tamil_font_can_be_checked_by_qc():
    try:
        font,_=resolve_font('ta')
    except SubtitleError as exc:
        pytest.skip(str(exc))
    assert missing_glyphs('தமிழ் செய்தி இந்தியா',font)==[]
