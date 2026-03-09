from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from typing import Optional, List
from datetime import datetime

from app.models import FileJob, JobStatus
from app.schemas import FileJobCreate


async def create_file_job(db: AsyncSession, job: FileJobCreate) -> FileJob:
    """
    Create a new file job in the database.
    
    Args:
        db: Database session
        job: File job creation schema
        
    Returns:
        Created FileJob instance
        
    Raises:
        SQLAlchemyError: If database operation fails
    """
    try:
        db_job = FileJob(
            s3_path=job.s3_path,
            req_id=job.req_id,
            split=1 if job.split else 0,  # Convert bool to int for SQLite
            request_metadata=job.request_metadata,
            status=job.status,
        )
        db.add(db_job)
        await db.flush()
        await db.refresh(db_job)
        return db_job
    except SQLAlchemyError as e:
        await db.rollback()
        raise e


async def get_file_job_by_req_id(db: AsyncSession, req_id: str) -> Optional[FileJob]:
    """
    Retrieve a file job by req_id.
    
    Args:
        db: Database session
        req_id: Unique request identifier
        
    Returns:
        FileJob instance or None if not found
    """
    result = await db.execute(
        select(FileJob).where(FileJob.req_id == req_id)
    )
    return result.scalar_one_or_none()


async def get_file_job_by_id(db: AsyncSession, job_id: int) -> Optional[FileJob]:
    """
    Retrieve a file job by ID.
    
    Args:
        db: Database session
        job_id: Job ID
        
    Returns:
        FileJob instance or None if not found
    """
    result = await db.execute(
        select(FileJob).where(FileJob.id == job_id)
    )
    return result.scalar_one_or_none()


async def get_jobs_by_status(db: AsyncSession, status: JobStatus) -> List[FileJob]:
    """
    Retrieve all file jobs with a specific status.
    
    Args:
        db: Database session
        status: JobStatus enum value to filter by
        
    Returns:
        List of FileJob instances
    """
    result = await db.execute(
        select(FileJob).where(FileJob.status == status)
    )
    return list(result.scalars().all())


async def update_job_status(
    db: AsyncSession,
    req_id: str,
    status: JobStatus,
    results: Optional[str] = None,
    webhook_result: Optional[str] = None,
) -> Optional[FileJob]:
    """
    Update the status, results, and webhook_result of a file job.
    
    Args:
        db: Database session
        req_id: Unique request identifier
        status: New JobStatus
        results: Optional results JSON string
        webhook_result: Optional JSON string of the webhook response
        
    Returns:
        Updated FileJob instance or None if not found
    """
    try:
        job = await get_file_job_by_req_id(db, req_id)
        if not job:
            return None
        
        job.status = status
        job.updated_at = datetime.utcnow()
        
        if results is not None:
            job.results = results

        if webhook_result is not None:
            job.webhook_result = webhook_result
        
        await db.flush()
        await db.refresh(job)
        return job
    except SQLAlchemyError as e:
        await db.rollback()
        raise e


async def get_all_jobs(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 100
) -> List[FileJob]:
    """
    Retrieve all file jobs with pagination.
    
    Args:
        db: Database session
        skip: Number of records to skip
        limit: Maximum number of records to return
        
    Returns:
        List of FileJob instances
    """
    result = await db.execute(
        select(FileJob).offset(skip).limit(limit)
    )
    return list(result.scalars().all())

