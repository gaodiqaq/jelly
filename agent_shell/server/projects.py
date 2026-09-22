"""Per-user project catalogue. A project's root is immutable after creation."""

from __future__ import annotations

import json
import threading
import uuid
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


class ProjectStore:
    def __init__(self, directory: Path):
        self.directory = directory
        self._lock = threading.RLock()

    def _path(self, project_id: str) -> Path:
        if len(project_id) != 32 or any(c not in "0123456789abcdef" for c in project_id):
            raise SessionError("项目不存在")
        return self.directory / f"{project_id}.json"

    def get(self, project_id: str) -> dict:
        with self._lock:
            path = self._path(project_id)
            if not path.is_file():
                raise SessionError("项目不存在")
            return json.loads(path.read_text(encoding="utf-8"))

    def list(self) -> list[dict]:
        with self._lock:
            return sorted(
                [json.loads(p.read_text(encoding="utf-8")) for p in self.directory.glob("*.json")],
                key=lambda p: p["created_at"],
            )

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
                }
            project.update(
                name=body.name.strip(), model=body.model.strip(), permission=body.permission
            )
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self._path(project["id"])
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
            temporary.replace(path)
            return project
