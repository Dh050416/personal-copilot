"""结构感知 + 递归字符 + 重叠分块（生产级 RAG 的主流组合策略）。

三层策略叠加：
1. 结构感知：按解析器给出的标题层级切，标题段触发块边界，且标题文本进入下一块开头，
   使每个块自带章节上下文；heading_path / page 作为块元数据保留。
2. 递归字符：单段超过 chunk_size 时，优先在段落边界（\\n）下刀，其次句末标点，
   最后才硬切字符——尽量在语义边界断开。
3. 重叠：相邻块共享 chunk_overlap 个字符，防止一句话被切断在两块边界导致语义丢失。

为什么不单用某一种：固定大小会切断语义；纯结构感知会产生过长/过短块；
纯递归缺章节上下文。三者组合是 LangChain/LlamaIndex 生产部署的事实标准。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.services.knowledge.parsers import SENTENCE_END, ParsedParagraph


@dataclass
class Chunk:
    """一个检索块：原始文本 + 溯源元数据。"""
    content: str
    heading_path: tuple[str, ...] = field(default_factory=tuple)
    page: int | None = None
    chunk_index: int = 0

    @property
    def heading_text(self) -> str:
        """章节路径拼成单行字符串，检索时附加到块前喂给模型。"""
        return ' > '.join(self.heading_path)


def _best_cut(window: str) -> int:
    """在窗口内找最佳切点（返回切点后的下标，含标点）。优先段落边界，其次句末标点。"""
    para = window.rfind('\n')
    if para > 0:
        return para + 1
    best = -1
    for ch in SENTENCE_END:
        pos = window.rfind(ch)
        if pos > best:
            best = pos
    return best + 1 if best >= 0 else len(window)


def split_text(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    """把一段文本切成 <= chunk_size 的块，相邻块重叠 chunk_overlap 字符。

    递归边界优先级：段落 > 句末标点 > 硬切。保证 start 严格前进，不会死循环。
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]
    overlap = min(chunk_overlap, chunk_size // 2)  # 重叠不得超过块一半，否则信息冗余
    pieces: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = start + chunk_size
        if end >= n:
            tail = text[start:].strip()
            if tail:
                pieces.append(tail)
            break
        cut = _best_cut(text[start:end])
        if cut <= overlap:  # 切点太近会导致回退后不前进，退化为硬切
            cut = chunk_size
        piece = text[start:start + cut].strip()
        if piece:
            pieces.append(piece)
        start = start + cut - overlap
    return pieces


def chunk_paragraphs(paras: list[ParsedParagraph], chunk_size: int = 400,
                     chunk_overlap: int = 50) -> list[Chunk]:
    """把结构化段落流分块。返回带元数据的 Chunk 列表（chunk_index 按顺序编号）。"""
    chunks: list[Chunk] = []
    buf = ''
    path: tuple[str, ...] = ()
    page: int | None = None

    def emit(text: str, p: tuple[str, ...], pg: int | None):
        for piece in split_text(text, chunk_size, chunk_overlap):
            chunks.append(Chunk(content=piece, heading_path=p, page=pg,
                                chunk_index=len(chunks)))

    for para in paras:
        if para.level > 0:
            # 标题段：冲掉旧缓冲，切换章节路径，标题文本进入下一块开头（提供上下文）
            if buf.strip():
                emit(buf, path, page)
                buf = ''
            path = para.heading_path
            page = para.page
            buf = para.text + '\n'
            continue
        if page is None:
            page = para.page
        candidate = (buf + para.text) if buf else para.text
        if buf.strip() and len(candidate) > chunk_size:
            # 缓冲已满：先切旧缓冲，再开新块
            emit(buf, path, page)
            buf = para.text
        else:
            buf = candidate
    if buf.strip():
        emit(buf, path, page)
    return chunks
