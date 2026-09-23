"""Durable, bounded snapshots for approved file tools; never replay shell side effects."""

from __future__ import annotations

import base64
import binascii
import difflib
import json
import os
import stat
import tempfile
import threading
import uuid
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path

from agent_shell.types import ToolCall, ToolResult

_LOCK = threading.RLock()
_LIMIT = 2_000_000
_MAX_RECORD_BYTES = 6_000_000
_STATES = {
    "pending", "ready", "failed", "unchanged", "snapshot_failed", "restoring", "restored"
}


class ChangeJournal:
    def __init__(self, directory: Path, root: Path):
        self.directory = directory
        self.root = root.resolve()
        self._issues: list[dict[str, str]] = []

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
                # The file tool has already run at this point. Preserve its real outcome
                # instead of reporting a false failure that may cause the model to retry.
                record.update(state="snapshot_failed", error=str(exc))
                with suppress(OSError):
                    self._save(record)
                return ToolResult(
                    content=f"{result.content}\n注意：文件已处理，但版本快照未保存：{exc}",
                    is_error=result.is_error,
                )
            return result

    @staticmethod
    def _encode(data):
        return None if data is None else base64.b64encode(data).decode("ascii")

    @staticmethod
    def _decode(data):
        return None if data is None else base64.b64decode(data, validate=True)

    def _save(self, record):
        target = self.directory / f"{record['id']}.json"
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            fd, temp_path = tempfile.mkstemp(dir=self.directory, suffix=".tmp")
            temporary = Path(temp_path)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            temporary = None
        finally:
            if temporary is not None:
                with suppress(OSError):
                    temporary.unlink(missing_ok=True)

    def _load(self, file: Path):
        with file.open("rb") as stream:
            raw = stream.read(_MAX_RECORD_BYTES + 1)
        if len(raw) > _MAX_RECORD_BYTES:
            raise ValueError("版本记录超过大小上限")
        record = json.loads(raw.decode("utf-8"))
        if (
            not isinstance(record, dict)
            or len(file.stem) != 32
            or any(char not in "0123456789abcdef" for char in file.stem)
            or record.get("id") != file.stem
        ):
            raise ValueError("版本 ID 与文件名不一致")
        if record.get("state") not in _STATES or record.get("tool") not in {"write", "edit"}:
            raise ValueError("版本状态或工具无效")
        if not isinstance(record.get("path"), str) or not record["path"].strip():
            raise ValueError("版本路径无效")
        if not isinstance(record.get("root"), str) or Path(record["root"]).resolve() != self.root:
            raise ValueError("版本不属于当前工作区")
        if self._target(record["path"]) == self.root:
            raise ValueError("版本路径无效")
        if not isinstance(record.get("created_at"), str):
            raise ValueError("版本时间无效")
        datetime.fromisoformat(record["created_at"])
        for key in ("before", "after"):
            value = record.get(key)
            if value is not None:
                if not isinstance(value, str) or len(value) > (_LIMIT * 4 // 3) + 8:
                    raise ValueError("版本快照无效")
                self._decode(value)
        return record

    def issues(self) -> list[dict[str, str]]:
        return [dict(issue) for issue in self._issues]

    def _reconcile_restore(self, record):
        """Resolve a restore interrupted between the file change and journal commit."""
        try:
            current = self._read(self._target(record["path"]))
        except (OSError, ValueError):
            return False
        if current == self._decode(record["before"]):
            next_state = "restored"
        elif current == self._decode(record["after"]):
            next_state = "ready"
        else:
            return False
        record["state"] = next_state
        try:
            self._save(record)
        except OSError:
            record["state"] = "restoring"
            return False
        return True

    def list(self, include_diff=True):
        with _LOCK:
            records = []
            issues = []
            for file in self.directory.glob("*.json"):
                try:
                    record = self._load(file)
                except (OSError, ValueError, TypeError, binascii.Error, UnicodeError):
                    issues.append({"file": file.name, "message": "版本记录损坏，已隔离"})
                    continue
                if record["state"] == "unchanged":
                    continue
                if record["state"] == "restoring" and not self._reconcile_restore(record):
                    issues.append({"file": file.name, "message": "恢复状态待核对，请检查当前文件"})
                public = {k: v for k, v in record.items() if k not in {"before", "after", "root"}}
                if not include_diff:
                    records.append(public)
                    continue
                public["kind"] = "created" if record["before"] is None else "modified"
                if record["after"] is None and record["state"] in {
                    "pending",
                    "snapshot_failed",
                }:
                    public["diff"] = ""
                    records.append(public)
                    continue
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
                records.append(public)
            self._issues = issues
            return sorted(records, key=lambda r: r["created_at"], reverse=True)

    def restore(self, change_id: str):
        if len(change_id) != 32 or any(c not in "0123456789abcdef" for c in change_id):
            raise ValueError("版本 ID 无效")
        with _LOCK:
            try:
                record = self._load(self.directory / f"{change_id}.json")
            except (OSError, ValueError, TypeError, binascii.Error, UnicodeError) as exc:
                raise ValueError("版本记录不存在或已损坏，无法恢复") from exc
            if record["state"] != "ready":
                raise ValueError("该版本不能在当前工作区恢复")
            path = self._target(record["path"])
            if self._read(path) != self._decode(record["after"]):
                raise ValueError("文件已被后续修改；为保留这些修改，未执行恢复")
            before = self._decode(record["before"])
            record["state"] = "restoring"
            self._save(record)
            try:
                if before is None:
                    path.unlink(missing_ok=True)
                else:
                    self._replace_bytes(path, before)
            except OSError:
                record["state"] = "ready"
                with suppress(OSError):
                    self._save(record)
                raise
            record["state"] = "restored"
            try:
                self._save(record)
            except OSError as exc:
                return {
                    "restored": True,
                    "path": record["path"],
                    "warning": f"文件已恢复，但版本状态暂未写入；重新打开后会核对：{exc}",
                }
            return {"restored": True, "path": record["path"]}

    @staticmethod
    def _replace_bytes(path: Path, content: bytes) -> None:
        fd, temp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        temporary = Path(temp_path)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            if path.exists():
                os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
            os.replace(temporary, path)
        finally:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)
