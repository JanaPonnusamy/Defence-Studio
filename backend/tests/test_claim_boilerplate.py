"""Regression: page chrome / citation fragments must not become claim candidates.

Fixtures reproduce the boilerplate observed in the 2026-10-15 S-400 acquisition
(CSIS Missile Threat page) next to representative substantive S-400 sentences.
"""

from defence_studio.analysis.claims import _sentence_spans, extract_claims
from defence_studio.analysis.normalize import normalize_html
from defence_studio.contracts.analysis import AnalysisLimits
import pytest

TOPIC = "S-400 Triumf air defence system"

PAGE = """
<html><head><title>S-400 Triumf | Missile Threat</title></head><body>
<header><a href="#c">Skip to the content</a> <span>facebook instagram linkedin twitter youtube</span></header>
<nav><ul><li>Missile Defense Project</li><li>Space Launch Capacity and Contenders</li></ul></nav>
<main><article>
<h1>S-400 Triumf</h1>
<h2>System Component: S-400 Triumf</h2>
<p>The S-400 Triumf (NATO: SA-21 Growler) is a mobile, surface-to-air missile system designed by Russia.
The S-400's capabilities are roughly comparable to the U.S. Patriot system.</p>
<p>The first S-400 systems became operational in 2007 and entered service with the First Air Defense Corps.</p>
<p>S-400 Basing Mobile, ground-based Warhead 143 kg</p>
<p>Source: Foss, S-400, Jane's (London: IHS Global, 2016), see http://www.
example.com/news/s-400.</p>
<p>The deployment status of the S-400 40N6 missile is unclear and remains unconfirmed.</p>
</article></main>
<aside><p>On September 9, 2026, analysts discussed space launch capacity.</p></aside>
<footer>&copy; 2026 Center for Strategic and International Studies. Subscribe to our newsletter.</footer>
<button>Print this page</button><noscript>Enable JavaScript to view menus</noscript>
</body></html>
"""


def _claims(text):
    return [c.claim_text for c in extract_claims(
        source_id="s1", text=text, language="en", cues=(), artifact_uri=None,
        source_published_at=None, acquisition_timestamp=None, topic=TOPIC,
        limits=AnalysisLimits())]


def test_html_chrome_is_removed_but_article_text_survives():
    text = normalize_html(PAGE).text
    for chrome in ("Skip to the content", "facebook", "Space Launch Capacity", "Subscribe",
                   "Print this page", "Enable JavaScript", "September 9, 2026"):
        assert chrome not in text
    assert "mobile, surface-to-air missile system" in text
    assert "in 2007" in text


def test_boilerplate_and_citation_fragments_are_not_claims():
    claims = _claims(normalize_html(PAGE).text)
    joined = " | ".join(claims)
    assert "Skip to" not in joined and "©" not in joined and "http" not in joined
    assert "example.com" not in joined
    assert "System Component: S-400 Triumf" not in claims      # bare heading
    assert "S-400 Triumf" not in claims


def test_substantive_s400_claims_are_retained_verbatim():
    claims = _claims(normalize_html(PAGE).text)
    assert any("(NATO: SA-21 Growler) is a mobile" in c for c in claims)
    assert any("became operational in 2007" in c for c in claims)
    assert any("Warhead 143 kg" in c for c in claims)           # spec row has a quantity
    unclear = [c for c in claims if "40N6" in c]
    assert unclear and "unclear" in unclear[0] and "unconfirmed" in unclear[0]  # qualifiers kept


def test_us_abbreviation_does_not_truncate_a_claim():
    spans = [t for _, _, t in _sentence_spans("It is comparable to the U.S. Patriot system, said Dr. Lee. Next.")]
    assert spans[0] == "It is comparable to the U.S. Patriot system, said Dr. Lee."
    claims = _claims("The S-400's capabilities are roughly comparable to the U.S. Patriot system.")
    assert claims and claims[0].endswith("U.S. Patriot system.")


def test_offsets_still_index_the_normalized_text():
    text = normalize_html(PAGE).text
    for c in extract_claims(source_id="s1", text=text, language="en", cues=(), artifact_uri=None,
                            source_published_at=None, acquisition_timestamp=None, topic=TOPIC,
                            limits=AnalysisLimits()):
        assert text[c.passage.start_offset:c.passage.end_offset] == c.claim_text


def test_extraction_never_marks_claims_approved_or_eligible():
    for c in extract_claims(source_id="s1", text=normalize_html(PAGE).text, language="en", cues=(),
                            artifact_uri=None, source_published_at=None, acquisition_timestamp=None,
                            topic=TOPIC, limits=AnalysisLimits()):
        assert not hasattr(c, "approved") and not getattr(c, "eligible", False)


def test_server_form_keeps_article_but_excludes_hidden_print_copy_and_attributes():
    sentence = "The S-400 system reportedly entered service in 2007."
    page = f'''<html><body><form id="form1">
      <p>{sentence}</p><div style="DISPLAY : none !important"><p>{sentence}</p></div>
      <div style="visibility: hidden">The S-400 allegedly entered service in 2008.</div>
      <input type="hidden" value="&lt;p&gt;The S-400 entered service in 2009.&lt;/p&gt;">
      <button>The S-400 site has 20 sharing options.</button>
      <div hidden>The S-400 entered service in 2010.</div>
    </form></body></html>'''
    text = normalize_html(page).text
    assert text == sentence
    assert _claims(text) == [sentence]


@pytest.mark.parametrize("container", ['<div hidden>', '<div aria-hidden="true">',
                                         '<div style="display:none">', '<nav>'])
def test_hidden_only_page_does_not_restore_excluded_text(container):
    closing = "nav" if container == "<nav>" else "div"
    page = f"{container}The S-400 system entered service in 2007.</{closing}>"
    assert normalize_html(page).text == ""


def test_hidden_article_does_not_turn_later_page_header_into_content():
    page = ('<article hidden>Hidden print copy.</article>'
            '<header>Site navigation and sharing links.</header>'
            '<article><p>The S-400 reportedly entered service in 2007.</p></article>')
    assert normalize_html(page).text == "The S-400 reportedly entered service in 2007."
