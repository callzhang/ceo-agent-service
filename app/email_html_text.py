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


def visible_email_text(value: str) -> str:
    """Remove a leading stylesheet left by older HTML-to-text extraction."""

    end = _leading_stylesheet_end(value)
    return value[end:].lstrip() if end else value


def _leading_stylesheet_end(value: str) -> int:
    position = _skip_css_whitespace_and_comments(value, 0)
    if position >= len(value) or (position == 0 and not value.startswith("@", position)):
        return 0
    consumed_rule = False
    while position < len(value):
        opening = _next_unquoted(value, position, "{")
        if opening is None:
            return position if consumed_rule else 0
        closing = _matching_brace(value, opening)
        if closing is None:
            return 0
        consumed_rule = True
        position = _skip_css_whitespace_and_comments(value, closing + 1)
    return position if consumed_rule else 0


def _skip_css_whitespace_and_comments(value: str, position: int) -> int:
    while position < len(value):
        if value[position].isspace():
            position += 1
            continue
        if value.startswith("/*", position):
            end = value.find("*/", position + 2)
            if end < 0:
                return len(value)
            position = end + 2
            continue
        return position
    return position


def _next_unquoted(value: str, position: int, target: str) -> int | None:
    quote = ""
    while position < len(value):
        character = value[position]
        if quote:
            if character == "\\":
                position += 2
                continue
            if character == quote:
                quote = ""
        elif character in {"'", '"'}:
            quote = character
        elif value.startswith("/*", position):
            end = value.find("*/", position + 2)
            if end < 0:
                return None
            position = end + 1
        elif character == target:
            return position
        position += 1
    return None


def _matching_brace(value: str, opening: int) -> int | None:
    depth = 1
    position = opening + 1
    quote = ""
    while position < len(value):
        character = value[position]
        if quote:
            if character == "\\":
                position += 2
                continue
            if character == quote:
                quote = ""
        elif character in {"'", '"'}:
            quote = character
        elif value.startswith("/*", position):
            end = value.find("*/", position + 2)
            if end < 0:
                return None
            position = end + 1
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return position
        position += 1
    return None
