"""
POST /v1/files/extract

Accepts a real file upload and returns real extracted text — this is
what the composer's attach button actually calls now, instead of just
sending a filename. Supports PDF, Word, PowerPoint, Excel, and
plain-text/code files. Anything else gets a clear "not supported yet"
error rather than silently failing.
"""

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel

from .. import auth, models
from ..document_extraction import UnsupportedFileType, extract_text

router = APIRouter(prefix="/v1/files", tags=["files"])

MAX_UPLOAD_BYTES = 15 * 1024 * 1024  # 15 MB


class ExtractedFile(BaseModel):
    filename: str
    text: str
    truncated: bool
    char_count: int


@router.post("/extract", response_model=ExtractedFile)
async def extract(
    file: UploadFile,
    current_user: models.User = Depends(auth.get_current_user),
):
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail={
                "error": {
                    "code": "file_too_large",
                    "message": f"File exceeds the {MAX_UPLOAD_BYTES // (1024*1024)} MB limit.",
                }
            },
        )

    try:
        result = extract_text(file.filename or "unnamed", data)
    except UnsupportedFileType as e:
        raise HTTPException(
            status_code=415,
            detail={"error": {"code": "unsupported_file_type", "message": str(e)}},
        )
    except Exception as e:  # noqa: BLE001 — a malformed file shouldn't 500 the endpoint
        raise HTTPException(
            status_code=422,
            detail={
                "error": {
                    "code": "extraction_failed",
                    "message": f"Couldn't read this file: {type(e).__name__}",
                }
            },
        )

    return ExtractedFile(filename=file.filename or "unnamed", **result)
