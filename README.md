# File Processing Service - Production-Ready FastAPI Application

A production-ready FastAPI application for asynchronous file processing with PostgreSQL database and background task management.

## Features

- ✅ **FastAPI** - Modern, fast web framework
- ✅ **Async SQLAlchemy** - Asynchronous database operations with PostgreSQL
- ✅ **Pydantic v2** - Data validation and settings management
- ✅ **Background Processing** - Using `asyncio.create_task` for non-blocking operations
- ✅ **Restart Safety** - Automatically restarts interrupted jobs on application startup
- ✅ **Production-Ready** - Proper error handling, logging, and project structure

## Project Structure

```
identification_orchestrator/
│
├── app/
│   ├── __init__.py         # Application package
│   ├── main.py             # FastAPI app, endpoints, and startup logic
│   ├── database.py         # Async SQLAlchemy engine and session management
│   ├── models.py           # SQLAlchemy models (FileJob)
│   ├── schemas.py          # Pydantic schemas for validation
│   ├── crud.py             # Database operations (CRUD)
│   ├── services.py         # Background processing logic
│   └── config.py           # Application configuration
│
├── uploads/                # Directory for uploaded files
├── requirements.txt        # Python dependencies
├── .env.example           # Example environment variables
└── README.md              # This file
```

## Database Schema

### FileJob Table

| Column      | Type      | Constraints                    | Description                    |
|-------------|-----------|--------------------------------|--------------------------------|
| id          | Integer   | Primary Key, Auto-increment    | Unique job identifier          |
| filename    | String    | Not Null                       | Original filename              |
| file_path   | String    | Not Null                       | Path where file is stored      |
| request_id  | String    | Unique, Indexed, Not Null      | UUID for request tracking      |
| channel_id  | Integer   | Indexed, Not Null              | Channel identifier             |
| status      | String    | Indexed, Not Null              | Job status                     |
| results     | Text      | Nullable                       | JSON string of results         |
| created_at  | DateTime  | Default: now()                 | Job creation timestamp         |
| updated_at  | DateTime  | Default: now(), Auto-update    | Job last update timestamp      |

**Indexes:**
- Primary index on `id`
- Unique index on `request_id`
- Index on `channel_id`
- Index on `status`
- Composite index on `(channel_id, status)`
- Composite index on `(status, updated_at)`

## Prerequisites

- Python 3.10+
- PostgreSQL 13+
- pip or poetry for package management

## Installation

### 1. Clone or Navigate to Project Directory

```bash
cd identification_orchestrator
```

### 2. Create Virtual Environment

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# Linux/Mac
source venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Set Up PostgreSQL Database

Create a PostgreSQL database:

```sql
CREATE DATABASE fileprocessing;
CREATE USER username WITH PASSWORD 'password';
GRANT ALL PRIVILEGES ON DATABASE fileprocessing TO username;
```

### 5. Configure Environment Variables

Copy `.env.example` to `.env` and update with your settings:

```bash
# Windows
copy .env.example .env

# Linux/Mac
cp .env.example .env
```

Edit `.env`:

```env
DATABASE_URL=postgresql+asyncpg://username:password@localhost:5432/fileprocessing
APP_NAME=File Processing Service
APP_VERSION=1.0.0
DEBUG=True
UPLOAD_DIR=uploads
MAX_UPLOAD_SIZE=104857600
```

## Running the Application

### Development Mode

```bash
# Using uvicorn directly
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Or using Python
python -m app.main
```

The application will be available at:
- API: http://localhost:8000
- Interactive API docs (Swagger): http://localhost:8000/docs
- Alternative API docs (ReDoc): http://localhost:8000/redoc

### Production Mode

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

## API Endpoints

### 1. Root Endpoint

**GET /** - Application information

**Response:**
```json
{
  "message": "File Processing Service API",
  "version": "1.0.0",
  "status": "running"
}
```

### 2. Health Check

**GET /health** - Health check endpoint

**Response:**
```json
{
  "status": "healthy"
}
```

### 3. Upload File

**POST /upload** - Upload file and start processing

**Request:**
- Form Data:
  - `file`: File (required)
  - `channel_id`: Integer (required)

**Example using curl:**
```bash
curl -X POST "http://localhost:8000/upload" \
  -F "file=@sample.txt" \
  -F "channel_id=1"
```

**Response (202 Accepted):**
```json
{
  "request_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "Processing",
  "message": "File uploaded and processing started"
}
```

### 4. Get Status

**GET /status/{request_id}** - Get job status and results

**Example:**
```bash
curl -X GET "http://localhost:8000/status/a1b2c3d4-e5f6-7890-abcd-ef1234567890"
```

**Response (Processing):**
```json
{
  "request_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "Processing",
  "results": null
}
```

**Response (Completed):**
```json
{
  "request_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "Finished Processing",
  "results": "{\"request_id\": \"...\", \"filename\": \"sample.txt\", ...}"
}
```

**Response (Failed):**
```json
{
  "request_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "Failed",
  "results": "{\"request_id\": \"...\", \"error\": \"...\", ...}"
}
```

## How It Works

### Upload Flow

1. Client uploads file via `POST /upload` with `channel_id`
2. Server generates unique `request_id` (UUID4)
3. File is saved to `uploads/` directory
4. Database record created with status = "Processing"
5. Background task started using `asyncio.create_task(process_file(request_id))`
6. Server immediately returns 202 response with `request_id`

### Background Processing

1. `process_file()` function runs asynchronously
2. Simulates external API call with `await asyncio.sleep(25)`
3. Generates dummy JSON result
4. Updates database:
   - `status` = "Finished Processing"
   - `results` = JSON string
   - `updated_at` = current timestamp
5. On error: Sets `status` = "Failed" with error details

### Restart Safety

On application startup (`@app.on_event("startup")`):
1. Queries database for jobs with `status = "Processing"`
2. Restarts each job using `asyncio.create_task(process_file(request_id))`
3. Ensures no jobs are lost due to server restart

## Configuration

All configuration is managed in [app/config.py](app/config.py) using Pydantic Settings:

```python
DATABASE_URL: str           # PostgreSQL connection string
APP_NAME: str              # Application name
APP_VERSION: str           # Version
DEBUG: bool                # Debug mode
UPLOAD_DIR: str            # Upload directory path
MAX_UPLOAD_SIZE: int       # Max file size in bytes
```

Override via environment variables or `.env` file.

## Development

### Running Tests

(Tests not included in this basic setup, but you can add pytest)

```bash
pip install pytest pytest-asyncio httpx
pytest tests/
```

### Database Migrations

For production, consider using Alembic for database migrations:

```bash
pip install alembic
alembic init alembic
# Configure alembic.ini and env.py
alembic revision --autogenerate -m "Initial migration"
alembic upgrade head
```

## Production Considerations

1. **Database Connection Pool**: Configured in `database.py` with `pool_size=10` and `max_overflow=20`

2. **Error Handling**: Comprehensive exception handling in all endpoints and background tasks

3. **Logging**: Structured logging throughout the application

4. **Async Operations**: All database operations and file processing are fully asynchronous

5. **Security**: 
   - Add authentication/authorization middleware
   - Enable CORS if needed
   - Validate file types and sizes
   - Sanitize filenames

6. **Monitoring**:
   - Add health check endpoints for database connectivity
   - Implement metrics collection (Prometheus)
   - Set up alerting for failed jobs

7. **Scalability**:
   - Use load balancer for multiple instances
   - Consider Redis for distributed task queue (if needed)
   - Implement rate limiting

## Troubleshooting

### Database Connection Issues

```
sqlalchemy.exc.OperationalError: could not connect to server
```

**Solution**: Check PostgreSQL is running and credentials in `.env` are correct.

### Module Import Errors

```
ModuleNotFoundError: No module named 'app'
```

**Solution**: Ensure you're running from the project root directory.

### File Upload Issues

```
413 Payload Too Large
```

**Solution**: Adjust `MAX_UPLOAD_SIZE` in config or nginx/reverse proxy settings.

## License

This project is provided as-is for educational and production use.

## Support

For issues and questions, please check the documentation or raise an issue.
