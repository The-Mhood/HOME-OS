import base64
import hashlib
import mimetypes
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from home_os.db.models import ActiveMediaSession, Device, DevicePermission, FileTransfer, MediaItem
from home_os.security.audit import append_audit_log
from home_os.services.command_orchestrator import CommandOrchestrator, CommandPipelineError
from home_os.services.event_bus import EventBus


class PathAuthorizationError(Exception):
    """Raised when a file request attempts directory traversal or accesses an unapproved path."""


def resolve_authorized_path(requested_path: str, allowed_roots: list[str]) -> Path:
    if not requested_path or "\x00" in requested_path:
        raise PathAuthorizationError("Malformed file path")
    if not allowed_roots:
        raise PathAuthorizationError("No authorized file roots have been configured")

    candidate = Path(requested_path).resolve(strict=False)
    for root_str in allowed_roots:
        root_real = Path(root_str).resolve(strict=False)
        try:
            candidate.relative_to(root_real)
            return candidate
        except ValueError:
            continue

    raise PathAuthorizationError(
        f"Path '{requested_path}' is outside user-authorized storage roots"
    )


class MediaAndFileService:
    """Authorized remote file browsing, media discovery, playback sessions, and SHA-256 verified device-to-device file transfers."""

    def __init__(self, orchestrator: CommandOrchestrator, event_bus: EventBus, staging_dir: Path):
        self.orchestrator = orchestrator
        self.event_bus = event_bus
        self.staging_dir = staging_dir
        self.staging_dir.mkdir(parents=True, exist_ok=True)

    async def browse_device_directory(
        self,
        *,
        db: Session,
        user_id: str,
        device_id: str,
        path: str | None = None,
    ) -> dict[str, Any]:
        cmd_exec = await self.orchestrator.submit_command(
            db=db,
            user_id=user_id,
            device_id=device_id,
            capability_key="files.browse",
            parameters={"path": path or ""},
        )
        if cmd_exec.status not in ("verified", "acknowledged"):
            raise CommandPipelineError(
                cmd_exec.error_code or "BROWSE_FAILED",
                cmd_exec.error_message or "Remote directory listing failed",
                status_code=502,
            )
        return cmd_exec.result_json

    async def index_device_media(
        self,
        *,
        db: Session,
        user_id: str,
        device_id: str,
    ) -> list[MediaItem]:
        listing = await self.browse_device_directory(db=db, user_id=user_id, device_id=device_id, path="")
        entries = listing.get("entries", [])
        indexed: list[MediaItem] = []

        for entry in entries:
            if entry.get("is_dir"):
                continue
            name = str(entry.get("name", ""))
            full_path = str(entry.get("path", name))
            mime, _ = mimetypes.guess_type(name)
            mime = mime or "application/octet-stream"
            media_type = None
            if mime.startswith("image/"):
                media_type = "photo"
            elif mime.startswith("video/"):
                media_type = "video"
            elif mime.startswith("audio/"):
                media_type = "audio"
            if not media_type:
                continue

            existing = db.execute(
                select(MediaItem).where(
                    MediaItem.device_id == device_id,
                    MediaItem.remote_path == full_path,
                )
            ).scalar_one_or_none()

            if existing is None:
                item = MediaItem(
                    device_id=device_id,
                    media_type=media_type,
                    title=name,
                    remote_path=full_path,
                    mime_type=mime,
                    size_bytes=int(entry.get("size", 0)),
                    indexed_at=datetime.now(timezone.utc),
                )
                db.add(item)
                indexed.append(item)
            else:
                existing.size_bytes = int(entry.get("size", existing.size_bytes))
                indexed.append(existing)

        db.commit()
        return indexed

    async def transfer_file_between_devices(
        self,
        *,
        db: Session,
        user_id: str,
        source_device_id: str,
        source_path: str,
        target_device_id: str,
        target_dir: str,
    ) -> FileTransfer:
        file_name = os.path.basename(source_path.rstrip("/")) or "transfer.bin"
        mime, _ = mimetypes.guess_type(file_name)
        target_path = f"{target_dir.rstrip('/')}/{file_name}" if target_dir else file_name

        transfer = FileTransfer(
            direction="device_to_device",
            source_device_id=source_device_id,
            target_device_id=target_device_id,
            source_path=source_path,
            target_path=target_path,
            file_name=file_name,
            mime_type=mime or "application/octet-stream",
            status="in_progress",
            authorized_by_user_id=user_id,
        )
        db.add(transfer)
        db.commit()

        # Step 1: Read authorized file payload from source device
        read_exec = await self.orchestrator.submit_command(
            db=db,
            user_id=user_id,
            device_id=source_device_id,
            capability_key="files.browse",
            parameters={"action": "read_file", "path": source_path},
        )
        if read_exec.status not in ("verified", "acknowledged") or "content_b64" not in read_exec.result_json:
            transfer.status = "failed"
            transfer.error_message = read_exec.error_message or "Failed to read authorized file from source device"
            db.commit()
            return transfer

        raw_bytes = base64.b64decode(read_exec.result_json["content_b64"])
        sha256_hex = hashlib.sha256(raw_bytes).hexdigest()

        # Verify source checksum if reported
        reported_sha = read_exec.result_json.get("sha256")
        if reported_sha and reported_sha != sha256_hex:
            transfer.status = "failed"
            transfer.error_message = "SHA-256 integrity mismatch reading from source device"
            db.commit()
            return transfer

        # Stage copy in hub transfers directory for download availability
        staged_file = self.staging_dir / f"{transfer.id}_{file_name}"
        staged_file.write_bytes(raw_bytes)
        transfer.staged_local_path = str(staged_file)
        transfer.size_bytes = len(raw_bytes)
        transfer.sha256_checksum = sha256_hex

        # Step 2: Write verified payload to target device
        write_exec = await self.orchestrator.submit_command(
            db=db,
            user_id=user_id,
            device_id=target_device_id,
            capability_key="files.transfer",
            parameters={
                "action": "write_file",
                "target_dir": target_dir,
                "file_name": file_name,
                "content_b64": read_exec.result_json["content_b64"],
                "expected_sha256": sha256_hex,
            },
        )
        if write_exec.status not in ("verified", "acknowledged"):
            transfer.status = "failed"
            transfer.error_message = write_exec.error_message or "Failed to write file to target device"
            db.commit()
            return transfer

        transfer.transferred_bytes = len(raw_bytes)
        transfer.status = "completed"
        transfer.completed_at = datetime.now(timezone.utc)
        append_audit_log(
            db,
            severity="info",
            category="files",
            event_type="file.device_to_device_completed",
            actor_type="user",
            actor_id=user_id,
            device_id=target_device_id,
            outcome="success",
            details={
                "transfer_id": transfer.id,
                "source_device_id": source_device_id,
                "target_device_id": target_device_id,
                "size_bytes": transfer.size_bytes,
                "sha256": sha256_hex,
            },
        )
        db.commit()
        await self.event_bus.publish(
            "file.transfer_updated",
            {"transfer_id": transfer.id, "status": "completed", "sha256": sha256_hex},
        )
        return transfer

    def upsert_media_session(
        self,
        *,
        db: Session,
        device_id: str,
        app_name: str,
        title: str,
        artist: str | None = None,
        album: str | None = None,
        playback_state: str = "playing",
        position_ms: int = 0,
        duration_ms: int = 0,
        volume_percent: int | None = None,
        is_muted: bool = False,
    ) -> ActiveMediaSession:
        existing = db.execute(
            select(ActiveMediaSession).where(ActiveMediaSession.device_id == device_id)
        ).scalar_one_or_none()
        now = datetime.now(timezone.utc)
        if existing is None:
            session = ActiveMediaSession(
                device_id=device_id,
                app_name=app_name,
                title=title,
                artist=artist,
                album=album,
                playback_state=playback_state,
                position_ms=position_ms,
                duration_ms=duration_ms,
                volume_percent=volume_percent,
                is_muted=is_muted,
                updated_at=now,
            )
            db.add(session)
        else:
            existing.app_name = app_name
            existing.title = title
            existing.artist = artist
            existing.album = album
            existing.playback_state = playback_state
            existing.position_ms = position_ms
            existing.duration_ms = duration_ms
            existing.volume_percent = volume_percent
            existing.is_muted = is_muted
            existing.updated_at = now
            session = existing
        db.commit()
        db.refresh(session)
        return session
