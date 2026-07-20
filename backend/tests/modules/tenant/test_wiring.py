def test_T15_tenant_route_registered_in_main_app():
    """POST /api/v1/tenants 应在 app.main 上注册。"""
    import app.main as main_mod

    paths = {getattr(r, "path", None) for r in main_mod.app.routes}
    assert "/api/v1/tenants" in paths