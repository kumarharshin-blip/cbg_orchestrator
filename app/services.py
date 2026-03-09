import asyncio
import json
import logging
import os
import tempfile
from datetime import datetime
from typing import Dict, Any, Optional, Tuple
from urllib.parse import urlparse

import httpx
import aioboto3
from botocore.exceptions import BotoCoreError, ClientError

from app.database import AsyncSessionLocal
from app.crud import get_file_job_by_request_id, update_job_status
from app.config import get_settings

# Get logger and settings
logger = logging.getLogger(__name__)
settings = get_settings()


def parse_s3_url(s3_url: str) -> Tuple[str, str]:
    """
    Parse S3 URL to extract bucket and key.
    
    Supports formats:
    - https://s3.region.amazonaws.com/bucket/key
    - https://bucket.s3.region.amazonaws.com/key
    - s3://bucket/key
    
    Args:
        s3_url: S3 URL string
        
    Returns:
        Tuple of (bucket_name, object_key)
    """
    if s3_url.startswith('s3://'):
        # s3://bucket/key format
        parts = s3_url[5:].split('/', 1)
        bucket = parts[0]
        key = parts[1] if len(parts) > 1 else ''
    else:
        # HTTPS URL format
        parsed = urlparse(s3_url)
        path_parts = parsed.path.lstrip('/').split('/', 1)
        
        # Check if bucket is in hostname (bucket.s3.region.amazonaws.com)
        if '.s3.' in parsed.netloc or '.s3-' in parsed.netloc:
            bucket = parsed.netloc.split('.')[0]
            key = parsed.path.lstrip('/')
        else:
            # Bucket is first part of path (s3.region.amazonaws.com/bucket/key)
            bucket = path_parts[0] if path_parts else ''
            key = path_parts[1] if len(path_parts) > 1 else ''
    
    return bucket, key


async def download_from_s3(s3_url: str, destination_path: str) -> None:
    """
    Download a file from S3 to local storage.
    
    Args:
        s3_url: S3 URL (https or s3:// format)
        destination_path: Local path where file should be saved
        
    Raises:
        Exception: If download fails
    """
    try:
        # Parse S3 URL
        bucket, key = parse_s3_url(s3_url)
        logger.info(f"Downloading from S3 - Bucket: {bucket}, Key: {key}")
        
        # Create S3 client
        session = aioboto3.Session()
        async with session.client('s3') as s3_client:
            # Download file
            await s3_client.download_file(bucket, key, destination_path)
        logger.info(f"Successfully downloaded file to {destination_path}")
        
    except (BotoCoreError, ClientError) as e:
        logger.error(f"S3 download failed: {str(e)}")
        raise Exception(f"Failed to download file from S3: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error during S3 download: {str(e)}")
        raise


async def process_file(request_id: str) -> None:
    """
    Background task to process a file job.
    
    This function:
    1. Fetches the job from the database
    2. Validates file source (local or S3)
    3. Downloads S3 file if needed
    4. Calls external split service API with the file
    5. Stores results in database
    6. Handles exceptions and updates status to "Failed" on error
    
    Args:
        request_id: Unique request identifier for the job
    """
    logger.info(f"Starting background processing for request_id: {request_id}")
    
    temp_file_path = None  # Track temporary file for cleanup
    
    async with AsyncSessionLocal() as db:
        try:
            # Fetch the job from database
            job = await get_file_job_by_request_id(db, request_id)
            
            if not job:
                logger.error(f"Job not found for request_id: {request_id}")
                return
            
            # Validate that we have either local_filepath or s3_path
            if not job.local_filepath and not job.s3_path:
                error_msg = "Neither local_filepath nor s3_path found in database"
                logger.error(f"Request {request_id}: {error_msg}")
                raise ValueError(error_msg)
            
            # Determine file path to use
            file_path = None
            filename = job.filename or "document"
            
            # Handle S3 files - download to temporary location
            if job.s3_path and not job.local_filepath:
                logger.info(f"Request {request_id}: Downloading file from S3")
                
                # Create temp file with proper extension
                file_ext = os.path.splitext(filename)[1] if filename else '.tmp'
                temp_fd, temp_file_path = tempfile.mkstemp(suffix=file_ext, prefix=f"{request_id}_")
                os.close(temp_fd)  # Close file descriptor, we'll use the path
                
                # Download from S3
                await download_from_s3(job.s3_path, temp_file_path)
                file_path = temp_file_path
                logger.info(f"Request {request_id}: File downloaded from S3 to temporary location")
            
            # Handle local files
            elif job.local_filepath:
                logger.info(f"Request {request_id}: Using local file '{job.filename}'")
                
                # Validate local file exists
                if not os.path.exists(job.local_filepath):
                    error_msg = f"Local file not found: {job.filename}"
                    logger.error(f"Request {request_id}: {error_msg}")
                    raise FileNotFoundError(error_msg)
                
                file_path = job.local_filepath
            
            # Parse metadata if available
            metadata_dict = {}
            if job.request_metadata:
                try:
                    metadata_dict = json.loads(job.request_metadata)
                except json.JSONDecodeError:
                    logger.warning(f"Request {request_id}: Failed to parse metadata, using empty dict")
                    metadata_dict = {}
            
            # Call external split service
            logger.info(f"Request {request_id}: Calling external split service at {settings.SPLIT_SERVICE_URL}")
            logger.info(f"Request {request_id}: Split mode={'enabled' if job.split else 'disabled'}")
            start_time = datetime.utcnow()
            
            async with httpx.AsyncClient(timeout=settings.SPLIT_SERVICE_TIMEOUT) as client:
                # Prepare multipart form data
                with open(file_path, 'rb') as f:
                    files = {
                        'file': (filename, f, 'application/octet-stream')
                    }
                    
                    data = {
                        'split': 'true' if job.split else 'false',
                        'metadata': json.dumps(metadata_dict),
                        'request_id': request_id,
                        'channel_id': settings.DEFAULT_CHANNEL_ID
                    }
                    
                    # Make API call
                    response = await client.post(
                        settings.SPLIT_SERVICE_URL,
                        files=files,
                        data=data
                    )
            
            end_time = datetime.utcnow()
            processing_time = (end_time - start_time).total_seconds()
            
            # Clean up temporary file if it was created
            if temp_file_path and os.path.exists(temp_file_path):
                try:
                    os.remove(temp_file_path)
                    logger.info(f"Request {request_id}: Cleaned up temporary file")
                    temp_file_path = None  # Mark as cleaned
                except Exception as e:
                    logger.warning(f"Request {request_id}: Failed to remove temp file: {str(e)}")
            
            # Check response status
            if response.status_code != 200:
                error_msg = f"Split service returned status {response.status_code}: {response.text}"
                logger.error(f"Request {request_id}: {error_msg}")
                raise Exception(error_msg)
            
            # Parse response
            try:
                api_response = response.json()
            except json.JSONDecodeError as e:
                error_msg = f"Failed to parse JSON response from split service: {str(e)}"
                logger.error(f"Request {request_id}: {error_msg}")
                raise Exception(error_msg)
            
            # Check if API returned an error
            if api_response.get('error'):
                error_msg = api_response.get('error')
                logger.error(f"Request {request_id}: Split service returned error: {error_msg}")
                raise Exception(f"Split service error: {error_msg}")
            
            # Log success
            logger.info(
                f"Request {request_id}: Successfully processed by split service. "
                f"Processing time: {api_response.get('processing_time', processing_time)}s"
            )
            
            # Build results object
            result_data: Dict[str, Any] = {
                "request_id": request_id,
                "filename": api_response.get('filename', filename),
                "split": bool(job.split),
                "processing_time_seconds": api_response.get('processing_time', processing_time),
                "processed_at": end_time.isoformat(),
                "status": "success",
                "source": "s3" if job.s3_path else "local",
                "results": api_response.get('results', []),
                "token_usage": api_response.get('token_usage'),
                "channel_id": api_response.get('channel_id'),
                "metadata": metadata_dict,
            }
            
            # Log result summary
            if api_response.get('results'):
                logger.info(f"Request {request_id}: Identified {len(api_response['results'])} document(s)")
            
            # Convert result to JSON string
            results_json = json.dumps(result_data, indent=2)
            
            # Update job status to "Finished Processing"
            updated_job = await update_job_status(
                db=db,
                request_id=request_id,
                status="Finished Processing",
                results=results_json
            )
            
            if updated_job:
                await db.commit()
                logger.info(f"Request {request_id}: Processing completed successfully")
            else:
                logger.error(f"Request {request_id}: Failed to update job status")
                await db.rollback()
                
        except Exception as e:
            logger.error(f"Request {request_id}: Processing failed - {str(e)}")
            
            # Clean up temporary file on error
            if temp_file_path and os.path.exists(temp_file_path):
                try:
                    os.remove(temp_file_path)
                    logger.info(f"Request {request_id}: Cleaned up temporary file after error")
                except Exception as cleanup_error:
                    logger.warning(f"Request {request_id}: Failed to remove temp file on error: {str(cleanup_error)}")
            
            # Update status to "Failed" on exception
            try:
                error_result = {
                    "request_id": request_id,
                    "status": "failed",
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "failed_at": datetime.utcnow().isoformat(),
                }
                
                await update_job_status(
                    db=db,
                    request_id=request_id,
                    status="Failed",
                    results=json.dumps(error_result)
                )
                await db.commit()
                logger.info(f"Request {request_id}: Job marked as Failed")
            except Exception as update_error:
                logger.error(f"Request {request_id}: Failed to update error status - {str(update_error)}")
                await db.rollback()


async def restart_processing_jobs() -> None:
    """
    Restart all jobs that were in "Processing" status.
    
    This function is called on application startup to handle jobs
    that were interrupted due to server restart or crash.
    """
    logger.info("Checking for jobs to restart...")
    
    async with AsyncSessionLocal() as db:
        try:
            from app.crud import get_jobs_by_status
            
            # Get all jobs with "Processing" status
            processing_jobs = await get_jobs_by_status(db, "Processing")
            
            if not processing_jobs:
                logger.info("No jobs to restart")
                return
            
            logger.info(f"Found {len(processing_jobs)} jobs to restart")
            
            # Restart each job using asyncio.create_task
            for job in processing_jobs:
                logger.info(f"Restarting job for request_id: {job.request_id}")
                asyncio.create_task(process_file(job.request_id))
            
            logger.info(f"Successfully restarted {len(processing_jobs)} jobs")
            
        except Exception as e:
            logger.error(f"Error restarting processing jobs: {str(e)}")
