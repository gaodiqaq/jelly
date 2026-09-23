"""Portable task deliverables with bounded file reads and provenance metadata."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import zipfile
from collections import Counter
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path

from agent_shell.errors import SessionError

_MAX_FILES = 500
_MAX_FILE_BYTES = 25_000_000
_MAX_TOTAL_BYTES = 150_000_000
_PRIVATE_NAMES = {
    ".git", ".ssh", ".aws", ".npmrc", ".pypirc", "id_rsa", "id_ed25519",
    "credentials.json", "secrets.json",
}
_PRIVATE_SUFFIXES = {".pem", ".p12", ".pfx", ".key"}


def _transcript(session) -> bytes:
    lines = [
        f"# {session.title or '未命名任务'}",
        "",
        "Jelly Studio 任务交付记录。工具输出和系统提示词未包含在此文档中。",
        "",
        "## 对话",
        "",
    ]
    for message in session.messages:
        if message.role == "user":
            lines.extend(("### 你", "", message.content, ""))
        elif message.role == "assistant" and message.content:
            lines.extend(("### 果冻", "", message.content, ""))
    return "\n".join(lines).encode("utf-8")


def create_task_bundle(manager, session_id: str) -> Path:
    """Build a private, temporary ZIP of one task's transcript and current artifacts."""
    session = manager.get_session(session_id)
    root = session.cwd.resolve()
    if not root.is_dir():
        raise ValueError("任务工作目录不可用，无法导出成果")
    versions = manager.changes(session_id).list(include_diff=False)
    paths = sorted(
        {
            item["path"]
            for item in versions
            if item["state"] in {"ready", "restored", "snapshot_failed"}
        }
    )
    if len(paths) > _MAX_FILES:
        raise ValueError(f"成果文件超过 {_MAX_FILES} 个，请分批整理后导出")

    transcript = _transcript(session)
    if len(transcript) > _MAX_TOTAL_BYTES:
        raise ValueError("对话记录过大，无法生成交付包")
    try:
        project = manager.project_for(session)
    except SessionError:
        project = None
    manifest = {
        "format": "jelly-task-delivery",
        "version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "task_id": session_id,
        "task_title": session.title,
        "project_name": project["name"] if project else None,
        "files": [],
        "omitted": [],
    }
    provenance = [
        {key: item.get(key) for key in ("id", "path", "tool", "state", "created_at")}
        for item in versions
    ]
    provenance_data = json.dumps(provenance, ensure_ascii=False, indent=2).encode("utf-8")
    if len(transcript) + len(provenance_data) > _MAX_TOTAL_BYTES:
        raise ValueError("任务记录过大，无法生成交付包")
    version_counts = Counter(
        item["path"]
        for item in versions
        if item["state"] in {"ready", "restored", "snapshot_failed"}
    )
    fd, temp_name = tempfile.mkstemp(prefix="jelly-delivery-", suffix=".zip")
    temporary = Path(temp_name)
    ready = False
    try:
        with os.fdopen(fd, "wb") as stream, zipfile.ZipFile(
            stream, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
        ) as archive:
            archive.writestr("task.md", transcript)
            archive.writestr("provenance.json", provenance_data)
            total = len(transcript) + len(provenance_data)
            for relative in paths:
                source = Path(relative)
                if source.is_absolute() or not source.parts or any(
                    part in {"", ".", ".."} or "\\" in part or ":" in part
                    or any(ord(char) < 32 for char in part)
                    for part in source.parts
                ):
                    manifest["omitted"].append({"path": relative, "reason": "路径不安全"})
                    continue
                if any(
                    part.lower() in _PRIVATE_NAMES or part.lower().startswith(".env")
                    or Path(part).suffix.lower() in _PRIVATE_SUFFIXES
                    for part in source.parts
                ):
                    manifest["omitted"].append({"path": relative, "reason": "常见敏感文件"})
                    continue
                try:
                    target = (root / source).resolve()
                except OSError:
                    manifest["omitted"].append({"path": relative, "reason": "无法读取文件"})
                    continue
                if not target.is_relative_to(root) or not target.is_file():
                    manifest["omitted"].append(
                        {"path": relative, "reason": "文件不存在或越出工作区"}
                    )
                    continue
                try:
                    with target.open("rb") as file:
                        content = file.read(_MAX_FILE_BYTES + 1)
                except OSError:
                    manifest["omitted"].append({"path": relative, "reason": "无法读取文件"})
                    continue
                if len(content) > _MAX_FILE_BYTES or total + len(content) > _MAX_TOTAL_BYTES:
                    raise ValueError("成果文件超过交付包大小上限（单文件 25 MB、总计 150 MB）")
                total += len(content)
                safe_path = source.as_posix()
                archive.writestr(f"artifacts/{safe_path}", content)
                manifest["files"].append(
                    {
                        "path": safe_path,
                        "bytes": len(content),
                        "sha256": hashlib.sha256(content).hexdigest(),
                        "version_count": version_counts[relative],
                    }
                )
            manifest_data = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
            if total + len(manifest_data) > _MAX_TOTAL_BYTES:
                raise ValueError("交付包超过 150 MB 上限")
            archive.writestr("manifest.json", manifest_data)
        ready = True
        return temporary
    finally:
        if not ready:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)
