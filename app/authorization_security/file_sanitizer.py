"""
File security / sanitization for orchestration use cases.

Secure file validation, MIME detection, PDF extraction, and image re-encoding.
Reusable by signature-card and other use cases that accept file uploads or file_data in payloads.
"""

from dataclasses import dataclass
from fastapi import UploadFile, HTTPException, status
from typing import Tuple, Optional
import asyncio
import io
import logging
import os
from PIL import Image
import magic

try:
    import fitz  # PyMuPDF
    PDF_SUPPORT = True
except ImportError:
    PDF_SUPPORT = False

try:
    import pikepdf
    PIKEPDF_SUPPORT = True
except ImportError:
    PIKEPDF_SUPPORT = False

MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE_MB", "10")) * 1024 * 1024
ALLOWED_MIME = {"image/jpeg", "image/png", "image/tiff", "application/pdf"}
JPEG_QUALITY = 90

GENERAL_SUSPICIOUS_PATTERNS = [
    b"<script", b"<?php", b"#!/bin/bash", b"eval(", b"powershell",
    b"<iframe", b"onload=", b"javascript:", b"vbscript:", b"onerror=", b"onclick=", b"<!entity", b"&xxe;",
]
PDF_SPECIFIC_PATTERNS = [b"/js", b"/javascript", b"/launch"]

logger = logging.getLogger(__name__)


@dataclass
class SanitizationResult:
    can_process: bool
    file_content: bytes


class FileSanitizer:
    """
    Secure file validation and sanitization: size, MIME, PDF security, first-page extraction, image re-encode.
    """

    def __init__(self):
        if not PDF_SUPPORT:
            logger.warning("PyMuPDF not installed. PDF support disabled.")
        if not PIKEPDF_SUPPORT:
            logger.warning("pikepdf not installed. Deep PDF security checks disabled.")

    async def sanitize(self, upload_file: UploadFile) -> SanitizationResult:
        original_filename = upload_file.filename or "document"
        logger.info(f"Starting sanitization for file: {original_filename}")
        file_content = await upload_file.read()

        if len(file_content) > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=400,
                detail=f"File size must be less than {MAX_FILE_SIZE / (1024 * 1024):.0f}MB"
            )

        detected_mime = await asyncio.to_thread(self._detect_mime, file_content)
        logger.info(f"File validation passed: {original_filename}, MIME: {detected_mime}, size: {len(file_content)} bytes")

        if detected_mime == "application/pdf":
            logger.info(f"PDF detected: {original_filename}, running security checks")
            await self._pdf_sniff_check(file_content, detected_mime)
            await self._deep_pdf_inspect(file_content, detected_mime)
            logger.info(f"PDF security checks passed: {original_filename}")
            logger.info(f"Extracting first page from PDF: {original_filename}")
            file_content, _ = await asyncio.to_thread(self._extract_first_page_from_pdf, file_content)

        logger.info(f"Re-encoding image for security: {original_filename}")
        file_content = await asyncio.to_thread(self._reencode_image, file_content)
        logger.info(f"Sanitization completed successfully for: {original_filename}")
        return SanitizationResult(can_process=True, file_content=file_content)

    async def _pdf_sniff_check(self, raw: bytes, mime: str) -> None:
        if mime != "application/pdf":
            return
        if not raw.startswith(b"%PDF-"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File rejected: Invalid PDF header")
        suspicious_pattern = await asyncio.to_thread(self._cpu_sniff_internal, raw)
        if suspicious_pattern:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File rejected: potentially malicious content detected")

    @staticmethod
    def _cpu_sniff_internal(data: bytes) -> Optional[str]:
        patterns = GENERAL_SUSPICIOUS_PATTERNS + PDF_SPECIFIC_PATTERNS
        chunk_size = 8192
        max_pattern_len = max(len(p) for p in patterns)
        overlap = max_pattern_len
        for i in range(0, len(data), chunk_size - overlap):
            chunk = data[i:i + chunk_size]
            chunk_lower = chunk.lower()
            for pattern in patterns:
                if pattern in chunk_lower:
                    return pattern.decode("utf-8", errors="ignore")
        return None

    async def _deep_pdf_inspect(self, raw: bytes, mime: str) -> None:
        if mime != "application/pdf":
            return
        if not raw.startswith(b"%PDF-"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File rejected: Invalid PDF header")
        await asyncio.to_thread(self._deep_pdf_inspect_internal, raw)

    @staticmethod
    def _deep_pdf_inspect_internal(raw: bytes) -> None:
        if not PIKEPDF_SUPPORT:
            return
        try:
            with pikepdf.open(io.BytesIO(raw)) as pdf:
                if pdf.is_encrypted:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File rejected: Encrypted PDFs are not allowed")
                root = pdf.Root
                if "/OpenAction" in root or "/AA" in root:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File rejected: PDF contains auto-executing actions")
                if "/AcroForm" in root:
                    acro = root["/AcroForm"]
                    if any(k in acro for k in ["/JS", "/JavaScript", "/XFA"]):
                        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File rejected: PDF form contains scripts")
                names = root.get("/Names")
                if names and (names.get("/EmbeddedFiles") or names.get("/JavaScript")):
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File rejected: PDF contains embedded files or scripts")
                forbidden_keys = {"/JS", "/JavaScript", "/AA", "/Launch", "/RichMedia", "/URI"}
                for page in pdf.pages:
                    if any(k in page.obj for k in forbidden_keys):
                        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File rejected: PDF page contains active content")
                    if "/Annots" in page.obj:
                        for annot in page.obj["/Annots"]:
                            if any(k in annot for k in ["/A", "/AA", "/JS", "/JavaScript"]):
                                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File rejected: PDF contains active annotations")
        except pikepdf.PdfError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File rejected: Corrupt or invalid PDF")

    def _detect_mime(self, file_bytes: bytes) -> str:
        try:
            detected_mime = magic.from_buffer(file_bytes[:4096], mime=True)
            if detected_mime not in ALLOWED_MIME:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported file type: {detected_mime}. Allowed: {', '.join(sorted(ALLOWED_MIME))}"
                )
            return detected_mime
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"MIME detection failed: {e}")
            raise HTTPException(status_code=400, detail=f"Failed to detect file type: {e}")

    def _extract_first_page_from_pdf(self, pdf_bytes: bytes) -> Tuple[bytes, str]:
        if not PDF_SUPPORT:
            raise HTTPException(status_code=500, detail="PDF processing not available. Install PyMuPDF and Pillow.")
        pdf_document = None
        try:
            pdf_document = fitz.open(stream=pdf_bytes, filetype="pdf")
            if len(pdf_document) == 0:
                raise HTTPException(status_code=400, detail="PDF file is empty or corrupted")
            first_page = pdf_document.load_page(0)
            pix = first_page.get_pixmap(alpha=False)
            img_bytes = pix.tobytes("jpeg")
            return img_bytes, "extracted_first_page.jpg"
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to extract first page from PDF: {e}")
            raise HTTPException(status_code=400, detail=f"Failed to extract first page from PDF: {e}")
        finally:
            if pdf_document is not None:
                try:
                    pdf_document.close()
                except Exception as close_error:
                    logger.warning(f"Error closing PDF: {close_error}")

    def _reencode_image(self, image_bytes: bytes) -> bytes:
        try:
            image = Image.open(io.BytesIO(image_bytes))
            try:
                image.load()
            except Exception as decode_error:
                logger.error(f"Image decoding failed: {decode_error}")
                raise HTTPException(status_code=400, detail="Invalid image file: corrupted or malformed image data")
            if image.mode != "RGB":
                image = image.convert("RGB")
            output_buffer = io.BytesIO()
            image.save(output_buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True, exif=b"")
            return output_buffer.getvalue()
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to re-encode image: {e}")
            raise HTTPException(status_code=400, detail=f"Failed to process image: {e}")
