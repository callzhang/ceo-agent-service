from html import unescape
from urllib.parse import parse_qs, unquote, urlparse


AFLOW_HOST = "aflow.dingtalk.com"
URL_TRAILING_CHARS = "\"'`>,.。；;，"


def extract_oa_url(text: str) -> str:
    for candidate in _urlish_candidates(text):
        nested = _nested_aflow_url(candidate)
        if nested:
            return nested
        direct = _aflow_url(candidate)
        if direct:
            return direct
        decoded = unquote(candidate)
        nested = _nested_aflow_url(decoded)
        if nested:
            return nested
        direct = _aflow_url(decoded)
        if direct:
            return direct
    return ""


def _urlish_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    text = unescape(text).replace("\\/", "/").replace("\\u0026", "&")
    for separator in ('"', "'", " ", "\n", "\t", "\r", "<", ">"):
        text = text.replace(separator, "\n")
    for raw in text.splitlines():
        candidate = raw.strip().strip("()[]{}")
        if candidate:
            candidates.append(candidate)
    return candidates


def _aflow_url(value: str) -> str:
    marker = f"https://{AFLOW_HOST}"
    start = value.find(marker)
    if start < 0:
        return ""
    url = value[start:]
    for delimiter in ("&quot;", "\\u0026quot;", "}", "]", ")"):
        position = url.find(delimiter)
        if position >= 0:
            url = url[:position]
    url = url.rstrip(URL_TRAILING_CHARS)
    parsed = urlparse(url)
    if parsed.netloc != AFLOW_HOST:
        return ""
    return url


def _nested_aflow_url(value: str) -> str:
    parsed = urlparse(value)
    query = parse_qs(parsed.query)
    for values in query.values():
        for item in values:
            direct = _aflow_url(unquote(item))
            if direct:
                return direct
    return ""
