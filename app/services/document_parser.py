import io
import os
import csv
import json
from pathlib import Path
from typing import Dict, Any, Union, Optional

import pypdf
import docx

def format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"

class DocumentParserService:
    """
    High-performance parser for multimodal document uploads:
    - PDF documents (.pdf) via pypdf (text extraction per page, outline, metadata)
    - Word documents (.docx) via python-docx (paragraphs, tables, headings)
    - Data tables (.csv, .tsv) with row counts and column structure
    - Structured data (.json, .yaml, .yml, .xml)
    - Text & code files (.txt, .md, .py, .js, .ts, .html, .env, .log, etc.)
    """

    SUPPORTED_EXTENSIONS = {
        ".pdf", ".docx", ".doc", ".txt", ".md", ".markdown",
        ".csv", ".tsv", ".json", ".xml", ".yaml", ".yml",
        ".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css",
        ".sql", ".sh", ".bash", ".zsh", ".env", ".log", ".toml", ".ini"
    }

    def parse_file(self, file_path: Union[str, Path], max_chars: int = 50000) -> Dict[str, Any]:
        """Parses a file from disk into structured document content and metadata."""
        p = Path(file_path).resolve()
        if not p.exists():
            return {"error": f"File not found: {str(p)}"}
        if p.is_dir():
            return {"error": f"Path is a directory: {str(p)}"}

        try:
            with open(p, "rb") as f:
                content_bytes = f.read()
            return self.parse_bytes(p.name, content_bytes, max_chars=max_chars, file_path=str(p))
        except Exception as e:
            return {"error": f"Failed to read file {p.name}: {str(e)}"}

    def parse_bytes(
        self,
        filename: str,
        content_bytes: bytes,
        max_chars: int = 50000,
        file_path: Optional[str] = None
    ) -> Dict[str, Any]:
        """Parses raw in-memory document bytes into clean text and rich metadata."""
        ext = Path(filename).suffix.lower()
        size_bytes = len(content_bytes)
        size_str = format_size(size_bytes)

        res: Dict[str, Any] = {
            "name": filename,
            "path": file_path or filename,
            "extension": ext,
            "size_bytes": size_bytes,
            "size_formatted": size_str,
            "is_document": True,
            "page_count": 1,
            "line_count": 0,
            "char_count": 0,
            "approx_tokens": 0,
            "content": "",
            "summary_preview": ""
        }

        # 1. PDF Parsing
        if ext == ".pdf":
            try:
                reader = pypdf.PdfReader(io.BytesIO(content_bytes))
                num_pages = len(reader.pages)
                res["page_count"] = num_pages

                extracted_pages = []
                for idx, page in enumerate(reader.pages):
                    t = page.extract_text() or ""
                    if t.strip():
                        extracted_pages.append(f"--- Page {idx + 1} of {num_pages} ---\n{t.strip()}")

                full_text = "\n\n".join(extracted_pages)
                if not full_text.strip():
                    full_text = f"[Scanned/Image PDF with {num_pages} pages: No selectable text extracted]"

                if len(full_text) > max_chars:
                    full_text = full_text[:max_chars] + f"\n\n... [Truncated for brevity; document has {num_pages} pages, {len(full_text)} total characters]"

                res["content"] = full_text
                res["char_count"] = len(full_text)
                res["line_count"] = full_text.count("\n") + 1
                res["approx_tokens"] = int(len(full_text) / 3.8)
                res["summary_preview"] = full_text[:300].strip()
                return res
            except Exception as e:
                res["error"] = f"Failed to parse PDF: {str(e)}"
                res["content"] = f"[Corrupt or unreadable PDF: {str(e)}]"
                return res

        # 2. Word DOCX Parsing
        if ext == ".docx":
            try:
                doc = docx.Document(io.BytesIO(content_bytes))
                paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]

                # Extract tables
                table_texts = []
                for t_idx, table in enumerate(doc.tables):
                    rows_text = []
                    for row in table.rows:
                        row_vals = [cell.text.strip().replace("\n", " ") for cell in row.cells]
                        rows_text.append(" | ".join(row_vals))
                    if rows_text:
                        table_texts.append(f"[Table {t_idx + 1}]\n" + "\n".join(rows_text))

                full_text = "\n\n".join(paragraphs)
                if table_texts:
                    full_text += "\n\n" + "\n\n".join(table_texts)

                if len(full_text) > max_chars:
                    full_text = full_text[:max_chars] + f"\n\n... [Truncated for brevity; docx has {len(full_text)} total characters]"

                res["content"] = full_text
                res["char_count"] = len(full_text)
                res["line_count"] = full_text.count("\n") + 1
                res["approx_tokens"] = int(len(full_text) / 3.8)
                res["summary_preview"] = full_text[:300].strip()
                return res
            except Exception as e:
                res["error"] = f"Failed to parse Word document: {str(e)}"
                res["content"] = f"[Corrupt or unreadable DOCX: {str(e)}]"
                return res

        # 3. CSV / TSV Parsing
        if ext in (".csv", ".tsv"):
            try:
                delimiter = "\t" if ext == ".tsv" else ","
                text_content = content_bytes.decode("utf-8", errors="replace")
                reader = csv.reader(io.StringIO(text_content), delimiter=delimiter)
                rows = list(reader)
                row_count = len(rows)
                header = rows[0] if rows else []

                preview_rows = rows[:25]
                table_str = "\n".join([" | ".join(r) for r in preview_rows])
                if row_count > 25:
                    table_str += f"\n... [{row_count - 25} more rows omitted; total {row_count} rows, {len(header)} columns]"

                res["content"] = f"Data Table: {filename} ({row_count} rows, Columns: {', '.join(header)})\n\n{table_str}"
                res["char_count"] = len(res["content"])
                res["line_count"] = row_count
                res["approx_tokens"] = int(len(res["content"]) / 3.8)
                res["summary_preview"] = f"Columns: {', '.join(header[:6])} | {row_count} rows"
                return res
            except Exception as e:
                res["error"] = f"Failed to parse CSV/TSV: {str(e)}"

        # 4. JSON Parsing
        if ext == ".json":
            try:
                text_content = content_bytes.decode("utf-8", errors="replace")
                data = json.loads(text_content)
                pretty_json = json.dumps(data, indent=2)
                if len(pretty_json) > max_chars:
                    pretty_json = pretty_json[:max_chars] + "\n... [Truncated large JSON]"

                res["content"] = pretty_json
                res["char_count"] = len(pretty_json)
                res["line_count"] = pretty_json.count("\n") + 1
                res["approx_tokens"] = int(len(pretty_json) / 3.8)
                res["summary_preview"] = pretty_json[:300].strip()
                return res
            except Exception:
                pass # Fall through to plaintext

        # 5. Plaintext, Markdown, Code & Configs
        try:
            text_content = content_bytes.decode("utf-8", errors="replace")
            if len(text_content) > max_chars:
                text_content = text_content[:max_chars] + f"\n\n... [Truncated for brevity; total {len(text_content)} characters]"

            res["content"] = text_content
            res["char_count"] = len(text_content)
            res["line_count"] = text_content.count("\n") + 1
            res["approx_tokens"] = int(len(text_content) / 3.8)
            res["summary_preview"] = text_content[:300].strip()
            return res
        except Exception as e:
            res["error"] = f"Failed to decode text: {str(e)}"
            return res

document_parser_service = DocumentParserService()
