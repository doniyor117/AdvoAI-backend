import requests
import re
import tempfile
import os
import logging
from typing import Dict, Any
from bs4 import BeautifulSoup
from markitdown import MarkItDown

logger = logging.getLogger(__name__)

class LexParser:
    """
    Fetches Lex.uz documents, cleans the UI noise using BeautifulSoup,
    extracts metadata, and converts the cleaned HTML to perfect Markdown 
    using Microsoft's MarkItDown.
    """
    
    def __init__(self):
        self.headers = {
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36'
        }
        self.soup = None
        self.url = None
        self.md_converter = MarkItDown()

    def fetch_html(self, url: str) -> bool:
        logger.info(f"🌐 Connecting to: {url}")
        self.url = url if "type=doc" in url else f'{url}?type=doc'
        try:
            response = requests.get(self.url, headers=self.headers, timeout=20)
            response.raise_for_status()
            self.soup = BeautifulSoup(response.text, 'lxml')
            logger.info("✅ Success! HTML loaded.")
            return True
        except Exception as err:
            logger.error(f"❌ Error fetching {url}: {err}")
            return False

    def clean_soup(self):
        """Removes Lex.uz UI elements so they don't end up in the Markdown."""
        if not self.soup: return

        for tag in self.soup(["script", "style", "meta", "link"]):
            tag.decompose()

        garbage_patterns = [
            re.compile(r"Suggestion to the document", re.IGNORECASE),
            re.compile(r"Listen to audio", re.IGNORECASE),
            re.compile(r"Get a link from a document", re.IGNORECASE)
        ]

        for pattern in garbage_patterns:
            for text_node in self.soup.find_all(string=pattern):
                parent = text_node.find_parent(['div', 'p', 'table'])
                if parent:
                    parent.decompose()

    def get_metadata(self) -> Dict[str, Any]:
        """Extracts fallback basic metadata. Advanced metadata is extracted later via LLM."""
        return {
            "source_url": self.url.split("?")[0] if self.url else None,
            "is_active": True
        }

    def parse(self) -> Dict[str, Any]:
        if not self.soup:
            raise ValueError("Soup not initialized. Call fetch_html() first.")

        # 1. Clean the UI buttons
        self.clean_soup()
        
        # 2. Extract Metadata
        metadata = self.get_metadata()
        
        # 3. MarkItDown usually requires a file path. We write our cleaned HTML 
        # to a temporary file, convert it, and delete the temp file.
        logger.info("📝 Converting cleaned HTML to Markdown...")
        cleaned_html_string = str(self.soup)
        
        with tempfile.NamedTemporaryFile(delete=False, suffix=".html", mode='w', encoding='utf-8') as temp_file:
            temp_file.write(cleaned_html_string)
            temp_file_path = temp_file.name

        try:
            result = self.md_converter.convert(temp_file_path)
            markdown_content = result.text_content
        finally:
            os.remove(temp_file_path) # Clean up

        return {
            "metadata": metadata,
            "markdown": markdown_content # The full, perfectly formatted document!
        }
    
if __name__ == "__main__":
    import json
    
    # Configure basic logging for direct script execution
    logging.basicConfig(level=logging.INFO)
    
    parser = LexParser()
    test_url = "https://lex.uz/docs/7339833"
    
    if parser.fetch_html(test_url):
        result = parser.parse()
        logger.info(f"Metadata:\n{json.dumps(result['metadata'], indent=2, ensure_ascii=False)}")
        
        # Optionally write markdown to file to view it
        with open("test_output.md", "w", encoding="utf-8") as f:
            f.write(result["markdown"])
        logger.info("Output written to test_output.md")