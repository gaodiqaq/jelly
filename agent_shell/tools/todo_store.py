"""待办事项存储（无依赖的纯数据模块，供 base.py 与 todo.py 共享）。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TodoItem:
    """单个待办事项。

    Attributes:
        id: 自增编号。
        content: 任务描述。
        done: 是否已完成。
    """

    id: int
    content: str
    done: bool = False


@dataclass
class TodoStore:
    """任务清单状态（由 core 层创建并注入上下文）。

    Attributes:
        items: 全部待办事项。
    """

    items: list[TodoItem] = field(default_factory=list)
    _next_id: int = field(default=1, init=False)

    def add(self, content: str) -> TodoItem:
        """新增待办事项。

        Args:
            content: 任务描述。

        Returns:
            创建的 TodoItem。
        """
        item = TodoItem(id=self._next_id, content=content)
        self._next_id += 1
        self.items.append(item)
        return item

    def set_done(self, item_id: int, done: bool) -> bool:
        """设置事项完成状态。

        Args:
            item_id: 事项编号。
            done: 目标状态。

        Returns:
            是否找到并更新成功。
        """
        for item in self.items:
            if item.id == item_id:
                item.done = done
                return True
        return False

    def render(self) -> str:
        """渲染任务清单文本。

        Returns:
            多行文本；无任务时返回空清单提示。
        """
        if not self.items:
            return "（当前无任务）"
        lines = []
        for item in self.items:
            mark = "[x]" if item.done else "[ ]"
            lines.append(f"{mark} #{item.id} {item.content}")
        return "\n".join(lines)
