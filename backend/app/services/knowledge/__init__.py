"""RAG 个人知识库服务层。

分层：
- parsers.py  多格式解析 → 统一结构化段落（带 heading_path / page）
- chunking.py 结构感知 + 递归字符 + 重叠分块
- embedding.py bge-m3 向量化 client（OpenAI 兼容 /embeddings，批量）
- ingest.py   摄入编排：去重 → 解析 → 分块 → 向量化 → 入库（状态机）
"""
