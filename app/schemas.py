from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime
from typing import Optional


class FileJobCreate(BaseModel):
    """Schema for creating a new file job."""
    filename: Optional[str] = Field(None, description="Original filename (nullable for S3)")
    local_filepath: Optional[str] = Field(None, description="Local path where file is stored")
    s3_path: Optional[str] = Field(None, description="S3 path to the file")
    request_id: str = Field(..., description="Unique request identifier")
    split: bool = Field(default=False, description="Whether to split the file")
    request_metadata: Optional[str] = Field(None, description="JSON string with metadata")
    status: str = Field(default="Processing", description="Job status")


class FileJobResponse(BaseModel):
    """Schema for file job response."""
    id: int
    filename: Optional[str]
    local_filepath: Optional[str]
    s3_path: Optional[str]
    request_id: str
    split: bool
    request_metadata: Optional[str]
    status: str
    results: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


class FileJobStatusResponse(BaseModel):
    """Schema for status endpoint response."""
    request_id: str
    status: str
    results: Optional[str] = None
    
    model_config = ConfigDict(from_attributes=True)


class FileUploadResponse(BaseModel):
    """Schema for file upload response."""
    request_id: str
    status: str
    message: str = "File uploaded and processing started"
