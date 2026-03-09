from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from typing import Optional
import logging
import time
import hashlib
import json
import os
from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)


class AuthorizationMiddleware(BaseHTTPMiddleware):
    """
    FastAPI middleware for SHA256-based authorization.

    Flow:
    1. Validate timestamp (not older than configured tolerance, e.g. 5 min from .env).
    2. Load channel config from env CLIENT_CONFIGS_JSON (JSON string, e.g. from K8s secret).
    3. Look up channel by client_id; check billing_enabled.
    4. Verify SHA256(api_key|client_id|timestamp|encrypted_payload) matches request hash.
    5. Decrypt payload with Fernet using encryption_key; pass decrypted body downstream.
    """

    ENV_CLIENT_CONFIGS_JSON = "CLIENT_CONFIGS_JSON"

    def __init__(self, app, timestamp_tolerance_seconds: int = 300):
        super().__init__(app)
        self.timestamp_tolerance = timestamp_tolerance_seconds
        self._channel_config_dict = None

        config_json = (os.getenv(self.ENV_CLIENT_CONFIGS_JSON) or "").strip()
        if config_json:
            try:
                self._channel_config_dict = json.loads(config_json)
                logger.info(
                    f"Authorization middleware: using channel config from env {self.ENV_CLIENT_CONFIGS_JSON} "
                    f"({len(self._channel_config_dict)} channel(s)), timestamp tolerance: {timestamp_tolerance_seconds}s"
                )
            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON in {self.ENV_CLIENT_CONFIGS_JSON}: {e}")
                self._channel_config_dict = None
        if self._channel_config_dict is None:
            logger.error("Authorization middleware: CLIENT_CONFIGS_JSON missing or invalid; auth will return 503")

    def _get_header(self, request: Request, *names: str) -> Optional[str]:
        lower_headers = {k.lower(): v for k, v in request.headers.items()}
        for name in names:
            value = lower_headers.get(name.lower())
            if value:
                return value
        return None

    async def dispatch(self, request: Request, call_next):
        if request.url.path in ["/health", "/docs", "/redoc", "/openapi.json", "/"]:
            return await call_next(request)

        logger.info(f"Authorizing request to {request.url.path}")

        client_id = self._get_header(request, "client_id", "client-id", "clientId")
        timestamp_str_raw = self._get_header(request, "timestamp")
        received_hash_raw = self._get_header(request, "hash")
        timestamp_str = timestamp_str_raw.strip() if timestamp_str_raw else None
        received_hash = received_hash_raw.strip() if received_hash_raw else None

        if not client_id:
            return JSONResponse(status_code=400, content={"error": "Bad Request", "message": "Missing required header: client_id"})
        if not timestamp_str:
            return JSONResponse(status_code=400, content={"error": "Bad Request", "message": "Missing required header: timestamp"})
        if not received_hash:
            return JSONResponse(status_code=400, content={"error": "Bad Request", "message": "Missing required header: hash"})

        try:
            request_timestamp = int(timestamp_str)
        except ValueError:
            return JSONResponse(status_code=400, content={"error": "Bad Request", "message": "Invalid timestamp format. Expected integer in milliseconds"})

        current_timestamp = int(time.time() * 1000)
        timestamp_diff = abs(current_timestamp - request_timestamp) / 1000
        if timestamp_diff > self.timestamp_tolerance:
            return JSONResponse(status_code=401, content={"error": "Unauthorized", "message": "Request expired"})

        try:
            body_bytes = await request.body()
            if not body_bytes:
                return JSONResponse(status_code=400, content={"error": "Bad Request", "message": "Request body cannot be empty"})
            try:
                body_json = json.loads(body_bytes.decode('utf-8'))
                encrypted_payload = body_json.get("encrypted_payload")
                if not encrypted_payload:
                    return JSONResponse(status_code=400, content={"error": "Bad Request", "message": "Missing encrypted_payload in request body"})
            except json.JSONDecodeError:
                encrypted_payload = body_bytes.decode('utf-8')
        except Exception as e:
            logger.error(f"Error reading request body: {e}")
            return JSONResponse(status_code=400, content={"error": "Bad Request", "message": "Failed to read request body"})

        if self._channel_config_dict is None:
            return JSONResponse(status_code=503, content={"error": "Service Unavailable", "message": "Channel configuration not available"})

        channel_info = self._channel_config_dict.get(str(client_id))

        if not channel_info:
            return JSONResponse(status_code=403, content={"error": "Forbidden", "message": "Channel not found"})
        if not channel_info.get("billing_enabled", False):
            return JSONResponse(status_code=403, content={"error": "Forbidden", "message": "Billing not enabled for this channel"})

        api_key = channel_info.get("api_key")
        if not api_key:
            return JSONResponse(status_code=500, content={"error": "Internal Server Error", "message": "Channel configuration error"})

        message = f"{api_key}|{client_id}|{timestamp_str}|{encrypted_payload}"
        try:
            computed_hash = hashlib.sha256(message.encode('utf-8')).hexdigest()
            if computed_hash != received_hash:
                return JSONResponse(status_code=401, content={"error": "Unauthorized", "message": "Authentication failed"})
        except Exception as e:
            logger.error(f"Error computing SHA256: {e}")
            return JSONResponse(status_code=500, content={"error": "Internal Server Error", "message": "Hash validation error"})

        encryption_key = channel_info.get("encryption_key")
        if not encryption_key:
            return JSONResponse(status_code=500, content={"error": "Internal Server Error", "message": "Channel configuration error"})
        if not encryption_key or len(encryption_key) != 44:
            return JSONResponse(status_code=500, content={"error": "Internal Server Error", "message": "Invalid encryption key configuration"})

        try:
            cipher = Fernet(encryption_key.encode('utf-8'))
            decrypted_bytes = cipher.decrypt(encrypted_payload.encode('utf-8'))
            decrypted_payload = json.loads(decrypted_bytes.decode('utf-8'))
        except InvalidToken:
            return JSONResponse(status_code=401, content={"error": "Unauthorized", "message": "Failed to decrypt payload - invalid encryption"})
        except json.JSONDecodeError:
            return JSONResponse(status_code=400, content={"error": "Bad Request", "message": "Decrypted payload is not valid JSON"})
        except Exception as e:
            logger.error(f"Error decrypting payload: {e}")
            return JSONResponse(status_code=500, content={"error": "Internal Server Error", "message": "Decryption error"})

        request.state.client_id = client_id
        decrypted_body_bytes = json.dumps(decrypted_payload).encode('utf-8')

        async def receive():
            return {"type": "http.request", "body": decrypted_body_bytes}

        request._receive = receive
        request._body = decrypted_body_bytes

        try:
            return await call_next(request)
        except Exception as e:
            logger.error(f"Unhandled exception in downstream handler: {e}")
            return JSONResponse(status_code=500, content={"error": "Internal Server Error", "message": "An unexpected error occurred"})
