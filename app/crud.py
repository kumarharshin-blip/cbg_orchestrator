from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from typing import Optional, List
from datetime import datetime

from app.models import FileJob
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
            filename=job.filename,
            local_filepath=job.local_filepath,
            s3_path=job.s3_path,
            request_id=job.request_id,
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


async def get_file_job_by_request_id(db: AsyncSession, request_id: str) -> Optional[FileJob]:
    """
    Retrieve a file job by request ID.
    
    Args:
        db: Database session
        request_id: Unique request identifier
        
    Returns:
        FileJob instance or None if not found
    """
    result = await db.execute(
        select(FileJob).where(FileJob.request_id == request_id)
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


async def get_jobs_by_status(db: AsyncSession, status: str) -> List[FileJob]:
    """
    Retrieve all file jobs with a specific status.
    
    Args:
        db: Database session
        status: Job status to filter by
        
    Returns:
        List of FileJob instances
    """
    result = await db.execute(
        select(FileJob).where(FileJob.status == status)
    )
    return list(result.scalars().all())


async def update_job_status(
    db: AsyncSession,
    request_id: str,
    status: str,
    results: Optional[str] = None
) -> Optional[FileJob]:
    """
    Update the status and results of a file job.
    
    Args:
        db: Database session
        request_id: Unique request identifier
        status: New status
        results: Optional results JSON string
        
    Returns:
        Updated FileJob instance or None if not found
    """
    try:
        # First get the job
        job = await get_file_job_by_request_id(db, request_id)
        if not job:
            return None
        
        # Update fields
        job.status = status
        job.updated_at = datetime.utcnow()
        
        if results is not None:
            job.results = results
        
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
