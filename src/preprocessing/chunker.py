import os
from typing import List, Dict
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_experimental.text_splitter import SemanticChunker
from langchain_core.documents import Document

class UniversalChunker:
    """
    Splits text based on semantic similarity rather than structural formatting.
    Ideal for mixed corpora (Laws, Resolutions, Lists).
    """

    def __init__(self, model_name: str = "BAAI/bge-m3", device: str = "cpu"):
        print(f"Loading Embedding Model ({model_name}) on {device}...")
        
        # Initialize the Embedding Model
        self.embeddings = HuggingFaceEmbeddings(
            model_name=model_name,
            model_kwargs={'device': device},
            encode_kwargs={'normalize_embeddings': True}
        )

        # Initialize Semantic Splitter
        # 'percentile': Splits when difference is in the top X% of all differences.
        # Lower breakpoint_threshold_amount = More chunks (more sensitive).
        # Higher breakpoint_threshold_amount = Fewer, larger chunks.
        self.splitter = SemanticChunker(
            self.embeddings,
            breakpoint_threshold_type="percentile", 
            breakpoint_threshold_amount=90 
        )

    def chunk_document(self, text: str, meta: Dict = None) -> List[Dict]:
        """
        :param text: Full raw text of the document.
        :param meta: Document-level metadata.
        :return: List of chunk dictionaries ready for DB.
        """
        # Ensure meta is a dict for downstream `.get()` usage
        meta = meta or {}

        if not text.strip():
            return []

        # Create LangChain Document object
        lc_doc = Document(page_content=text, metadata=meta or {})
        
        # Perform Semantic Splitting
        try:
            split_docs = self.splitter.split_documents([lc_doc])
        except Exception as e:
            print(f"Error during semantic chunking: {e}")
            # Fallback: If semantic fails (too short), return whole text
            split_docs = [lc_doc]

        # Format for Basira Pipeline
        formatted_chunks = []
        for i, doc in enumerate(split_docs):
            chunk_id = f"{meta.get('doc_id', 'doc')}_{i}"
            
            # We add a small header to help the LLM identify the source later
            context_header = f"Hujjat: {meta.get('title', 'Unknown')}\n\n"
            
            formatted_chunks.append({
                "chunk_id": chunk_id,
                "text": context_header + doc.page_content, # Content for Embedding
                "original_text": doc.page_content,         # Content for LLM Reading
                "metadata": doc.metadata
            })

        return formatted_chunks

# --- Quick Test Block ---
if __name__ == "__main__":
    import json

    path: str = input("Enter path for a document text (or paste a long one):\n")

    # If the input is a path, read the file content; otherwise treat input as raw text
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    else:
        text = path

    meta_input = input("Enter metadata as JSON or path for the file (e.g., {\"doc_id\": \"123\", \"title\": \"Sample Doc\"}):\n")
    meta = {}
    if os.path.exists(meta_input):
        with open(meta_input, "r", encoding="utf-8") as f:
            try:
                meta = json.load(f)
            except Exception as e:
                print("Failed to parse metadata file as JSON:", e)
                meta = {}
    else:
        try:
            meta = json.loads(meta_input)
        except Exception:
            print("Failed to parse metadata input as JSON; using empty metadata.")
            meta = {}

    # Use 'cuda' if you have a GPU, otherwise 'cpu'
    chunker = UniversalChunker(device="cpu")
    chunks = chunker.chunk_document(text=text, meta=meta)

    for chunk in chunks:
        print(f"--- Chunk {chunk['chunk_id']} ---")
        print(chunk['text'])
        print("\n")