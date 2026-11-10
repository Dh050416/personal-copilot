"""多格式文档解析 → 统一「结构化段落」流。

设计目标：把 TXT / Markdown / Word / PDF 拍平成同一种中间表示（ParsedParagraph），
让分块器（chunking.py）只面对一种结构，不必关心来源格式。

每个段落携带两类结构信息（企业级 RAG 与玩具 RAG 的分水岭）：
- heading_path：章节路径（如 ("医保政策", "门诊报销")），分块时作为元数据附加到块上，
  检索命中后连同路径一起喂给模型，避免"70%"这类数字脱离上下文。
- page：页码（仅 PDF 有），用于溯源定位。

各格式的结构可信度：
- Word：文档本身存了 Heading 样式，标题层级精确。
- Markdown：# 数量即层级，精确。
- PDF：本质是"画"出的文字，没有标题概念，只能靠字号启发式推测（近似）；页码精确。
- TXT：无结构，全部正文。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from io import BytesIO

# 句末标点：分块与 PDF 断行合并共用（中英文都覆盖）
SENTENCE_END = '。！？；!?.;'


@dataclass
class ParsedParagraph:
    """解析出的一个结构化段落。

    level=0 表示正文；level>=1 表示标题（1 为最高级）。
    标题段的 heading_path 已包含标题自身；正文段的 heading_path 是其所属章节路径。
    """
    text: str
    heading_path: tuple[str, ...] = field(default_factory=tuple)
    page: int | None = None
    level: int = 0


# --------------------------------------------------------------------------- #
# TXT / Markdown
# --------------------------------------------------------------------------- #
def parse_text(content: str) -> list[ParsedParagraph]:
    """纯文本：按空行分段，全部视为正文（无标题结构）。"""
    paras = []
    for block in re.split(r'\n\s*\n', content):
        block = block.strip()
        if block:
            paras.append(ParsedParagraph(text=block))
    return paras


_MD_HEADING = re.compile(r'^(#{1,6})\s+(.*)$')


def parse_markdown(content: str) -> list[ParsedParagraph]:
    """Markdown：# 数量即标题层级；用标题栈维护每段的章节路径。"""
    paras: list[ParsedParagraph] = []
    stack: list[tuple[int, str]] = []  # [(level, title)]
    buffer: list[str] = []

    def flush():
        text = '\n'.join(buffer).strip()
        buffer.clear()
        if text:
            paras.append(ParsedParagraph(
                text=text, heading_path=tuple(t for _, t in stack), level=0))

    for line in content.splitlines():
        m = _MD_HEADING.match(line.strip())
        if m:
            flush()
            level = len(m.group(1))
            title = m.group(2).strip()
            stack = [(l, t) for l, t in stack if l < level]
            stack.append((level, title))
            paras.append(ParsedParagraph(
                text=title, heading_path=tuple(t for _, t in stack), level=level))
        else:
            buffer.append(line)
            if not line.strip():
                flush()
    flush()
    return paras


# --------------------------------------------------------------------------- #
# Word (.docx)
# --------------------------------------------------------------------------- #
def parse_docx(file_bytes: bytes) -> list[ParsedParagraph]:
    """Word：python-docx 读段落，按 Heading 样式维护标题栈（层级精确）。

    表格单元格文本按行追加为正文（简单处理，第一版不做表头结构化）。
    """
    from docx import Document

    doc = Document(BytesIO(file_bytes))
    paras: list[ParsedParagraph] = []
    stack: list[tuple[int, str]] = []

    def heading_level(para) -> int:
        name = (para.style.name or '').lower() if para.style is not None else ''
        m = re.search(r'heading\s*(\d)', name)
        if m:
            return int(m.group(1))
        # 中文 Word 常见「标题 1」「标题 2」样式名
        m = re.search(r'标题\s*(\d)', para.style.name if para.style is not None else '')
        return int(m.group(1)) if m else 0

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        level = heading_level(para)
        if level:
            stack = [(l, t) for l, t in stack if l < level]
            stack.append((level, text))
            paras.append(ParsedParagraph(
                text=text, heading_path=tuple(t for _, t in stack), level=level))
        else:
            paras.append(ParsedParagraph(
                text=text, heading_path=tuple(t for _, t in stack), level=0))

    # 表格：每行单元格用「 | 」连接，作为正文段（继承当前章节路径）
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                paras.append(ParsedParagraph(
                    text=' | '.join(cells), heading_path=tuple(t for _, t in stack), level=0))
    return paras


# --------------------------------------------------------------------------- #
# PDF（文字型）：pdfplumber 按页提取 + 字号启发式判标题 + 基础清洗
# --------------------------------------------------------------------------- #
def _pdf_page_lines(page) -> list[dict]:
    """把一页的字符聚成「行」，每行带文本、字号（众数）、纵向位置。

    用 extract_words(extra_attrs=['size']) 拿词级字号，再按 top 坐标聚行。
    """
    words = page.extract_words(extra_attrs=['size'], keep_blank_chars=False)
    if not words:
        return []
    words = sorted(words, key=lambda w: (round(w['top'], 1), w['x0']))
    lines: list[dict] = []
    cur: list[dict] = [words[0]]
    for w in words[1:]:
        if abs(w['top'] - cur[-1]['top']) <= 3:  # 同一视觉行
            cur.append(w)
        else:
            lines.append(_make_line(cur))
            cur = [w]
    lines.append(_make_line(cur))
    return lines


def _make_line(words: list[dict]) -> dict:
    text = ''.join(w['text'] for w in words).strip()
    sizes = [round(float(w.get('size', 0)), 1) for w in words]
    body_size = max(set(sizes), key=sizes.count) if sizes else 0.0
    return {'text': text, 'size': body_size, 'top': words[0]['top']}


def _detect_running_headers(pages_lines: list[list[dict]]) -> set[str]:
    """页眉页脚检测：某行文本在超过半数页面的首行或末行重复出现 → 判定为页眉/页脚。"""
    from collections import Counter
    counter: Counter = Counter()
    n = len(pages_lines)
    for lines in pages_lines:
        if not lines:
            continue
        counter[lines[0]['text']] += 1
        if len(lines) > 1:
            counter[lines[-1]['text']] += 1
    threshold = max(2, (n + 1) // 2)
    return {text for text, cnt in counter.items() if text and cnt >= threshold}


def parse_pdf(file_bytes: bytes) -> list[ParsedParagraph]:
    """文字型 PDF：按页提取，基础清洗（去页眉页脚、合并断行），字号启发式判标题。

    - 标题判定：行字号 > 全文中位字号 * 1.2 视为标题；层级按字号相对大小粗分。
    - 扫描件 PDF（无文字层）会抛出 ValueError，由摄入层标记 failed 并提示走 OCR（二期）。
    """
    import statistics

    import pdfplumber

    with pdfplumber.open(BytesIO(file_bytes)) as pdf:
        pages_lines = [_pdf_page_lines(p) for p in pdf.pages]

    all_sizes = [ln['size'] for lines in pages_lines for ln in lines if ln['text']]
    if not all_sizes:
        raise ValueError('PDF 未提取到文字（可能是扫描件），请使用文字型 PDF；扫描件 OCR 将在二期支持。')
    body_size = statistics.median(all_sizes)
    running = _detect_running_headers(pages_lines)

    paras: list[ParsedParagraph] = []
    stack: list[tuple[int, str]] = []
    for page_no, lines in enumerate(pages_lines, start=1):
        pending = ''  # 断行合并缓冲
        for ln in lines:
            text = ln['text']
            if not text or text in running:
                continue  # 跳过空行与页眉页脚
            is_heading = ln['size'] > body_size * 1.2 and len(text) <= 60
            if is_heading:
                if pending:
                    paras.append(ParsedParagraph(text=pending, heading_path=_path(stack),
                                                 page=page_no, level=0))
                    pending = ''
                # 字号越大层级越高：>1.5 倍为一级，>1.2 倍为二级
                level = 1 if ln['size'] > body_size * 1.5 else 2
                stack = [(l, t) for l, t in stack if l < level]
                stack.append((level, text))
                paras.append(ParsedParagraph(text=text, heading_path=_path(stack),
                                             page=page_no, level=level))
                continue
            # 正文：断行合并——上一段未以句末标点结尾则拼接
            if pending and not pending.endswith(tuple(SENTENCE_END)):
                pending += text
            else:
                if pending:
                    paras.append(ParsedParagraph(text=pending, heading_path=_path(stack),
                                                 page=page_no, level=0))
                pending = text
        if pending:
            paras.append(ParsedParagraph(text=pending, heading_path=_path(stack),
                                         page=page_no, level=0))
    return paras


def _path(stack: list[tuple[int, str]]) -> tuple[str, ...]:
    return tuple(t for _, t in stack)


# --------------------------------------------------------------------------- #
# 统一入口
# --------------------------------------------------------------------------- #
def parse_document(filename: str, file_bytes: bytes) -> list[ParsedParagraph]:
    """按扩展名分发到各解析器。返回结构化段落流（供 chunking 使用）。"""
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    if ext in {'txt', 'text'}:
        return parse_text(file_bytes.decode('utf-8', errors='replace'))
    if ext in {'md', 'markdown'}:
        return parse_markdown(file_bytes.decode('utf-8', errors='replace'))
    if ext == 'docx':
        return parse_docx(file_bytes)
    if ext == 'pdf':
        return parse_pdf(file_bytes)
    raise ValueError(f'暂不支持的文件格式：.{ext}（第一期支持 txt / md / docx / pdf）')
