"""Skill 安装器：从远程仓库 / URL / 本地目录安装标准 Skill。

支持三种来源:
1. GitHub 仓库 URL: ``https://github.com/Owner/Repo`` 或
   ``https://github.com/Owner/Repo/tree/<branch>`` —— 递归下载整棵树
   （SKILL.md + references/ + scripts/ + assets/ 全部保留）
2. 单个 SKILL.md 文件 URL（``raw.githubusercontent.com`` 等）
3. 本地目录或文件路径

标准 Skill 格式（Claude Code 生态）:
```markdown
---
name: chinese-novelist
description: |
  多行描述，说明何时使用该 skill
metadata:
  trigger: 自然语言触发词（逗号分隔）
---
# 正文（markdown 指令，可引用 references/xxx.md）
```

同时兼容旧版 jelly 格式（front matter 带 ``triggers`` 列表，正文为单一
prompt template），安装时自动归一化为标准目录结构。

安装目标: ``~/.agent_shell/skills/<name>/``，与内置 skills 分离，
卸载即删除整个目录。
"""

from __future__ import annotations

import os
import re
import shutil
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from agent_shell.skills.base import MarkdownSkill

# 用户级 skills 安装根目录
SKILLS_ROOT = Path.home() / ".agent_shell" / "skills"

# 内置 skills 目录（只读，不安装到此处）
BUILTINS_DIR = Path(__file__).parent / "builtins"

_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]*$")

_UA = "Jelly-Skill-Installer/2.0"


class SkillDefinition:
    """解析后的 skill 定义（与来源无关的中间表示）。"""

    def __init__(
        self,
        name: str,
        description: str,
        body: str,
        triggers: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        author: str = "",
        version: str = "",
    ) -> None:
        self.name = name.strip()
        self.description = description.strip()
        self.body = body.strip()
        self.triggers = triggers or []
        self.metadata = metadata or {}
        self.author = author
        self.version = version

    def validate(self) -> None:
        """验证 skill 定义是否合法。"""
        if not _NAME_RE.match(self.name):
            raise ValueError(
                f"Invalid skill name: {self.name!r} (must be alphanumeric, start with letter)"
            )
        if not self.description:
            raise ValueError(f"Skill '{self.name}' description is required")
        if not self.body:
            raise ValueError(f"Skill '{self.name}' body is required")

    def normalize_triggers(self) -> list[str]:
        """归一化触发词：优先 metadata.trigger（逗号/顿号/分号分隔），
        其次旧版 triggers。

        始终附加 ``/<name>`` 斜杠命令作为兜底入口。

        Returns:
            去重后的触发词列表。
        """
        import re as _re

        raw: list[str] = []
        meta_trigger = self.metadata.get("trigger")
        if isinstance(meta_trigger, str) and meta_trigger.strip():
            # 支持中英文逗号、顿号、分号、竖线分隔
            for part in _re.split(r"[，,、;；|]", meta_trigger):
                part = part.strip()
                if part:
                    raw.append(part)
        raw += [t.strip() for t in self.triggers if t.strip()]
        # 补充斜杠命令
        raw.append(f"/{self.name}")
        # 去重（保留顺序）
        seen: set[str] = set()
        result: list[str] = []
        for t in raw:
            if t and t.lower() not in seen:
                seen.add(t.lower())
                result.append(t)
        return result

    def to_skill(self, resource_dir: str | Path = "") -> MarkdownSkill:
        """转换为可注册的 MarkdownSkill。"""
        return MarkdownSkill(
            name=self.name,
            description=self.description,
            triggers=self.normalize_triggers(),
            body=self.body,
            resource_dir=resource_dir,
        )


# ---------------------------------------------------------------------------
# front matter 解析
# ---------------------------------------------------------------------------


def _split_front_matter(content: str) -> tuple[dict[str, Any], str] | None:
    """拆分 YAML front matter 与正文。

    Returns:
        ``(metadata, body)``；无 front matter 返回 None。
    """
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", content, re.DOTALL)
    if not m:
        return None
    yaml_text, body = m.group(1), m.group(2)
    try:
        metadata = yaml.safe_load(yaml_text) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"SKILL.md front matter YAML 语法错误: {exc}") from exc
    if not isinstance(metadata, dict):
        raise ValueError("SKILL.md front matter 必须是 YAML 映射")
    return metadata, body


def parse_skill_markdown(content: str) -> SkillDefinition:
    """从 SKILL.md 内容解析 skill 定义（兼容新旧两种格式）。

    Args:
        content: SKILL.md 全文。

    Returns:
        SkillDefinition。

    Raises:
        ValueError: front matter 缺失或字段非法。
    """
    parts = _split_front_matter(content)
    if parts is None:
        raise ValueError("Invalid skill format: missing YAML front matter (---...---)")
    metadata, body = parts

    name = metadata.get("name", "")
    if not isinstance(name, str):
        raise ValueError("skill name 必须是字符串")
    name = name.strip()

    description = metadata.get("description", "")
    if not isinstance(description, str):
        description = str(description)

    triggers = metadata.get("triggers", [])
    if not isinstance(triggers, list):
        triggers = []
    triggers = [t for t in triggers if isinstance(t, str)]

    meta = metadata.get("metadata")
    if not isinstance(meta, dict):
        meta = {}

    definition = SkillDefinition(
        name=name,
        description=description,
        body=body,
        triggers=triggers,
        metadata=meta,
        author=str(metadata.get("author", "") or meta.get("author", "")),
        version=str(metadata.get("version", "") or meta.get("version", "")),
    )
    definition.validate()
    return definition


# ---------------------------------------------------------------------------
# 来源获取
# ---------------------------------------------------------------------------


def _build_opener() -> urllib.request.OpenerDirector:
    """构造 urllib opener（读取环境代理，兼容 Clash 等本地代理）。"""
    proxies = {}
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        val = os.environ.get(key)
        if val:
            if key.lower().startswith("https"):
                proxies["https"] = val
            else:
                proxies["http"] = val
    if proxies:
        return urllib.request.build_opener(urllib.request.ProxyHandler(proxies))
    return urllib.request.build_opener()


def _http_get(url: str, timeout: int = 30) -> bytes:
    """GET 请求，带 User-Agent 与代理支持。

    Raises:
        ValueError: 网络失败时给出中文可读错误。
    """
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with _build_opener().open(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise ValueError(f"HTTP {exc.code} 获取失败: {url}") from exc
    except urllib.error.URLError as exc:
        raise ValueError(
            f"网络错误无法获取 {url}: {exc.reason}\n"
            "提示: 若需代理访问 GitHub，请设置环境变量 HTTP_PROXY/HTTPS_PROXY"
            "（如 http://127.0.0.1:7897）"
        ) from exc


def fetch_skill_from_url(url: str) -> str:
    """获取单个 skill 文件内容（旧接口，保留兼容）。"""
    return _http_get(url).decode("utf-8")


def _parse_github_url(url: str) -> tuple[str, str, str, str]:
    """解析 GitHub URL 为 (owner, repo, branch, subdir)。

    支持:
    - https://github.com/owner/repo
    - https://github.com/owner/repo/tree/branch
    - https://github.com/owner/repo/tree/branch/subdir
    - https://raw.githubusercontent.com/owner/repo/branch/path/SKILL.md
    """
    parsed = urlparse(url)
    if parsed.netloc not in ("github.com", "raw.githubusercontent.com"):
        raise ValueError(f"不是 GitHub 地址: {url}")
    parts = [p for p in parsed.path.split("/") if p]
    if parsed.netloc == "raw.githubusercontent.com":
        # owner / repo / branch / path...
        if len(parts) < 4:
            raise ValueError(f"raw 地址格式非法: {url}")
        return parts[0], parts[1], parts[2], "/".join(parts[3:])
    # github.com
    if len(parts) < 2:
        raise ValueError(f"github 地址格式非法: {url}")
    owner, repo = parts[0], parts[1]
    branch = "HEAD"
    subdir = ""
    if len(parts) >= 4 and parts[2] == "tree":
        branch = parts[3]
        subdir = "/".join(parts[4:])
    return owner, repo, branch, subdir


def _github_tree(owner: str, repo: str, branch: str) -> list[dict[str, str]]:
    """获取 GitHub 仓库文件树（blob 列表）。

    Returns:
        [{path, url(blob API 地址)}, ...]
    """
    # GitHub 支持 tree/HEAD 直接解析默认分支
    api_url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
    data = json_loads(_http_get(api_url))
    tree = data.get("tree") or []
    blobs = [
        {"path": item["path"], "url": item["url"]} for item in tree if item.get("type") == "blob"
    ]
    return blobs


def json_loads(raw: bytes) -> dict[str, Any]:
    """解析 JSON 响应（含错误信息提取）。"""
    import json

    text = raw.decode("utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"GitHub API 响应解析失败: {text[:300]}") from exc


def _download_blob(url: str, dest: Path) -> None:
    """下载 GitHub blob 到目标路径（blob API 返回 base64 内容）。"""
    raw = _http_get(url)
    import base64
    import json

    try:
        data = json.loads(raw.decode("utf-8"))
        if isinstance(data, dict) and data.get("encoding") == "base64":
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(base64.b64decode(data["content"]))
            return
    except (ValueError, json.JSONDecodeError):
        pass
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(raw)


def _blob_content(url: str) -> str:
    """获取 blob 的文本内容（用于解析 SKILL.md）。"""
    raw = _http_get(url)
    import base64
    import json

    try:
        data = json.loads(raw.decode("utf-8"))
        if isinstance(data, dict) and data.get("encoding") == "base64":
            return base64.b64decode(data["content"]).decode("utf-8")
    except (ValueError, json.JSONDecodeError):
        pass
    return raw.decode("utf-8")


def _install_from_github(url: str, dest_root: Path) -> SkillDefinition:
    """从 GitHub 仓库 URL 安装完整 skill（含 references/scripts/assets）。"""
    owner, repo, branch, subdir = _parse_github_url(url)
    blobs = _github_tree(owner, repo, branch)
    if not blobs:
        raise ValueError(f"仓库 {owner}/{repo} 为空或无法访问")
    # 找 SKILL.md（优先子目录内）
    skill_blob = None
    for blob in blobs:
        p = blob["path"]
        if p.endswith("/SKILL.md") and (not subdir or p.startswith(subdir + "/")):
            skill_blob = blob
            break
    if skill_blob is None:
        raise ValueError(f"仓库 {owner}/{repo} 中没有 SKILL.md，不是标准 skill 仓库")
    skill_md = parse_skill_markdown(_blob_content(skill_blob["url"]))
    dest = dest_root / skill_md.name
    if dest.exists():
        raise ValueError(f"Skill '{skill_md.name}' 已存在（{dest}），可先卸载再安装")
    dest.mkdir(parents=True, exist_ok=True)
    # 下载全部 blob 到目标目录（保持相对路径，SKILL.md 落于根目录）
    for blob in blobs:
        rel = blob["path"]
        if subdir and rel.startswith(subdir + "/"):
            rel = rel[len(subdir) + 1 :]
        if not rel:
            continue
        target = dest / rel
        _download_blob(blob["url"], target)
    return skill_md


def _install_from_skill_file(source: Path, dest_root: Path) -> SkillDefinition:
    """从本地 SKILL.md 文件安装（复制同目录资源）。

    若 SKILL.md 所在目录含有 references/scripts/assets 等，一并复制，
    保持 skill 完整可用。
    """
    content = source.read_text(encoding="utf-8")
    definition = parse_skill_markdown(content)
    dest = dest_root / definition.name
    if dest.exists():
        raise ValueError(f"Skill '{definition.name}' 已存在（{dest}），可先卸载再安装")
    dest.mkdir(parents=True, exist_ok=True)
    # 复制 SKILL.md 本身
    shutil.copy2(source, dest / "SKILL.md")
    # 复制同目录资源（排除 SKILL.md 自身）
    src_dir = source.parent
    for child in src_dir.iterdir():
        if child.name == "SKILL.md" or child.name.startswith("."):
            continue
        if child.is_dir():
            shutil.copytree(child, dest / child.name, dirs_exist_ok=True)
        else:
            shutil.copy2(child, dest / child.name)
    return definition


def _install_from_dir(source: Path, dest_root: Path) -> SkillDefinition:
    """从本地目录安装（目录内须有 SKILL.md）。"""
    skill_md = source / "SKILL.md"
    if not skill_md.is_file():
        raise ValueError(f"目录中没有 SKILL.md: {source}")
    content = skill_md.read_text(encoding="utf-8")
    definition = parse_skill_markdown(content)
    dest = dest_root / definition.name
    if dest.exists():
        raise ValueError(f"Skill '{definition.name}' 已存在（{dest}），可先卸载再安装")
    shutil.copytree(source, dest, ignore=shutil.ignore_patterns("__pycache__", ".git"))
    return definition


# ---------------------------------------------------------------------------
# 公开安装接口
# ---------------------------------------------------------------------------


def install_skill_from_url(
    url: str,
    skill_dir: Path | None = None,
    force: bool = False,
) -> SkillDefinition:
    """从 URL / 本地路径安装 skill。

    Args:
        url: GitHub 仓库 URL / raw 文件 URL / 本地文件或目录路径。
        skill_dir: 安装根目录；None 使用 ``~/.agent_shell/skills``。
        force: 已存在时覆盖安装。

    Returns:
        安装的 SkillDefinition。

    Raises:
        ValueError: 来源不合法或安装失败。
    """
    dest_root = (skill_dir or SKILLS_ROOT).expanduser().resolve()
    dest_root.mkdir(parents=True, exist_ok=True)

    candidate = Path(url)
    is_local = False
    try:
        is_local = candidate.exists()
    except OSError:
        is_local = False

    if is_local:
        if candidate.is_dir():
            definition = _install_from_dir(candidate, dest_root)
        else:
            definition = _install_from_skill_file(candidate, dest_root)
    else:
        parsed = urlparse(url)
        if parsed.scheme in ("http", "https") and parsed.netloc in (
            "github.com",
            "raw.githubusercontent.com",
        ):
            if parsed.netloc == "raw.githubusercontent.com" or "raw" in url:
                # 单文件下载：抓内容，本地资源无法随附（提示用户）
                content = fetch_skill_from_url(url)
                definition = parse_skill_markdown(content)
                dest = dest_root / definition.name
                if dest.exists() and not force:
                    raise ValueError(f"Skill '{definition.name}' 已存在（{dest}）")
                dest.mkdir(parents=True, exist_ok=True)
                (dest / "SKILL.md").write_text(content, encoding="utf-8")
            else:
                definition = _install_from_github(url, dest_root)
        else:
            raise ValueError(f"不支持的 skill 来源: {url}（支持 GitHub 仓库/raw URL/本地路径）")
    return definition


def install_skill_from_definition(
    name: str,
    description: str,
    triggers: list[str] | None = None,
    prompt_template: str = "",
    skill_dir: Path | None = None,
    author: str = "",
    body: str = "",
) -> SkillDefinition:
    """从参数安装 skill（供 Web API / 程序调用，兼容旧接口）。

    Args:
        name: skill 名称。
        description: 描述。
        triggers: 触发词列表（旧格式）。
        prompt_template: 旧格式的 prompt template（作为正文）。
        skill_dir: 安装根目录。
        author: 作者。
        body: 新格式正文（优先于 prompt_template）。

    Returns:
        安装的 SkillDefinition。
    """
    dest_root = (skill_dir or SKILLS_ROOT).expanduser().resolve()
    dest_root.mkdir(parents=True, exist_ok=True)
    definition = SkillDefinition(
        name=name,
        description=description,
        body=body or prompt_template,
        triggers=triggers or [],
        author=author,
    )
    definition.validate()
    dest = dest_root / definition.name
    if dest.exists():
        raise ValueError(f"Skill '{definition.name}' 已存在（{dest}）")
    dest.mkdir(parents=True, exist_ok=True)
    front = {
        "name": definition.name,
        "description": definition.description,
        "triggers": definition.triggers,
    }
    if author:
        front["author"] = author
    content = (
        "---\n"
        + yaml.safe_dump(front, allow_unicode=True, sort_keys=False)
        + "---\n\n"
        + definition.body
        + "\n"
    )
    (dest / "SKILL.md").write_text(content, encoding="utf-8")
    return definition


def uninstall_skill(name: str, skill_dir: Path | None = None) -> bool:
    """卸载 skill（删除整个目录）。

    Args:
        name: skill 名称。
        skill_dir: 技能根目录；None 使用用户级目录。

    Returns:
        是否成功删除。
    """
    dest_root = (skill_dir or SKILLS_ROOT).expanduser().resolve()
    if not _NAME_RE.match(name):
        # 拒绝路径遍历（如 "../xxx"），名字必须是合法 skill 名
        return False
    target = dest_root / name
    if target.is_dir():
        shutil.rmtree(target)
        return True
    # 兼容旧版：builtins 目录下的 .py 文件
    builtin_file = BUILTINS_DIR / f"{name}.py"
    if builtin_file.is_file():
        builtin_file.unlink()
        return True
    return False


def list_installed(skill_dir: Path | None = None) -> list[dict[str, Any]]:
    """列出已安装的用户级 skills。

    Returns:
        [{name, path, has_skill_md, description}, ...]
    """
    dest_root = (skill_dir or SKILLS_ROOT).expanduser().resolve()
    result: list[dict[str, Any]] = []
    if not dest_root.is_dir():
        return result
    for child in sorted(dest_root.iterdir()):
        if not child.is_dir():
            continue
        skill_md = child / "SKILL.md"
        if skill_md.is_file():
            try:
                definition = parse_skill_markdown(skill_md.read_text(encoding="utf-8"))
                result.append(
                    {
                        "name": definition.name,
                        "path": str(child),
                        "description": definition.description,
                        "triggers": definition.normalize_triggers(),
                        "has_resources": any(
                            (child / d).exists() for d in ("references", "scripts", "assets")
                        ),
                    }
                )
            except ValueError:
                result.append(
                    {
                        "name": child.name,
                        "path": str(child),
                        "description": "(SKILL.md 解析失败)",
                        "triggers": [],
                        "has_resources": False,
                    }
                )
    return result
