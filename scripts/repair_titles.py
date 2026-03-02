"""
repair_titles.py — One-time script to fix "Untitled Document" titles 
by re-fetching the title from the source URLs.

Usage:
    conda activate basira_libs
    cd yurika-backend
    python -m scripts.repair_titles
"""

import os
import re
import sys

import requests
import psycopg2
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()


def extract_title_from_url(url: str) -> str:
    """Fetches the page and extracts the title from the HTML <title> tag."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    full_url = url if "type=doc" in url else f"{url}?type=doc"
    
    try:
        resp = requests.get(full_url, headers=headers, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'lxml')
        
        if soup.title:
            title_text = soup.title.get_text(strip=True)
            # Format: "784-сон 11.12.2025. Actual Title Here"
            match = re.search(r"^\d+\s+\d{2}\.\d{2}\.\d{4}\.\s*(.*)", title_text, re.DOTALL)
            if match:
                actual = match.group(1).strip()
                if actual:
                    return actual
            return title_text  # fallback to full <title>
    except Exception as e:
        print(f"  ❌ Failed to fetch {url}: {e}")
    
    return ""


def main():
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("❌ DATABASE_URL not set in .env")
        sys.exit(1)

    conn = psycopg2.connect(db_url)
    cur = conn.cursor()

    # Find documents with bad titles
    cur.execute("""
        SELECT id, source_doc_id, title, source_url 
        FROM documents 
        WHERE title IN ('Untitled Document', 'Unknown', '')
           OR title IS NULL;
    """)
    rows = cur.fetchall()

    if not rows:
        print("✅ No documents need title repair.")
        cur.close()
        conn.close()
        return

    print(f"🔧 Found {len(rows)} document(s) needing title repair:\n")

    for doc_id, src_id, old_title, source_url in rows:
        print(f"  📄 {src_id} ({str(doc_id)[:8]}...) — current: {repr(old_title)}")
        
        new_title = extract_title_from_url(source_url)
        if new_title and new_title != old_title:
            cur.execute(
                "UPDATE documents SET title = %s WHERE id = %s;",
                (new_title, str(doc_id))
            )
            print(f"     ✅ Updated to: {new_title[:80]}")
        else:
            print(f"     ⏭️  Skipped (no better title found)")

    conn.commit()
    cur.close()
    conn.close()
    print(f"\n✅ Title repair complete.")


if __name__ == "__main__":
    main()
