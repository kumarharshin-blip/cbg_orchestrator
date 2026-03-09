from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime
from typing import Optional

from app.models import JobStatus


class FileJobCreate(BaseModel):
    """Schema for creating a new file job."""
    s3_path: str = Field(..., description="Full S3 URL to the file")
    req_id: str = Field(..., description="Unique request identifier (extracted from s3_path)")
    split: bool = Field(default=False, description="Whether to split the file")
    request_metadata: Optional[str] = Field(None, description="JSON string with metadata")
    status: JobStatus = Field(default=JobStatus.Processing, description="Job status")


class FileJobResponse(BaseModel):
    """Schema for file job response."""
    id: int
    s3_path: str
    req_id: str
    split: bool
    request_metadata: Optional[str]
    status: JobStatus
    results: Optional[str] = None
    webhook_result: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class FileJobStatusResponse(BaseModel):
    """Schema for status endpoint response."""
    req_id: str
    status: JobStatus
    results: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class FileUploadResponse(BaseModel):
    """Schema for identification endpoint response."""
    req_id: str
    status: JobStatus
    message: str = "Request received and processing started"
