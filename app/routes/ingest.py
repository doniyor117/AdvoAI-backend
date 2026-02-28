"""
ingest.py — Ingestion API Route

Remote trigger for the data ingestion pipeline.
Parses a Lex.uz URL, generates embeddings, and saves to the database.
"""

import logging
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel, HttpUrl

from app.ingestion.main_ingest import process_and_ingest_law

router = APIRouter()
logger = logging.getLogger(__name__)


class IngestRequest(BaseModel):
    url: str  # e.g. "https://lex.uz/en/docs/-7904841"
    device: str = "cpu"
    skip_db: bool = False


@router.post("/")
def trigger_ingestion(request: IngestRequest):
    """
    Triggers the synchronous ingestion pipeline for a given document.
    
    WARNING: This is a heavy, slow process (especially on CPU). 
    In a true production environment, this should be executed asynchronously 
    via Celery/Redis, but for this MVP, it runs synchronously.
    """
    try:
        # Note: If running on CPU, this might take several minutes and could trigger
        # a gateway timeout on standard PaaS providers if not configured correctly.
        result = process_and_ingest_law(
            url=request.url,
            device=request.device,
            skip_db=request.skip_db
        )
        
        if not result:
            # Result is None if HTML fetch failed, or if document already exists
            raise HTTPException(
                status_code=400, 
                detail="Ingestion failed. Document might already exist or URL is invalid. Check server logs."
            )
            
        parent = result["parent_document"]
        chunks = result["search_chunks"]
        
        # We DO NOT return the full markdown nor the massive 1024-dim vectors
        # in the API response, as the JSON payload would be gigantic (megabytes).
        # We only return the metadata and ingestion summary.
        return {
            "status": "success",
            "message": "Document successfully ingested.",
            "data": {
                "document_id": parent["id"],
                "source_doc_id": parent["source_doc_id"],
                "title": parent["title"],
                "act_type": parent["act_type"],
                "doc_date": parent["doc_date"],
                "chars_length": len(parent["full_markdown"]),
                "chunks_created": len(chunks),
                "device_used": request.device
            }
        }
        
    except Exception as e:
        logger.error(f"Ingestion API error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
