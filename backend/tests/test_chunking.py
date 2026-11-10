"""分块器单测：递归字符切分、重叠、结构感知、chunk_index 编号。"""
from app.services.knowledge.chunking import chunk_paragraphs, split_text
from app.services.knowledge.parsers import ParsedParagraph


# ----- split_text -----
def test_split_short_no_cut():
    """短文本不超过块上限，原样返回一块。"""
    assert split_text('短文本', 400, 50) == ['短文本']


def test_split_empty():
    assert split_text('   ', 400, 50) == []


def test_split_long_with_overlap():
    """长文本按 chunk_size 切，相邻块共享 overlap 字符。"""
    text = '甲' * 1000  # 远超 chunk_size=100
    pieces = split_text(text, 100, 20)
    assert len(pieces) >= 9  # 1000 / (100-20) ≈ 12
    # 每块不超过上限
    assert all(len(p) <= 100 for p in pieces)
    # 相邻块头部应是上一块尾部的子串（重叠）
    assert pieces[1][:20] == pieces[0][-20:]


def test_split_sentence_boundary():
    """优先在句末标点处下刀，不硬切句子。"""
    text = '句子一。' * 30  # 每句 4 字，120 字，chunk_size=20
    pieces = split_text(text, 20, 4)
    # 每块应以句号结尾（在句末标点切）
    assert all(p.endswith('。') for p in pieces)


# ----- chunk_paragraphs -----
def test_chunk_structure_aware():
    """标题段切换 heading_path，正文块带上章节元数据。"""
    paras = [
        ParsedParagraph('第一章', heading_path=('第一章',), level=1),
        ParsedParagraph('正文段落内容', heading_path=('第一章',)),
        ParsedParagraph('1.1节', heading_path=('第一章', '1.1节'), level=2),
        ParsedParagraph('节内正文', heading_path=('第一章', '1.1节')),
    ]
    chunks = chunk_paragraphs(paras, chunk_size=400, chunk_overlap=50)
    assert len(chunks) >= 2
    # 标题文本进入下一块开头（提供上下文）
    assert chunks[0].content.startswith('第一章')
    # 节切换后，后续块的 heading_path 含 1.1节
    section_chunk = next(c for c in chunks if '1.1节' in c.heading_path)
    assert section_chunk.heading_text == '第一章 > 1.1节'


def test_chunk_index_sequential():
    """chunk_index 从 0 递增。"""
    text = '段落。' * 200
    paras = [ParsedParagraph(text)]
    chunks = chunk_paragraphs(paras, chunk_size=100, chunk_overlap=20)
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_chunk_single_short_paragraph():
    """单段短文本只产出一块。"""
    paras = [ParsedParagraph('一句话')]
    chunks = chunk_paragraphs(paras, 400, 50)
    assert len(chunks) == 1
    assert chunks[0].content == '一句话'
