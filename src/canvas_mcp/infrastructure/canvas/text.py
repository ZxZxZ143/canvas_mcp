"""One shared inert-string projection for secret screening and display mapping."""

import unicodedata
from html.parser import HTMLParser


class _PlainText(HTMLParser):
    _BLOCKS = frozenset(
        (
            "address",
            "article",
            "aside",
            "blockquote",
            "dd",
            "div",
            "dl",
            "dt",
            "fieldset",
            "figcaption",
            "figure",
            "footer",
            "form",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "header",
            "hr",
            "li",
            "main",
            "nav",
            "ol",
            "p",
            "pre",
            "section",
            "table",
            "tr",
            "ul",
        )
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self.hidden += 1
        elif not self.hidden:
            if tag in self._BLOCKS or tag == "br":
                self.parts.append("\n")
            elif tag in ("td", "th"):
                self.parts.append("\t")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self.hidden:
            self.hidden -= 1
        elif not self.hidden and tag in self._BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def inert_text(value: str) -> str:
    parser = _PlainText()
    parser.feed(value)
    parser.close()
    return "".join(
        char
        for char in "".join(parser.parts)
        if char in "\n\t" or not unicodedata.category(char).startswith("C")
    ).strip()
