"""Safe, readable text projection for stored HTML email bodies."""

from __future__ import annotations

import html.parser
import re


class _HTMLTextExtractor(html.parser.HTMLParser):
    _BLOCK_TAGS = frozenset(
        {
            "address",
            "article",
            "br",
            "div",
            "footer",
            "header",
            "li",
            "p",
            "section",
            "table",
            "tr",
        }
    )
    _NON_CONTENT_TAGS = frozenset({"head", "script", "style", "template"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.non_content_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.lower()
        if normalized_tag in self._NON_CONTENT_TAGS:
            self.non_content_depth += 1
        elif self.non_content_depth == 0 and normalized_tag in self._BLOCK_TAGS:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.lower()
        if normalized_tag in self._NON_CONTENT_TAGS and self.non_content_depth:
            self.non_content_depth -= 1
        elif self.non_content_depth == 0 and normalized_tag in self._BLOCK_TAGS:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if self.non_content_depth == 0:
            self.parts.append(data)

    def text(self) -> str:
        return re.sub(r"\s+", " ", "".join(self.parts)).strip()


def html_to_text(value: str) -> str:
    """Return visible text without executing or retaining HTML implementation data."""

    parser = _HTMLTextExtractor()
    parser.feed(value)
    parser.close()
    return parser.text()
