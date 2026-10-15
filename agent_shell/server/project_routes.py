"""Project settings and artifact discovery, scoped to the authenticated manager."""

import os
import secrets
import shutil
import tempfile
import threading
import time
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from agent_shell.errors import SessionError
from agent_shell.server.delivery import create_task_bundle
from agent_shell.server.project_backup import (
    MAX_BACKUP_UPLOAD_BYTES,
    create_project_backup,
    import_project_backup,
    recover_project_imports,
)
from agent_shell.server.projects import (
    ManagedProjectCreate,
    ProjectArchive,
    ProjectCreate,
    ProjectSettings,
)

_BACKUP_TICKET_TTL_SECONDS = 60.0
_MAX_BACKUP_TICKETS = 8


def managed_root(user: str | None) -> Path:
    configured = os.environ.get("AGENT_PROJECTS_ROOT", "").strip()
    root = Path(configured).expanduser() if configured else Path.home() / "Jelly Projects"
    return (root / user if user else root).resolve()


def backup_filename(manager, project_id: str) -> str:
    name = manager.projects.get(project_id)["name"]
    safe_name = manager.projects.directory_name(name)
    date = datetime.now(timezone.utc).date().isoformat()
    return f"Jelly-{safe_name}-v1-{date}.zip"


def project_router(managers, auth, runs):
    router = APIRouter(prefix="/api")
    protected = APIRouter(dependencies=[Depends(auth)])
    backup_gate = threading.BoundedSemaphore(1)
    backup_tickets: dict[str, tuple[float, Path, str]] = {}
    ticket_lock = threading.Lock()

    def remove_backup(path: Path) -> None:
        with suppress(OSError):
            path.unlink(missing_ok=True)

    def expire_backup_ticket(ticket: str, deadline: float) -> None:
        """Remove an unclaimed ticket and its temporary archive after expiry."""
        remaining = deadline - time.monotonic()
        if remaining > 0:
            timer = threading.Timer(remaining, expire_backup_ticket, (ticket, deadline))
            timer.daemon = True
            timer.start()
            return
        expired = None
        with ticket_lock:
            current = backup_tickets.get(ticket)
            if current is not None and current[0] == deadline:
                expired = backup_tickets.pop(ticket)[1]
        if expired is not None:
            remove_backup(expired)

    def register_backup_ticket(path: Path, filename: str) -> tuple[str, float]:
        """Store a bounded, high-entropy, single-use download capability."""
        now = time.monotonic()
        discarded: list[Path] = []
        with ticket_lock:
            for key, (deadline, old_path, _old_filename) in list(backup_tickets.items()):
                if deadline <= now:
                    backup_tickets.pop(key, None)
                    discarded.append(old_path)
            while len(backup_tickets) >= _MAX_BACKUP_TICKETS:
                oldest = min(backup_tickets, key=lambda key: backup_tickets[key][0])
                discarded.append(backup_tickets.pop(oldest)[1])
            ticket = secrets.token_urlsafe(32)
            while ticket in backup_tickets:
                ticket = secrets.token_urlsafe(32)
            deadline = now + _BACKUP_TICKET_TTL_SECONDS
            backup_tickets[ticket] = (deadline, path, filename)
        for old_path in discarded:
            remove_backup(old_path)
        timer = threading.Timer(
            max(0.0, deadline - time.monotonic()),
            expire_backup_ticket,
            (ticket, deadline),
        )
        timer.daemon = True
        timer.start()
        return ticket, deadline

    def require_backup_intent(request: Request) -> None:
        """Require a non-simple browser header so local no-token mode resists CSRF."""
        if request.headers.get("x-jelly-intent") != "project-backup":
            raise HTTPException(status_code=403, detail="缺少项目备份操作标识")

    @protected.get("/projects")
    def list_projects(request: Request):
        mgr = managers.for_user(request.state.user)
        recovery_warnings = []
        acquired = backup_gate.acquire(blocking=False)
        if acquired:
            try:
                recovery_warnings = recover_project_imports(
                    mgr, managed_root(request.state.user)
                )
            except OSError as exc:
                recovery_warnings = [
                    {"file": "project-import", "message": f"无法检查未完成的项目导入：{exc}"}
                ]
            finally:
                backup_gate.release()
        sessions = mgr.list_sessions()
        projects = mgr.projects.list()
        return {
            "projects": [
                {
                    **project,
                    "available": Path(project["cwd"]).is_dir(),
                    "task_count": sum(
                        session["project_id"] == project["id"] for session in sessions
                    ),
                }
                for project in projects
            ],
            "warnings": [*mgr.projects.issues(), *recovery_warnings],
            "backup_busy": not acquired,
        }

    @protected.post("/projects")
    def create_project(request: Request, body: ProjectCreate):
        try:
            return managers.for_user(request.state.user).projects.save(body)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @protected.get("/projects/managed-root")
    def get_managed_root(request: Request):
        return {"root": str(managed_root(request.state.user))}

    @protected.post("/projects/managed")
    def create_managed_project(request: Request, body: ManagedProjectCreate):
        try:
            mgr = managers.for_user(request.state.user)
            return mgr.projects.create_managed(body, managed_root(request.state.user))
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=400, detail=f"无法创建工作目录: {exc}") from exc

    @protected.put("/projects/{project_id}")
    def update_project(request: Request, project_id: str, body: ProjectSettings):
        try:
            return managers.for_user(request.state.user).projects.save(body, project_id)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @protected.patch("/projects/{project_id}/archive")
    def archive_project(request: Request, project_id: str, body: ProjectArchive):
        mgr = managers.for_user(request.state.user)
        try:
            with mgr._lifecycle_lock:
                if body.archived and any(
                    session["project_id"] == project_id and (
                        session["running"]
                        or runs(request).snapshot(session["session_id"])["busy"]
                    )
                    for session in mgr.list_sessions()
                ):
                    raise HTTPException(
                        status_code=409, detail="项目仍有任务正在运行，请先停止后再归档"
                    )
                return mgr.projects.set_archived(project_id, body.archived)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def build_project_backup(request: Request, project_id: str) -> Path:
        """Create an archive while project state is stable."""
        if not backup_gate.acquire(blocking=False):
            raise HTTPException(status_code=429, detail="已有项目备份操作正在进行，请稍后再试")
        manager = managers.for_user(request.state.user)
        try:
            with manager._lifecycle_lock:
                manager.projects.get(project_id)
                if any(
                    session["project_id"] == project_id and (
                        session["running"]
                        or runs(request).snapshot(session["session_id"])["busy"]
                    )
                    for session in manager.list_sessions()
                ):
                    raise HTTPException(
                        status_code=409, detail="项目仍有任务在运行，请结束后再备份"
                    )
                return create_project_backup(manager, project_id)
        except SessionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            backup_gate.release()

    @protected.get("/projects/{project_id}/backup")
    def download_project_backup(request: Request, project_id: str):
        require_backup_intent(request)
        backup = build_project_backup(request, project_id)
        return FileResponse(
            backup,
            media_type="application/zip",
            filename=backup_filename(managers.for_user(request.state.user), project_id),
            headers={"Cache-Control": "no-store"},
            background=BackgroundTask(backup.unlink, missing_ok=True),
        )

    @protected.post("/projects/{project_id}/backup-ticket")
    def create_backup_ticket(request: Request, project_id: str):
        """Create a short-lived native-download URL without exposing credentials."""
        require_backup_intent(request)
        backup = build_project_backup(request, project_id)
        filename = backup_filename(managers.for_user(request.state.user), project_id)
        try:
            ticket, deadline = register_backup_ticket(backup, filename)
        except Exception:
            remove_backup(backup)
            raise
        return {
            "url": f"/api/project-backups/{ticket}",
            "filename": filename,
            "expires_in": max(0, round(deadline - time.monotonic())),
        }

    @router.get("/project-backups/{ticket}")
    def download_ticketed_backup(ticket: str):
        """Atomically consume one ticket and stream its temporary archive."""
        if len(ticket) > 128:
            raise HTTPException(status_code=404, detail="备份下载地址无效或已过期")
        entry = None
        with ticket_lock:
            entry = backup_tickets.pop(ticket, None)
        if entry is None:
            raise HTTPException(status_code=404, detail="备份下载地址无效或已过期")
        deadline, backup, filename = entry
        if deadline <= time.monotonic():
            remove_backup(backup)
            raise HTTPException(status_code=404, detail="备份下载地址无效或已过期")
        return FileResponse(
            backup,
            media_type="application/zip",
            filename=filename,
            headers={"Cache-Control": "no-store"},
            background=BackgroundTask(remove_backup, backup),
        )

    @protected.post("/projects/import", status_code=201)
    async def import_project(request: Request):
        """Receive a raw ZIP stream, verify it, and create a separate managed project."""
        require_backup_intent(request)
        if request.headers.get("content-type", "").split(";", 1)[0].strip() != "application/zip":
            raise HTTPException(status_code=415, detail="项目备份必须是 ZIP 文件")
        if not backup_gate.acquire(blocking=False):
            raise HTTPException(status_code=429, detail="已有项目备份操作正在进行，请稍后再试")
        upload = None
        try:
            raw_length = request.headers.get("content-length")
            if raw_length is not None:
                try:
                    content_length = int(raw_length)
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail="上传长度无效") from exc
                if content_length < 0:
                    raise HTTPException(status_code=400, detail="上传长度无效")
                if content_length > MAX_BACKUP_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="备份文件超过上传上限")
                if shutil.disk_usage(tempfile.gettempdir()).free < content_length + 64_000_000:
                    raise HTTPException(
                        status_code=400, detail="临时磁盘空间不足，无法接收项目备份"
                    )
            fd, temp_name = tempfile.mkstemp(prefix="jelly-import-upload-", suffix=".zip")
            upload = Path(temp_name)
            count = 0
            buffered = bytearray()
            with os.fdopen(fd, "wb") as stream:
                async for chunk in request.stream():
                    count += len(chunk)
                    if count > MAX_BACKUP_UPLOAD_BYTES:
                        raise HTTPException(status_code=413, detail="备份文件超过上传上限")
                    view = memoryview(chunk)
                    while view:
                        available = 4 * 1024 * 1024 - len(buffered)
                        buffered.extend(view[:available])
                        view = view[available:]
                        if len(buffered) == 4 * 1024 * 1024:
                            await run_in_threadpool(stream.write, bytes(buffered))
                            buffered.clear()
                if buffered:
                    await run_in_threadpool(stream.write, bytes(buffered))
            if not count:
                raise HTTPException(status_code=400, detail="请选择项目备份 ZIP")
            manager = managers.for_user(request.state.user)
            try:
                return await run_in_threadpool(
                    import_project_backup, manager, upload, managed_root(request.state.user)
                )
            except (ValueError, SessionError, OSError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ClientDisconnect as exc:
            raise HTTPException(status_code=499, detail="备份上传已断开") from exc
        except OSError as exc:
            raise HTTPException(
                status_code=400, detail=f"无法接收项目备份，请检查磁盘空间与目录权限：{exc}"
            ) from exc
        finally:
            if upload is not None:
                with suppress(OSError):
                    upload.unlink(missing_ok=True)
            backup_gate.release()

    @protected.get("/sessions/{session_id}/artifacts")
    def list_artifacts(request: Request, session_id: str):
        mgr = managers.for_user(request.state.user)
        root = mgr.workspace(session_id)
        journal = mgr.changes(session_id)
        versions = journal.list(include_diff=False)
        paths = {}
        for version in versions:
            if version["state"] in {"ready", "restored", "snapshot_failed"}:
                paths.setdefault(version["path"], []).append(version)
        artifacts = []
        for relative, records in paths.items():
            path = (root / relative).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                continue
            try:
                info = path.stat()
            except OSError:
                continue
            artifacts.append(
                {
                    "path": relative,
                    "name": Path(relative).name,
                    "size": info.st_size,
                    "updated_at": records[0]["created_at"],
                    "version": len(records),
                    "revision": f"{info.st_mtime_ns}-{info.st_size}",
                    "extension": path.suffix.lower(),
                }
            )
        return {"artifacts": artifacts, "warnings": journal.issues()}

    @protected.get("/sessions/{session_id}/delivery")
    def download_task_delivery(request: Request, session_id: str):
        manager = managers.for_user(request.state.user)
        try:
            if runs(request).snapshot(session_id)["busy"] or session_id in manager._running:
                raise HTTPException(status_code=409, detail="任务运行中，请完成或停止后再导出")
            bundle = create_task_bundle(manager, session_id)
        except SessionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=400, detail=f"无法生成交付包: {exc}") from exc
        return FileResponse(
            bundle,
            media_type="application/zip",
            filename=f"Jelly-{session_id}.zip",
            background=BackgroundTask(bundle.unlink, missing_ok=True),
        )

    router.include_router(protected)
    return router
