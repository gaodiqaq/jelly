"""Durable, bounded snapshots for approved file tools; never replay shell side effects."""

from __future__ import annotations

import base64
import difflib
import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from agent_shell.types import ToolCall, ToolResult

_LOCK = threading.RLock()
_LIMIT = 2_000_000


class ChangeJournal:
    def __init__(self, directory: Path, root: Path):
        self.directory = directory
        self.root = root.resolve()

    def _target(self, value: str) -> Path:
        path = Path(value).expanduser()
        path = (path if path.is_absolute() else self.root / path).resolve()
        if not path.is_relative_to(self.root) or path.is_relative_to(self.directory.resolve()):
            raise ValueError("文件不在可记录的工作区内")
        return path

    @staticmethod
    def _read(path: Path) -> bytes | None:
        if not path.exists():
            return None
        if path.stat().st_size > _LIMIT:
            raise ValueError("文件超过 2 MB，无法建立版本快照")
        return path.read_bytes()

    def execute(self, call: ToolCall, invoke) -> ToolResult:
        if call.name not in {"write", "edit"}:
            return invoke()
        with _LOCK:
            try:
                path = self._target(call.arguments.get("path", ""))
                before = self._read(path)
                self.directory.mkdir(parents=True, exist_ok=True)
                record = {
                    "id": uuid.uuid4().hex,
                    "path": path.relative_to(self.root).as_posix(),
                    "root": str(self.root),
                    "tool": call.name,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "before": self._encode(before),
                    "after": None,
                    "state": "pending",
                }
                self._save(record)
            except (OSError, ValueError, TypeError) as exc:
                return ToolResult(content=f"无法建立文件快照，修改未执行：{exc}", is_error=True)
            result = invoke()
            try:
                after = self._read(path)
                record.update(
                    after=self._encode(after), state="failed" if result.is_error else "ready"
                )
                if after == before:
                    record["state"] = "unchanged"
                self._save(record)
            except (OSError, ValueError) as exc:
                return ToolResult(content=f"{result.content}\n版本记录未完成：{exc}", is_error=True)
            return result

    @staticmethod
    def _encode(data):
        return None if data is None else base64.b64encode(data).decode("ascii")

    @staticmethod
    def _decode(data):
        return None if data is None else base64.b64decode(data)

    def _save(self, record):
        target = self.directory / f"{record['id']}.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        temporary.replace(target)

    def list(self):
        with _LOCK:
            records = []
            for file in self.directory.glob("*.json"):
                record = json.loads(file.read_text(encoding="utf-8"))
                if record["state"] == "unchanged":
                    continue
                public = {k: v for k, v in record.items() if k not in {"before", "after", "root"}}
                before = (self._decode(record["before"]) or b"").decode("utf-8", errors="replace")
                after = (self._decode(record["after"]) or b"").decode("utf-8", errors="replace")
                public["diff"] = "".join(
                    difflib.unified_diff(
                        before.splitlines(True),
                        after.splitlines(True),
                        fromfile="修改前/" + record["path"],
                        tofile="修改后/" + record["path"],
                    )
                )[:100_000]
                public["kind"] = "created" if record["before"] is None else "modified"
                records.append(public)
            return sorted(records, key=lambda r: r["created_at"], reverse=True)

    def restore(self, change_id: str):
        if len(change_id) != 32 or any(c not in "0123456789abcdef" for c in change_id):
            raise ValueError("版本 ID 无效")
        with _LOCK:
            record = json.loads((self.directory / f"{change_id}.json").read_text(encoding="utf-8"))
            if record["state"] != "ready" or Path(record["root"]).resolve() != self.root:
                raise ValueError("该版本不能在当前工作区恢复")
            path = self._target(record["path"])
            if self._read(path) != self._decode(record["after"]):
                raise ValueError("文件已被后续修改；为保留这些修改，未执行恢复")
            before = self._decode(record["before"])
            if before is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(before)
            record["state"] = "restored"
            self._save(record)
            return {"restored": True, "path": record["path"]}
