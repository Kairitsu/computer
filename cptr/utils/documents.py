"""Document text extraction utilities.

Extracts readable text from binary document formats so LLMs can
consume their content.  Each ``extract_*`` function takes a file path
and returns the document's text as a plain string.

All imports are lazy — missing libraries produce a clear ImportError
with install instructions, not a crash.
"""

import os
import zipfile


def extract_pdf(file_path: str) -> str:
    """Extract text from a PDF file."""
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ImportError("pip install pypdf")

    reader = PdfReader(file_path)
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def extract_docx(file_path: str) -> str:
    """Extract text from a Word (.docx) file."""
    try:
        from docx import Document as DocxDocument
    except ImportError:
        raise ImportError("pip install python-docx")

    doc = DocxDocument(file_path)
    parts = []
    for para in doc.paragraphs:
        parts.append(para.text)
    for table in doc.tables:
        for row in table.rows:
            parts.append("\t".join(cell.text for cell in row.cells))
    return "\n".join(parts)


def extract_xlsx(file_path: str) -> str:
    """Extract text from an Excel (.xlsx) file."""
    try:
        from openpyxl import load_workbook
    except ImportError:
        raise ImportError("pip install openpyxl")

    wb = load_workbook(file_path, read_only=True, data_only=True)
    parts = []
    for sheet in wb.worksheets:
        parts.append(f"--- {sheet.title} ---")
        for row in sheet.iter_rows(values_only=True):
            parts.append("\t".join(str(c) if c is not None else "" for c in row))
    wb.close()
    return "\n".join(parts)


def extract_pptx(file_path: str) -> str:
    """Extract text from a PowerPoint (.pptx) file."""
    try:
        from pptx import Presentation
    except ImportError:
        raise ImportError("pip install python-pptx")

    prs = Presentation(file_path)
    parts = []
    for i, slide in enumerate(prs.slides, 1):
        parts.append(f"--- Slide {i} ---")
        for shape in slide.shapes:
            if shape.has_text_frame:
                parts.append(shape.text_frame.text)
    return "\n".join(parts)


def extract_rtf(file_path: str) -> str:
    """Extract text from a Rich Text Format (.rtf) file."""
    try:
        from striprtf.striprtf import rtf_to_text
    except ImportError:
        raise ImportError("pip install striprtf")

    with open(file_path, "rb") as f:
        raw = f.read()
    return rtf_to_text(raw.decode("utf-8", errors="replace"))


# Preview caps. A spreadsheet expands far past the upload size once parsed.
_PREVIEW_SHEETS = 20
_PREVIEW_ROWS = 500
_PREVIEW_COLS = 50


def _cell_text(value: object) -> str:
    if value is None:
        return ""
    return str(value)


def workbook_preview(data: bytes, filename: str) -> dict:
    """Plain cell text for an .xlsx or .xls preview.

    Values stay strings. The caller renders them as text, not as HTML.
    ``truncated`` is true when a sheet, row, or column cap dropped cells.
    """
    ext = os.path.splitext(filename or "")[1].lower()
    if ext == ".xls":
        return _xls_preview(data)
    return _xlsx_preview(data)


def _xlsx_preview(data: bytes) -> dict:
    from io import BytesIO

    try:
        from openpyxl import load_workbook
    except ImportError:
        raise ImportError("pip install openpyxl")

    wb = load_workbook(BytesIO(data), read_only=True, data_only=True)
    truncated = False
    sheets: list[dict] = []
    try:
        for index, sheet in enumerate(wb.worksheets):
            if index >= _PREVIEW_SHEETS:
                truncated = True
                break
            rows: list[list[str]] = []
            for row_index, row in enumerate(sheet.iter_rows(values_only=True)):
                if row_index >= _PREVIEW_ROWS:
                    truncated = True
                    break
                if len(row) > _PREVIEW_COLS:
                    truncated = True
                rows.append([_cell_text(cell) for cell in row[:_PREVIEW_COLS]])
            sheets.append({"name": sheet.title, "rows": rows})
    finally:
        wb.close()
    return {"sheets": sheets, "truncated": truncated}


def _xls_preview(data: bytes) -> dict:
    try:
        import xlrd
    except ImportError:
        raise ImportError("pip install xlrd")

    book = xlrd.open_workbook(file_contents=data)
    truncated = book.nsheets > _PREVIEW_SHEETS
    sheets: list[dict] = []
    for sheet in book.sheets()[:_PREVIEW_SHEETS]:
        row_count = min(sheet.nrows, _PREVIEW_ROWS)
        col_count = min(sheet.ncols, _PREVIEW_COLS)
        if sheet.nrows > _PREVIEW_ROWS or sheet.ncols > _PREVIEW_COLS:
            truncated = True
        rows = []
        for row_index in range(row_count):
            rows.append(
                [_cell_text(sheet.cell_value(row_index, col_index)) for col_index in range(col_count)]
            )
        sheets.append({"name": sheet.name, "rows": rows})
    return {"sheets": sheets, "truncated": truncated}


def extract_xls(file_path: str) -> str:
    """Extract text from a legacy Excel (.xls) file."""
    try:
        import xlrd
    except ImportError:
        raise ImportError("pip install xlrd")

    wb = xlrd.open_workbook(file_path)
    parts = []
    for sheet in wb.sheets():
        parts.append(f"--- {sheet.name} ---")
        for row_idx in range(sheet.nrows):
            parts.append("\t".join(
                str(sheet.cell_value(row_idx, col_idx))
                for col_idx in range(sheet.ncols)
            ))
    return "\n".join(parts)


def extract_odt(file_path: str) -> str:
    """Extract text from an OpenDocument Text (.odt) file."""
    try:
        from lxml import etree
    except ImportError:
        raise ImportError("pip install lxml")

    with zipfile.ZipFile(file_path) as zf:
        with zf.open("content.xml") as f:
            tree = etree.parse(f)
    ns = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
    return "\n".join(
        "".join(p.itertext())
        for p in tree.iter(f"{{{ns}}}p")
    )


def extract_ods(file_path: str) -> str:
    """Extract text from an OpenDocument Spreadsheet (.ods) file."""
    try:
        from lxml import etree
    except ImportError:
        raise ImportError("pip install lxml")

    with zipfile.ZipFile(file_path) as zf:
        with zf.open("content.xml") as f:
            tree = etree.parse(f)
    ns_table = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
    ns_text = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
    parts = []
    for table in tree.iter(f"{{{ns_table}}}table"):
        name = table.get(f"{{{ns_table}}}name", "Sheet")
        parts.append(f"--- {name} ---")
        for row in table.iter(f"{{{ns_table}}}table-row"):
            cells = []
            for cell in row.iter(f"{{{ns_table}}}table-cell"):
                cell_text = " ".join(
                    "".join(p.itertext())
                    for p in cell.iter(f"{{{ns_text}}}p")
                )
                cells.append(cell_text)
            parts.append("\t".join(cells))
    return "\n".join(parts)


def extract_odp(file_path: str) -> str:
    """Extract text from an OpenDocument Presentation (.odp) file."""
    try:
        from lxml import etree
    except ImportError:
        raise ImportError("pip install lxml")

    with zipfile.ZipFile(file_path) as zf:
        with zf.open("content.xml") as f:
            tree = etree.parse(f)
    ns_draw = "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"
    ns_text = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
    parts = []
    for i, page in enumerate(tree.iter(f"{{{ns_draw}}}page"), 1):
        parts.append(f"--- Slide {i} ---")
        for p in page.iter(f"{{{ns_text}}}p"):
            text = "".join(p.itertext()).strip()
            if text:
                parts.append(text)
    return "\n".join(parts)


def extract_epub(file_path: str) -> str:
    """Extract text from an EPUB e-book."""
    try:
        from lxml import etree
    except ImportError:
        raise ImportError("pip install lxml")

    parts = []
    with zipfile.ZipFile(file_path) as zf:
        # Parse the container to find the root file
        with zf.open("META-INF/container.xml") as cf:
            container = etree.parse(cf)
        ns_container = "urn:oasis:names:tc:opendocument:xmlns:container"
        rootfile = container.find(f".//{{{ns_container}}}rootfile")
        if rootfile is None:
            rootfile = container.xpath("//*[local-name()='rootfile']")
            rootfile = rootfile[0] if rootfile else None

        if rootfile is not None:
            opf_path = rootfile.get("full-path", "")
            opf_dir = opf_path.rsplit("/", 1)[0] + "/" if "/" in opf_path else ""
            with zf.open(opf_path) as opf_file:
                opf = etree.parse(opf_file)
            spine_ids = [
                item.get("idref")
                for item in opf.xpath("//*[local-name()='itemref']")
            ]
            manifest = {
                item.get("id"): item.get("href")
                for item in opf.xpath("//*[local-name()='item']")
            }
            for idref in spine_ids:
                href = manifest.get(idref, "")
                item_path = opf_dir + href if not href.startswith("/") else href.lstrip("/")
                try:
                    with zf.open(item_path) as html_file:
                        html_tree = etree.parse(html_file, etree.HTMLParser())
                        body = html_tree.find(".//body")
                        if body is not None:
                            text = "".join(body.itertext())
                            parts.append(text.strip())
                except (KeyError, etree.XMLSyntaxError):
                    continue
        else:
            for name in zf.namelist():
                if name.endswith((".html", ".xhtml", ".htm")):
                    try:
                        with zf.open(name) as html_file:
                            html_tree = etree.parse(html_file, etree.HTMLParser())
                            body = html_tree.find(".//body")
                            if body is not None:
                                text = "".join(body.itertext())
                                parts.append(text.strip())
                    except etree.XMLSyntaxError:
                        continue
    return "\n\n".join(parts)


def extract_eml(file_path: str) -> str:
    """Extract text from an email message (.eml)."""
    import email
    from email import policy

    with open(file_path, "rb") as f:
        msg = email.message_from_binary_file(f, policy=policy.default)
    parts = []
    for header in ("From", "To", "Cc", "Date", "Subject"):
        val = msg.get(header)
        if val:
            parts.append(f"{header}: {val}")
    parts.append("")  # blank line after headers
    body = msg.get_body(preferencelist=("plain", "html"))
    if body:
        content = body.get_content()
        if body.get_content_type() == "text/html":
            try:
                from lxml import etree
                tree = etree.HTML(content)
                content = "".join(tree.itertext()) if tree is not None else content
            except ImportError:
                pass  # Fall back to raw HTML text
        parts.append(content)
    return "\n".join(parts)


# MIME type / extension → extractor mapping.
# Each entry: (mime_type_or_None, file_extension_or_None, extractor)
EXTRACTORS: list[tuple[str | None, str | None, callable]] = [
    ("application/pdf", None, extract_pdf),
    ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", None, extract_docx),
    ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", None, extract_xlsx),
    ("application/vnd.openxmlformats-officedocument.presentationml.presentation", None, extract_pptx),
    ("application/rtf", ".rtf", extract_rtf),
    ("application/vnd.ms-excel", ".xls", extract_xls),
    ("application/vnd.oasis.opendocument.text", ".odt", extract_odt),
    ("application/vnd.oasis.opendocument.spreadsheet", ".ods", extract_ods),
    ("application/vnd.oasis.opendocument.presentation", ".odp", extract_odp),
    ("application/epub+zip", ".epub", extract_epub),
    ("message/rfc822", ".eml", extract_eml),
]


def extract_by_path(file_path: str) -> str | None:
    """Try all extractors for a file by MIME type / extension.

    Returns extracted text, or None if no extractor matches.
    Raises ImportError with install instructions if the matching
    extractor's library is missing.
    """
    import mimetypes

    mime, _ = mimetypes.guess_type(file_path)
    ext = os.path.splitext(file_path)[1].lower()

    for ext_mime, ext_suffix, extractor in EXTRACTORS:
        if (ext_mime and mime == ext_mime) or (ext_suffix and ext == ext_suffix):
            return extractor(file_path)
    return None
