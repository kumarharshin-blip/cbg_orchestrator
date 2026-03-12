"""
Test helper for the Identification Orchestrator API.

Builds a properly signed + Fernet-encrypted request for POST /identification,
fires it, and saves the curl command plus server response to a local .txt file.

Usage (from project root, venv activated):
    python -m app.authorization_security.create_test_request

Environment overrides:
    IDENTIFICATION_URL   Full endpoint URL              (default: http://localhost:8001/identification)
    CLIENT_ID            Channel identifier             (default: cbg_seg_101)
    API_KEY              Shared API key                 (default: see below)
    ENCRYPTION_KEY       44-char Fernet base64 key      (default: see below)
    CASE_ID              case_id in the payload         (default: test_case_001)
    S3_PATH              s3_path in the payload         (default: s3://fc-dp-athena/test/clubbed_docs.pdf)
    SPLIT                "true" or "false"              (default: false)
"""

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from cryptography.fernet import Fernet


# â”€â”€ Default credentials (override via env vars or edit here) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
DEFAULT_CLIENT_ID     = "infy_5yh64"
DEFAULT_API_KEY       = "c3effcdf-8c0c-47f9-96a8-84de81959c85"
DEFAULT_ENCRYPTION_KEY = "mWtlH2H4q84jPZJe4g17GFkLTNmlj1yWwiOd6jSovXM="

DEFAULT_URL    = "http://localhost:8001/identification"
DEFAULT_CASE_ID = "test_case_003"
DEFAULT_S3_PATH = "s3://fc-dp-athena/test/clubbed_docs.pdf"
DEFAULT_SPLIT   = "false"


def build_request(
    client_id: str,
    api_key: str,
    encryption_key: str,
    case_id: str,
    s3_path: str,
    split: str = "false",
    metadata: dict = None,
) -> dict:
    """
    Encrypt the payload and build the headers + body dict.

    Payload shape (before encryption):
        {
            "case_id":  "<case_id>",
            "s3_path":  "<s3_path>",
            "split":    "true" | "false",
            "metadata": { ...optional... }
        }

    Hash: SHA256(api_key|client_id|timestamp|encrypted_payload)

    Returns:
        {"headers": {...}, "body": {"encrypted_payload": "..."}}
    """
    payload = {
        "case_id":  case_id,
        "s3_path":  s3_path,
        "split":    split,
        "metadata": metadata or {},
    }

    cipher = Fernet(encryption_key.encode("utf-8"))
    encrypted_payload = cipher.encrypt(json.dumps(payload).encode("utf-8")).decode("utf-8")

    timestamp = str(int(time.time() * 1000))
    message = f"{api_key}|{client_id}|{timestamp}|{encrypted_payload}"
    hash_sig = hashlib.sha256(message.encode("utf-8")).hexdigest()

    return {
        "headers": {
            "client_id":    client_id,
            "timestamp":    timestamp,
            "hash":         hash_sig,
            "Content-Type": "application/json",
        },
        "body": {"encrypted_payload": encrypted_payload},
    }


def send_request(url: str, req: dict) -> tuple[int | str, str]:
    """Fire the request and return (status_code, response_body_str)."""
    body_bytes = json.dumps(req["body"]).encode("utf-8")
    http_req = urllib.request.Request(
        url, data=body_bytes, headers=req["headers"], method="POST"
    )
    try:
        with urllib.request.urlopen(http_req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            status = resp.status
            try:
                body = json.dumps(json.loads(raw), indent=2)
            except json.JSONDecodeError:
                body = raw
    except urllib.error.HTTPError as e:
        status = e.code
        raw = e.read().decode("utf-8")
        try:
            body = json.dumps(json.loads(raw), indent=2)
        except json.JSONDecodeError:
            body = raw
    except urllib.error.URLError as e:
        status = "connection_error"
        body = str(e.reason)
    return status, body


def build_curl(url: str, req: dict) -> str:
    body_json = json.dumps(req["body"])
    h = req["headers"]
    return (
        f'curl -X POST "{url}" \\\n'
        f'  -H "client_id: {h["client_id"]}" \\\n'
        f'  -H "timestamp: {h["timestamp"]}" \\\n'
        f'  -H "hash: {h["hash"]}" \\\n'
        f'  -H "Content-Type: application/json" \\\n'
        f"  -d '{body_json}'"
    )


if __name__ == "__main__":
    # â”€â”€ Load config from env (or fall back to defaults) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    client_id      = os.getenv("CLIENT_ID",      DEFAULT_CLIENT_ID)
    api_key        = os.getenv("API_KEY",         DEFAULT_API_KEY)
    encryption_key = os.getenv("ENCRYPTION_KEY",  DEFAULT_ENCRYPTION_KEY)
    url            = os.getenv("IDENTIFICATION_URL", DEFAULT_URL)
    case_id        = os.getenv("CASE_ID",         DEFAULT_CASE_ID)
    s3_path        = os.getenv("S3_PATH",         DEFAULT_S3_PATH)
    split          = os.getenv("SPLIT",           DEFAULT_SPLIT).lower()

    if len(encryption_key) != 44:
        print("ERROR: ENCRYPTION_KEY must be 44 characters (Fernet base64).")
        exit(1)

    print(f"Endpoint : {url}")
    print(f"client_id: {client_id}")
    print(f"case_id  : {case_id}")
    print(f"s3_path  : {s3_path}")
    print(f"split    : {split}")
    print()

    req = build_request(client_id, api_key, encryption_key, case_id, s3_path, split)

    curl_cmd = build_curl(url, req)
    print("=== CURL ===")
    print(curl_cmd)
    print()

    print("Sending request...")
    status, body = send_request(url, req)
    print(f"Status : {status}")
    print(f"Response:\n{body}")

    # â”€â”€ Save output â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    out_path = os.path.join(os.path.dirname(__file__), "identification_test_result.txt")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("=== IDENTIFICATION API TEST ===\n\n")
        f.write(f"URL     : {url}\n")
        f.write(f"case_id : {case_id}\n")
        f.write(f"s3_path : {s3_path}\n")
        f.write(f"split   : {split}\n\n")
        f.write("=== CURL ===\n\n")
        f.write(curl_cmd)
        f.write("\n\n=== RESPONSE ===\n\n")
        f.write(f"Status: {status}\n\n")
        f.write(body)
        f.write("\n")

    print(f"\nFull output saved to: {out_path}")
    if status == "connection_error":
        exit(1)

