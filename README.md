## Basira AI - Legal Chatbot with RAG

```
project_root/
│
├── data/                        # YOUR RAW FUEL
│   ├── raw_pdfs/                # Original PDF files (Legal/Car docs)
│   ├── processed_markdown/      # Cleaned text files (verified by human)
│   └── golden_dataset.csv       # The 50 Q&A pairs for validation
│
├── storage/                     # YOUR PERSISTENT MEMORY
│   ├── chromadb_store/          # Local Vector DB files (Do not touch manually)
│   └── bm25_index.pkl           # Sparse vector index (saved as a pickle file)
│
├── src/                         # THE SOURCE CODE
│   ├── ingestion/               # STEP 1: READING & CLEANING
│   │   ├── __init__.py
│   │   ├── pdf_parser.py        # Uses 'pdfplumber' to extract text & tables
│   │   ├── ocr_engine.py        # Uses 'pytesseract' for scanned docs
│   │   └── cleaner.py           # Regex rules to remove headers/footers
│   │
│   ├── preprocessing/           # STEP 2: CHUNKING
│   │   ├── __init__.py
│   │   ├── window_chunker.py    # IMPL: Sentence Window Retrieval (Sentence + 3 neighbors)
│   │   └── semantic_chunker.py  # IMPL: Splitting by cosine similarity (Advanced)
│   │
│   ├── models/                  # STEP 3: THE BRAINS
│   │   ├── __init__.py
│   │   ├── embedder.py          # Wrapper for 'BGE-M3' (handles Matryoshka slicing)
│   │   ├── reranker.py          # Wrapper for 'bge-reranker-v2-m3'
│   │   └── llm_client.py        # Wrapper for 'Ollama' API (Llama 3)
│   │
│   ├── retrieval/               # STEP 4: SEARCH LOGIC
│   │   ├── __init__.py
│   │   ├── vector_search.py     # Connects to ChromaDB/FAISS
│   │   ├── keyword_search.py    # IMPL: BM25 algorithm (Sparse Vectors)
│   │   └── hybrid_engine.py     # IMPL: Combines Vector + Keyword results (RRF)
│   │
│   └── evaluation/              # STEP 5: METRICS
│       ├── __init__.py
│       ├── metrics.py           # IMPL: Hit Rate, MRR calculation
│       └── evaluator.py         # Runs the pipeline against 'golden_dataset.csv'
│
├── notebooks/                   # YOUR EXPERIMENTS (The "From Scratch" Lab)
│   ├── 01_tfidf_from_scratch.ipynb  # You building TF-IDF with NumPy
│   ├── 02_chunking_visualizer.ipynb # Visualizing window overlaps
│   └── 03_hybrid_search_test.ipynb  # Comparing BM25 vs Vectors
│
├── config.py                    # Global settings (Chunk size, Model names)
├── main.py                      # The entry point to run the CLI bot
├── requirements.txt             # Dependencies (pdfplumber, chromadb, torch)
└── README.md                    # Documentation
```
