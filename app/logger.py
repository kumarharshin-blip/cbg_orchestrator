"""
Application Logger - Comprehensive logging for orchestration service

This module provides detailed, structured logging for the signature-card orchestration service.
Logs are written immediately to a continuous log file in a hybrid format that combines
human-readable prefixes with structured JSON for optimal parsing and readability.

Format: TIMESTAMP | SERVICE_NAME | LEVEL | {json_payload}

Each log entry includes request context (req_id, case_id, client_id, HTTP details) making it
easy to filter and correlate logs across multiple requests.

Configuration:
    ENABLE_FILE_LOGGING: Set to "false" to disable file logging.
                         Defaults to "true" in development, "false" in production.
    LOG_LEVEL: Controls which levels are console-logged.
               "standard"   -> INFO + ERROR  (default)
               "diagnostic" -> INFO + WARNING + ERROR
               "verbose"    -> DEBUG + INFO + WARNING + ERROR
    SERVICE_LOG_DIR: Directory for log files (default: "<project_root>/logs")
    SERVICE_LOG_FILE: Log file name (default: "audit_logs.log")
    SERVICE_NAME: Service identifier (default: "vx-service")
    ENVIRONMENT: Environment name (default: "development")
    SERVICE_VERSION: Service version (default: "1.0.0")

Usage:
    from logger import customLogger, LogContext
    
    LogContext.initialize(request)
    LogContext.update_context_ids(req_id="abc123", case_id="case123")
    customLogger.logInfo("request_processed", "Request successfully processed")
    # Logs are written in real-time to <project_root>/logs/audit_logs.log
"""

import logging
import json
import os
import uuid
import time
import base64
from datetime import datetime, timezone, timedelta
from contextvars import ContextVar
from pathlib import Path

from fastapi.encoders import jsonable_encoder
# ==========================================================
# Timezone Configuration
# ==========================================================
IST = timezone(timedelta(hours=5, minutes=30))

# ==========================================================
# Configuration (Environment-Based)
# ==========================================================
SERVICE_NAME = os.getenv("SERVICE_NAME", "vx-service")
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
SERVICE_VERSION = os.getenv("SERVICE_VERSION", "1.0.0")

# Calculate project root (2 levels up from orchestration/signature-card/)
PROJECT_ROOT = Path(__file__).parent.parent.parent
LOG_DIR = os.getenv("SERVICE_LOG_DIR", str(PROJECT_ROOT / "logs"))
LOG_FILE_NAME = os.getenv("SERVICE_LOG_FILE", "audit_logs.log")

# File logging: enabled by default in development, disabled in production
_default_file_logging = "false" if ENVIRONMENT in ("production", "prod") else "true"
ENABLE_FILE_LOGGING = os.getenv("ENABLE_FILE_LOGGING", _default_file_logging).lower() == "true"

# Log level filtering
# "standard" -> INFO + ERROR | "diagnostic" -> INFO + WARNING + ERROR | "verbose" -> all four
LOG_LEVEL_CONFIG = os.getenv("LOG_LEVEL", "standard").lower()

_ALLOWED_LEVELS_MAP = {
    "standard":   {logging.INFO, logging.ERROR},
    "diagnostic": {logging.INFO, logging.WARNING, logging.ERROR},
    "verbose":    {logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR},
}
ALLOWED_LOG_LEVELS: set = _ALLOWED_LEVELS_MAP.get(LOG_LEVEL_CONFIG, _ALLOWED_LEVELS_MAP["standard"])
# ==========================================================
# Log Level Filter
# ==========================================================
class LevelFilter(logging.Filter):
    """Only allow log records whose level is in the configured allowed set."""
    def __init__(self, allowed_levels: set):
        super().__init__()
        self.allowed_levels = allowed_levels

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno in self.allowed_levels


# ==========================================================
# Request Scoped Context (Async Safe)
# ==========================================================
req_id_ctx = ContextVar("req_id", default=None)
case_id_ctx = ContextVar("case_id", default=None)
client_id_ctx = ContextVar("client_id", default=None)
http_method_ctx = ContextVar("http_method", default=None)
http_path_ctx = ContextVar("http_path", default=None)
http_client_ip_ctx = ContextVar("http_client_ip", default=None)
http_user_agent_ctx = ContextVar("http_user_agent", default=None)
http_status_code_ctx = ContextVar("http_status_code", default=None)
request_start_time_ctx = ContextVar("request_start_time", default=None)




class LogContext:
    @staticmethod
    def initialize(request):
        """
        Called at the beginning of every request.
        Sets HTTP request context data.
        """
        http_method_ctx.set(request.method)
        http_path_ctx.set(request.url.path)
        http_client_ip_ctx.set(request.client.host if request.client else None)
        http_user_agent_ctx.set(request.headers.get("user-agent"))
        request_start_time_ctx.set(time.time())
    
    @staticmethod
    def update_context_ids(req_id=None, case_id=None, client_id=None):
        """
        Updates req_id, case_id, and client_id.
        Can be called separately after initialization.
        """
        if req_id is not None:
            req_id_ctx.set(req_id)
        if case_id is not None:
            case_id_ctx.set(case_id)
        if client_id is not None:
            client_id_ctx.set(client_id)
    
    @staticmethod
    def set_status_code(status_code):
        http_status_code_ctx.set(status_code)

    @staticmethod
    def clear():
        req_id_ctx.set(None)
        case_id_ctx.set(None)
        client_id_ctx.set(None)
        http_method_ctx.set(None)
        http_path_ctx.set(None)
        http_client_ip_ctx.set(None)
        http_user_agent_ctx.set(None)
        http_status_code_ctx.set(None)
        request_start_time_ctx.set(None)

    @staticmethod
    def get():
        duration = None
        start_time = request_start_time_ctx.get()
        if start_time:
            duration = int((time.time() - start_time) * 1000)  # ms

        return {
            "req": {
                "id": req_id_ctx.get(),
            },
            "case": {
                "id": case_id_ctx.get(),
            },
            "client": {
                "id": client_id_ctx.get(),
                "ip": http_client_ip_ctx.get(),
            },
            "http": {
                "request": {
                    "method": http_method_ctx.get(),
                },
                "response": {
                    "status_code": http_status_code_ctx.get(),
                },
            },
            "url": {
                "path": http_path_ctx.get(),
            },
            "user_agent": {
                "original": http_user_agent_ctx.get(),
            },
            "event": {
                "duration_ms": duration,
            },
        }
    

# ==========================================================
# JSON Formatter
# ==========================================================
class JsonFormatter(logging.Formatter):
    def _default(self, obj):
        if isinstance(obj, (bytes, bytearray, memoryview)):
            return {
                "_type": "bytes",
                "_encoding": "base64",
                "_value": base64.b64encode(bytes(obj)).decode("ascii"),
            }
        try:
            return jsonable_encoder(obj)
        except Exception:
            return str(obj)

    def format(self, record):
        # Get request context
        ctx = LogContext.get()
        
        # Build flattened log structure
        log_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "log_level": record.levelname,
            "service_name": SERVICE_NAME,
            "service_env": ENVIRONMENT,
            "service_version": SERVICE_VERSION,
        }
        
        # Add request context fields (flattened)
        if ctx.get("req", {}).get("id"):
            log_data["req_id"] = ctx["req"]["id"]
        if ctx.get("case", {}).get("id"):
            log_data["case_id"] = ctx["case"]["id"]
        if ctx.get("client", {}).get("id"):
            log_data["client_id"] = ctx["client"]["id"]
        if ctx.get("http", {}).get("request", {}).get("method"):
            log_data["request_method"] = ctx["http"]["request"]["method"]
        if ctx.get("url", {}).get("path"):
            log_data["url_path"] = ctx["url"]["path"]
        if ctx.get("client", {}).get("ip"):
            log_data["client_ip"] = ctx["client"]["ip"]
        if ctx.get("user_agent", {}).get("original"):
            log_data["user_agent"] = ctx["user_agent"]["original"]
        if ctx.get("http", {}).get("response", {}).get("status_code"):
            log_data["status_code"] = ctx["http"]["response"]["status_code"]
        if ctx.get("event", {}).get("duration_ms"):
            log_data["duration_ms"] = ctx["event"]["duration_ms"]
        
        # Event message (combine event name and description)
        event_message = []
        if hasattr(record, "event"):
            event_message.append(record.event)
        if hasattr(record, "description"):
            event_message.append(record.description)
        if event_message:
            log_data["event_message"] = ": ".join(event_message) if len(event_message) > 1 else event_message[0]
        else:
            # Fallback for standard logger.info("msg") calls that don't set extras
            msg = record.getMessage()
            if msg:
                log_data["event_message"] = msg

        # Event details (context object)
        if hasattr(record, "context_object") and record.context_object:
            log_data["event_details"] = record.context_object
        
        # Error details
        if record.exc_info:
            log_data["error"] = {
                "type": record.exc_info[0].__name__,
                "message": str(record.exc_info[1]),
                "stack_trace": self.formatException(record.exc_info),
            }

        return json.dumps(log_data, default=self._default)
    


# ==========================================================
# Custom Logger
# ==========================================================
class CustomLogger:
    def __init__(self):
        self.logger = logging.getLogger("app_logger")
        self.logger.setLevel(logging.DEBUG)  # let the filter decide, not the level
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        handler.addFilter(LevelFilter(ALLOWED_LOG_LEVELS))
        if not self.logger.handlers:
            self.logger.addHandler(handler)
        
        # Setup log file path
        self.enable_file_logging = ENABLE_FILE_LOGGING
        if self.enable_file_logging:
            self.log_file_path = Path(LOG_DIR) / LOG_FILE_NAME
            self.log_file_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            self.log_file_path = None


    def _log(self, level, event, description, context=None, exc=None):
        extra_fields = {
            "event": event,
            "description": description,
            "context_object": context or {}
        }
        
        # Log to console via standard logging
        self.logger.log(
            level,
            description,
            exc_info=exc,
            extra=extra_fields
        )
        
        # Write immediately to log file (if enabled and level is allowed)
        if self.enable_file_logging and level in ALLOWED_LOG_LEVELS:
            log_entry = self._build_log_entry(level, event, description, context, exc)
            self._write_log_immediately(log_entry, level)
    
    def _build_log_entry(self, level, event, description, context, exc):
        """Build a complete log entry for file storage."""
        ctx = LogContext.get()
        
        # Build log with identifiers first
        log_data = {}
        
        # 1. Add identifiers first
        if ctx.get("req", {}).get("id"):
            log_data["req_id"] = ctx["req"]["id"]
        if ctx.get("case", {}).get("id"):
            log_data["case_id"] = ctx["case"]["id"]
        if ctx.get("client", {}).get("id"):
            log_data["client_id"] = ctx["client"]["id"]
        
        # 2. Add timestamp (IST) and service info
        log_data["timestamp"] = datetime.now(IST).isoformat()
        log_data["log_level"] = logging.getLevelName(level)
        log_data["service_name"] = SERVICE_NAME
        log_data["service_env"] = ENVIRONMENT
        log_data["service_version"] = SERVICE_VERSION
        
        # 3. Add remaining request context fields
        if ctx.get("http", {}).get("request", {}).get("method"):
            log_data["request_method"] = ctx["http"]["request"]["method"]
        if ctx.get("url", {}).get("path"):
            log_data["url_path"] = ctx["url"]["path"]
        if ctx.get("client", {}).get("ip"):
            log_data["client_ip"] = ctx["client"]["ip"]
        if ctx.get("user_agent", {}).get("original"):
            log_data["user_agent"] = ctx["user_agent"]["original"]
        if ctx.get("http", {}).get("response", {}).get("status_code"):
            log_data["status_code"] = ctx["http"]["response"]["status_code"]
        if ctx.get("event", {}).get("duration_ms"):
            log_data["duration_ms"] = ctx["event"]["duration_ms"]
        
        # Event message
        event_message = []
        if event:
            event_message.append(event)
        if description:
            event_message.append(description)
        if event_message:
            log_data["event_message"] = ": ".join(event_message) if len(event_message) > 1 else event_message[0]
        
        # Event details
        if context:
            log_data["event_details"] = context
        
        # Error details
        if exc:
            log_data["error"] = {
                "type": type(exc).__name__,
                "message": str(exc),
                "stack_trace": str(exc)
            }
        
        return log_data
    
    def _write_log_immediately(self, log_entry, level):
        """Write log entry immediately to file with human-readable prefix."""
        try:
            # Extract timestamp in readable format (YYYY-MM-DD HH:MM:SS)
            timestamp_str = log_entry.get("timestamp", "")
            if timestamp_str:
                # Convert ISO format to readable format
                dt = datetime.fromisoformat(timestamp_str)
                readable_timestamp = dt.strftime("%Y-%m-%d %H:%M:%S")
            else:
                readable_timestamp = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
            
            # Determine log level
            level_name = logging.getLevelName(level)
            
            # Build human-readable prefix
            prefix = f"{readable_timestamp} | {SERVICE_NAME} | {level_name} | "
            
            # Write prefix + JSON to file
            with open(self.log_file_path, 'a', encoding='utf-8') as f:
                f.write(prefix)
                json.dump(log_entry, f, ensure_ascii=False)
                f.write('\n')
        except Exception as e:
            # Log error but don't fail the application
            print(f"Error writing log entry to file: {e}")

    def logInfo(self, event, description, context=None):
        self._log(logging.INFO, event, description, context)

    def logWarning(self, event, description, context=None):
        self._log(logging.WARNING, event, description, context)

    def logDebug(self, event, description, context=None):
        self._log(logging.DEBUG, event, description, context)

    def logError(self, event, description, context=None, exc=None):
        self._log(logging.ERROR, event, description, context, exc)
    
    def dump_logs(self, filepath=None):
        """
        Returns the path to the continuous log file.
        Note: Logs are now written immediately on each log entry, not accumulated.
        
        Args:
            filepath: Not used anymore, kept for backward compatibility
        
        Returns:
            str: Path to the continuous log file, or None if file logging is disabled
        """
        # Return the log file path if file logging is enabled
        if not self.enable_file_logging:
            return None
        
        return str(self.log_file_path)
    
    def clear_logs(self):
        """Clear accumulated logs (no-op since logs are streamed to file)."""
        # Kept for backward compatibility but does nothing since logs
        # are written immediately to file and not accumulated in memory
        pass


# Singleton
customLogger = CustomLogger()