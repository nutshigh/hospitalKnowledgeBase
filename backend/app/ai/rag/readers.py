from pathlib import Path
from typing import List

from llama_index.core import Document


def load_documents(file_path: str, filename: str) -> List[Document]:
    """按扩展名路由到 LlamaIndex reader，返回带 metadata 的 Document 列表"""
    ext = Path(filename).suffix.lower()

    if ext == ".pdf":
        return _read_pdf(file_path, filename)
    elif ext in (".docx", ".doc"):
        return _read_docx(file_path, filename)
    elif ext in (".xlsx", ".xls"):
        return _read_excel(file_path, filename)
    elif ext in (".txt", ".md"):
        return _read_text(file_path, filename)
    else:
        raise ValueError(f"Unsupported file format: {ext}")


def _read_pdf(file_path: str, filename: str) -> List[Document]:
    from llama_index.readers.file import PyMuPDFReader

    reader = PyMuPDFReader()
    docs = reader.load(file_path=file_path)
    for d in docs:
        d.metadata["source_file"] = filename
        d.metadata["file_ext"] = ".pdf"
    return docs


def _read_docx(file_path: str, filename: str) -> List[Document]:
    from llama_index.readers.file import DocxReader

    reader = DocxReader()
    docs = reader.load(file_path=file_path)
    for d in docs:
        d.metadata["source_file"] = filename
        d.metadata["file_ext"] = ".docx"
    return docs


def _read_excel(file_path: str, filename: str) -> List[Document]:
    from llama_index.readers.file import PandasExcelReader

    reader = PandasExcelReader(sheet_name=None)
    docs = reader.load(file_path=file_path)
    for d in docs:
        d.metadata["source_file"] = filename
        d.metadata["file_ext"] = ".xlsx"
    return docs


def _read_text(file_path: str, filename: str) -> List[Document]:
    with open(file_path, "r", encoding="utf-8") as f:
        text = f.read()
    if not text.strip():
        return []
    return [Document(
        text=text,
        metadata={"source_file": filename, "file_ext": Path(filename).suffix.lower()},
    )]
