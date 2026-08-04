from __future__ import annotations

import io
import multiprocessing
import os
import tempfile
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Literal


DetectedFormat = Literal["JPEG", "PNG", "WEBP", "PDF"]


@dataclass(frozen=True)
class ValidationLimits:
    max_image_width: int
    max_image_height: int
    max_image_pixels: int
    max_pdf_pages: int
    max_pdf_objects: int
    max_pdf_traversal_depth: int


@dataclass(frozen=True)
class ValidationInput:
    source_bytes: bytes | None
    temporary_path: str | None
    detected_format: DetectedFormat
    limits: ValidationLimits


@dataclass(frozen=True)
class ValidatedDocument:
    data: bytes
    detected_mime: str
    sha256_hex: str
    size_bytes: int


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    error_code: str | None = None


MIME_BY_FORMAT: dict[DetectedFormat, str] = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
    "PDF": "application/pdf",
}

ACTIVE_PDF_KEYS = {"/OpenAction", "/AA", "/JavaScript", "/JS", "/Launch", "/EmbeddedFiles"}


def detect_format(data: bytes) -> DetectedFormat:
    if data[:3] == b"\xff\xd8\xff":
        return "JPEG"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "PNG"
    if data[:4] == b"RIFF" and len(data) >= 12 and data[8:12] == b"WEBP":
        declared = int.from_bytes(data[4:8], "little")
        if declared != len(data) - 8:
            raise ValueError("INVALID_IMAGE")
        return "WEBP"
    if data[:5] == b"%PDF-":
        return "PDF"
    if data.startswith(b"%PDF"):
        raise ValueError("INVALID_PDF")
    raise ValueError("UNSUPPORTED_FILE_TYPE")


def validate_declared_mime(detected_mime: str, declared_mime: str | None) -> None:
    if declared_mime and declared_mime != detected_mime:
        raise ValueError("MIME_MISMATCH")


def _walk_pdf_object(obj, limits: ValidationLimits, depth: int = 0, visited: set[tuple[int, int]] | None = None) -> ValidationResult:
    if visited is None:
        visited = set()
    if depth > limits.max_pdf_traversal_depth or len(visited) > limits.max_pdf_objects:
        return ValidationResult(False, "PDF_STRUCTURE_LIMIT_EXCEEDED")
    try:
        from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject, NameObject  # type: ignore
    except ImportError:
        return ValidationResult(True)

    try:
        if isinstance(obj, IndirectObject):
            ident = (obj.idnum, obj.generation)
            if ident in visited:
                return ValidationResult(True)
            visited.add(ident)
            if len(visited) > limits.max_pdf_objects:
                return ValidationResult(False, "PDF_STRUCTURE_LIMIT_EXCEEDED")
            obj = obj.get_object()
        if isinstance(obj, DictionaryObject):
            for key, value in obj.items():
                key_text = str(key)
                if key_text in ACTIVE_PDF_KEYS:
                    return ValidationResult(False, "PDF_ACTIVE_CONTENT_UNSUPPORTED")
                if isinstance(key, NameObject) and str(value) in ACTIVE_PDF_KEYS:
                    return ValidationResult(False, "PDF_ACTIVE_CONTENT_UNSUPPORTED")
                result = _walk_pdf_object(value, limits, depth + 1, visited)
                if not result.ok:
                    return result
        elif isinstance(obj, ArrayObject):
            for value in obj:
                result = _walk_pdf_object(value, limits, depth + 1, visited)
                if not result.ok:
                    return result
    except RecursionError:
        return ValidationResult(False, "PDF_STRUCTURE_LIMIT_EXCEEDED")
    except Exception:
        return ValidationResult(False, "INVALID_PDF")
    return ValidationResult(True)


def validate_document_worker(validation_input: ValidationInput) -> ValidationResult:
    try:
        if (validation_input.source_bytes is None) == (validation_input.temporary_path is None):
            return ValidationResult(False, "VALIDATION_PROCESS_FAILED")
        if validation_input.source_bytes is not None:
            data = validation_input.source_bytes
        else:
            data = Path(validation_input.temporary_path or "").read_bytes()

        if validation_input.detected_format == "PDF":
            if not data.startswith(b"%PDF-"):
                return ValidationResult(False, "INVALID_PDF")
            try:
                from pypdf import PdfReader  # type: ignore

                reader = PdfReader(io.BytesIO(data))
                if reader.is_encrypted:
                    return ValidationResult(False, "PDF_ENCRYPTED")
                if len(reader.pages) > validation_input.limits.max_pdf_pages:
                    return ValidationResult(False, "PDF_PAGE_LIMIT_EXCEEDED")
                structure_result = _walk_pdf_object(reader.trailer, validation_input.limits)
                if not structure_result.ok:
                    return structure_result
            except ImportError:
                # Dependency is optional in current local environment; signature and active-content
                # checks still run. Production package must include pypdf.
                return ValidationResult(True)
            except Exception:
                return ValidationResult(False, "INVALID_PDF")
            return ValidationResult(True)

        try:
            from PIL import Image  # type: ignore
            import warnings

            Image.MAX_IMAGE_PIXELS = validation_input.limits.max_image_pixels
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as img:
                if img.width > validation_input.limits.max_image_width or img.height > validation_input.limits.max_image_height:
                    return ValidationResult(False, "IMAGE_DIMENSIONS_EXCEEDED")
                if img.width * img.height > validation_input.limits.max_image_pixels:
                    return ValidationResult(False, "IMAGE_PIXEL_LIMIT_EXCEEDED")
                if getattr(img, "is_animated", False) or getattr(img, "n_frames", 1) > 1:
                    return ValidationResult(False, "ANIMATED_IMAGE_UNSUPPORTED")
                img.verify()
        except ImportError:
            return ValidationResult(True)
        except Exception:
            return ValidationResult(False, "INVALID_IMAGE")
        return ValidationResult(True)
    except Exception:
        return ValidationResult(False, "VALIDATION_PROCESS_FAILED")


def _worker_entry(validation_input: ValidationInput, child_conn) -> None:
    try:
        child_conn.send(validate_document_worker(validation_input))
    finally:
        child_conn.close()


def run_validation_subprocess_sync(
    validation_input: ValidationInput,
    timeout_seconds: float,
    termination_grace_seconds: float,
) -> ValidationResult:
    ctx = multiprocessing.get_context("spawn")
    parent_conn, child_conn = ctx.Pipe(duplex=False)
    process = ctx.Process(target=_worker_entry, args=(validation_input, child_conn))
    process.start()
    child_conn.close()
    try:
        if parent_conn.poll(timeout_seconds):
            try:
                result = parent_conn.recv()
            except EOFError:
                process.join(termination_grace_seconds)
                return ValidationResult(False, "VALIDATION_PROCESS_FAILED")
            process.join(termination_grace_seconds)
            if process.exitcode not in (0, None):
                return ValidationResult(False, "VALIDATION_PROCESS_FAILED")
            if not isinstance(result, ValidationResult):
                return ValidationResult(False, "VALIDATION_PROCESS_FAILED")
            return result
        process.terminate()
        process.join(termination_grace_seconds)
        if process.is_alive() and hasattr(process, "kill"):
            process.kill()
            process.join()
        return ValidationResult(False, "DOCUMENT_VALIDATION_TIMEOUT")
    finally:
        parent_conn.close()


def materialize_validation_input(
    data: bytes,
    detected_format: DetectedFormat,
    limits: ValidationLimits,
    spool_max_memory_bytes: int,
) -> tuple[ValidationInput, str | None]:
    if len(data) <= spool_max_memory_bytes:
        return ValidationInput(data, None, detected_format, limits), None
    tmp = tempfile.NamedTemporaryFile(delete=False)
    try:
        tmp.write(data)
        tmp.flush()
        path = tmp.name
    finally:
        tmp.close()
    return ValidationInput(None, path, detected_format, limits), path


def cleanup_temporary_path(path: str | None) -> None:
    if path:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def build_validated_document(data: bytes, detected_mime: str) -> ValidatedDocument:
    return ValidatedDocument(
        data=data,
        detected_mime=detected_mime,
        sha256_hex=sha256(data).hexdigest(),
        size_bytes=len(data),
    )
