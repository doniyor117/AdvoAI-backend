import logging
import uuid
import os
import tempfile
import aioboto3
from typing import Optional, Tuple
from app.config import settings

logger = logging.getLogger(__name__)

def is_s3_configured() -> bool:
    return bool(
        settings.S3_ENDPOINT_URL and
        settings.S3_ACCESS_KEY_ID and
        settings.S3_SECRET_ACCESS_KEY and
        settings.S3_BUCKET_NAME
    )

async def upload_file_to_s3(file_path: str, display_name: str, mime_type: str) -> Optional[str]:
    """
    Uploads a local file to S3 (or Cloudflare R2) and returns the S3 key.
    If S3 is not configured, returns None.
    """
    if not is_s3_configured():
        return None

    s3_key = f"{uuid.uuid4().hex}_{display_name}"
    
    try:
        session = aioboto3.Session()
        async with session.client(
            's3',
            endpoint_url=settings.S3_ENDPOINT_URL,
            aws_access_key_id=settings.S3_ACCESS_KEY_ID,
            aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY,
            region_name="auto" # Cloudflare R2 supports "auto"
        ) as s3:
            await s3.upload_file(
                file_path,
                settings.S3_BUCKET_NAME,
                s3_key,
                ExtraArgs={'ContentType': mime_type}
            )
            logger.info(f"Successfully uploaded {s3_key} to S3/R2")
            return s3_key
    except Exception as e:
        logger.error(f"Failed to upload to S3/R2: {e}")
        return None

async def download_file_from_s3(s3_key: str) -> Optional[str]:
    """
    Downloads a file from S3 (or Cloudflare R2) to a local temporary file.
    Returns the path to the temporary file, or None if failed.
    """
    if not is_s3_configured():
        return None
        
    try:
        session = aioboto3.Session()
        async with session.client(
            's3',
            endpoint_url=settings.S3_ENDPOINT_URL,
            aws_access_key_id=settings.S3_ACCESS_KEY_ID,
            aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY,
            region_name="auto"
        ) as s3:
            ext = os.path.splitext(s3_key)[1] or ".bin"
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
            tmp.close()
            
            await s3.download_file(
                settings.S3_BUCKET_NAME,
                s3_key,
                tmp.name
            )
            logger.info(f"Successfully downloaded {s3_key} from S3/R2 to {tmp.name}")
            return tmp.name
    except Exception as e:
        logger.error(f"Failed to download from S3/R2: {e}")
        return None


async def generate_presigned_url(s3_key: str, expires_in: int = 3600) -> Optional[str]:
    """
    Generates a presigned URL for a file in S3/R2.
    The URL is time-limited (default: 1 hour) — safe to expose to the frontend.
    Returns None if S3 is not configured or generation fails.
    """
    if not is_s3_configured():
        return None

    try:
        session = aioboto3.Session()
        async with session.client(
            's3',
            endpoint_url=settings.S3_ENDPOINT_URL,
            aws_access_key_id=settings.S3_ACCESS_KEY_ID,
            aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY,
            region_name="auto"
        ) as s3:
            url = await s3.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': settings.S3_BUCKET_NAME,
                    'Key': s3_key,
                },
                ExpiresIn=expires_in,
            )
            return url
    except Exception as e:
        logger.error(f"Failed to generate presigned URL for {s3_key}: {e}")
        return None
