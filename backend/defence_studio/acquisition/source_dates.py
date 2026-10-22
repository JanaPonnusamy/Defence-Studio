"""Read an explicit PIB release date; never infer dates from retrieval time or URLs."""
import re
from datetime import date
from html.parser import HTMLParser
from urllib.parse import urlsplit


class _VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skipped = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skipped += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skipped:
            self.skipped -= 1

    def handle_data(self, data):
        if not self.skipped:
            self.parts.append(data)


def pib_publication_date(html: str, url: str) -> str | None:
    """Date-only ISO value from PIB's labelled release date; ambiguous values stay unknown."""
    try:
        if (urlsplit(url).hostname or "").lower() not in ("www.pib.gov.in", "pib.gov.in"):
            return None
        parser = _VisibleText()
        parser.feed(html)
        parser.close()
        text = " ".join(" ".join(parser.parts).split())
        matches = re.findall(r"(?:Posted\s+On|प्रविष्टि\s+तिथि)\s*:\s*(\d{1,2})\s+"
                             r"(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s+(\d{4})\b", text, re.I)
        months = {m: i for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split(), 1)}
        values = {date(int(y), months[m.upper()], int(d)).isoformat() for d, m, y in matches}
        return values.pop() if len(values) == 1 else None
    except (ValueError, TypeError):
        return None
