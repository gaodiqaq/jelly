"""Bounded, portable project backups with verified, non-overwriting import."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import shutil
import stat
import struct
import tempfile
import unicodedata
import uuid
import zipfile
from contextlib import contextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from agent_shell.core.session import Session
from agent_shell.errors import SessionError
from agent_shell.llm.prompts import build_system_prompt
from agent_shell.server.projects import ProjectRecord, ProjectStore
from agent_shell.types import SystemMessage

_MAX_FILES = 20_000
_MAX_TOTAL_BYTES = 2_000_000_000
MAX_BACKUP_UPLOAD_BYTES = 2_100_000_000
_MAX_METADATA_BYTES = 25_000_000
_MAX_MANIFEST_BYTES = 8_000_000
_MAX_CENTRAL_DIRECTORY_BYTES = 32_000_000
_MAX_ARCHIVE_PATH_BYTES = 1024
_COMPRESSION_RATIO_THRESHOLD = 50_000_000
_MAX_COMPRESSION_RATIO = 100
_MIN_FREE_RESERVE = 64_000_000
_TRANSACTION_PREFIX = ".jelly-import-transaction-"
_TRANSACTION_SENTINEL = ".jelly-import-owner-"
_STAGE_PREFIX = ".jelly-import-stage-"
_STAGE_SENTINEL = ".jelly-stage-owner"
_CHUNK = 1024 * 1024


def _sync_directory(path: Path) -> None:
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _portable_key(name: str) -> tuple[str, ...]:
    return tuple(
        unicodedata.normalize("NFC", part).casefold()
        for part in PurePosixPath(name).parts
    )


def _file_identity(info: os.stat_result) -> tuple[int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _stat_is_link_like(info: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return stat.S_ISLNK(info.st_mode) or bool(
        reparse_flag and getattr(info, "st_file_attributes", 0) & reparse_flag
    )


def _is_link_like(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return True
    return _stat_is_link_like(info)


def _check_directory(info: os.stat_result, expected: os.stat_result, path: Path) -> None:
    if (
        _stat_is_link_like(info)
        or not stat.S_ISDIR(info.st_mode)
        or (info.st_dev, info.st_ino) != (expected.st_dev, expected.st_ino)
    ):
        raise ValueError(f"目录在备份期间发生变化：{path}")


def _windows_backup_fd(path: Path, *, directory: bool) -> int:
    """Open the path itself without following its final reparse point."""
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    read_access = (0x1 | 0x80) if directory else 0x80000000
    share_read = 0x1
    open_existing = 3
    open_flags = 0x00200000 | (0x02000000 if directory else 0)
    handle = create_file(
        str(path), read_access, share_read, None, open_existing, open_flags, None,
    )
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY | os.O_NOINHERIT)
    except BaseException:
        close_handle(handle)
        raise


def _windows_directory_fd(path: Path) -> int:
    """Hold a directory without allowing replacement or reparse-point changes."""
    return _windows_backup_fd(path, directory=True)


def _windows_source_fd(path: Path) -> int:
    return _windows_backup_fd(path, directory=False)


def _open_backup_file(source: Path, directory_fd: int | None = None):
    if os.name == "nt":
        descriptor = _windows_source_fd(source)
    else:
        # A regular file can become a FIFO between stat and open. Nonblocking
        # mode lets fstat reject it without waiting for a writer indefinitely.
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        descriptor = os.open(
            source if directory_fd is None else source.name, flags, dir_fd=directory_fd,
        )
    try:
        opened = os.fstat(descriptor)
        if _stat_is_link_like(opened) or not stat.S_ISREG(opened.st_mode):
            raise ValueError(f"文件在备份期间发生变化：{source.name}")
        return os.fdopen(descriptor, "rb")
    except BaseException:
        with suppress(OSError):
            os.close(descriptor)
        raise


@contextmanager
def _workspace_directory(root: Path, path: Path, identities: dict[Path, os.stat_result]):
    """Open from a verified root, keeping every component safe until the read ends."""
    descriptors = []
    workspace_chain = [root]
    current = root
    for component in path.relative_to(root).parts:
        current /= component
        workspace_chain.append(current)
    try:
        if os.name == "nt":
            # Pin absolute ancestors too: locking only ``root`` would still let
            # a parent of the root redirect subsequent absolute-path opens.
            chain = [*reversed(path.parents), path]
            for directory in chain:
                expected = identities.get(directory)
                if expected is None:
                    expected = directory.lstat()
                descriptor = _windows_directory_fd(directory)
                descriptors.append(descriptor)
                _check_directory(os.fstat(descriptor), expected, directory)
            yield None
        else:
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            descriptor = os.open(root, flags)
            descriptors.append(descriptor)
            _check_directory(os.fstat(descriptor), identities[root], root)
            current = root
            for component in path.relative_to(root).parts:
                current /= component
                descriptor = os.open(component, flags, dir_fd=descriptor)
                descriptors.append(descriptor)
                _check_directory(os.fstat(descriptor), identities[current], current)
            yield descriptor
        for directory in workspace_chain:
            _check_directory(directory.lstat(), identities[directory], directory)
    except OSError as exc:
        raise ValueError(f"目录在备份期间发生变化，无法安全读取：{path}") from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


class _WorkspaceFiles(list):
    """Keep enumeration identities with list entries so later stats cannot reset them."""

    def __init__(self, root: Path):
        super().__init__()
        info = root.lstat()
        _check_directory(info, info, root)
        self.directories = {root: info}
        self.identities = {}


@contextmanager
def _backup_source(source: Path, root: Path, workspace_files: _WorkspaceFiles):
    if source not in workspace_files.identities:
        with _open_backup_file(source) as stream:
            yield stream
        return
    with (
        _workspace_directory(root, source.parent, workspace_files.directories) as directory_fd,
        _open_backup_file(source, directory_fd) as stream,
    ):
        opened = os.fstat(stream.fileno())
        if (
            _stat_is_link_like(opened)
            or not stat.S_ISREG(opened.st_mode)
            or _file_identity(opened) != _file_identity(workspace_files.identities[source])
        ):
            raise ValueError(f"文件在备份期间发生变化：{source.name}")
        yield stream


def _archive_path(name: str) -> PurePosixPath:
    if (
        not isinstance(name, str)
        or not name
        or "\\" in name
        or ":" in name
        or len(name.encode("utf-8")) > _MAX_ARCHIVE_PATH_BYTES
    ):
        raise ValueError("备份包含不安全的文件路径")
    path = PurePosixPath(name)
    if path.is_absolute() or any(
        part in {"", ".", ".."} or any(ord(char) < 32 for char in part)
        for part in name.split("/")
    ):
        raise ValueError("备份包含不安全的文件路径")
    for part in path.parts:
        if (
            len(part.encode("utf-8")) > 255
            or any(char in '<>"|?*' for char in part)
            or part.rstrip(" .") != part
            or re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", part, re.I)
        ):
            raise ValueError("备份包含不兼容的文件名")
    return path


def _validate_workspace_layout(directories: list[str], files: list[str]) -> None:
    directory_keys = [_portable_key(name) for name in directories]
    file_keys = [_portable_key(name) for name in files]
    if len(directory_keys) != len(set(directory_keys)) or len(file_keys) != len(set(file_keys)):
        raise ValueError("工作目录包含跨平台会发生冲突的文件名")
    directories_set = set(directory_keys)
    files_set = set(file_keys)
    if directories_set & files_set:
        raise ValueError("工作目录包含文件与目录同名冲突")
    for key in files_set:
        if any(key[:index] in files_set for index in range(1, len(key))):
            raise ValueError("工作目录包含无法安全恢复的路径层级")
    for key in directories_set:
        if any(key[:index] in files_set for index in range(1, len(key))):
            raise ValueError("工作目录包含无法安全恢复的路径层级")


def _workspace_entries(root: Path):
    directories = []
    files = _WorkspaceFiles(root)
    pending = [root]
    while pending:
        current = pending.pop()
        with (
            _workspace_directory(root, current, files.directories) as directory_fd,
            os.scandir(current if directory_fd is None else directory_fd) as entries,
        ):
            for entry in entries:
                path = current / entry.name
                # Windows DirEntry metadata can omit the file ID; lstat
                # while the parent handles are pinned records the real ID.
                info = path.lstat() if os.name == "nt" else entry.stat(follow_symlinks=False)
                if _stat_is_link_like(info):
                    raise ValueError(f"工作目录包含符号链接，无法安全备份：{path}")
                relative = path.relative_to(root).as_posix()
                _archive_path(f"workspace/{relative}")
                if stat.S_ISDIR(info.st_mode):
                    directories.append(relative)
                    files.directories[path] = info
                    pending.append(path)
                elif stat.S_ISREG(info.st_mode):
                    files.append((f"workspace/{relative}", path))
                    files.identities[path] = info
                else:
                    raise ValueError(f"工作目录包含非常规文件，无法安全备份：{path}")
                if len(files) + len(directories) > _MAX_FILES:
                    raise ValueError(f"项目文件超过 {_MAX_FILES} 个，无法生成单个备份")
    directories.sort()
    files.sort()
    _validate_workspace_layout(
        directories, [name.removeprefix("workspace/") for name, _path in files]
    )
    return directories, files


def _metadata_entries(manager, project_id: str, root: Path):
    entries = []
    all_sessions = manager.list_sessions()
    listed = {meta["session_id"] for meta in all_sessions}
    if any(path.stem not in listed for path in manager._session_dir.glob("*.jsonl")):
        raise ValueError("存在无法识别的任务记录，无法保证项目备份完整")
    if sum(meta["project_id"] == project_id for meta in all_sessions) > 2000:
        raise ValueError("项目任务超过 2000 个，无法生成单个备份")
    for meta in all_sessions:
        if meta["project_id"] != project_id:
            continue
        sid = meta["session_id"]
        session_path = Session.path_for(manager._session_dir, sid)
        session = Session.resume(manager._session_dir, sid)
        if session.project_id != project_id or session.cwd.resolve() != root:
            raise ValueError("项目任务记录与工作目录不一致，无法生成完整备份")
        entries.append((f"sessions/{sid}.jsonl", session_path))
        changes = manager._session_dir / "changes" / sid
        for path in sorted(changes.glob("*.json")):
            entries.append((f"changes/{sid}/{path.name}", path))
        run = manager._session_dir / "runs" / f"{sid}.json"
        if run.is_file():
            entries.append((f"runs/{sid}.json", run))
    return sorted(entries)


def create_project_backup(manager, project_id: str) -> Path:
    with manager._lifecycle_lock:
        return _create_project_backup(manager, project_id)


def _create_project_backup(manager, project_id: str) -> Path:
    project = manager.projects.get(project_id)
    root = Path(project["cwd"]).resolve()
    if not root.is_dir():
        raise ValueError("项目工作目录不存在，无法备份")
    if any(manager.workspace(sid) == root for sid in manager._running):
        raise ValueError("项目仍有任务在运行，请完成或停止后再备份")
    directories, workspace_files = _workspace_entries(root)
    entries = workspace_files + _metadata_entries(manager, project_id, root)
    if len(entries) + len(directories) > _MAX_FILES:
        raise ValueError(f"项目文件超过 {_MAX_FILES} 个，无法生成单个备份")
    prepared = []
    planned_total = 0
    for archive_name, source in entries:
        _archive_path(archive_name)
        info = (
            workspace_files.identities[source]
            if source in workspace_files.identities else source.lstat()
        )
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"无法备份非常规文件：{source}")
        if not archive_name.startswith("workspace/") and info.st_size > _MAX_METADATA_BYTES:
            raise ValueError(f"项目记录过大，无法备份：{source.name}")
        planned_total += info.st_size
        if planned_total > _MAX_TOTAL_BYTES:
            raise ValueError("项目超过 2 GB 备份上限")
        prepared.append((archive_name, source, info))
    store_without_compression = planned_total > _COMPRESSION_RATIO_THRESHOLD
    fd, name = tempfile.mkstemp(prefix="jelly-project-", suffix=".zip")
    output = Path(name)
    ready = False
    try:
        with os.fdopen(fd, "wb") as stream, zipfile.ZipFile(
            stream, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6,
            allowZip64=True,
        ) as archive:
            manifest = {
                "format": "jelly-project-backup",
                "version": 1,
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "project": project,
                "directories": directories,
                "files": [],
            }
            total = 0
            for archive_name, source, planned_info in prepared:
                info_before = source.lstat()
                if _file_identity(info_before) != _file_identity(planned_info):
                    raise ValueError(f"文件在备份期间发生变化：{source.name}")
                total += info_before.st_size
                if total > _MAX_TOTAL_BYTES:
                    raise ValueError("项目超过 2 GB 备份上限")
                digest = hashlib.sha256()
                count = 0
                zip_info = zipfile.ZipInfo(archive_name)
                zip_info.compress_type = (
                    zipfile.ZIP_STORED if store_without_compression else zipfile.ZIP_DEFLATED
                )
                zip_info.external_attr = (stat.S_IFREG | (info_before.st_mode & 0o777)) << 16
                with (
                    _backup_source(source, root, workspace_files) as src,
                    archive.open(zip_info, "w") as dst,
                ):
                    opened_before = os.fstat(src.fileno())
                    if _file_identity(opened_before) != _file_identity(info_before):
                        raise ValueError(f"文件在备份期间发生变化：{source.name}")
                    while chunk := src.read(_CHUNK):
                        count += len(chunk)
                        if count > info_before.st_size:
                            raise ValueError(f"文件在备份期间发生变化：{source.name}")
                        digest.update(chunk)
                        dst.write(chunk)
                    opened_after = os.fstat(src.fileno())
                info_after = source.lstat()
                if (
                    count != info_before.st_size
                    or _file_identity(opened_after) != _file_identity(opened_before)
                    or _file_identity(info_after) != _file_identity(info_before)
                ):
                    raise ValueError(f"文件在备份期间发生变化：{source.name}")
                manifest["files"].append({
                    "path": archive_name,
                    "size": count,
                    "sha256": digest.hexdigest(),
                    "mode": info_before.st_mode & 0o777,
                })
            # Empty directories have no file read to trigger ancestor checks.
            for directory, expected in workspace_files.directories.items():
                _check_directory(directory.lstat(), expected, directory)
            encoded = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
            if len(encoded) > _MAX_MANIFEST_BYTES:
                raise ValueError("项目备份清单过大")
            archive.writestr("manifest.json", encoded)
        ready = True
        return output
    finally:
        if not ready:
            with suppress(OSError):
                output.unlink(missing_ok=True)


def _preflight_zip(path: Path) -> None:
    """Bound ZIP directory parsing before ``zipfile`` allocates one object per entry."""
    size = path.stat().st_size
    if size > MAX_BACKUP_UPLOAD_BYTES:
        raise ValueError("备份文件超过上传上限")
    if size < 22:
        raise ValueError("不是有效的 ZIP 项目备份")
    with path.open("rb") as stream:
        tail_size = min(size, 22 + 65_535 + 20)
        stream.seek(size - tail_size)
        tail = stream.read(tail_size)
        cursor = len(tail)
        eocd = None
        while cursor:
            index = tail.rfind(b"PK\x05\x06", 0, cursor)
            if index < 0:
                break
            if index + 22 <= len(tail):
                values = struct.unpack_from("<4s4H2LH", tail, index)
                if size - tail_size + index + 22 + values[-1] == size:
                    eocd = (size - tail_size + index, values)
                    break
            cursor = index
        if eocd is None:
            raise ValueError("不是有效的 ZIP 项目备份")
        eocd_offset, values = eocd
        _, disk, directory_disk, disk_entries, entry_count, directory_size, offset, _ = values
        boundary = eocd_offset
        if (
            entry_count == 0xFFFF
            or disk_entries == 0xFFFF
            or directory_size == 0xFFFFFFFF
            or offset == 0xFFFFFFFF
        ):
            locator_offset = eocd_offset - 20
            if locator_offset < 0:
                raise ValueError("ZIP64 备份目录损坏")
            stream.seek(locator_offset)
            locator = stream.read(20)
            if len(locator) != 20:
                raise ValueError("ZIP64 备份目录损坏")
            signature, locator_disk, zip64_offset, total_disks = struct.unpack(
                "<4sLQL", locator
            )
            if signature != b"PK\x06\x07" or locator_disk != 0 or total_disks != 1:
                raise ValueError("不支持分卷 ZIP 项目备份")
            stream.seek(zip64_offset)
            record = stream.read(56)
            if len(record) != 56:
                raise ValueError("ZIP64 备份目录损坏")
            (
                signature,
                record_size,
                _made_by,
                _needed,
                disk,
                directory_disk,
                disk_entries,
                entry_count,
                directory_size,
                offset,
            ) = struct.unpack("<4sQ2H2L4Q", record)
            if (
                signature != b"PK\x06\x06"
                or record_size < 44
                or zip64_offset + 12 + record_size != locator_offset
            ):
                raise ValueError("ZIP64 备份目录损坏")
            boundary = zip64_offset
        if disk != 0 or directory_disk != 0 or disk_entries != entry_count:
            raise ValueError("不支持分卷 ZIP 项目备份")
        if entry_count > _MAX_FILES + 1:
            raise ValueError("备份文件数量超过上限")
        if directory_size > _MAX_CENTRAL_DIRECTORY_BYTES:
            raise ValueError("备份目录索引超过上限")
        # ``zipfile`` treats any gap between the declared directory offset and
        # the EOCD as prepended data, then reads the directory immediately
        # before the EOCD.  Requiring adjacency makes this bounded preflight
        # inspect the exact bytes that ``ZipFile`` will parse; otherwise an
        # archive can put a small fake directory at ``offset`` and a much
        # larger real directory before the EOCD.
        if offset > boundary or directory_size != boundary - offset:
            raise ValueError("备份目录索引损坏")

        stream.seek(offset)
        remaining = directory_size
        actual_entries = 0
        while remaining:
            if remaining < 46:
                raise ValueError("备份目录索引损坏")
            header = stream.read(46)
            if len(header) != 46 or header[:4] != b"PK\x01\x02":
                raise ValueError("备份目录索引损坏")
            name_length, extra_length, comment_length = struct.unpack_from("<3H", header, 28)
            record_size = 46 + name_length + extra_length + comment_length
            if (
                not name_length
                or name_length > _MAX_ARCHIVE_PATH_BYTES
                or record_size > remaining
            ):
                raise ValueError("备份目录索引损坏")
            stream.seek(record_size - 46, os.SEEK_CUR)
            remaining -= record_size
            actual_entries += 1
            if actual_entries > _MAX_FILES + 1:
                raise ValueError("备份文件数量超过上限")
        if actual_entries != entry_count:
            raise ValueError("备份目录索引数量不一致")


def _validate_manifest(archive: zipfile.ZipFile):
    infos = archive.infolist()
    if len(infos) > _MAX_FILES + 1:
        raise ValueError("备份文件数量超过上限")
    names = [info.filename for info in infos]
    if len(names) != len({_portable_key(name) for name in names}) or "manifest.json" not in names:
        raise ValueError("备份清单缺失或文件名重复")
    for info in infos:
        _archive_path(info.filename)
        mode = info.external_attr >> 16
        if info.is_dir() or stat.S_ISLNK(mode) or info.flag_bits & 1:
            raise ValueError("备份包含不支持的目录项或符号链接")
        if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
            raise ValueError("备份使用了不支持的压缩方式")
    if archive.getinfo("manifest.json").file_size > _MAX_MANIFEST_BYTES:
        raise ValueError("备份清单过大")
    manifest = json.loads(archive.read("manifest.json"))
    if not isinstance(manifest, dict) or (
        manifest.get("format"), manifest.get("version")
    ) != ("jelly-project-backup", 1):
        raise ValueError("不是受支持的 Jelly 项目备份")
    project = ProjectRecord.model_validate(manifest.get("project"))
    files = manifest.get("files")
    directories = manifest.get("directories")
    if not isinstance(files, list) or not isinstance(directories, list):
        raise ValueError("备份文件清单无效")
    if len(files) + len(directories) > _MAX_FILES:
        raise ValueError("备份文件与目录数量超过上限")
    if len(files) != len(infos) - 1:
        raise ValueError("备份文件清单与实际内容不一致")
    expected = set()
    workspace_files = []
    total = 0
    metadata_total = 0
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("备份文件清单无效")
        name = item.get("path")
        path = _archive_path(name)
        if name in expected or path.parts[0] not in {"workspace", "sessions", "changes", "runs"}:
            raise ValueError("备份文件清单包含重复或未知路径")
        if path.parts[0] == "workspace":
            if len(path.parts) < 2:
                raise ValueError("备份工作区路径无效")
            workspace_files.append(PurePosixPath(*path.parts[1:]).as_posix())
        elif path.parts[0] == "changes":
            if len(path.parts) != 3 or not path.parts[2].endswith(".json"):
                raise ValueError("备份版本路径无效")
        elif len(path.parts) != 2:
            raise ValueError("备份元数据路径无效")
        if path.parts[0] in {"sessions", "runs"}:
            extension = ".jsonl" if path.parts[0] == "sessions" else ".json"
            if not path.parts[1].endswith(extension):
                raise ValueError("备份任务路径无效")
            Session.path_for(Path("."), path.parts[1].removesuffix(extension))
        size = item.get("size")
        digest = item.get("sha256")
        mode = item.get("mode")
        if (
            not isinstance(size, int) or isinstance(size, bool) or size < 0
            or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or not isinstance(mode, int) or isinstance(mode, bool) or mode < 0 or mode > 0o777
        ):
            raise ValueError("备份文件摘要无效")
        if archive.getinfo(name).file_size != size:
            raise ValueError("备份文件大小与清单不一致")
        if path.parts[0] != "workspace" and size > _MAX_METADATA_BYTES:
            raise ValueError("备份元数据超过上限")
        total += size
        if path.parts[0] != "workspace":
            metadata_total += size
        if total > _MAX_TOTAL_BYTES:
            raise ValueError("备份超过 2 GB 上限")
        expected.add(name)
    if expected != set(names) - {"manifest.json"}:
        raise ValueError("备份文件清单与实际内容不一致")
    checked_directories = []
    for name in directories:
        if not isinstance(name, str):
            raise ValueError("备份目录清单无效")
        path = _archive_path(f"workspace/{name}")
        if len(path.parts) < 2:
            raise ValueError("备份目录清单无效")
        checked_directories.append(PurePosixPath(*path.parts[1:]).as_posix())
    _validate_workspace_layout(checked_directories, workspace_files)
    uncompressed = sum(info.file_size for info in infos)
    compressed = sum(info.compress_size for info in infos)
    if (
        uncompressed > _COMPRESSION_RATIO_THRESHOLD
        and uncompressed > max(compressed, 1) * _MAX_COMPRESSION_RATIO
    ):
        raise ValueError("备份压缩比异常，可能不是安全的项目备份")
    return project, files, directories, total, metadata_total


def _ensure_free_space(path: Path, required: int) -> None:
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    reserve = max(_MIN_FREE_RESERVE, min(512_000_000, required // 10))
    if shutil.disk_usage(probe).free < required + reserve:
        raise ValueError("磁盘可用空间不足，无法安全导入项目备份")


def _load_import_session(
    source_sessions: Path, session_id: str, original: ProjectRecord
) -> Session:
    path = Session.path_for(source_sessions, session_id)
    try:
        with path.open("rb") as stream:
            first = stream.readline(2_000_001)
    except OSError as exc:
        raise ValueError("项目任务记录无法读取") from exc
    if not first or len(first) > 2_000_000 or not first.endswith(b"\n"):
        raise ValueError("项目任务元信息无效")
    try:
        meta = json.loads(first.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("项目任务元信息无效") from exc
    limits = {
        "session_id": 256,
        "title": 64,
        "created_at": 128,
        "updated_at": 128,
        "model": 512,
        "cwd": 4096,
        "skill_addon": 2_000_000,
    }
    if not isinstance(meta, dict) or any(
        field not in meta or not isinstance(meta[field], str) or len(meta[field]) > limit
        for field, limit in limits.items()
    ):
        raise ValueError("项目任务元信息无效")
    if (
        meta["session_id"] != session_id
        or meta.get("project_id") != original.id
        or meta["cwd"] != original.cwd
    ):
        raise ValueError("任务与备份项目不一致")
    try:
        return Session.resume(source_sessions, session_id)
    except SessionError as exc:
        raise ValueError("项目任务记录损坏或内容不完整") from exc


def _stage_backup(archive, stage: Path, files, directories):
    workspace = stage / "workspace"
    workspace.mkdir()
    for name in directories:
        (workspace / Path(name)).mkdir(parents=True, exist_ok=True)
    for item in files:
        path = stage.joinpath(*PurePosixPath(item["path"]).parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        count = 0
        with archive.open(item["path"]) as src, path.open("wb") as dst:
            while chunk := src.read(_CHUNK):
                count += len(chunk)
                if count > item["size"]:
                    raise ValueError("备份内容大小与清单不一致")
                digest.update(chunk)
                dst.write(chunk)
        if count != item["size"] or digest.hexdigest() != item["sha256"]:
            raise ValueError("备份内容校验失败，未导入项目")


def _new_name(project_name: str, root: Path):
    for number in range(1, 1000):
        suffix = "（导入）" if number == 1 else f"（导入 {number}）"
        name = f"{project_name[:64 - len(suffix)]}{suffix}"
        target = root / ProjectStore.directory_name(name)
        if not target.exists():
            return name, target
    raise ValueError("无法为导入项目找到可用目录名")


def _remove_new_workspace(target: Path, root: Path) -> None:
    if target.parent != root or _is_link_like(target):
        raise OSError("导入目录超出管理范围，无法自动清理")

    def clear_readonly(function, name, _error):
        os.chmod(name, 0o700)
        function(name)

    shutil.rmtree(target, onerror=clear_readonly)


_STAGE_RECORD_PREFIX = ".jelly-import-stage-record-"
_ACTIVE_STAGES: set[str] = set()


def _assert_workspace_unreferenced(manager, target: Path) -> None:
    """A recovery marker must never authorize deletion of a catalogued workspace."""
    projects = manager.projects.list()
    if manager.projects.issues():
        raise OSError("项目目录记录不完整，已保留导入数据")
    candidate = target.resolve()
    for project in projects:
        workspace = Path(project["cwd"]).expanduser().resolve()
        if candidate.is_relative_to(workspace) or workspace.is_relative_to(candidate):
            raise OSError("导入目录与已有项目工作区重叠，已保留数据")


def _stage_process_is_active(payload: dict) -> bool:
    process_id = payload["process_id"]
    if process_id == os.getpid():
        return payload["owner_token"] in _ACTIVE_STAGES
    if os.name == "nt":
        # os.kill(pid, 0) terminates a process on Windows. Query only, without
        # requesting termination rights or sending a signal.
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        open_process.restype = wintypes.HANDLE
        get_exit_code = kernel32.GetExitCodeProcess
        get_exit_code.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        get_exit_code.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL
        handle = open_process(0x1000, False, process_id)
        if not handle:
            return ctypes.get_last_error() != 87
        try:
            exit_code = wintypes.DWORD()
            if not get_exit_code(handle, ctypes.byref(exit_code)):
                return True
            return exit_code.value == 259  # STILL_ACTIVE
        finally:
            close_handle(handle)
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    except OSError as exc:
        # An inaccessible or reused PID is not evidence that it is safe to delete.
        return not (exc.errno == errno.ESRCH or getattr(exc, "winerror", None) == 87)
    return True


def _read_stage_registration(marker: Path, root: Path) -> dict:
    if _is_link_like(marker) or not marker.is_file() or marker.stat().st_size > 4096:
        raise ValueError("导入暂存登记无效")
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("导入暂存登记损坏") from exc
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError("导入暂存登记无效")
    token = payload.get("owner_token")
    stage_name = payload.get("stage_name")
    process_id = payload.get("process_id")
    if (
        not isinstance(token, str)
        or not re.fullmatch(r"[0-9a-f]{32}", token)
        or marker.name != f"{_STAGE_RECORD_PREFIX}{token}.json"
        or not isinstance(stage_name, str)
        or not re.fullmatch(re.escape(_STAGE_PREFIX) + r"[a-zA-Z0-9_-]{6,64}", stage_name)
        or payload.get("managed_root") != str(root)
        or not isinstance(process_id, int)
        or isinstance(process_id, bool)
        or process_id <= 0
        or process_id > 0x7FFFFFFF
    ):
        raise ValueError("导入暂存登记无效")
    return payload


@contextmanager
def _owned_stage(manager, root: Path):
    stage = Path(tempfile.mkdtemp(prefix=_STAGE_PREFIX, dir=root))
    owner = stage / _STAGE_SENTINEL
    token = uuid.uuid4().hex
    marker = manager._session_dir / f"{_STAGE_RECORD_PREFIX}{token}.json"
    payload = {
        "version": 1,
        "owner_token": token,
        "stage_name": stage.name,
        "managed_root": str(root),
        "process_id": os.getpid(),
    }
    _ACTIVE_STAGES.add(token)
    try:
        with owner.open("x", encoding="utf-8") as stream:
            stream.write(token)
            stream.flush()
            os.fsync(stream.fileno())
        manager._session_dir.mkdir(parents=True, exist_ok=True)
        with marker.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False))
            stream.flush()
            os.fsync(stream.fileno())
        _sync_directory(manager._session_dir)
        yield stage
    finally:
        _ACTIVE_STAGES.discard(token)
        try:
            if stage.exists():
                with manager.projects._lock:
                    _assert_workspace_unreferenced(manager, stage)
                    if (
                        _is_link_like(owner)
                        or not owner.is_file()
                        or owner.read_text(encoding="utf-8") != token
                    ):
                        raise OSError("无法确认暂存目录归属")
                    _remove_new_workspace(stage, root)
            marker.unlink(missing_ok=True)
            _sync_directory(manager._session_dir)
        except (OSError, UnicodeError):
            # Keep the private registration so a later recovery can retry safely.
            pass


def _write_import_transaction(
    root: Path, transaction_id: str, project_id: str, target: Path, session_ids: list[str]
) -> Path:
    marker = root / f"{_TRANSACTION_PREFIX}{transaction_id}.json"
    payload = {
        "version": 1,
        "transaction_id": transaction_id,
        "project_id": project_id,
        "target_name": target.name,
        "session_ids": session_ids,
    }
    temporary = None
    try:
        fd, name = tempfile.mkstemp(dir=root, prefix=marker.name, suffix=".tmp")
        temporary = Path(name)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, marker)
        temporary = None
        _sync_directory(root)
    finally:
        if temporary is not None:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)
    return marker


def _read_import_transaction(marker: Path) -> dict:
    if _is_link_like(marker) or not marker.is_file() or marker.stat().st_size > 1_000_000:
        raise ValueError("导入恢复记录无效")
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("导入恢复记录损坏") from exc
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError("导入恢复记录格式无效")
    transaction_id = payload.get("transaction_id")
    project_id = payload.get("project_id")
    target_name = payload.get("target_name")
    session_ids = payload.get("session_ids")
    if (
        not isinstance(transaction_id, str)
        or not re.fullmatch(r"[0-9a-f]{32}", transaction_id)
        or marker.name != f"{_TRANSACTION_PREFIX}{transaction_id}.json"
        or not isinstance(project_id, str)
        or not re.fullmatch(r"[0-9a-f]{32}", project_id)
        or not isinstance(target_name, str)
        or not target_name
        or Path(target_name).name != target_name
        or not isinstance(session_ids, list)
        or len(session_ids) > 2000
        or any(not isinstance(session_id, str) for session_id in session_ids)
        or len(session_ids) != len(set(session_ids))
    ):
        raise ValueError("导入恢复记录格式无效")
    _archive_path(f"workspace/{target_name}")
    for session_id in session_ids:
        if not isinstance(session_id, str):
            raise ValueError("导入恢复记录格式无效")
        Session.path_for(Path("."), session_id)
    return payload


def _remove_import_records(manager, project_id: str, session_ids: list[str]) -> bool:
    complete = True
    for session_id in session_ids:
        session_path = Session.path_for(manager._session_dir, session_id)
        run_path = manager._session_dir / "runs" / f"{session_id}.json"
        changes = manager._session_dir / "changes" / session_id
        if not session_path.exists():
            if run_path.exists() or changes.exists():
                complete = False
            continue
        if _is_link_like(session_path):
            complete = False
            continue
        if session_path.exists():
            try:
                try:
                    session = Session.resume(manager._session_dir, session_id)
                except SessionError:
                    complete = False
                    continue
                if session.project_id != project_id:
                    complete = False
                    continue
                session_path.unlink()
            except OSError:
                complete = False
        try:
            run_path.unlink(missing_ok=True)
        except OSError:
            complete = False
        if changes.exists():
            try:
                if _is_link_like(changes):
                    raise OSError("unexpected symlink")
                for child in changes.iterdir():
                    if _is_link_like(child) or not child.is_file():
                        raise OSError("unexpected change record")
                    child.unlink()
                changes.rmdir()
            except OSError:
                complete = False
    return complete


def recover_project_imports(manager, managed_root: Path) -> list[dict[str, str]]:
    """Finish cleanup after an interrupted import without touching unowned paths."""
    root = managed_root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    warnings = []
    with manager.projects._lock:
        # Workspace contents, including a copied sentinel, are untrusted.  Only
        # registrations in the private configuration directory can own a stage.
        for registration in sorted(
            manager._session_dir.glob(f"{_STAGE_RECORD_PREFIX}*.json")
        ):
            try:
                payload = _read_stage_registration(registration, root)
                if _stage_process_is_active(payload):
                    continue
                stage = root / payload["stage_name"]
                if not stage.exists():
                    registration.unlink()
                    continue
                _assert_workspace_unreferenced(manager, stage)
                owner = stage / _STAGE_SENTINEL
                if (
                    not stage.is_dir()
                    or _is_link_like(stage)
                    or not owner.is_file()
                    or _is_link_like(owner)
                    or owner.read_text(encoding="utf-8") != payload["owner_token"]
                ):
                    raise OSError("无法确认暂存目录归属")
                _remove_new_workspace(stage, root)
                registration.unlink()
                _sync_directory(manager._session_dir)
            except (OSError, UnicodeError, ValueError) as exc:
                warnings.append(
                    {"file": registration.name, "message": f"上次项目导入暂存数据未能清理：{exc}"}
                )
        for marker in sorted(root.glob(f"{_TRANSACTION_PREFIX}*.json")):
            try:
                payload = _read_import_transaction(marker)
                project_id = payload["project_id"]
                transaction_id = payload["transaction_id"]
                target = root / payload["target_name"]
                sentinel = target / f"{_TRANSACTION_SENTINEL}{transaction_id}"
                project_exists = manager.projects._path(project_id).is_file()
                if project_exists:
                    if (
                        sentinel.is_file()
                        and not _is_link_like(sentinel)
                        and sentinel.read_text(encoding="utf-8") == transaction_id
                    ):
                        sentinel.unlink()
                    marker.unlink()
                    _sync_directory(root)
                    continue
                _assert_workspace_unreferenced(manager, target)
                complete = _remove_import_records(
                    manager, project_id, payload["session_ids"]
                )
                if target.exists():
                    if (
                        target.is_dir()
                        and not _is_link_like(target)
                        and sentinel.is_file()
                        and not _is_link_like(sentinel)
                        and sentinel.read_text(encoding="utf-8") == transaction_id
                    ):
                        _remove_new_workspace(target, root)
                    else:
                        complete = False
                if complete:
                    marker.unlink()
                    _sync_directory(root)
                else:
                    raise OSError("仍有无法确认归属的导入数据")
            except (OSError, UnicodeError, ValueError, SessionError) as exc:
                warnings.append(
                    {"file": marker.name, "message": f"上次项目导入未能自动清理：{exc}"}
                )
    return warnings


def _rewrite_sidecars(stage: Path, original: ProjectRecord, target: Path, id_map: dict):
    source_dir = stage / "changes"
    output = stage / "import-changes"
    for old_id, new_id in id_map.items():
        source = source_dir / old_id
        if not source.exists():
            continue
        for file in source.glob("*.json"):
            raw = file.read_bytes()
            try:
                record = json.loads(raw)
                if isinstance(record, dict) and record.get("root") == original.cwd:
                    record["root"] = str(target)
                    path = Path(record.get("path", ""))
                    if path.is_absolute():
                        record["path"] = path.relative_to(Path(original.cwd)).as_posix()
                    raw = json.dumps(record, ensure_ascii=False).encode("utf-8")
            except (ValueError, TypeError, UnicodeError):
                pass  # Preserve damaged records; the journal will isolate them on read.
            destination = output / new_id / file.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
    run_dir = stage / "runs"
    output = stage / "import-runs"
    for old_id, new_id in id_map.items():
        file = run_dir / f"{old_id}.json"
        if not file.exists():
            continue
        raw = file.read_bytes()
        try:
            state = json.loads(raw)
            if isinstance(state, dict) and state.get("busy"):
                state.update(
                    busy=False, status="从备份导入；上次运行已中断",
                    pending=None, approval=None,
                )
                raw = json.dumps(state, ensure_ascii=False).encode("utf-8")
        except (ValueError, UnicodeError):
            pass
        destination = output / f"{new_id}.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)


def _copy_import_file(
    source: Path, destination: Path, created_files: list[tuple[Path, os.stat_result]]
) -> None:
    # Exclusive creation is the check: an exists()/copy2() pair can overwrite a
    # file created by another process between the two operations.
    with source.open("rb") as src, destination.open("xb") as dst:
        created_files.append((destination, os.fstat(dst.fileno())))
        shutil.copyfileobj(src, dst, _CHUNK)
        dst.flush()
        os.fsync(dst.fileno())


def _remove_created_import_files(
    manager, created_files: list[tuple[Path, os.stat_result]], session_ids: list[str]
) -> bool:
    complete = True
    for path, identity in reversed(created_files):
        try:
            if not path.exists():
                continue
            if (
                _is_link_like(path)
                or _file_identity(path.lstat())[:2] != _file_identity(identity)[:2]
            ):
                raise OSError("导入记录已被替换，已保留数据")
            path.unlink()
        except OSError:
            complete = False
    for session_id in session_ids:
        changes = manager._session_dir / "changes" / session_id
        if changes.exists():
            try:
                changes.rmdir()
            except OSError:
                complete = False
    return complete


def import_project_backup(manager, backup: Path, managed_root: Path) -> dict:
    root = managed_root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    recover_project_imports(manager, root)
    _preflight_zip(backup)
    try:
        archive = zipfile.ZipFile(backup)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError("不是有效的 ZIP 项目备份") from exc
    with archive, _owned_stage(manager, root) as stage:
        try:
            original, files, directories, total, metadata_total = _validate_manifest(archive)
            _ensure_free_space(root, total + metadata_total)
            _ensure_free_space(manager._session_dir, metadata_total * 2)
            _stage_backup(archive, stage, files, directories)
        except (
            zipfile.BadZipFile,
            KeyError,
            json.JSONDecodeError,
            NotImplementedError,
            RuntimeError,
        ) as exc:
            raise ValueError("项目备份损坏或内容不完整") from exc
        source_sessions = stage / "sessions"
        ids = sorted(file.stem for file in source_sessions.glob("*.jsonl"))
        if any(not Session.path_for(source_sessions, sid).is_file() for sid in ids):
            raise ValueError("项目任务记录无效")
        orphan_runs = any(
            path.is_file() and path.stem not in ids
            for path in (stage / "runs").glob("*.json")
        )
        changes_dir = stage / "changes"
        orphan_changes = changes_dir.exists() and any(
            path.name not in ids for path in changes_dir.iterdir()
        )
        if orphan_runs or orphan_changes:
            raise ValueError("备份包含无归属的任务记录")
        if len(ids) > 2000:
            raise ValueError("项目任务超过 2000 个，无法生成单个备份")
        sessions = {
            session_id: _load_import_session(source_sessions, session_id, original)
            for session_id in ids
        }
        with manager.projects._lock:
            name, target = _new_name(original.name, root)
            project_id = uuid.uuid4().hex
            id_map = {}
            for sid in ids:
                session = sessions[sid]
                while True:
                    new_id = Session.create(
                        stage / "ids", session.model, target, system_prompt=""
                    ).session_id
                    if new_id not in id_map.values() and not Session.path_for(
                        manager._session_dir, new_id
                    ).exists():
                        id_map[sid] = new_id
                        break
            rewritten = stage / "import-sessions"
            for sid in ids:
                session = sessions[sid]
                restored = Session(id_map[sid], rewritten, session.model, target)
                restored.project_id = project_id
                restored.title = session.title
                restored.created_at = session.created_at
                # Installed Skills are shared settings and are not part of a project backup.
                restored.skill_addon = ""
                restored.messages = list(session.messages)
                prompt = manager._settings.system_prompt or build_system_prompt(
                    target, session.model, manager._skill_catalog()
                )
                restored.messages[0] = SystemMessage(content=prompt)
                restored.save()
            _rewrite_sidecars(stage, original, target, id_map)
            now = datetime.now(timezone.utc).isoformat()
            project = ProjectRecord(
                id=project_id, name=name, cwd=str(target), model=original.model,
                permission="ask", created_at=now, updated_at=now,
                archived_at=None,
            ).model_dump()
            transaction_id = uuid.uuid4().hex
            marker = _write_import_transaction(
                root, transaction_id, project_id, target, list(id_map.values())
            )
            sentinel = target / f"{_TRANSACTION_SENTINEL}{transaction_id}"
            committed = False
            workspace_created = False
            created_files: list[tuple[Path, os.stat_result]] = []
            try:
                target.mkdir(mode=0o700)
                workspace_created = True
                with sentinel.open("x", encoding="utf-8") as stream:
                    stream.write(transaction_id)
                    stream.flush()
                    os.fsync(stream.fileno())
                for child in (stage / "workspace").iterdir():
                    os.replace(child, target / child.name)
                for item in files:
                    if item["path"].startswith("workspace/"):
                        path = target.joinpath(*PurePosixPath(item["path"]).parts[1:])
                        os.chmod(path, item["mode"])
                for folder in ("import-sessions", "import-changes", "import-runs"):
                    source = stage / folder
                    if not source.exists():
                        continue
                    destination_root = manager._session_dir / folder.removeprefix("import-")
                    if folder == "import-sessions":
                        destination_root = manager._session_dir
                    for file in source.rglob("*"):
                        if not file.is_file():
                            continue
                        destination = destination_root / file.relative_to(source)
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        _copy_import_file(file, destination, created_files)
                manager.projects._write(project)
                _sync_directory(manager.projects.directory)
                committed = True
            finally:
                if committed:
                    with suppress(OSError):
                        sentinel.unlink(missing_ok=True)
                    with suppress(OSError):
                        marker.unlink(missing_ok=True)
                    _sync_directory(root)
                else:
                    cleanup_complete = _remove_created_import_files(
                        manager, created_files, list(id_map.values())
                    )
                    if workspace_created and target.is_relative_to(root) and target.exists():
                        try:
                            _assert_workspace_unreferenced(manager, target)
                            if (
                                _is_link_like(sentinel)
                                or not sentinel.is_file()
                                or sentinel.read_text(encoding="utf-8") != transaction_id
                            ):
                                raise OSError("无法确认导入目录归属")
                            _remove_new_workspace(target, root)
                        except OSError:
                            cleanup_complete = False
                    if cleanup_complete:
                        with suppress(OSError):
                            marker.unlink(missing_ok=True)
                        _sync_directory(root)
            return {"project": project, "task_count": len(ids)}
