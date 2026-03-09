import enum

from sqlalchemy import Column, Integer, String, Text, DateTime, Index, Enum as SQLAlchemyEnum
from sqlalchemy.sql import func
from app.database import Base


class JobStatus(str, enum.Enum):
    """Enum representing the lifecycle states of a FileJob."""
    Processing = "Processing"
    FinishedProcessing = "Finished Processing"
    Failed = "Failed"


class FileJob(Base):
    """
    FileJob model for tracking file processing jobs.

    Attributes:
        id: Primary key
        s3_path: Full S3 URL to the file (required)
        req_id: Unique identifier extracted from the s3_path URL
        split: Boolean flag indicating if file should be split
        request_metadata: JSON string containing additional metadata
        status: Current status of the job (JobStatus enum)
        results: JSON string containing processing results
        webhook_result: JSON string with the response received from the result webhook
        created_at: Timestamp when job was created
        updated_at: Timestamp when job was last updated
    """
    __tablename__ = "file_jobs_v2"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    s3_path = Column(String(1024), nullable=False)
    req_id = Column(String(100), unique=True, nullable=False, index=True)
    split = Column(Integer, nullable=False, default=0)  # SQLite-compatible boolean
    request_metadata = Column(Text, nullable=True)
    status = Column(
        SQLAlchemyEnum(JobStatus),
        nullable=False,
        index=True,
        default=JobStatus.Processing,
    )
    results = Column(Text, nullable=True)
    webhook_result = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Composite index for common queries
    __table_args__ = (
        Index('ix_status_updated', 'status', 'updated_at'),
    )

    def __repr__(self) -> str:
        return f"<FileJob(id={self.id}, req_id={self.req_id}, status={self.status})>"
