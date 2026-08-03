"""富文本控制台封装。

ui 层唯一的输出出口：所有渲染都通过 RichConsole 完成，
其他层级不持有任何 Console 实例。

Windows GBK 控制台（如中文 Windows 的 conhost）无法编码部分 Unicode
字符（❯、emoji 等），这里对输出流启用 ``errors="replace"`` 保证任何
环境下都不会因编码崩溃。
"""

from __future__ import annotations

import contextlib
import sys
from typing import TextIO

from rich.console import Console


def _error_tolerant(stream: TextIO) -> TextIO:
    """让文本流对无法编码的字符做替换而非抛异常。

    Args:
        stream: 输出流（stdout/stderr）。

    Returns:
        原流（原地修改 encoding 错误策略）。
    """
    with contextlib.suppress(AttributeError, ValueError, OSError):
        stream.reconfigure(errors="replace")
    return stream


def console_supports_unicode() -> bool:
    """检测当前控制台是否支持扩展 Unicode（UTF-8 系编码）。

    Returns:
        支持时返回 True；GBK 等传统编码返回 False。
    """
    encoding = (getattr(sys.stdout, "encoding", "") or "").lower().replace("_", "-")
    return encoding in ("utf-8", "utf8", "cp65001")


def create_console() -> Console:
    """创建全局 Rich Console（16.7M 色、超链接关闭、编码容错）。

    Returns:
        配置好的 Console 实例。
    """
    return Console(
        file=_error_tolerant(sys.stdout),
        highlight=True,
        color_system="auto",
        emoji=True,
        width=120,
        soft_wrap=False,
    )
