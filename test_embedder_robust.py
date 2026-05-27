import logging
logging.basicConfig(level=logging.DEBUG)

from app.services.embedder import get_embedder

mock_chunks = [{"id": f"id-{i}", "text": f"This is test text number {i}", "parent_id": "doc-uuid"} for i in range(105)]

print(f"Creating embedder and embedding {len(mock_chunks)} chunks...")
embedder = get_embedder()
embedded = embedder.embed_chunks(mock_chunks, doc_title="Robustness Test")

print(f"Successfully embedded {len(embedded)} chunks!")
print(f"First embedding length: {len(embedded[0]['embedding'])}")
