# llm_extraction.py
# =============================================================================
# Provider : Ollama (serveur interne). We use the native endpoint /api/chat
# instead of OpenAI-compatible endpoint /v1/chat/completions because:
#   - to set num_ctx per request (SDS text is greater than 2048 tokens as default,
#     otherwise context is truncated and the extraction is worst) ;
#   - to force a structured output via a schema JSON (format=...), which 
#     avoids almost all parsing errors.
# =============================================================================
from __future__ import annotations

import json
import logging
import math
from typing import Optional

import requests
from pydantic import BaseModel, field_validator

from config import (
    OLLAMA_BASE_URL,
    OLLAMA_NUM_CTX,
    OLLAMA_MAX_NUM_CTX,
    OLLAMA_NUM_THREAD,
    OLLAMA_TIMEOUT,
)

# Native Endpoint of Ollama
_OLLAMA_CHAT_PATH = "/api/chat"

# Empty values returned are normalized as None
_EMPTY_VALUES = {"null", "none", "no data available", "n/a", "n.a.", "", "not specified"}

# Rough chars-per-token ratio for English SDS text. OCR output is noisier than
# PyMuPDF output (broken words, stray glyphs) and tokenizes worse, hence a
# conservative value. Only used for the pre-flight context estimate.
_CHARS_PER_TOKEN = 2.9
 
# Tokens reserved for the model's own answer. storage_and_handling is a verbatim
# copy of sections 7 & 10, which can be long.
_NUM_PREDICT = 2048
_NUM_CTX_STEP = 2048


class SDSExtraction(BaseModel):
    """
    Data extracted from an SDS by the LLM.
    Validators normalize "empty" values to None and constrain
    physical_state to solid/liquid/gas.
    """
    physical_state:       Optional[str] = None
    boiling_point:        Optional[str] = None
    flash_point:          Optional[str] = None
    storage_and_handling: Optional[str] = None

    @field_validator("*", mode="before")
    @classmethod
    def _empty_to_none(cls, v):
        if v is None:
            return None
        if isinstance(v, str) and v.strip().lower() in _EMPTY_VALUES:
            return None
        return v.strip() if isinstance(v, str) else v

    @field_validator("physical_state")
    @classmethod
    def _constrain_state(cls, v):
        if v is None:
            return None
        v_low = v.lower()
        if v_low in {"solid", "liquid", "gas"}:
            return v_low
        # Fallback mapping in case the LLM didn't map it itself
        mapping = {
            "powder": "solid", "crystalline": "solid", "crystal": "solid",
            "granular": "solid", "pellet": "solid", "pellets": "solid",
            "aqueous solution": "liquid", "solution": "liquid", "fluid": "liquid",
            "vapour": "gas", "vapor": "gas",
        }
        return mapping.get(v_low)  # None if not found


_EXTRACTION_PROMPT = """
You are analyzing a chemical Safety Data Sheet (SDS).
 
=== BEGIN SDS DOCUMENT ===
{sds_text}
=== END SDS DOCUMENT ===
 
From the SDS above, extract exactly the following four values.
 
1. physical_state
   Look in section 9 (Physical and Chemical Properties).
   The value must be strictly one of: solid, liquid, gas.
   Map equivalents to the closest category (powder -> solid,
   aqueous solution -> liquid, vapour -> gas).
   If the state cannot be determined unambiguously, return null.
 
2. boiling_point
   From section 9. Copy the value with its unit as written in the document.
 
3. flash_point
   From section 9. Copy the value with its unit as written in the document.
 
4. storage_and_handling
   Locate section 7 (Handling and storage) and section 10 (Stability and
   reactivity). Copy the text of these two sections VERBATIM into this field,
   section 7 first, then section 10. Do NOT summarize, do NOT rephrase, do NOT
   shorten. Reproduce the wording of the document.
 
Respond with ONLY a valid JSON object using exactly these four keys:
physical_state, boiling_point, flash_point, storage_and_handling.
Use null (not the string "null") for any value that is absent from the document.
"""


def _fit_num_ctx(prompt: str, configured_num_ctx: int, max_num_ctx: int) -> tuple[int, int]:
    """
    Estimate the prompt size and return (num_ctx_to_use, estimated_prompt_tokens).
 
    Mirrors what the Ollama desktop app does automatically: grow the context
    window to fit the prompt instead of letting the server truncate it. Capped
    at OLLAMA_MAX_NUM_CTX -- beyond that the document is genuinely too large and the
    caller is warned rather than served a silently mutilated prompt.
    """
    est_tokens = math.ceil(len(prompt) / _CHARS_PER_TOKEN)
    needed = est_tokens + _NUM_PREDICT
 
    if needed <= configured_num_ctx:
        return configured_num_ctx, est_tokens
 
    fitted = min(
        max_num_ctx,
        int(math.ceil(needed / _NUM_CTX_STEP) * _NUM_CTX_STEP),
    )
    if needed > max_num_ctx:
        logging.warning(
            f"⚠️  Prompt ~{est_tokens} tokens + {_NUM_PREDICT} reserved for the "
            f"answer exceeds the ceiling num_ctx={max_num_ctx}. Ollama WILL truncate."
        )
    else:
        logging.info(
            f"ℹ️  Prompt ~{est_tokens} tokens + {_NUM_PREDICT} reserved > "
            f"configured num_ctx={configured_num_ctx}; raising num_ctx "
            f"to {fitted} for this call (slower prefill)."
        )
    return fitted, est_tokens

def _log_run_stats(data: dict, num_ctx: int, est_tokens: int) -> None:
    """
    Surface the counters Ollama returns.

    Truncation means the prompt filled the whole window: Ollama cuts at
    num_ctx, not at num_ctx - num_predict. Subtracting the output reservation
    here would double-count it, since _fit_num_ctx already sized the window as
    prompt + num_predict -- which made the check fire on every auto-fitted call.
    """
    prompt_tokens = data.get("prompt_eval_count")
    out_tokens    = data.get("eval_count")
    done_reason   = data.get("done_reason")

    logging.info(
        f"📊 Ollama: prompt_eval_count={prompt_tokens} (estimated {est_tokens}), "
        f"eval_count={out_tokens}, num_ctx={num_ctx}, done_reason={done_reason}"
    )

    if prompt_tokens and prompt_tokens >= num_ctx - 32:
        logging.error(
            f"❌ PROMPT TRUNCATED: prompt_eval_count={prompt_tokens} fills the "
            f"window (num_ctx={num_ctx}). Extracted values are unreliable."
        )
    elif prompt_tokens and (num_ctx - prompt_tokens) < 256:
        logging.warning(
            f"⚠️  Only {num_ctx - prompt_tokens} tokens left for generation "
            f"(prompt={prompt_tokens}, num_ctx={num_ctx})."
        )

    if done_reason == "length":
        logging.warning(
            f"⚠️  Generation stopped on num_predict={_NUM_PREDICT} "
            f"(eval_count={out_tokens}); the last field is probably cut off."
        )

    # Drift check: an underestimate shrinks the headroom _fit_num_ctx computed.
    if prompt_tokens and est_tokens and prompt_tokens > est_tokens * 1.05:
        logging.info(
            f"ℹ️  Token estimate low by "
            f"{(prompt_tokens / est_tokens - 1) * 100:.0f}% "
            f"({est_tokens} estimated vs {prompt_tokens} actual); "
            f"consider lowering _CHARS_PER_TOKEN."
        )

def extract_sds_data_via_llm(
    text: str,
    model_name: str,
    base_url: str | None = None,
    num_ctx: int | None = None,
    max_num_ctx : int | None = None,
    timeout: int | None = None,
    num_thread: int | None = None,
) -> SDSExtraction:
    """
    Single Ollama call returning validated SDS data as an SDSExtraction object.
 
    Uses the native /api/chat endpoint with:
      - stream=False               -> single JSON response
      - format=<Pydantic schema>   -> structured output constrained to SDSExtraction
      - options.num_ctx            -> large enough context for a full SDS
      - options.temperature=0      -> deterministic output (nightly batch)
      - options.num_thread         -> only when OLLAMA_NUM_THREAD > 0 (see README)
 
    Falls back to an all-None SDSExtraction on any error (network, malformed
    JSON, validation) -- the caller can always rely on the four fields existing.
    """
    base_url = (base_url or OLLAMA_BASE_URL).rstrip("/")
    url      = f"{base_url}{_OLLAMA_CHAT_PATH}"
    num_ctx  = num_ctx if num_ctx is not None else OLLAMA_NUM_CTX
    max_num_ctx  = max_num_ctx if max_num_ctx is not None else OLLAMA_MAX_NUM_CTX
    timeout  = timeout if timeout is not None else OLLAMA_TIMEOUT
    num_thread = num_thread if num_thread is not None else OLLAMA_NUM_THREAD

    prompt = _EXTRACTION_PROMPT.format(sds_text=text)
    num_ctx, est_tokens = _fit_num_ctx(prompt, num_ctx, max_num_ctx)
 
    logging.info(
        f"🤖 LLM call: model={model_name}, sds_text={len(text)} chars, "
        f"prompt={len(prompt)} chars"
    )

    payload = {
        "model": model_name,
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "stream": False,
        # Structured output : model is constrained to Pydantic schema.
        # Fallback (fences, prose parasite) managed by _parse_json_content.
        "format": SDSExtraction.model_json_schema(),
        "options": {
            "temperature": 0,
            "num_ctx": num_ctx,
            "num_predict": _NUM_PREDICT,
        },
    }
    # num_thread is sent only if explicitely configured (> 0).
    # At 0, Ollama is making its own decision, which is the recommended behaviour
    # by default.
    if num_thread and num_thread > 0:
        payload["options"]["num_thread"] = num_thread

    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        # Native endpoint : response is in message.content (not choices[]).
        #logging.info(f"RAW LLM content: {resp.json()["message"]!r}")
        content = data["message"]["content"]
    except Exception as exc:
        logging.error(f"❌ Ollama call failed ({url}): {exc}")
        return SDSExtraction()

    _log_run_stats(data, num_ctx, est_tokens)

    parsed = _parse_json_content(content)
    if parsed is None:
        logging.error("❌ Could not parse JSON from Ollama response: {content[:500]!r}")
        return SDSExtraction()

    try:
        return SDSExtraction(**parsed)
    except Exception as exc:
        logging.error(f"❌ Pydantic validation failed: {exc}")
        return SDSExtraction()


def _parse_json_content(content: str) -> dict | None:
    """
    Parse a JSON object from the LLM content, tolerating ```json fences```
    <think>...</think> blocks (reasoning models like Qwen3) and surrounding prose.
    """
    if not content:
        return None
    content = content.strip()
    # Remove reasoning block <think>...</think>
    if "<think>" in content and "</think>" in content:
        content = content.split("</think>", 1)[1].strip()
    # Remove markdown fences
    if content.startswith("```"):
        content = content.split("```", 2)[1]
        if content.startswith("json"):
            content = content[4:]
        content = content.strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        # Last resort : isolate first object {...}
        start, end = content.find("{"), content.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(content[start:end + 1])
            except json.JSONDecodeError:
                return None
        return None
