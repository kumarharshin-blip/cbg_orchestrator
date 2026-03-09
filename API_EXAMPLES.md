# API Examples

## Quick Reference

### 1. Upload File with Metadata

```bash
curl -X POST "http://localhost:8000/identification" \
  -F "file=@sample.pdf" \
  -F "request_id=req123" \
  -F "split=true" \
  -F 'metadata={"key": "value", "environment": "production"}'
```

### 2. Process File from S3

```bash
curl -X POST "http://localhost:8000/identification" \
  -F "request_id=req456" \
  -F "split=false" \
  -F "s3_path=https://s3.ap-south-1.amazonaws.com/bucket_name/req456/raw/document.pdf" \
  -F 'metadata={"source": "s3", "bucket": "bucket_name"}'
```

### 3. Check Status

```bash
curl -X GET "http://localhost:8000/status/req123"
```

### 4. Health Check

```bash
curl -X GET "http://localhost:8000/health"
```

## Response Examples

### Upload Response (202 Accepted)

```json
{
  "request_id": "req123",
  "status": "Processing",
  "message": "Request received and processing started"
}
```

### Status Response (Processing)

```json
{
  "request_id": "req123",
  "status": "Processing",
  "results": null
}
```

### Status Response (Completed)

```json
{
  "request_id": "req123",
  "status": "Finished Processing",
  "results": "{\"request_id\": \"req123\", \"filename\": \"sample.pdf\", \"split\": true, ...}"
}
```

### Status Response (Failed)

```json
{
  "request_id": "req123",
  "status": "Failed",
  "results": "{\"request_id\": \"req123\", \"error\": \"...\", \"error_type\": \"ValueError\", ...}"
}
```

## Error Responses

### 400 Bad Request - Missing Both File and S3 Path

```json
{
  "detail": "Either 'file' or 's3_path' must be provided"
}
```

### 400 Bad Request - Both File and S3 Path Provided

```json
{
  "detail": "Cannot provide both 'file' and 's3_path'. Choose one."
}
```

### 404 Not Found - Invalid Request ID

```json
{
  "detail": "Job not found for request_id: invalid_id"
}
```
