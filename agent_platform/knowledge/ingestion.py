"""Safe text extraction adapters; binary formats are optional plug-ins."""
from __future__ import annotations
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Protocol
class DocumentParser(Protocol):
    supported_media_types: tuple[str, ...]
    def parse(self, content: bytes, media_type: str) -> str: ...
class _TextHTMLParser(HTMLParser):
    def __init__(self) -> None: super().__init__(); self.parts: list[str] = []; self.hidden = 0
    def handle_starttag(self, tag: str, attrs: list[tuple[str,str|None]]) -> None:
        if tag in {"script","style","noscript","svg"}: self.hidden += 1
        if tag in {"p","div","br","li","h1","h2","h3","tr"}: self.parts.append("\\n")
    def handle_endtag(self, tag: str) -> None:
        if tag in {"script","style","noscript","svg"}: self.hidden = max(0,self.hidden-1)
        if tag in {"p","div","li","h1","h2","h3","tr"}: self.parts.append("\\n")
    def handle_data(self, data: str) -> None:
        if not self.hidden: self.parts.append(data)
@dataclass
class PlainTextParser:
    supported_media_types: tuple[str,...] = ("text/plain","text/markdown","text/x-markdown")
    def parse(self, content: bytes, media_type: str) -> str:
        if media_type not in self.supported_media_types: raise ValueError(f"unsupported media type: {media_type}")
        return content.decode("utf-8-sig")
@dataclass
class HTMLParserAdapter:
    supported_media_types: tuple[str,...] = ("text/html","application/xhtml+xml")
    def parse(self, content: bytes, media_type: str) -> str:
        if media_type not in self.supported_media_types: raise ValueError(f"unsupported media type: {media_type}")
        parser=_TextHTMLParser(); parser.feed(content.decode("utf-8-sig",errors="replace")); return "".join(parser.parts)
class OptionalParserAdapter:
    def __init__(self, media_types: tuple[str,...], parser: DocumentParser) -> None: self.supported_media_types=media_types; self.parser=parser
    def parse(self, content: bytes, media_type: str) -> str:
        if media_type not in self.supported_media_types: raise ValueError(f"unsupported media type: {media_type}")
        return self.parser.parse(content,media_type)
class ParserRegistry:
    def __init__(self, parsers: tuple[DocumentParser,...]=()) -> None:
        from agent_platform.knowledge.office_parsers import DOCXParser,PDFParser,PPTXParser,XLSXParser
        self.parsers=(PlainTextParser(),HTMLParserAdapter(),PDFParser(),DOCXParser(),XLSXParser(),PPTXParser(),*parsers)
    def parse(self, content: bytes, media_type: str) -> str:
        for parser in self.parsers:
            if media_type in parser.supported_media_types:
                text=parser.parse(content,media_type)
                if not text.strip(): raise ValueError("document contains no extractable text")
                return text
        raise ValueError(f"no parser registered for {media_type}")
