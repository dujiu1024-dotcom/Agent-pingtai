"""Safe text extraction adapters; binary formats are optional plug-ins."""
from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Protocol


class DocumentParser(Protocol):
    supported_media_types: tuple[str, ...]

    def parse(self, content: bytes, media_type: str) -> str: ...


class OCRProvider(Protocol):
    """Host-supplied OCR engine; implementations own model/runtime and data handling."""

    def extract_text(self, content: bytes, media_type: str) -> str: ...


class _TextHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self.hidden += 1
        if tag in {"p", "div", "br", "li", "h1", "h2", "h3", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self.hidden = max(0, self.hidden - 1)
        if tag in {"p", "div", "li", "h1", "h2", "h3", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


@dataclass
class PlainTextParser:
    supported_media_types: tuple[str, ...] = ("text/plain", "text/markdown", "text/x-markdown")

    def parse(self, content: bytes, media_type: str) -> str:
        if media_type not in self.supported_media_types:
            raise ValueError(f"unsupported media type: {media_type}")
        return content.decode("utf-8-sig")


@dataclass
class HTMLParserAdapter:
    supported_media_types: tuple[str, ...] = ("text/html", "application/xhtml+xml")

    def parse(self, content: bytes, media_type: str) -> str:
        if media_type not in self.supported_media_types:
            raise ValueError(f"unsupported media type: {media_type}")
        parser = _TextHTMLParser()
        parser.feed(content.decode("utf-8-sig", errors="replace"))
        return "".join(parser.parts)


class OptionalParserAdapter:
    def __init__(self, media_types: tuple[str, ...], parser: DocumentParser) -> None:
        self.supported_media_types = media_types
        self.parser = parser

    def parse(self, content: bytes, media_type: str) -> str:
        if media_type not in self.supported_media_types:
            raise ValueError(f"unsupported media type: {media_type}")
        return self.parser.parse(content, media_type)


class OCRParserAdapter:
    """Explicit opt-in adapter for scanned documents and image-only PDFs.

    Register this after the built-in parsers. The built-in PDF parser gets first
    refusal; when it extracts no text, the registry falls through to this adapter.
    No OCR engine, network call, or document upload is enabled by default.
    """

    def __init__(
        self,
        provider: OCRProvider,
        media_types: tuple[str, ...] = ("application/pdf", "image/tiff", "image/png", "image/jpeg"),
        *,
        max_output_chars: int = 2_000_000,
    ) -> None:
        allowed = "abcdefghijklmnopqrstuvwxyz0123456789!$&^_.+-"
        if (
            not isinstance(media_types, tuple)
            or not media_types
            or any(
                not isinstance(item, str)
                or item != item.strip()
                or item != item.lower()
                or len(item.split("/")) != 2
                or any(
                    not part or any(char not in allowed for char in part)
                    for part in item.split("/")
                )
                for item in media_types
            )
            or len(set(media_types)) != len(media_types)
        ):
            raise ValueError("OCR media types must be unique non-empty type/subtype values")
        if (
            isinstance(max_output_chars, bool)
            or not isinstance(max_output_chars, int)
            or max_output_chars < 1
        ):
            raise ValueError("max_output_chars must be a positive integer")
        self.provider = provider
        self.supported_media_types = media_types
        self.max_output_chars = max_output_chars

    def parse(self, content: bytes, media_type: str) -> str:
        if media_type not in self.supported_media_types:
            raise ValueError(f"unsupported media type: {media_type}")
        if not isinstance(content, bytes) or not content:
            raise ValueError("OCR content must be non-empty bytes")
        text = self.provider.extract_text(content, media_type)
        if not isinstance(text, str):
            raise TypeError("OCR provider must return text")
        if len(text) > self.max_output_chars:
            raise ValueError("OCR output exceeds configured character limit")
        return text


class ParserRegistry:
    def __init__(self, parsers: tuple[DocumentParser, ...] = ()) -> None:
        from agent_platform.knowledge.office_parsers import (
            DOCXParser,
            PDFParser,
            PPTXParser,
            XLSXParser,
        )

        self.parsers = (
            PlainTextParser(),
            HTMLParserAdapter(),
            PDFParser(),
            DOCXParser(),
            XLSXParser(),
            PPTXParser(),
            *parsers,
        )

    def parse(self, content: bytes, media_type: str) -> str:
        matched = False
        for parser in self.parsers:
            if media_type not in parser.supported_media_types:
                continue
            matched = True
            text = parser.parse(content, media_type)
            # Empty text from a PDF text layer is not necessarily a bad file:
            # it can be a scanned PDF, so allow an explicitly registered OCR
            # parser later in the chain to handle it.
            if text.strip():
                return text
        if matched:
            raise ValueError("document contains no extractable text")
        raise ValueError(f"no parser registered for {media_type}")
