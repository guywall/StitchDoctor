"""Optional LLM interpretation of findings.

Strictly additive (plan v2 §1.6): with no provider/key configured the caller
treats explanation as unavailable and shows static text instead. Errors and
timeouts degrade to a neutral fallback string, never to a 500 that would
break the rest of the UI.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

import requests

from .. import settings

_SYSTEM_PROMPT = (
    "You are a helpful embroidery expert. A diagnostic tool has analysed an "
    "embroidery file and produced a finding. Explain the finding in 2-4 "
    "plain-English sentences for a home embroiderer: what it means, whether "
    "it matters for their design, and a one-line recommendation. Be concrete. "
    "Do not invent facts that are not in the finding data."
)

_FALLBACK = (
    "Automated interpretation is unavailable right now. The finding data "
    "above is measured directly from the file; use your judgement or consult "
    "the finding's caveats before applying the fix."
)


def available() -> bool:
    return settings.llm_available()


def explain(finding: Dict[str, Any], summary: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return {"text": ...} or None when no LLM is configured."""
    if not available():
        return None

    prompt = (
        json.dumps({
            "design_summary": {
                "stitches": summary.get("total_stitches"),
                "colors": (summary.get("color_changes") or 0) + 1,
                "jumps": summary.get("jump_count"),
                "size_mm": summary.get("extents"),
            },
            "finding": finding,
        }, default=str)
    )

    model = settings.default_llm_model()
    try:
        if settings.LLM_PROVIDER == "gemini":
            return {"text": _gemini(prompt, model)}
        if settings.LLM_PROVIDER == "groq":
            return {"text": _groq(prompt, model)}
    except Exception:  # noqa: BLE001 — LLM must never break the app
        return {"text": _FALLBACK, "fallback": True}
    return None


def _gemini(prompt: str, model: str) -> str:
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent"
    )
    resp = requests.post(
        url,
        params={"key": settings.LLM_API_KEY},
        json={"contents": [{"parts": [{"text": prompt}]}],
              "systemInstruction": {"parts": [{"text": _SYSTEM_PROMPT}]}},
        timeout=settings.LLM_TIMEOUT_SECS,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"].strip()


def _groq(prompt: str, model: str) -> str:
    resp = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {settings.LLM_API_KEY}"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
            "max_tokens": 300,
        },
        timeout=settings.LLM_TIMEOUT_SECS,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()
