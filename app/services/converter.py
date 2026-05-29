"""
converter.py — Document-to-Markdown Conversion Service

Uses Microsoft MarkItDown to convert messy document formats (HTML, DOC, DOCX, RTF, CSV)
into clean, token-efficient Markdown before uploading to the Gemini Files API.

Why this matters:
  - A raw HTML page can contain 200,000+ tokens (CSS, scripts, markup).
  - The same page converted to Markdown typically uses 5,000–15,000 tokens.
  - .doc files from legal portals (like lex.uz) are often HTML disguised as .doc.
  - Clean Markdown = better answers, lower cost, no INVALID_ARGUMENT token overflows.

Supported conversions:
  text/html           → Markdown (strips all CSS/JS/tags)
  application/msword  → Markdown (handles HTML-disguised .doc perfectly)
  application/vnd.openxmlformats-officedocument.wordprocessingml.document → Markdown
  application/rtf / text/rtf → Markdown
  text/csv            → Markdown table
"""

import logging
import asyncio
import tempfile
import os
import httpx
from typing import Optional

logger = logging.getLogger(__name__)

# MIME types that must be converted to Markdown before Gemini upload
CONVERTIBLE_MIME_TYPES = {
    "text/html",
    "application/msword",                                                                    # .doc
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",              # .docx
    "application/rtf",
    "text/rtf",
    "text/csv",
}


def _run_markitdown(file_path: str) -> str:
    """
    Synchronous MarkItDown conversion. Runs in a thread via asyncio.to_thread.
    Returns the converted Markdown string.
    """
    from markitdown import MarkItDown  # lazy import to avoid slow startup
    md = MarkItDown()
    result = md.convert(file_path)
    return result.text_content


async def convert_to_markdown(file_path: str, mime_type: str, display_name: str = "") -> Optional[str]:
    """
    Converts a document file to clean Markdown text using MarkItDown.

    Args:
        file_path:    Path to the uploaded temp file.
        mime_type:    MIME type of the original file.
        display_name: Original filename (used only for logging).

    Returns:
        Markdown string if conversion succeeded, None if it should be skipped.

    Raises:
        ValueError if conversion fails catastrophically.
    """
    if mime_type not in CONVERTIBLE_MIME_TYPES:
        return None  # Not a convertible type — caller should upload directly

    logger.info(f"Converting '{display_name}' ({mime_type}) to Markdown via MarkItDown...")

    try:
        markdown_text = await asyncio.to_thread(_run_markitdown, file_path)

        if not markdown_text or not markdown_text.strip():
            raise ValueError(f"MarkItDown produced empty output for '{display_name}'.")

        char_count = len(markdown_text)
        logger.info(f"Conversion successful: '{display_name}' → {char_count:,} chars of Markdown")
        return markdown_text

    except Exception as e:
        logger.error(f"MarkItDown conversion failed for '{display_name}': {e}")
        raise ValueError(
            f"Could not read the contents of '{display_name}'. "
            f"The file may be corrupted, password-protected, or in an unsupported sub-format. "
            f"Please try saving it as a PDF or plain text (.txt) and re-uploading."
        )


async def fetch_and_convert_url(url: str) -> Optional[str]:
    """
    Fetches a URL and converts it to Markdown.
    """
    logger.info(f"Implicitly scraping URL: {url}")
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            response = await client.get(
                url, 
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            )
            response.raise_for_status()
            html_content = response.text
            
        def _save(data: str) -> str:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".html", mode="w", encoding="utf-8") as tmp:
                tmp.write(data)
                return tmp.name

        tmp_path = await asyncio.to_thread(_save, html_content)
        
        try:
            markdown_text = await asyncio.to_thread(_run_markitdown, tmp_path)
            if markdown_text and markdown_text.strip():
                logger.info(f"Successfully scraped URL: {url} ({len(markdown_text)} chars)")
                return markdown_text
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
                
    except Exception as e:
        logger.warning(f"Failed to scrape URL {url}: {e}")
        
    return None


async def save_markdown_as_tempfile(markdown_text: str, original_display_name: str) -> str:
    """
    Saves Markdown text to a temporary .md file for Gemini upload.

    Returns:
        Path to the temporary .md file. Caller is responsible for deleting it.
    """
    def _write(text: str) -> str:
        with tempfile.NamedTemporaryFile(
            delete=False,
            suffix=".md",
            mode="w",
            encoding="utf-8"
        ) as tmp:
            tmp.write(text)
            return tmp.name

    tmp_path = await asyncio.to_thread(_write, markdown_text)
    logger.debug(f"Saved converted Markdown to temp file: {tmp_path}")
    return tmp_path
