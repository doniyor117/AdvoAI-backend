# Basira AI - Legal Chatbot with RAG

```text
project_basira/
│
├── .env                         # Secrets! >> cp from .env.example
├── .env.example                 # .env file variables structure
├── .gitignore                   # Ignores data/ and .env
├── requirements.txt             # Deps: pdfplumber, psycopg2-binary, pgvector, torch
├── config.py                    # (+) Central place for settings (CHUNK_SIZE, MODEL_NAME)
│
├── data/                        
│   └── golden_dataset.csv       # (+) For testing your bot later
│   
├── notebooks/                   # (+) Your experimental lab
│   └── lab.ipynb                # Test, experiment, and evaluate here
│
├── src/
│   ├── ingestion/               # PHASE 1: READ
│   │   ├── __init__.py
│   │   ├── lex_parser.py        # Parsing disguised doc file (html) form lex.uz on the fly
│   │   └── cleaner.py           # Funcs: regex cleaning
│   │
│   ├── database/                # PHASE 2: STORE
│   │   ├── __init__.py
│   │   ├── schema.sql           # SQL: CREATE TABLE ...
│   │   └── neon_client.py       # Class: DatabaseManager (Handling connection & queries)
│   │
│   ├── preprocessing/           # PHASE 3: CHUNK
│   │   ├── __init__.py
│   │   └── chunker.py           # Funcs: window_chunker (Sentence + Neighbors)
│   │
│   ├── models/                  # PHASE 4: INTELLIGENCE
│   │   ├── __init__.py
│   │   ├── embedder.py          # Class: EmbeddingModel (BGE-M3)
│   │   ├── reranker.py          # (+) Class: CrossEncoder (The Quality Control)
│   │   └── llm_client.py        # Class: Google AI Studio (Gemini 2.5 flash) (it is cheap and have context caching)
│   │
│   └── app/                     # (+) Organize the app logic
│       └── chat.py              # The CLI Chat interface
│
├── main_ingest.py               # Script: Run Ingestion Pipeline (Local)
├── main.py                      # Script: Run Chatbot (App)
|
└── README.md
```

## Basira: (Arabic) "Insight" or "Inner Vision."
