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

    def __init__(self, *, preserve_blocks: bool = False) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.non_content_depth = 0
        self.preserve_blocks = preserve_blocks
        self.saw_markup = False

    def _block_separator(self) -> str:
        return "\n\n" if self.preserve_blocks else " "

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        raw = self.get_starttag_text() or ""
        if not _is_valid_html_tag_name(tag):
            self.parts.append(raw)
            return
        self.saw_markup = True
        normalized_tag = tag.lower()
        if normalized_tag in self._NON_CONTENT_TAGS:
            self.non_content_depth += 1
        elif self.non_content_depth == 0 and normalized_tag in self._BLOCK_TAGS:
            self.parts.append(self._block_separator())

    def handle_endtag(self, tag: str) -> None:
        if not _is_valid_html_tag_name(tag):
            self.parts.append(f"</{tag}>")
            return
        self.saw_markup = True
        normalized_tag = tag.lower()
        if normalized_tag in self._NON_CONTENT_TAGS and self.non_content_depth:
            self.non_content_depth -= 1
        elif self.non_content_depth == 0 and normalized_tag in self._BLOCK_TAGS:
            self.parts.append(self._block_separator())

    def handle_data(self, data: str) -> None:
        if self.non_content_depth == 0:
            self.parts.append(data)

    def text(self) -> str:
        if self.preserve_blocks:
            return "\n\n".join(
                " ".join(line.split())
                for line in "".join(self.parts).splitlines()
                if line.strip()
            )
        return re.sub(r"\s+", " ", "".join(self.parts)).strip()


def html_to_text(value: str) -> str:
    """Return visible text without executing or retaining HTML implementation data."""

    parser = _HTMLTextExtractor()
    parser.feed(value)
    parser.close()
    return parser.text()


def visible_email_text(value: str) -> str:
    """Project legacy plain bodies without residual HTML implementation data."""

    end = _leading_stylesheet_end(value)
    text = value[end:].lstrip() if end else value
    text = _remove_truncated_internal_markup(text)
    parser = _HTMLTextExtractor(preserve_blocks=True)
    parser.feed(text)
    parser.close()
    return parser.text() if parser.saw_markup else text


def _is_valid_html_tag_name(value: str) -> bool:
    return bool(value) and value[0].isalpha() and all(
        character.isalnum() or character == "-" for character in value
    )


def _remove_truncated_internal_markup(value: str) -> str:
    """Drop an incomplete tag made by durable internal URL redaction only."""

    opening = value.find("<")
    while opening >= 0:
        closing = value.find(">", opening + 1)
        fragment = value[opening:] if closing < 0 else value[opening : closing + 1]
        if (
            closing < 0
            and len(fragment) > 1
            and fragment[1].isspace()
            and "[UNSUBSCRIBE_CANDIDATE:" in fragment
        ):
            return value[:opening].rstrip()
        opening = value.find("<", opening + 1)
    return value


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
