"""去除 Qwen3 系列模型（如 MedGo）输出中的 thinking 标签。

MedGo 基于 Qwen3-32B 微调，即使 chat_template 不开启 thinking，模型仍可能
产出空的 ``...`` 包裹。这些标签不是合法 Markdown，前端会渲染成空白或
多余换行。本模块提供流式与非流式两种剥离方式。
"""
import re

# 匹配完整的 <think>...</think> 块（含内容，DOTALL 跨行）
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)
# 匹配孤立的未闭合 <think> 开标签（流式中途可能出现）
_THINK_OPEN = re.compile(r"<think>")
# 匹配孤立的未配对 </think> 闭标签
_THINK_CLOSE = re.compile(r"</think>")


def strip_think_tags(text: str) -> str:
    """剥离完整 think 块与孤立 think 标签，并清理多余前导空白。

    用于非流式完整文本（如最终回复、结构化输出前的纯文本字段）。
    """
    if not text:
        return text
    text = _THINK_BLOCK.sub("", text)
    text = _THINK_OPEN.sub("", text)
    text = _THINK_CLOSE.sub("", text)
    return text.lstrip("\n").strip()


class ThinkStreamFilter:
    """流式 thinking 标签过滤器。

    Qwen3 的 thinking 内容通常是空的，但 `` 和 `` 标签会跨多个
    chunk 到达。直接对每个 chunk 做正则替换会破坏跨 chunk 的标签，因此用
    本类缓冲可能处于标签边界的 chunk。

    用法：
        f = ThinkStreamFilter()
        for chunk in stream:
            clean = f.feed(chunk)
            if clean:
                emit(clean)
        tail = f.flush()
        if tail:
            emit(tail)
    """

    def __init__(self) -> None:
        # 缓冲区：仅保存不足以判断是否为标签前缀的尾部
        self._buf: str = ""

    def feed(self, chunk: str) -> str:
        """喂入一个 chunk，返回可安全输出的已清洗文本。

        可能把部分文本留在缓冲区（当它是 `` 或 `` 的前缀时）。
        """
        if not chunk:
            return ""
        text = self._buf + chunk
        self._buf = ""
        out_parts: list[str] = []
        i = 0
        n = len(text)
        while i < n:
            # 检测可能的 think 标签起始
            if text[i] == "<":
                # 完整 <think>...</think> 块
                m = _THINK_BLOCK.match(text, i)
                if m:
                    i = m.end()
                    continue
                # 完整 <think> 开标签
                if text.startswith("<think>", i):
                    i += len("<think>")
                    continue
                # 完整 </think> 闭标签
                if text.startswith("</think>", i):
                    i += len("</think>")
                    continue
                # 可能是不完整的标签前缀（如 "<thi"），缓冲等待下一个 chunk
                tag = "think>"
                # 检查从 i 到结尾是否是 "<think>" 或 "</think>" 的前缀
                candidate_open = "<" + tag
                candidate_close = "</" + tag
                tail = text[i:]
                if tail == "<" or candidate_open.startswith(tail) or candidate_close.startswith(tail):
                    self._buf = tail
                    break
            out_parts.append(text[i])
            i += 1
        return "".join(out_parts)

    def flush(self) -> str:
        """流结束时调用，返回缓冲区剩余内容（已无完整标签可能，直接清洗）。"""
        tail = self._buf
        self._buf = ""
        if not tail:
            return ""
        # 剩余不可能是完整标签，但可能残留 "<" 或 "<thi" 之类——直接输出
        return tail
