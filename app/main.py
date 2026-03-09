import asyncio
import base64
import json
import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings, setup_logging
from app.database import get_db, init_db
from app.schemas import FileJobCreate, FileUploadResponse, FileJobStatusResponse
from app.crud import create_file_job, get_file_job_by_request_id
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
        
        # Create uploads directory if it doesn't exist
        upload_dir = Path(settings.UPLOAD_DIR)
        upload_dir.mkdir(exist_ok=True)
        logger.info(f"Upload directory ready: {upload_dir.absolute()}")
        
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
    Process a file for identification (either from base64 file_data or S3 path).

    Expects a Fernet-encrypted JSON body (decrypted by AuthorizationMiddleware).

    Payload fields:
        case_id   (str, required)  — client-supplied unique identifier for this request
        split     (bool, optional, default False) — whether to split the file
        s3_path   (str, optional)  — S3 URL; mutually exclusive with file_data
        metadata  (str/dict, optional) — arbitrary metadata (JSON-serialisable)
        file_data (str, optional)  — base64-encoded file bytes; mutually exclusive with s3_path
        filename  (str, optional)  — original filename; required when file_data is provided

    Headers (processed by middleware before reaching here):
        client_id  — channel identifier (available on request.state.client_id)
        timestamp  — Unix ms timestamp
        hash       — SHA256 of (api_key|client_id|timestamp|encrypted_payload)

    Returns:
        FileUploadResponse with case_id (as request_id) and status='Processing'
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

    # ── 3. Extract and validate required field: case_id ───────────────────────
    case_id = decrypted_payload.get("case_id")
    if not case_id:
        raise HTTPException(status_code=400, detail="Missing required field: case_id")

    # Use case_id as the internal request_id
    request_id = case_id

    try:
        logger.info(f"Received identification request: case_id={case_id}, client_id={client_id}")

        # ── 4. Extract optional fields ────────────────────────────────────────
        split = decrypted_payload.get("split", False)
        if isinstance(split, str):
            split = split.lower() == "true"

        s3_path: Optional[str] = decrypted_payload.get("s3_path")
        metadata = decrypted_payload.get("metadata")
        file_data_b64: Optional[str] = decrypted_payload.get("file_data")
        filename: Optional[str] = decrypted_payload.get("filename")

        # Normalise metadata: if dict/list, serialise back to JSON string for consistency
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
                logger.error(f"Request {request_id}: Invalid JSON in metadata")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid JSON format in metadata",
                )

        # ── 5. Validate file source: exactly one of file_data or s3_path ─────
        if file_data_b64 is None and s3_path is None:
            logger.error(f"Request {request_id}: Neither file_data nor s3_path provided")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Either 'file_data' or 's3_path' must be provided",
            )

        if file_data_b64 is not None and s3_path is not None:
            logger.error(f"Request {request_id}: Both file_data and s3_path provided")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot provide both 'file_data' and 's3_path'. Choose one.",
            )

        # ── 6. Handle file_data (base64) upload ───────────────────────────────
        local_filepath: Optional[str] = None

        if file_data_b64 is not None:
            if not filename:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="'filename' is required when 'file_data' is provided",
                )
            try:
                file_bytes = base64.b64decode(file_data_b64)
            except Exception as e:
                logger.error(f"Request {request_id}: Failed to decode base64 file_data - {e}")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid base64 encoding in file_data: {e}",
                )

            safe_filename = f"{request_id}_{filename}"
            local_filepath = os.path.join(settings.UPLOAD_DIR, safe_filename)
            try:
                with open(local_filepath, "wb") as fout:
                    fout.write(file_bytes)
                logger.info(f"Request {request_id}: File saved to {safe_filename}")
            except Exception as e:
                logger.error(f"Request {request_id}: Error saving file - {str(e)}")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to save file: {str(e)}",
                )

        # ── 7. Handle S3 path ─────────────────────────────────────────────────
        if s3_path is not None:
            logger.info(f"Request {request_id}: Using S3 path")
            if not filename and '/' in s3_path:
                filename = s3_path.split('/')[-1]

        # ── 8. Persist job record ─────────────────────────────────────────────
        job_create = FileJobCreate(
            filename=filename,
            local_filepath=local_filepath,
            s3_path=s3_path,
            request_id=request_id,
            split=split,
            request_metadata=metadata,
            status="Processing",
        )

        try:
            await create_file_job(db, job_create)
            await db.commit()
            logger.info(f"Request {request_id}: Job created in database")
        except Exception as e:
            if local_filepath and os.path.exists(local_filepath):
                os.remove(local_filepath)
            logger.error(f"Request {request_id}: Error creating job in database - {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to create job: {str(e)}",
            )

        # ── 9. Fire background processing task ───────────────────────────────
        asyncio.create_task(process_file(request_id))
        logger.info(f"Request {request_id}: Background processing started")

        return FileUploadResponse(
            request_id=request_id,
            status="Processing",
            message="Request received and processing started",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Request {request_id}: Unexpected error - {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An unexpected error occurred: {str(e)}",
        )


@app.get("/status/{request_id}", response_model=FileJobStatusResponse)
async def get_status(
    request_id: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Get the status and results of a file processing job.
    
    Args:
        request_id: Unique request identifier
        db: Database session (dependency injection)
        
    Returns:
        FileJobStatusResponse with request_id, status, and results
    """
    try:
        # Fetch job from database
        job = await get_file_job_by_request_id(db, request_id)
        
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Job not found for request_id: {request_id}"
            )
        
        # Return status response
        return FileJobStatusResponse(
            request_id=job.request_id,
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
