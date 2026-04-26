from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any

from kalshi_weather.discovery_universe.families import MarketFamily
from kalshi_weather.discovery_universe.models import ScoreExplanation


def _parse_float(s: Any) -> float | None:
    if s is None:
        return None
    try:
        return float(str(s).strip())
    except ValueError:
        return None


def spread_width_dollars(market: dict[str, Any]) -> float | None:
    yb = _parse_float(market.get("yes_bid_dollars"))
    ya = _parse_float(market.get("yes_ask_dollars"))
    if yb is None or ya is None or ya <= yb:
        return None
    return ya - yb


def hours_to_close_from_market(market: dict[str, Any], *, now: datetime | None = None) -> float | None:
    ct = market.get("close_time")
    if not isinstance(ct, str) or not ct.strip():
        return None
    now = now or datetime.now(timezone.utc)
    try:
        # Kalshi returns RFC3339 / ISO8601
        close = datetime.fromisoformat(ct.replace("Z", "+00:00"))
        if close.tzinfo is None:
            close = close.replace(tzinfo=timezone.utc)
        delta = (close - now.astimezone(close.tzinfo)).total_seconds() / 3600.0
        return delta
    except ValueError:
        return None


_COMBO_PAT = re.compile(
    r"\b(combo|multivariate|parlay|same game|sgp|basket|correlated)\b", re.I
)
_SCALAR_PAT = re.compile(
    r"\b(scalar|custom index|bespoke|average of|median of|formula)\b", re.I
)
_CULTURE_PAT = re.compile(
    r"\b(mention|mentions|ceo|movie|oscar|grammy|culture|company stock|ticker symbol)\b", re.I
)
_SOCCER_BROAD_PAT = re.compile(
    r"\b(soccer|fifa|uefa|world cup|epl|mls|ucl|tournament winner|golden boot)\b", re.I
)


def detect_penalty_flags(
    market: dict[str, Any],
    event: dict[str, Any] | None,
    *,
    metadata: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Return penalty_id -> short reason (for explanations)."""
    out: dict[str, str] = {}
    text_parts: list[str] = []
    for key in ("title", "yes_sub_title", "no_sub_title", "rules_primary", "rules_secondary"):
        v = market.get(key)
        if isinstance(v, str) and v.strip():
            text_parts.append(v)
    if event:
        for key in ("title", "sub_title", "category"):
            v = event.get(key)
            if isinstance(v, str) and v.strip():
                text_parts.append(v)
    blob = "\n".join(text_parts)

    if _COMBO_PAT.search(blob):
        out["combo_or_multivariate"] = "Text suggests combo / multivariate / correlated structure."
    if _SCALAR_PAT.search(blob):
        out["scalar_or_custom_settlement"] = "Text suggests scalar or custom settlement complexity."
    if _CULTURE_PAT.search(blob):
        out["niche_culture_or_mentions"] = "Culture / mentions / company-style wording detected."
    if _SOCCER_BROAD_PAT.search(blob):
        out["broad_soccer_universe"] = "Soccer / broad tournament or universe-style wording."

    tags = []
    if isinstance(market.get("tags"), list):
        tags.extend(str(t) for t in market["tags"] if t is not None)
    if event and isinstance(event.get("tags"), list):
        tags.extend(str(t) for t in event["tags"] if t is not None)
    tag_blob = " ".join(tags).lower()
    if "soccer" in tag_blob and _SOCCER_BROAD_PAT.search(blob):
        out["broad_soccer_universe"] = "Soccer-tagged market with broad-universe phrasing."

    if metadata:
        ss = metadata.get("settlement_sources")
        if isinstance(ss, list) and len(ss) > 3:
            out["complex_settlement_sources"] = "Many settlement sources in metadata."

    return out


def score_market(
    market: dict[str, Any],
    family: MarketFamily,
    *,
    event: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> tuple[float, ScoreExplanation]:
    """Compute 0–100 style score with explicit breakdown."""
    exp = ScoreExplanation()
    exp.family_weight = max(0.35, 1.05 - 0.1 * (family.priority - 1))

    spread = spread_width_dollars(market)
    if spread is not None:
        # Tighter spread → higher score (cap contribution at 25)
        tight = max(0.0, 1.0 - min(spread / 0.25, 1.0))
        exp.components["spread_quality"] = round(25.0 * tight, 3)
    else:
        exp.components["spread_quality"] = 0.0
        exp.notes.append("missing_or_crossed_book")

    yb = _parse_float(market.get("yes_bid_size_fp"))
    ya = _parse_float(market.get("yes_ask_size_fp"))
    depth = 0.0
    if yb is not None:
        depth += math.log1p(max(0.0, yb))
    if ya is not None:
        depth += math.log1p(max(0.0, ya))
    exp.components["book_depth"] = round(min(25.0, depth * 4.0), 3)

    vol = _parse_float(market.get("volume_24h_fp")) or _parse_float(market.get("volume_fp"))
    if vol is not None:
        act = min(20.0, math.log1p(max(0.0, vol)) * 2.5)
        exp.components["recent_activity"] = round(act, 3)
    else:
        exp.components["recent_activity"] = 0.0

    htc = hours_to_close_from_market(market)
    if htc is not None:
        if htc <= 0:
            exp.components["time_to_close"] = 0.0
            exp.notes.append("already_closed_or_past_close")
        elif htc < 2:
            exp.components["time_to_close"] = 5.0
            exp.notes.append("very_short_horizon")
        elif htc <= 720:
            # Prefer roughly 6h–14d window
            mid = 168.0
            dist = abs(htc - mid) / mid
            q = max(0.0, 1.0 - min(dist, 1.0))
            exp.components["time_to_close"] = round(15.0 * q, 3)
        else:
            exp.components["time_to_close"] = 3.0
            exp.notes.append("long_dated_close")
    else:
        exp.components["time_to_close"] = 5.0
        exp.notes.append("missing_close_time")

    st = str(market.get("status") or "").lower()
    if st in {"open", "active"}:
        exp.components["status"] = 10.0
    elif st in {"unopened", "initialized"}:
        exp.components["status"] = 4.0
    else:
        exp.components["status"] = 0.0
        exp.notes.append(f"status_{st or 'unknown'}")

    mtype = str(market.get("market_type") or "").lower()
    if mtype in ("binary", "yes_no", ""):
        exp.components["binary_simplicity"] = 5.0
    else:
        exp.components["binary_simplicity"] = 2.0
        exp.notes.append(f"market_type_{mtype or 'unknown'}")

    if family.title_hints:
        hint_blob = " ".join(
            [
                str(market.get("title") or ""),
                str((event or {}).get("title") or ""),
                str(market.get("series_ticker") or ""),
            ]
        ).lower()
        if any(h in hint_blob for h in family.title_hints):
            exp.components["family_title_hint"] = 3.0

    penalties = detect_penalty_flags(market, event, metadata=metadata)
    penalty_weights = {
        "combo_or_multivariate": -22.0,
        "scalar_or_custom_settlement": -18.0,
        "niche_culture_or_mentions": -15.0,
        "broad_soccer_universe": -20.0,
        "complex_settlement_sources": -10.0,
    }
    for pid, reason in penalties.items():
        w = penalty_weights.get(pid, -8.0)
        exp.penalties[pid] = w
        exp.notes.append(f"penalty:{pid}:{reason}")

    score = min(100.0, max(0.0, exp.adjusted_score()))
    return score, exp


def passes_safe_phase_one(
    market: dict[str, Any],
    score: float,
    penalty_ids: set[str],
    *,
    min_score: float = 55.0,
    max_spread: float = 0.22,
    min_hours_to_close: float = 2.0,
    max_hours_to_close: float = 720.0,
) -> bool:
    if score < min_score:
        return False
    if str(market.get("status") or "").lower() not in {"open", "active"}:
        return False
    sw = spread_width_dollars(market)
    if sw is None or sw > max_spread:
        return False
    htc = hours_to_close_from_market(market)
    if htc is None or htc < min_hours_to_close or htc > max_hours_to_close:
        return False
    block = {"combo_or_multivariate", "scalar_or_custom_settlement", "broad_soccer_universe"}
    if penalty_ids & block:
        return False
    return True
