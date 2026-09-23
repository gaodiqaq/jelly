"""Project settings and artifact discovery, scoped to the authenticated manager."""

import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from agent_shell.errors import SessionError
from agent_shell.server.delivery import create_task_bundle
from agent_shell.server.projects import (
    ManagedProjectCreate,
    ProjectArchive,
    ProjectCreate,
    ProjectSettings,
)


def managed_root(user: str | None) -> Path:
    configured = os.environ.get("AGENT_PROJECTS_ROOT", "").strip()
    root = Path(configured).expanduser() if configured else Path.home() / "Jelly Projects"
    return (root / user if user else root).resolve()


def project_router(managers, auth, runs):
    router = APIRouter(prefix="/api", dependencies=[Depends(auth)])

    @router.get("/projects")
    def list_projects(request: Request):
        mgr = managers.for_user(request.state.user)
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
            "warnings": mgr.projects.issues(),
        }

    @router.post("/projects")
    def create_project(request: Request, body: ProjectCreate):
        try:
            return managers.for_user(request.state.user).projects.save(body)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/projects/managed-root")
    def get_managed_root(request: Request):
        return {"root": str(managed_root(request.state.user))}

    @router.post("/projects/managed")
    def create_managed_project(request: Request, body: ManagedProjectCreate):
        try:
            mgr = managers.for_user(request.state.user)
            return mgr.projects.create_managed(body, managed_root(request.state.user))
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=400, detail=f"无法创建工作目录: {exc}") from exc

    @router.put("/projects/{project_id}")
    def update_project(request: Request, project_id: str, body: ProjectSettings):
        try:
            return managers.for_user(request.state.user).projects.save(body, project_id)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.patch("/projects/{project_id}/archive")
    def archive_project(request: Request, project_id: str, body: ProjectArchive):
        mgr = managers.for_user(request.state.user)
        if body.archived and any(
            session["project_id"] == project_id and session["running"]
            for session in mgr.list_sessions()
        ):
            raise HTTPException(status_code=409, detail="项目仍有任务正在运行，请先停止后再归档")
        try:
            return mgr.projects.set_archived(project_id, body.archived)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/sessions/{session_id}/artifacts")
    def list_artifacts(request: Request, session_id: str):
        mgr = managers.for_user(request.state.user)
        root = mgr.workspace(session_id)
        versions = mgr.changes(session_id).list(include_diff=False)
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
        return {"artifacts": artifacts}

    @router.get("/sessions/{session_id}/delivery")
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

    return router
