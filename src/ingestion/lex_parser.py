import requests
import re
from typing import Dict, Optional
from bs4 import BeautifulSoup

# [ADDED] We bring in the specific HTML partitioner from unstructured
from unstructured.partition.html import partition_html

class LexParser:
    """
    A specialized scraper for Lex.uz.
    Fetches the document, cleans Lex-specific UI noise, extracts custom legal metadata,
    and passes the cleaned HTML directly to Unstructured for semantic partitioning.
    """
    
    def __init__(self):
        self.headers = {
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br, zstd',
        'Accept-Language': 'en-US,en;q=0.9',
        'Cache-Control': 'max-age=0',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36'
        }
        self.soup = None
        self.url = None

    def fetch_html(self, url: str) -> bool:
        """[KEPT] Fetches data and initializes soup. No changes needed."""
        print(f"🌐 Connecting to: {url}")
        self.url = url if "type=doc" in url else f'{url}?type=doc'

        try:
            response = requests.get(self.url, headers=self.headers, timeout=20)
            response.raise_for_status()
            self.soup = BeautifulSoup(response.text, 'lxml')
            print("✅ Success! HTML loaded.")
            return True
        except Exception as err:
            print(f"❌ Error fetching {url}: {err}")
            return False

    def clean_soup(self):
        """
        [UPDATED] We keep this! Even though unstructured is smart, it might 
        accidentally parse the Lex.uz "Listen to audio" buttons as NarrativeText. 
        It is always best to scrub the UI noise before partitioning.
        """
        if not self.soup: return

        for tag in self.soup(["script", "style", "meta", "link", "xml", "o:p"]):
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

    # [REMOVED] def _html_table_to_markdown(...)
    # GONE! We deleted all 50+ lines of this. Unstructured handles complex HTML 
    # rowspans and colspans natively now.

    def get_metadata(self) -> Dict[str, any]:
        """
        [KEPT] We absolutely keep this! Unstructured can find the "Title", but it 
        doesn't know what a Lex.uz "doc_id" or "act_type" is. This custom metadata 
        will be injected into your Vector DB later for powerful filtering.
        """
        meta = {
            "source_url": self.url.split("?")[0] if self.url else None,
            "doc_id": None,
            "doc_date": None,
            "act_type": "Unknown",
            "title": "Untitled Document",
            "is_active": True,
            "status_details": "Active"
        }

        if self.soup.title:
            title_text = self.soup.title.get_text(strip=True)
            match = re.search(r"^(\d+)\s+(\d{2}\.\d{2}\.\d{4})", title_text)
            if match:
                meta["doc_id"] = match.group(1)
                meta["doc_date"] = match.group(2)

        act_form = self.soup.find(class_=lambda c: c and "ACT_FORM" in c)
        if act_form:
            meta["act_type"] = act_form.get_text(strip=True)

        act_title = self.soup.find(class_=lambda c: c and "ACT_TITLE" in c)
        if act_title:
            meta["title"] = act_title.get_text(strip=True)

        expiration_node = self.soup.find(class_="aExp")
        if expiration_node:
            text = expiration_node.get_text(strip=True)
            if text:
                meta["is_active"] = False
                meta["status_details"] = text

        return meta

    # [REMOVED] def get_clean_text(...)
    # GONE! We no longer want to flatten the HTML into plain text. We want to preserve 
    # the HTML tags (like <table> and <b>) so Unstructured can "see" the document structure.

    def parse(self) -> Dict[str, any]:
        """
        [UPDATED] Instead of returning plain text, this now runs `partition_html` 
        directly on the cleaned HTML string and returns the structured elements!
        """
        if not self.soup:
            raise ValueError("Soup not initialized. Call fetch_html() first.")

        # 1. Scrub the Lex.uz UI buttons
        self.clean_soup()
        
        # 2. Grab our custom legal metadata
        metadata = self.get_metadata()
        
        # 3. Get the cleaned HTML as a raw string
        cleaned_html_string = str(self.soup)

        # 4. Pass the HTML string directly to Unstructured!
        # Notice we use `text=` instead of `filename=` because we are doing this in memory.
        print("🧠 Partitioning document with Unstructured...")
        elements = partition_html(text=cleaned_html_string)

        return {
            "metadata": metadata,
            "elements": elements # This is now a list of Unstructured Element objects!
        }
    
if __name__ == '__main__':
    url = input("Enter a Lex.uz document URL: ")
    parser = LexParser()
    if parser.fetch_html(url):
        result = parser.parse()
        print("Metadata:", result["metadata"], end="\n\n")
        print("First 3 Elements:\n")
        for element in result["elements"][:3]:  # Show the first 3 elements for brevity
            # It gives you a list of Element objects, carrying metadata and category info
            print(f"Type: {element.category} | Text: {element.text[:50]}...")