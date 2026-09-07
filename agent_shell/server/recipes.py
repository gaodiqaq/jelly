"""User-reviewed reusable instructions, stored separately from conversation history."""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

_LOCK = threading.RLock()


class RecipeInput(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    instruction: str = Field(min_length=1, max_length=20_000)


class RecipeStore:
    def __init__(self, directory: Path):
        self.directory = directory

    def _path(self, recipe_id):
        if len(recipe_id) != 32 or any(c not in "0123456789abcdef" for c in recipe_id):
            raise ValueError("配方 ID 无效")
        return self.directory / f"{recipe_id}.json"

    def list(self):
        with _LOCK:
            return sorted(
                [json.loads(p.read_text(encoding="utf-8")) for p in self.directory.glob("*.json")],
                key=lambda r: r["updated_at"],
                reverse=True,
            )

    def save(self, body: RecipeInput, recipe_id=None):
        if not body.title.strip() or not body.instruction.strip():
            raise ValueError("名称和工作步骤不能为空")
        with _LOCK:
            path = self._path(recipe_id or uuid.uuid4().hex)
            old = json.loads(path.read_text(encoding="utf-8")) if recipe_id else None
            record = {
                "id": path.stem,
                "title": body.title.strip(),
                "instruction": body.instruction.strip(),
                "version": old["version"] + 1 if old else 1,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            self.directory.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
            temporary.replace(path)
            return record

    def delete(self, recipe_id):
        with _LOCK:
            self._path(recipe_id).unlink()
