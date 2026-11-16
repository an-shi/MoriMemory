"""Backup creation, download and restore endpoints."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import FileResponse

from ...engine.types import Principal
from ...errors import ValidationError
from ...services.backup import BackupService
from ..deps import get_backup, require_admin
from ..responses import ok

router = APIRouter(prefix="/backups", tags=["backups"])

MAX_UPLOAD_BYTES = 1024 * 1024 * 1024


@router.get("")
async def list_backups(
    _principal: Principal = Depends(require_admin),
    service: BackupService = Depends(get_backup),
) -> dict:
    return ok({"items": service.list()})


@router.post("")
async def create_backup(
    _principal: Principal = Depends(require_admin),
    service: BackupService = Depends(get_backup),
) -> dict:
    path = await service.create()
    return ok(
        {"name": path.name, "size": path.stat().st_size},
        message="backup created",
    )


@router.post("/restore")
async def restore_backup(
    file: UploadFile = File(...),
    _principal: Principal = Depends(require_admin),
    service: BackupService = Depends(get_backup),
) -> dict:
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise ValidationError("a .zip backup file is required")
    temp_path = service.paths.data_dir / (f".upload-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip")
    total = 0
    try:
        with temp_path.open("wb") as handle:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise ValidationError("backup file is too large")
                handle.write(chunk)
        result = await service.restore(temp_path)
        return ok(result, message="backup restored")
    finally:
        await file.close()
        temp_path.unlink(missing_ok=True)


@router.get("/{name}")
async def download_backup(
    name: str,
    _principal: Principal = Depends(require_admin),
    service: BackupService = Depends(get_backup),
) -> FileResponse:
    path = service.resolve_backup(name)
    return FileResponse(
        path,
        media_type="application/zip",
        filename=path.name,
    )


@router.delete("/{name}")
async def delete_backup(
    name: str,
    _principal: Principal = Depends(require_admin),
    service: BackupService = Depends(get_backup),
) -> dict:
    path = service.resolve_backup(name)
    path.unlink(missing_ok=True)
    return ok({}, message="backup deleted")
