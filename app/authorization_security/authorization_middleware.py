from fastapi import Request
from starlette.types import ASGIApp, Receive, Scope, Send
from typing import Optional
import logging
import time
import hashlib
import json
import os
from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings

logger = logging.getLogger(__name__)


class AuthorizationMiddleware:
    """
    Pure-ASGI middleware for SHA256-based authorization.

    Using pure ASGI (not BaseHTTPMiddleware) avoids the known Starlette bug
    where BaseHTTPMiddleware's streaming wrapper can drop response body bytes.

    Flow:
    1. Validate timestamp (not older than configured tolerance).
    2. Load channel config from Settings (CLIENT_CONFIGS_JSON from .env).
    3. Look up channel by client_id; check billing_enabled.
    4. Verify SHA256(api_key|client_id|timestamp|encrypted_payload) matches request hash.
    5. Decrypt payload with Fernet using encryption_key; pass decrypted body downstream.
    """

    ENV_CLIENT_CONFIGS_JSON = "CLIENT_CONFIGS_JSON"
    BYPASS_PATHS = {"/health", "/docs", "/redoc", "/openapi.json", "/"}

    def __init__(self, app: ASGIApp, timestamp_tolerance_seconds: int = 300):
        self.app = app
        self.timestamp_tolerance = timestamp_tolerance_seconds
        self._channel_config_dict = None

        config_json = (get_settings().CLIENT_CONFIGS_JSON or os.getenv(self.ENV_CLIENT_CONFIGS_JSON) or "").strip()
        if config_json:
            try:
                self._channel_config_dict = json.loads(config_json)
                logger.info(
                    f"Authorization middleware: loaded {len(self._channel_config_dict)} channel(s), "
                    f"timestamp tolerance: {timestamp_tolerance_seconds}s"
                )
            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON in {self.ENV_CLIENT_CONFIGS_JSON}: {e}")
                self._channel_config_dict = None
        if self._channel_config_dict is None:
            logger.error("Authorization middleware: CLIENT_CONFIGS_JSON missing or invalid; auth will return 503")

    def _get_header(self, headers: dict, *names: str) -> Optional[str]:
        for name in names:
            value = headers.get(name.lower())
            if value:
                return value
        return None

    def _json_response(self, status_code: int, content: dict):
        body = json.dumps(content).encode("utf-8")
        return [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ], body, status_code

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path in self.BYPASS_PATHS:
            await self.app(scope, receive, send)
            return

        # ── Build a fake Request just for header/body access ─────────────────
        request = Request(scope, receive)

        async def _send_error(status_code: int, content: dict):
            resp_headers, body, code = self._json_response(status_code, content)
            await send({"type": "http.response.start", "status": code, "headers": resp_headers})
            await send({"type": "http.response.body", "body": body, "more_body": False})

        logger.info(f"Authorizing request to {path}")

        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}

        client_id      = self._get_header(headers, "client_id", "client-id", "clientid")
        timestamp_str  = (self._get_header(headers, "timestamp") or "").strip()
        received_hash  = (self._get_header(headers, "hash") or "").strip()

        if not client_id:
            await _send_error(400, {"error": "Bad Request", "message": "Missing required header: client_id"}); return
        if not timestamp_str:
            await _send_error(400, {"error": "Bad Request", "message": "Missing required header: timestamp"}); return
        if not received_hash:
            await _send_error(400, {"error": "Bad Request", "message": "Missing required header: hash"}); return

        try:
            request_timestamp = int(timestamp_str)
        except ValueError:
            await _send_error(400, {"error": "Bad Request", "message": "Invalid timestamp format. Expected integer in milliseconds"}); return

        current_timestamp = int(time.time() * 1000)
        if abs(current_timestamp - request_timestamp) / 1000 > self.timestamp_tolerance:
            await _send_error(401, {"error": "Unauthorized", "message": "Request expired"}); return

        try:
            body_bytes = await request.body()
            if not body_bytes:
                await _send_error(400, {"error": "Bad Request", "message": "Request body cannot be empty"}); return
            body_json = json.loads(body_bytes.decode("utf-8"))
            encrypted_payload = body_json.get("encrypted_payload")
            if not encrypted_payload:
                await _send_error(400, {"error": "Bad Request", "message": "Missing encrypted_payload in request body"}); return
        except json.JSONDecodeError:
            await _send_error(400, {"error": "Bad Request", "message": "Request body is not valid JSON"}); return
        except Exception as e:
            logger.error(f"Error reading request body: {e}")
            await _send_error(400, {"error": "Bad Request", "message": "Failed to read request body"}); return

        if self._channel_config_dict is None:
            await _send_error(503, {"error": "Service Unavailable", "message": "Channel configuration not available"}); return

        channel_info = self._channel_config_dict.get(str(client_id))
        if not channel_info:
            await _send_error(403, {"error": "Forbidden", "message": "Channel not found"}); return
        if not channel_info.get("billing_enabled", False):
            await _send_error(403, {"error": "Forbidden", "message": "Billing not enabled for this channel"}); return

        api_key = channel_info.get("api_key")
        if not api_key:
            await _send_error(500, {"error": "Internal Server Error", "message": "Channel configuration error"}); return

        message = f"{api_key}|{client_id}|{timestamp_str}|{encrypted_payload}"
        computed_hash = hashlib.sha256(message.encode("utf-8")).hexdigest()
        if computed_hash != received_hash:
            await _send_error(401, {"error": "Unauthorized", "message": "Authentication failed"}); return

        encryption_key = channel_info.get("encryption_key", "")
        if len(encryption_key) != 44:
            await _send_error(500, {"error": "Internal Server Error", "message": "Invalid encryption key configuration"}); return

        try:
            cipher = Fernet(encryption_key.encode("utf-8"))
            decrypted_bytes = cipher.decrypt(encrypted_payload.encode("utf-8"))
            decrypted_payload = json.loads(decrypted_bytes.decode("utf-8"))
        except InvalidToken:
            await _send_error(401, {"error": "Unauthorized", "message": "Failed to decrypt payload - invalid encryption"}); return
        except json.JSONDecodeError:
            await _send_error(400, {"error": "Bad Request", "message": "Decrypted payload is not valid JSON"}); return
        except Exception as e:
            logger.error(f"Error decrypting payload: {e}")
            await _send_error(500, {"error": "Internal Server Error", "message": "Decryption error"}); return

        # ── Inject decrypted body and client_id into scope for downstream ─────
        decrypted_body_bytes = json.dumps(decrypted_payload).encode("utf-8")

        # Store client_id in scope state so request.state.client_id works downstream
        if "state" not in scope:
            scope["state"] = {}
        scope["state"]["client_id"] = client_id

        body_consumed = False

        async def patched_receive():
            nonlocal body_consumed
            if not body_consumed:
                body_consumed = True
                return {"type": "http.request", "body": decrypted_body_bytes, "more_body": False}
            return await receive()

        try:
            await self.app(scope, patched_receive, send)
        except Exception as e:
            logger.error(f"Unhandled exception in downstream handler: {e}")
            await _send_error(500, {"error": "Internal Server Error", "message": "An unexpected error occurred"})

