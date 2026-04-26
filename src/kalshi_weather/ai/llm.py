from __future__ import annotations

import json
import logging
import time
from typing import Any

from langchain_core.messages import SystemMessage, HumanMessage
from langchain_openai import ChatOpenAI

logger = logging.getLogger(__name__)


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    # Try strict JSON first
    try:
        out = json.loads(text)
        return out if isinstance(out, dict) else {"value": out}
    except json.JSONDecodeError:
        pass
    # Fallback: find first {...} block
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            out = json.loads(text[start : end + 1])
            return out if isinstance(out, dict) else {"value": out}
        except json.JSONDecodeError:
            return {"raw_text": text}
    return {"raw_text": text}


def call_llm_json(
    *,
    model: str,
    system: str,
    user: str,
    temperature: float = 0.2,
    trace_label: str | None = None,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    if trace_label:
        logger.info("[AI] start %s model=%s", trace_label, model)
    llm = ChatOpenAI(model=model, temperature=temperature)
    msg = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
    content = getattr(msg, "content", "")
    if not isinstance(content, str):
        content = str(content)
    out = _extract_json(content)
    if trace_label:
        logger.info("[AI] done %s elapsed=%.3fs", trace_label, time.perf_counter() - t0)
    return out

