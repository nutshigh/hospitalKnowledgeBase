import os
import tempfile

from app.ai.rag.readers import load_documents


def test_load_txt_document():
    """txt 文件解析为 LlamaIndex Document"""
    with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", delete=False, encoding="utf-8") as f:
        f.write("这是一段测试文本。\n第二段落内容。")
        path = f.name
    try:
        docs = load_documents(path, "test.txt")
        assert len(docs) >= 1
        assert "测试文本" in docs[0].text
        assert docs[0].metadata.get("source_file") == "test.txt"
    finally:
        os.unlink(path)


def test_load_unsupported_format_raises():
    """不支持的格式抛出 ValueError"""
    import pytest
    with tempfile.NamedTemporaryFile(suffix=".xyz", delete=False) as f:
        path = f.name
    try:
        with pytest.raises(ValueError, match="Unsupported"):
            load_documents(path, "test.xyz")
    finally:
        os.unlink(path)
