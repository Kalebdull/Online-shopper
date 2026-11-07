import importlib
import os


def test_api_demo(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("DEMO_MODE", "1")
    from arbitrage import config
    importlib.reload(config)
    from arbitrage import scanner as sc
    importlib.reload(sc)
    from arbitrage import app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient

    s = appmod.db.get_settings(); s.auto_scan = False; appmod.db.save_settings(s)
    with TestClient(appmod.app) as c:
        assert c.get("/api/status").json()["demo"] is True
        assert appmod.scanner.run_deal_scan()["status"] == "ok"
        plays = c.get("/api/plays?all=true").json()
        assert plays
        p = plays[0]
        assert c.patch(f"/api/plays/{p['source']}/{p['asin']}", json={"status": "watching"}).status_code == 200
        r = c.post("/api/leads", json={"csv": "code,cost\n012345678905,9.99\n"}).json()
        assert r[0]["lead_code"] == "012345678905"
        assert c.put("/api/settings", json={"min_roi": "0.5"}).json()["min_roi"] == 0.5
        assert c.get("/api/summary").status_code == 200
        assert "Flip Finder" in c.get("/").text
