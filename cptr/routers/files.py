"""Managed file uploads: blob storage + DB metadata.

POST   /api/files              multipart upload → storage + DB → { id, url }
GET    /api/files/{id}         stream from storage (login required)
GET    /api/files/{id}/sheets  spreadsheet preview as plain cell text
DELETE /api/files/{id}         verify ownership → delete storage + DB
"""

from __future__ import annotations

import hashlib
import os

from fastapi import APIRouter, Request, UploadFile, File as FastAPIFile, HTTPException
from fastapi.responses import Response

from cptr.models.files import File
from cptr.utils.config import now_ms
from cptr.utils.documents import workbook_preview
from cptr.utils.storage import get_storage
from cptr.utils.uploads import upload_headers

router = APIRouter(prefix="/api/files", tags=["files"])

# 10 MB max upload
MAX_UPLOAD_SIZE = 10 * 1024 * 1024


@router.post("")
async def upload(request: Request, file: UploadFile = FastAPIFile(...)):
    """Upload a file. Returns { id, url }."""
    data = await file.read()
    if len(data) > MAX_UPLOAD_SIZE:
        raise HTTPException(413, "File too large (max 10 MB)")

    user_id = getattr(request.state, "auth", None)
    user_id = user_id.user_id if user_id else None

    content_type = file.content_type or "application/octet-stream"
    file_hash = hashlib.sha256(data).hexdigest()
    original_name = file.filename or "upload"
    _, ext = os.path.splitext(original_name)

    record = await File.create(
        user_id=user_id,
        filename=original_name,
        meta={
            "content_type": content_type,
            "size": len(data),
        },
        created_at=now_ms(),
    )

    await get_storage().put(record.id, data)

    # Include original extension in URL so browsers / tools can infer file type
    url_path = f"/api/files/{record.id}{ext}" if ext else f"/api/files/{record.id}"
    return {"id": record.id, "url": url_path}


async def _stored_upload(file_id_ext: str) -> tuple[File, bytes]:
    file_id, _ = os.path.splitext(file_id_ext)
    record = await File.get_by_id(file_id)
    if not record:
        raise HTTPException(404, "Not found")
    data = await get_storage().get(record.id)
    if data is None:
        raise HTTPException(404, "Blob missing")
    return record, data


@router.get("/{file_id_ext}/sheets")
async def spreadsheet_preview(file_id_ext: str):
    """Cell text for an uploaded .xlsx or .xls file. Login is required."""
    record, data = await _stored_upload(file_id_ext)
    try:
        return workbook_preview(data, record.filename or "")
    except ImportError as exc:
        raise HTTPException(500, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(422, "Could not read spreadsheet") from exc


@router.get("/{file_id_ext:path}")
async def get_upload(file_id_ext: str):
    """Serve an uploaded file. Login is required.

    Accepts both /api/files/{uuid} and /api/files/{uuid}.{ext}.
    Images sniffed from the bytes are inline. Every other type is an
    attachment with an opaque media type, so the browser does not run it.
    """
    record, data = await _stored_upload(file_id_ext)
    media_type, headers = upload_headers(data, record.filename or "file")
    return Response(content=data, media_type=media_type, headers=headers)


@router.delete("/{file_id_ext:path}")
async def delete_upload(request: Request, file_id_ext: str):
    """Delete an uploaded file. Must be the owner."""
    file_id, _ = os.path.splitext(file_id_ext)

    record = await File.get_by_id(file_id)
    if not record:
        raise HTTPException(404, "Not found")

    auth = getattr(request.state, "auth", None)
    if not auth or auth.user_id != record.user_id:
        raise HTTPException(403, "Not your file")

    await get_storage().delete(record.id)
    await File.delete_by_id(record.id)

    return {"ok": True}
