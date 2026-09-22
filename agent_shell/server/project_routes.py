"""Project settings and artifact discovery, scoped to the authenticated manager."""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request

from agent_shell.server.projects import ProjectCreate, ProjectSettings


def project_router(managers, auth):
    router = APIRouter(prefix="/api", dependencies=[Depends(auth)])

    @router.get("/projects")
    def list_projects(request: Request):
        mgr = managers.for_user(request.state.user)
        sessions = mgr.list_sessions()
        return {
            "projects": [
                {**p, "task_count": sum(s["project_id"] == p["id"] for s in sessions)}
                for p in mgr.projects.list()
            ]
        }

    @router.post("/projects")
    def create_project(request: Request, body: ProjectCreate):
        try:
            return managers.for_user(request.state.user).projects.save(body)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.put("/projects/{project_id}")
    def update_project(request: Request, project_id: str, body: ProjectSettings):
        try:
            return managers.for_user(request.state.user).projects.save(body, project_id)
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

    return router
