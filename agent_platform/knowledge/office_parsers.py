"""Optional PDF and Office extraction adapters. Imports are lazy and failures are explicit."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class BinaryDocumentParser(Protocol):
    supported_media_types: tuple[str,...]
    def parse(self,content: bytes,media_type: str)->str:...
@dataclass
class PDFParser:
    supported_media_types: tuple[str,...]=("application/pdf",)
    def parse(self,content: bytes,media_type: str)->str:
        try:from pypdf import PdfReader  # noqa: I001
        except ImportError as exc:raise RuntimeError("Install pypdf from requirements-knowledge-parsers.txt") from exc
        import io
        reader=PdfReader(io.BytesIO(content));pages=[]
        for index,page in enumerate(reader.pages,1):
            pages.append("\n[page:"+str(index)+"]\n"+(page.extract_text() or ""))
        return "\n".join(pages)
@dataclass
class DOCXParser:
    supported_media_types: tuple[str,...]=("application/vnd.openxmlformats-officedocument.wordprocessingml.document",)
    def parse(self,content: bytes,media_type: str)->str:
        try:from docx import Document as WordDocument  # noqa: I001
        except ImportError as exc:raise RuntimeError("Install python-docx from requirements-knowledge-parsers.txt") from exc
        import io
        doc=WordDocument(io.BytesIO(content));return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
@dataclass
class XLSXParser:
    supported_media_types: tuple[str,...]=("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",)
    def parse(self,content: bytes,media_type: str)->str:
        try:import openpyxl  # noqa: I001
        except ImportError as exc:raise RuntimeError("Install openpyxl from requirements-knowledge-parsers.txt") from exc
        import io
        book=openpyxl.load_workbook(io.BytesIO(content),read_only=True,data_only=True);parts=[]
        for sheet in book.worksheets:
            parts.append("[sheet:"+sheet.title+"]")
            for row in sheet.iter_rows(values_only=True):
                values=[str(v) for v in row if v is not None]
                if values:parts.append(" | ".join(values))
        return "\n".join(parts)
@dataclass
class PPTXParser:
    supported_media_types: tuple[str,...]=("application/vnd.openxmlformats-officedocument.presentationml.presentation",)
    def parse(self,content: bytes,media_type: str)->str:
        try:from pptx import Presentation  # noqa: I001
        except ImportError as exc:raise RuntimeError("Install python-pptx from requirements-knowledge-parsers.txt") from exc
        import io
        deck=Presentation(io.BytesIO(content));parts=[]
        for index,slide in enumerate(deck.slides,1):
            parts.append("[slide:"+str(index)+"]")
            parts.extend(shape.text for shape in slide.shapes if getattr(shape,"has_text_frame",False) and shape.text.strip())
        return "\n".join(parts)
