from sqlalchemy import Column, Integer, String, Text, DateTime, Index
from sqlalchemy.sql import func
from app.database import Base


class FileJob(Base):
    """
    FileJob model for tracking file processing jobs.
    
    Attributes:
        id: Primary key
        filename: Original filename (nullable for S3-only requests)
        local_filepath: Local path where file is stored (nullable if S3)
        s3_path: S3 path to the file (nullable if local)
        request_id: Unique identifier for the request (provided by client)
        split: Boolean flag indicating if file should be split
        request_metadata: JSON string containing additional metadata
        status: Current status of the job
        results: JSON string containing processing results
        created_at: Timestamp when job was created
        updated_at: Timestamp when job was last updated
    """
    __tablename__ = "file_jobs"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    filename = Column(String(255), nullable=True)
    local_filepath = Column(String(512), nullable=True)
    s3_path = Column(String(1024), nullable=True)
    request_id = Column(String(100), unique=True, nullable=False, index=True)
    split = Column(Integer, nullable=False, default=0)  # SQLite compatible boolean
    request_metadata = Column(Text, nullable=True)
    status = Column(String(50), nullable=False, index=True, default="Processing")
    results = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False
    )
    
    # Create composite indexes for common queries
    __table_args__ = (
        Index('ix_status_updated', 'status', 'updated_at'),
    )
    
    def __repr__(self) -> str:
        return f"<FileJob(id={self.id}, request_id={self.request_id}, status={self.status})>"
