"""Per-user project catalogue. A project's root is immutable after creation."""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import uuid
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from agent_shell.errors import SessionError


class ProjectSettings(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    model: str = Field(default="", max_length=128)
    permission: Literal["ask", "auto", "readonly", "deny"] = "ask"


class ProjectCreate(ProjectSettings):
    cwd: str = Field(min_length=1, max_length=4096)


class ManagedProjectCreate(ProjectSettings):
    """Create a project and its own directory under Jelly's managed root."""


class ProjectArchive(BaseModel):
    archived: bool


class ProjectRecord(ProjectSettings):
    id: str = Field(pattern=r"^[0-9a-f]{32}$")
    cwd: str = Field(min_length=1, max_length=4096)
    created_at: str = Field(min_length=1)
    updated_at: str = ""
    archived_at: str | None = None


class ProjectStore:
    def __init__(self, directory: Path):
        self.directory = directory
        self._lock = threading.RLock()
        self._issues: list[dict[str, str]] = []

    def _path(self, project_id: str) -> Path:
        if len(project_id) != 32 or any(c not in "0123456789abcdef" for c in project_id):
            raise SessionError("项目不存在")
        return self.directory / f"{project_id}.json"

    def get(self, project_id: str) -> dict:
        with self._lock:
            path = self._path(project_id)
            if not path.is_file():
                raise SessionError("项目不存在")
            try:
                project = self._normalise(json.loads(path.read_text(encoding="utf-8")))
                if project["id"] != project_id:
                    raise ValueError("项目 ID 与文件名不一致")
                return project
            except (OSError, ValueError, TypeError) as exc:
                raise SessionError(f"项目记录损坏: {path.name}") from exc

    @staticmethod
    def _normalise(project: dict) -> dict:
        """Add fields introduced after the first project schema without a migration step."""
        normalised = dict(project)
        normalised.setdefault("updated_at", normalised.get("created_at", ""))
        normalised.setdefault("archived_at", None)
        return ProjectRecord.model_validate(normalised).model_dump()

    def list(self) -> list[dict]:
        with self._lock:
            projects = []
            issues = []
            for path in self.directory.glob("*.json"):
                try:
                    project = self._normalise(json.loads(path.read_text(encoding="utf-8")))
                    if project["id"] != path.stem:
                        raise ValueError("项目 ID 与文件名不一致")
                    projects.append(project)
                except (OSError, ValueError, TypeError):
                    issues.append(
                        {"file": path.name, "message": "项目记录损坏，已从工作台隔离"}
                    )
            self._issues = issues
            return sorted(projects, key=lambda project: project["created_at"])

    def issues(self) -> list[dict[str, str]]:
        with self._lock:
            return [dict(issue) for issue in self._issues]

    def _write(self, project: dict) -> dict:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(project["id"])
        temporary: Path | None = None
        try:
            fd, temp_path = tempfile.mkstemp(dir=self.directory, suffix=".tmp")
            temporary = Path(temp_path)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(json.dumps(project, ensure_ascii=False))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            temporary = None
        finally:
            if temporary is not None:
                with suppress(OSError):
                    temporary.unlink(missing_ok=True)
        return project

    @staticmethod
    def directory_name(name: str) -> str:
        """Keep display names human-readable while avoiding unsafe platform filenames."""
        cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", name.strip())
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")[:64].rstrip(" .")
        if not cleaned:
            raise ValueError("请填写可用的项目名称")
        if re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", cleaned, re.I):
            cleaned = f"{cleaned[:60]}-项目"
        return cleaned

    def create_managed(self, body: ManagedProjectCreate, root: Path) -> dict:
        """Create one new workspace, rolling back an empty directory on record failure."""
        with self._lock:
            directory = root.expanduser().resolve()
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            target = directory / self.directory_name(body.name)
            try:
                target.mkdir(mode=0o700)
            except FileExistsError as exc:
                raise ValueError("同名工作目录已存在，请修改项目名称或绑定现有目录") from exc
            try:
                return self.save(
                    ProjectCreate(
                        name=body.name,
                        cwd=str(target),
                        model=body.model,
                        permission=body.permission,
                    )
                )
            except Exception:
                with suppress(OSError):
                    target.rmdir()
                raise

    def save(self, body: ProjectSettings, project_id: str | None = None) -> dict:
        with self._lock:
            if not body.name.strip():
                raise ValueError("请填写项目名称")
            if project_id:
                project = self.get(project_id)
            else:
                root = Path(body.cwd).expanduser()
                if not root.is_absolute() or not root.is_dir():
                    raise ValueError("请选择已存在的目录，并填写绝对路径")
                project = {
                    "id": uuid.uuid4().hex,
                    "cwd": str(root.resolve()),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "archived_at": None,
                }
            now = datetime.now(timezone.utc).isoformat()
            project.update(
                name=body.name.strip(),
                model=body.model.strip(),
                permission=body.permission,
                updated_at=now,
            )
            return self._write(project)

    def set_archived(self, project_id: str, archived: bool) -> dict:
        with self._lock:
            project = self.get(project_id)
            now = datetime.now(timezone.utc).isoformat()
            project["archived_at"] = now if archived else None
            project["updated_at"] = now
            return self._write(project)
