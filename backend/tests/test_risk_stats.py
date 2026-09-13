from app.modules.statistics import disease_service as ds


def test_dim_expr_hit_uses_dh_unit():
    assert "dh.unit_name" in ds._dim_expr_hit("unit")


def test_dim_expr_hit_age_group():
    assert "ri.age" in ds._dim_expr_hit("age_group")


def test_disease_mode_dispatches_to_hit_queries(monkeypatch):
    class R:
        pass

    db = R()
    called = {}

    def fake_cross(db, dim, s, e):
        called["cross"] = (dim, s, e)
        return {"stat_mode": "disease"}

    monkeypatch.setattr(ds, "_indicator_cross_disease", fake_cross)
    out = ds.indicator_cross(db, "unit", "disease", None, "2025-01-01", "2025-12-31")
    assert out["stat_mode"] == "disease"
    assert called["cross"] == ("unit", "2025-01-01", "2025-12-31")


def test_excluded_diseases_not_in_stats():
    """白名单已清空(2026-09-02, MAJOR 移除后无需剔除指标自映射杂项)。

    真实体检异常发现(屈光不正/龋齿/扁桃体肥大等)保留统计, 按类别归"异常";
    指标不再自映射进 disease_hit, 无需 NOT IN 剔除。空集合时过滤函数返回空串。
    """
    assert ds._STATS_EXCLUDED_DISEASES == ()
    assert ds._excluded_sql() == ""
    assert ds._excluded_item_sql() == ""


def test_indicator_mode_untouched(monkeypatch):
    """indicator 模式仍走原 indicator_judgment 路径, 不查 disease_hit。"""
    class R:
        pass

    class FakeResult:
        label = "单位A"
        sample_size = 10
        item_name = "血糖"
        cnt = 3

    class FakeRows:
        def __init__(self, out):
            self.out = out

        def fetchall(self):
            return self.out

        def __iter__(self):
            return iter(self.out)

    class FakeExec:
        def __init__(self, out):
            self.out = out

        def __call__(self, sql, params=None):
            return FakeRows(self.out)

    db = R()
    db.execute = FakeExec([FakeResult()] * 2)
    monkeypatch.setattr(ds, "_indicator_cross_disease",
                        lambda *a: (_ for _ in ()).throw(AssertionError("不应调用")))
    out = ds.indicator_cross(db, "unit", "indicator", None, "2025-01-01", "2025-12-31")
    assert out["stat_mode"] == "indicator"
    assert len(out["data"]) == 1
