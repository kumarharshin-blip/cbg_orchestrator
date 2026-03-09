import asyncio
import json
import logging
from datetime import datetime
from typing import Dict, Any

import httpx

from app.database import AsyncSessionLocal
from app.crud import get_file_job_by_req_id, update_job_status
from app.models import JobStatus
from app.config import get_settings

# Get logger and settings
logger = logging.getLogger(__name__)
settings = get_settings()


async def call_webhook(req_id: str, result_data: Dict[str, Any]) -> str:
    """
    POST result_data to the configured webhook URL and return the raw response text.

    Args:
        req_id: Job identifier (for logging)
        result_data: Dict to send as JSON body

    Returns:
        Raw response text from the webhook (empty string on failure)
    """
    try:
        async with httpx.AsyncClient(timeout=settings.WEBHOOK_TIMEOUT) as client:
            response = await client.post(
                settings.WEBHOOK_URL,
                json=result_data,
                headers={"Content-Type": "application/json"},
            )
        logger.info(
            f"Request {req_id}: Webhook called — status={response.status_code}"
        )
        return response.text
    except Exception as e:
        logger.warning(f"Request {req_id}: Webhook call failed — {e}")
        return json.dumps({"error": str(e)})


async def process_file(req_id: str) -> None:
    """
    Background task to process a file job.

    Flow:
    1. Fetch the job from the database.
    2. Parse request_metadata JSON.
    3. POST to the external split service with s3_path + other fields as JSON.
    4. On HTTP 200 success:
       a. Build result_data dict.
       b. Call the result webhook; store the response.
       c. update_job_status → JobStatus.FinishedProcessing (with results + webhook_result).
    5. On non-200 or any exception:
       a. update_job_status → JobStatus.Failed (with error details).

    Args:
        req_id: Unique request identifier for the job
    """
    logger.info(f"Starting background processing for req_id: {req_id}")

    async with AsyncSessionLocal() as db:
        try:
            # ── 1. Fetch job ──────────────────────────────────────────────────
            job = await get_file_job_by_req_id(db, req_id)
            if not job:
                logger.error(f"Job not found for req_id: {req_id}")
                return

            if not job.s3_path:
                raise ValueError("s3_path is missing from the job record")

            # ── 2. Parse metadata ─────────────────────────────────────────────
            metadata_dict: Dict[str, Any] = {}
            if job.request_metadata:
                try:
                    metadata_dict = json.loads(job.request_metadata)
                except json.JSONDecodeError:
                    logger.warning(
                        f"Request {req_id}: Failed to parse metadata, using empty dict"
                    )

            # ── 3. Call external split service ────────────────────────────────
            logger.info(
                f"Request {req_id}: Calling split service at {settings.SPLIT_SERVICE_URL}"
            )
            logger.info(
                f"Request {req_id}: Split mode={'enabled' if job.split else 'disabled'}"
            )

            payload = {
                "s3_path": job.s3_path,
                "req_id": req_id,
                "split": "true" if job.split else "false",
                "metadata": metadata_dict,
                "channel_id": settings.DEFAULT_CHANNEL_ID,
            }

            start_time = datetime.utcnow()
            async with httpx.AsyncClient(timeout=settings.SPLIT_SERVICE_TIMEOUT) as client:
                response = await client.post(
                    settings.SPLIT_SERVICE_URL,
                    json=payload,
                )
            end_time = datetime.utcnow()
            processing_time = (end_time - start_time).total_seconds()

            if response.status_code != 200:
                raise Exception(
                    f"Split service returned status {response.status_code}: {response.text}"
                )

            try:
                api_response = response.json()
            except json.JSONDecodeError as e:
                raise Exception(
                    f"Failed to parse JSON response from split service: {e}"
                )

            # Split service returns a JSON array of identified documents directly
            if not isinstance(api_response, list):
                raise Exception(
                    f"Unexpected split service response type: expected list, got {type(api_response).__name__}"
                )

            logger.info(
                f"Request {req_id}: Split service responded successfully. "
                f"Processing time: {processing_time:.2f}s — "
                f"{len(api_response)} document(s) identified"
            )

            # ── 4a. Build result_data ─────────────────────────────────────────
            result_data: Dict[str, Any] = {
                "req_id": req_id,
                "s3_path": job.s3_path,
                "split": bool(job.split),
                "processing_time_seconds": processing_time,
                "processed_at": end_time.isoformat(),
                "status": "success",
                "results": api_response,  # raw list of document objects
                "metadata": metadata_dict,
            }

            results_json = json.dumps(result_data, indent=2)

            # ── 4b. Call result webhook ───────────────────────────────────────
            logger.info(f"Request {req_id}: Sending result to webhook {settings.WEBHOOK_URL}")
            webhook_response_text = await call_webhook(req_id, result_data)

            # ── 4c. Persist final status ──────────────────────────────────────
            updated_job = await update_job_status(
                db=db,
                req_id=req_id,
                status=JobStatus.FinishedProcessing,
                results=results_json,
                webhook_result=webhook_response_text,
            )

            if updated_job:
                await db.commit()
                logger.info(f"Request {req_id}: Processing completed successfully")
            else:
                logger.error(f"Request {req_id}: Failed to update job status")
                await db.rollback()

        except Exception as e:
            logger.error(f"Request {req_id}: Processing failed — {e}")

            try:
                error_result = {
                    "req_id": req_id,
                    "status": "failed",
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "failed_at": datetime.utcnow().isoformat(),
                }
                await update_job_status(
                    db=db,
                    req_id=req_id,
                    status=JobStatus.Failed,
                    results=json.dumps(error_result),
                )
                await db.commit()
                logger.info(f"Request {req_id}: Job marked as Failed")
            except Exception as update_error:
                logger.error(
                    f"Request {req_id}: Failed to update error status — {update_error}"
                )
                await db.rollback()


async def restart_processing_jobs() -> None:
    """
    Restart all jobs that were in Processing status.

    Called at application startup to resume jobs that were interrupted by a
    server crash or restart.
    """
    logger.info("Checking for jobs to restart...")

    async with AsyncSessionLocal() as db:
        try:
            from app.crud import get_jobs_by_status

            processing_jobs = await get_jobs_by_status(db, JobStatus.Processing)

            if not processing_jobs:
                logger.info("No jobs to restart")
                return

            logger.info(f"Found {len(processing_jobs)} job(s) to restart")

            for job in processing_jobs:
                logger.info(f"Restarting job for req_id: {job.req_id}")
                asyncio.create_task(process_file(job.req_id))

            logger.info(f"Successfully queued {len(processing_jobs)} job(s) for restart")

        except Exception as e:
            logger.error(f"Error restarting processing jobs: {e}")

