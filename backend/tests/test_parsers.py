"""解析器单测：TXT / Markdown / Word 结构化解析。PDF 因需构造文件由端到端验证覆盖。"""
from io import BytesIO

from app.services.knowledge.parsers import parse_markdown, parse_text


# ----- TXT -----
def test_parse_text_split_by_blank_lines():
    content = '第一段。\n\n第二段内容\n还有第二行。\n\n\n第三段。'
    paras = parse_text(content)
    assert len(paras) == 3
    assert paras[0].text == '第一段。'
    assert all(p.level == 0 for p in paras)  # 纯文本无标题


# ----- Markdown -----
def test_parse_markdown_heading_path():
    md = '# 文档标题\n\n绪言。\n\n## 章节 A\n\nA 的正文。\n\n## 章节 B\n\nB 的正文。'
    paras = parse_markdown(md)
    # 标题段
    titles = [p for p in paras if p.level > 0]
    assert len(titles) == 3
    assert paras[0].heading_path == ('文档标题',) and paras[0].level == 1
    # 章节 A 正文带正确路径
    a_body = next(p for p in paras if p.text == 'A 的正文。')
    assert a_body.heading_path == ('文档标题', '章节 A')
    # 章节 B 路径切换，不含章节 A（同级替换）
    b_body = next(p for p in paras if p.text == 'B 的正文。')
    assert b_body.heading_path == ('文档标题', '章节 B')


def test_parse_markdown_nested_headings():
    md = '# H1\n## H2\n正文\n### H3\n深正文\n## H2b\n末正文'
    paras = parse_markdown(md)
    deep = next(p for p in paras if p.text == '深正文')
    assert deep.heading_path == ('H1', 'H2', 'H3')
    last = next(p for p in paras if p.text == '末正文')
    assert last.heading_path == ('H1', 'H2b')


# ----- Word (.docx) -----
def test_parse_docx_heading_and_table():
    """构造一个 docx：标题样式段落 + 表格，验证层级与表格行解析。"""
    from docx import Document

    doc = Document()
    doc.add_heading('文档标题', level=1)
    doc.add_paragraph('这是正文段落。')
    doc.add_heading('子节', level=2)
    doc.add_paragraph('子节正文。')
    # 加一个表格
    table = doc.add_table(rows=2, cols=2)
    table.rows[0].cells[0].text = '姓名'
    table.rows[0].cells[1].text = '部门'
    table.rows[1].cells[0].text = '张三'
    table.rows[1].cells[1].text = '技术'

    buf = BytesIO()
    doc.save(buf)
    from app.services.knowledge.parsers import parse_docx
    paras = parse_docx(buf.getvalue())

    # 子节正文带正确路径
    body = next(p for p in paras if p.text == '子节正文。')
    assert body.heading_path == ('文档标题', '子节')
    # 表格行作为正文段，用 | 连接
    table_texts = [p.text for p in paras if '张三' in p.text or '姓名' in p.text]
    assert any('张三' in t and '技术' in t for t in table_texts)


def test_parse_unsupported_format_raises():
    from app.services.knowledge.parsers import parse_document
    try:
        parse_document('file.csv', b'x')
        assert False, '应抛 ValueError'
    except ValueError as e:
        assert 'csv' in str(e).lower() or '格式' in str(e)
