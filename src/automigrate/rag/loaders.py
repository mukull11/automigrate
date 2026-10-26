"""Document loaders for Markdown, reStructuredText, text, and HTML."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

SUPPORTED_SUFFIXES = {".md", ".rst", ".txt", ".html", ".htm"}
RST_UNDERLINE_CHARS = "=-~^\"'`#*+"


class LoadedDocument:
    def __init__(self, path: Path, source_doc: str, text: str) -> None:
        self.path = path
        self.source_doc = source_doc
        self.text = text


def iter_document_paths(docs_dir: str | Path) -> list[Path]:
    root = Path(docs_dir)
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
    )


def read_document(path: str | Path) -> str:
    path_obj = Path(path)
    suffix = path_obj.suffix.lower()
    raw = path_obj.read_text(encoding="utf-8", errors="replace")
    if suffix in {".html", ".htm"}:
        return _html_to_markdown(raw)
    if suffix == ".rst":
        return _rst_to_markdown(raw)
    return raw


def load_document(path: str | Path, docs_dir: str | Path) -> LoadedDocument:
    path_obj = Path(path)
    root = Path(docs_dir)
    try:
        source_doc = path_obj.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        source_doc = path_obj.name
    return LoadedDocument(path_obj, source_doc, read_document(path_obj))


class _MarkdownHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._skip_depth += 1
            return
        if re.fullmatch(r"h[1-6]", tag):
            level = int(tag[1])
            self.parts.append("\n\n" + "#" * level + " ")
        elif tag in {"p", "div", "br", "li", "tr", "section", "article"}:
            self.parts.append("\n\n")
        elif tag in {"td", "th"}:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._skip_depth:
            self._skip_depth -= 1
        elif re.fullmatch(r"h[1-6]", tag) or tag in {"p", "div", "li", "tr"}:
            self.parts.append("\n\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)

    def text(self) -> str:
        joined = "".join(self.parts)
        joined = re.sub(r"[ \t]+", " ", joined)
        joined = re.sub(r"\n{3,}", "\n\n", joined)
        return joined.strip() + "\n"


def _html_to_markdown(html: str) -> str:
    parser = _MarkdownHTMLParser()
    parser.feed(html)
    parser.close()
    return parser.text()


def _rst_to_markdown(rst: str) -> str:
    lines = rst.splitlines()
    levels: dict[str, int] = {}
    output: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if (
            index + 1 < len(lines)
            and line.strip()
            and _is_rst_underline(lines[index + 1], line)
        ):
            char = lines[index + 1].strip()[0]
            level = levels.setdefault(char, len(levels) + 1)
            output.append("#" * min(level, 6) + " " + line.strip())
            index += 2
            continue
        if _is_rst_underline(line, "") and output:
            index += 1
            continue
        output.append(line)
        index += 1
    return "\n".join(output).strip() + "\n"


def _is_rst_underline(line: str, title: str) -> bool:
    stripped = line.strip()
    if len(stripped) < 3:
        return False
    char = stripped[0]
    if char not in RST_UNDERLINE_CHARS or len(set(stripped)) != 1:
        return False
    return not title or len(stripped) >= len(title.strip())
