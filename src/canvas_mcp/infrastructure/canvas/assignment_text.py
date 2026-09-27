"""Deterministic visible assignment wording; inert links, no HTML execution/fetch."""

import re
import unicodedata
from dataclasses import dataclass, field
from ipaddress import ip_address
from html.parser import HTMLParser
from urllib.parse import urlsplit

from canvas_mcp.domain.models import ExternalText
from canvas_mcp.domain.errors import BudgetExceededError


def _public_link(value: str) -> bool:
    try:
        parts = urlsplit(value)
        try:
            ip_address(parts.hostname or "")
            return False
        except ValueError:
            pass
        return bool(
            parts.scheme == "https"
            and parts.hostname
            and re.fullmatch(r"[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+", parts.hostname)
            and not parts.username
            and not parts.password
            and parts.port in (None, 443)
            and not parts.query
            and not parts.fragment
            and not re.search(r"(?i)(token|verifier|signature|secret|api[_-]?key)", parts.path)
            and not re.search(r"[\s<>\\]", value)
        )
    except ValueError:
        return False


def _marker(number: int, style: str) -> str:
    if style in ("a", "A") and number > 0:
        letters = ""
        while number:
            number, remainder = divmod(number - 1, 26)
            letters = chr(97 + remainder) + letters
        return letters.upper() if style == "A" else letters
    if style in ("i", "I") and 0 < number < 4000:
        roman = ""
        for value, symbol in (
            (1000, "M"),
            (900, "CM"),
            (500, "D"),
            (400, "CD"),
            (100, "C"),
            (90, "XC"),
            (50, "L"),
            (40, "XL"),
            (10, "X"),
            (9, "IX"),
            (5, "V"),
            (4, "IV"),
            (1, "I"),
        ):
            count, number = divmod(number, value)
            roman += symbol * count
        return roman.lower() if style == "i" else roman
    return str(number)


@dataclass
class _List:
    kind: str
    number: int
    step: int
    style: str
    infer_start: bool
    markers: list[tuple[int, int | None, str]] = field(default_factory=list)


class _VisibleAssignment(HTMLParser):
    BLOCKS = {
        "p",
        "div",
        "section",
        "article",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "blockquote",
        "dl",
        "dt",
        "dd",
        "tr",
    }
    HIDDEN = {"script", "style", "template", "head", "iframe", "object"}
    VOID = {
        "br",
        "hr",
        "img",
        "input",
        "meta",
        "link",
        "source",
        "wbr",
        "embed",
        "area",
        "base",
        "col",
        "param",
        "track",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.stack: list[tuple[str, bool]] = []
        self.lists: list[_List] = []
        self.links: list[str | None] = []
        self.pre = 0
        self.redacted = False
        self.nontext = False

    @property
    def hidden(self) -> bool:
        return any(hidden for _, hidden in self.stack)

    def boundary(self, paragraph: bool = False) -> None:
        previous = self.parts[-1] if self.parts else ""
        needed = 2 if paragraph else 1
        existing = len(previous) - len(previous.rstrip("\n"))
        if existing < needed:
            self.parts.append("\n" * (needed - existing))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        own_hidden = (
            self.hidden
            or "hidden" in attributes
            or attributes.get("aria-hidden") == "true"
            or bool(
                re.search(
                    r"(?i)(display\s*:\s*none|visibility\s*:\s*hidden)",
                    attributes.get("style") or "",
                )
            )
        )
        unsupported = tag in ("math", "svg", "canvas", "iframe", "object") or bool(
            re.search(r"(?i)(katex|mathjax)", attributes.get("class") or "")
        )
        if not own_hidden and unsupported:
            self.parts.append("[Non-text source omitted; inspect original]")
            self.nontext = True
        hidden = own_hidden or unsupported or tag in self.HIDDEN
        if tag not in self.VOID:
            if len(self.stack) >= 256:
                raise BudgetExceededError()
            self.stack.append((tag, hidden))
        if hidden:
            return
        if tag == "img":
            self.parts.append("[Image alt text: " + (attributes.get("alt") or "not provided") + "]")
            self.nontext = True
        if tag in self.BLOCKS:
            self.boundary(True)
        elif tag in ("br", "hr"):
            self.boundary()
        elif tag in ("td", "th"):
            self.parts.append("\t")
        elif tag in ("ol", "ul"):
            start = attributes.get("start") or "1"
            self.lists.append(
                _List(
                    tag,
                    int(start) if re.fullmatch(r"-?[0-9]{1,6}", start) else 1,
                    -1 if "reversed" in attributes else 1,
                    attributes.get("type") or "1",
                    "reversed" in attributes and "start" not in attributes,
                )
            )
            self.boundary()
        elif tag == "li":
            self.boundary()
            prefix = "• "
            indent = "  " * max(0, len(self.lists) - 1)
            if self.lists and self.lists[-1].kind == "ol":
                ordered = self.lists[-1]
                override = attributes.get("value") or ""
                explicit = int(override) if re.fullmatch(r"-?[0-9]{1,6}", override) else None
                number = ordered.number if explicit is None else explicit
                prefix = f"{_marker(number, ordered.style)}. "
                ordered.markers.append((len(self.parts), explicit, indent))
                ordered.number = number + ordered.step
            self.parts.append(indent + prefix)
        elif tag == "pre":
            self.boundary(True)
            self.pre += 1
        elif tag == "a":
            self.links.append(attributes.get("href"))
        elif tag in ("sup", "sub"):
            self.parts.append("^(" if tag == "sup" else "_(")

    def handle_endtag(self, tag: str) -> None:
        was_hidden = self.hidden
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break
        if was_hidden:
            return
        if tag in self.BLOCKS:
            self.boundary(True)
        elif tag in ("ol", "ul"):
            if self.lists:
                ordered = self.lists.pop()
                if ordered.kind == "ol" and ordered.infer_start:
                    number = len(ordered.markers)
                    for index, explicit, indent in ordered.markers:
                        number = number if explicit is None else explicit
                        self.parts[index] = indent + _marker(number, ordered.style) + ". "
                        number += ordered.step
            self.boundary()
        elif tag == "li":
            self.boundary()
        elif tag == "pre":
            self.pre = max(0, self.pre - 1)
            self.boundary(True)
        elif tag == "a" and self.links:
            link = self.links.pop()
            if link:
                if _public_link(link):
                    self.parts.append(f" ({link})")
                else:
                    self.parts.append(" [reference omitted]")
                    self.redacted = True
        elif tag in ("sup", "sub"):
            self.parts.append(")")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            text = data if self.pre else re.sub(r"[ \t\r\n\f]+", " ", data)
            if not self.pre and not text.strip() and self.parts and self.parts[-1].endswith("\n"):
                return
            self.parts.append(text)


def assignment_visible_text(value: str, maximum: int = 16000) -> tuple[ExternalText, bool, bool]:
    parser = _VisibleAssignment()
    parser.feed(value)
    parser.close()
    text = "".join(parser.parts)

    def screen(match: re.Match[str]) -> str:
        url = match.group()
        if _public_link(url):
            return url
        parser.redacted = True
        return "[reference omitted]"

    text = re.sub(
        r"(?i)(?:(?:https?|file|ftp|data|javascript):|(?<!\w)//(?=[a-z0-9-]+(?:\.[a-z0-9-]+)+(?:[/:?#]|$)|\[[a-f0-9:]+\])|(?<!\w)/(?:api/v1|courses|files|users)/)[^\s<>]+",
        screen,
        text,
    )
    text = re.sub(
        r"(?i)[^\s<>]*[?&](?:verifier|access_token|token|signature|x-amz-signature)=[^\s<>]*",
        screen,
        text,
    )
    text = "".join(c for c in text if c in "\n\t" or not unicodedata.category(c).startswith("C"))
    text = text.strip("\n")
    return ExternalText(text[:maximum], len(text) > maximum), parser.redacted, parser.nontext
