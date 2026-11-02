from app.modules.szbf.conclusion_report import (
    build_conclusion_only_markdown,
    write_conclusion_only_report,
)


def test_build_conclusion_only_markdown():
    md = build_conclusion_only_markdown(
        "天津总站", "280", [("2", "李四"), ("1", "张三")]
    )
    assert "# 天津总站：仅有结论、无明细用户" in md
    assert "共 2 人。" in md
    assert "| 1 | 张三 | 1 |" in md
    assert "| 2 | 李四 | 2 |" in md


def test_write_conclusion_only_report(tmp_path):
    path = write_conclusion_only_report(
        "天津总站", "280", [("1", "张三")], tmp_path
    )
    assert path.name == "天津总站_仅有结论无明细用户.md"
    text = path.read_text(encoding="utf-8")
    assert "张三" in text
    assert "| 1 | 张三 | 1 |" in text
