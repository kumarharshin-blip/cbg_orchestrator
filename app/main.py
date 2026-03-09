import asyncio
import json
import logging
from typing import Optional
from urllib.parse import urlparse

from fastapi import FastAPI, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings, setup_logging
from app.database import get_db, init_db
from app.schemas import FileJobCreate, FileUploadResponse, FileJobStatusResponse
from app.crud import create_file_job, get_file_job_by_req_id
from app.models import JobStatus
from app.services import process_file, restart_processing_jobs
from app.authorization_security.authorization_middleware import AuthorizationMiddleware

# Get settings
settings = get_settings()

# Setup logging (will create pipeline.log)
setup_logging()
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Production-ready file processing service with async operations",
)

# Register authorization middleware
# Bypassed paths: /health, /docs, /redoc, /openapi.json, /
app.add_middleware(AuthorizationMiddleware)


@app.on_event("startup")
async def startup_event():
    """
    Startup event handler.
    
    Initializes the database and restarts any jobs that were in "Processing" status.
    """
    logger.info("Application starting up...")
    
    try:
        # Initialize database tables
        await init_db()
        logger.info("Database initialized successfully")
        
        # Restart jobs that were in "Processing" status
        await restart_processing_jobs()
        
        logger.info("Application startup completed successfully")
        
    except Exception as e:
        logger.error(f"Error during startup: {str(e)}")
        raise


@app.on_event("shutdown")
async def shutdown_event():
    """Shutdown event handler."""
    logger.info("Application shutting down...")


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "message": "File Processing Service API",
        "version": settings.APP_VERSION,
        "status": "running"
    }


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}


@app.post("/identification", response_model=FileUploadResponse, status_code=status.HTTP_202_ACCEPTED)
async def identification_endpoint(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Submit an identification job via S3 path.

    Expects a Fernet-encrypted JSON body (decrypted by AuthorizationMiddleware).

    Payload fields:
        case_id   (str, required)  — client-supplied case identifier (for logging)
        s3_path   (str, required)  — S3 URL in format:
                                     https://s3.<region>.amazonaws.com/<bucket>/<req_id>/raw
                                     req_id is extracted automatically from this URL.
        split     (bool, optional, default False) — whether to split the document
        metadata  (str/dict, optional) — arbitrary metadata (JSON-serialisable)

    Headers (processed by middleware before reaching here):
        client_id  — channel identifier
        timestamp  — Unix ms timestamp
        hash       — SHA256 of (api_key|client_id|timestamp|encrypted_payload)

    Returns:
        FileUploadResponse with req_id (from s3_path) and status=Processing
    """
    # ── 1. client_id injected by AuthorizationMiddleware ─────────────────────
    client_id = getattr(request.state, "client_id", None)
    if not client_id:
        raise HTTPException(status_code=500, detail="Internal error: client_id not available")

    # ── 2. Parse decrypted body ───────────────────────────────────────────────
    body_bytes = await request.body()
    if not body_bytes:
        raise HTTPException(status_code=400, detail="Request body is empty")

    try:
        decrypted_payload = json.loads(body_bytes.decode("utf-8"))
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid payload format - expected JSON")

    # ── 3. Extract and validate required fields ───────────────────────────────
    case_id = decrypted_payload.get("case_id")
    if not case_id:
        raise HTTPException(status_code=400, detail="Missing required field: case_id")

    s3_path: Optional[str] = decrypted_payload.get("s3_path")
    if not s3_path:
        raise HTTPException(status_code=400, detail="Missing required field: s3_path")

    # ── 4. Extract req_id from s3_path ────────────────────────────────────────
    # Expected format: https://s3.<region>.amazonaws.com/<bucket>/<req_id>/raw
    try:
        path_segments = [p for p in urlparse(s3_path).path.split("/") if p]
        # path_segments = ['<bucket>', '<req_id>', 'raw', ...]
        if len(path_segments) < 2:
            raise ValueError("s3_path has too few path segments to extract req_id")
        req_id = path_segments[1]
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Could not extract req_id from s3_path: {e}",
        )

    try:
        logger.info(
            f"Received identification request: case_id={case_id}, "
            f"req_id={req_id}, client_id={client_id}"
        )

        # ── 5. Extract optional fields ────────────────────────────────────────
        split = decrypted_payload.get("split", False)
        if isinstance(split, str):
            split = split.lower() == "true"

        metadata = decrypted_payload.get("metadata")

        # Normalise metadata: dict/list → JSON string
        if metadata is not None and not isinstance(metadata, str):
            try:
                metadata = json.dumps(metadata)
            except (TypeError, ValueError) as e:
                raise HTTPException(status_code=400, detail=f"Invalid metadata format: {e}")

        # Validate metadata is valid JSON when provided as a string
        if isinstance(metadata, str) and metadata:
            try:
                json.loads(metadata)
            except json.JSONDecodeError:
                logger.error(f"Request {req_id}: Invalid JSON in metadata")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid JSON format in metadata",
                )

        # ── 6. Persist job record ─────────────────────────────────────────────
        job_create = FileJobCreate(
            s3_path=s3_path,
            req_id=req_id,
            split=split,
            request_metadata=metadata,
            status=JobStatus.Processing,
        )

        try:
            await create_file_job(db, job_create)
            await db.commit()
            logger.info(f"Request {req_id}: Job created in database")
        except Exception as e:
            logger.error(f"Request {req_id}: Error creating job in database - {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to create job: {str(e)}",
            )

        # ── 7. Fire background processing task ───────────────────────────────
        asyncio.create_task(process_file(req_id))
        logger.info(f"Request {req_id}: Background processing started")

        return FileUploadResponse(
            req_id=req_id,
            status=JobStatus.Processing,
            message="Request received and processing started",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Request {req_id}: Unexpected error - {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An unexpected error occurred: {str(e)}",
        )


@app.get("/status/{req_id}", response_model=FileJobStatusResponse)
async def get_status(
    req_id: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Get the status and results of a file processing job.
    
    Args:
        req_id: Unique request identifier (extracted from s3_path at submission time)
        db: Database session (dependency injection)
        
    Returns:
        FileJobStatusResponse with req_id, status, and results
    """
    try:
        job = await get_file_job_by_req_id(db, req_id)
        
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Job not found for req_id: {req_id}"
            )
        
        return FileJobStatusResponse(
            req_id=job.req_id,
            status=job.status,
            results=job.results
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching job status: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch job status: {str(e)}"
        )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )
