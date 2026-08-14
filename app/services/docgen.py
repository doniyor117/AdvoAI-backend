"""docgen.py — rendering a structured contract into a real .docx file.

The only module that touches python-docx. It is a pure function of its input: the LLM
produces the *content* as JSON, this produces the *document*. Keeping generation and
rendering apart is what makes the output format deterministic — the model cannot
accidentally emit Markdown asterisks into a Word file, and this can be unit-tested
without any network access.
"""

import logging
import os
import re
import tempfile
from typing import Any, Dict, List, Optional

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, Cm

logger = logging.getLogger(__name__)

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# Cyrillic-capable and present on virtually every system that opens these files.
_BODY_FONT = "Times New Roman"
_BODY_SIZE = Pt(12)


def _safe_filename(title: str, fallback: str = "document") -> str:
    """A filesystem- and header-safe basename derived from the contract title."""
    name = re.sub(r"[^\w\s-]", "", title or "", flags=re.UNICODE).strip()
    name = re.sub(r"\s+", "_", name)
    return (name or fallback)[:60]


def _style_document(doc: Document) -> None:
    style = doc.styles["Normal"]
    style.font.name = _BODY_FONT
    style.font.size = _BODY_SIZE
    for section in doc.sections:
        section.top_margin = Cm(2)
        section.bottom_margin = Cm(2)
        section.left_margin = Cm(3)     # binding edge, per local convention
        section.right_margin = Cm(1.5)


def render_contract(contract: Dict[str, Any], output_dir: Optional[str] = None) -> str:
    """Renders a contract dict into a .docx and returns the local path.

    Expects the shape produced by `GeminiClient.draft_contract`:
        {title, intro?, sections: [{number, heading, body}], signature_blocks?: [...]}
    """
    doc = Document()
    _style_document(doc)

    title = (contract.get("title") or "Shartnoma").strip()
    heading = doc.add_paragraph()
    heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = heading.add_run(title.upper())
    run.bold = True
    run.font.size = Pt(14)
    doc.add_paragraph()

    intro = (contract.get("intro") or "").strip()
    if intro:
        p = doc.add_paragraph(intro)
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        doc.add_paragraph()

    sections: List[Dict[str, Any]] = contract.get("sections") or []
    for idx, section in enumerate(sections, start=1):
        number = section.get("number") or idx
        sec_heading = (section.get("heading") or "").strip()
        if sec_heading:
            # Don't double-number when the model already prefixed the heading.
            label = sec_heading if re.match(r"^\s*\d+[.)]", sec_heading) else f"{number}. {sec_heading}"
            hp = doc.add_paragraph()
            hrun = hp.add_run(label)
            hrun.bold = True

        body = (section.get("body") or "").strip()
        for para in [b for b in body.split("\n") if b.strip()]:
            bp = doc.add_paragraph(para.strip())
            bp.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        doc.add_paragraph()

    blocks = contract.get("signature_blocks") or []
    if blocks:
        doc.add_paragraph()
        table = doc.add_table(rows=1, cols=max(2, len(blocks)))
        cells = table.rows[0].cells
        for i, block in enumerate(blocks[: len(cells)]):
            cell = cells[i]
            cell.text = ""
            role = cell.paragraphs[0]
            rrun = role.add_run((block.get("party_role") or "").strip())
            rrun.bold = True
            cell.add_paragraph((block.get("party_name") or "").strip())
            cell.add_paragraph()
            cell.add_paragraph("_______________________")
            cell.add_paragraph("(imzo / подпись)")

    target_dir = output_dir or tempfile.mkdtemp(prefix="advoai_docgen_")
    os.makedirs(target_dir, exist_ok=True)
    path = os.path.join(target_dir, f"{_safe_filename(title)}.docx")
    doc.save(path)
    logger.info(f"Rendered contract '{title}' -> {path}")
    return path


def contract_to_markdown(contract: Dict[str, Any]) -> str:
    """Flattens a contract to Markdown.

    Stored as the draft's `extracted_text` so the document goes into the same durable
    store as an upload — which is what lets the user say "change the salary" on the
    next turn and have the model actually see the draft it just produced.
    """
    lines = [f"# {contract.get('title', 'Shartnoma')}", ""]
    intro = (contract.get("intro") or "").strip()
    if intro:
        lines += [intro, ""]
    for idx, section in enumerate(contract.get("sections") or [], start=1):
        number = section.get("number") or idx
        lines.append(f"## {number}. {section.get('heading', '').strip()}")
        lines += [(section.get("body") or "").strip(), ""]
    for block in contract.get("signature_blocks") or []:
        lines.append(f"**{block.get('party_role', '')}**: {block.get('party_name', '')}")
    return "\n".join(lines)
