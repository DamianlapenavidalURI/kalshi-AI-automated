from __future__ import annotations

from kalshi_weather.tools import nws


def test_alerts_brief_uses_point_scope_when_state_missing(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _fake_http_get_json(url: str, *, params=None, timeout_s: float = 8.0):  # type: ignore[no-untyped-def]
        _ = timeout_s
        captured["url"] = url
        captured["params"] = dict(params or {})
        return {"features": [{}, {}]}

    monkeypatch.setattr(nws, "cached_loader", lambda *, key, ttl_s, loader: loader())
    monkeypatch.setattr(nws, "http_get_json", _fake_http_get_json)

    out = nws.alerts_brief(state_code=None, lat=44.9778, lon=-93.2650, timeout_s=0.1)
    assert out["ok"] is True
    assert out["active_alert_count"] == 2
    assert str(out.get("scope") or "").startswith("point:")
    params = captured["params"]
    assert isinstance(params, dict)
    assert "point" in params
    assert "area" not in params


def test_alerts_brief_without_location_is_neutral_skip() -> None:
    out = nws.alerts_brief(state_code=None, lat=None, lon=None, timeout_s=0.1)
    assert out["ok"] is True
    assert out.get("skipped") is True
    assert out["active_alert_count"] == 0
