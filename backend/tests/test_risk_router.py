def test_routes_exist():
    from app.modules.risk.router import router
    paths = {r.path for r in router.routes}
    assert "/risk/mappings" in paths
    assert "/risk/rules" in paths
    assert "/risk/mappings/{mapping_id}/enabled" in paths
    assert "/risk/rules/{rule_id}/enabled" in paths
    assert "/risk/mappings" in {r.path for r in router.routes}


def test_router_requires_service_auth():
    from app.modules.risk.router import router
    deps = router.dependencies
    assert len(deps) == 1
