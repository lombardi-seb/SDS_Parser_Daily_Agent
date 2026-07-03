# llm_extraction.py
from __future__ import annotations

import json
import logging
from typing import Optional

import requests
from pydantic import BaseModel, field_validator

_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Empty values are normalized as None
_EMPTY_VALUES = {"null", "none", "no data available", "n/a", "n.a.", "", "not specified"}


class SDSExtraction(BaseModel):
    """
    Données extraites d'une SDS par le LLM.
    Les validators normalisent les valeurs "vides" en None et contraignent
    physical_state à solid/liquid/gas.
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
        # Fallback mapping
        mapping = {
            "powder": "solid", "crystalline": "solid", "crystal": "solid",
            "granular": "solid", "pellet": "solid", "pellets": "solid",
            "aqueous solution": "liquid", "solution": "liquid", "fluid": "liquid",
            "vapour": "gas", "vapor": "gas",
        }
        return mapping.get(v_low)  # None if not found


_EXTRACTION_PROMPT = """
You are analyzing a chemical Safety Data Sheet (SDS).

Please analyze the document below and locate section 9 (Physical and Chemical Properties).
Then extract only the following properties:
- Physical state
- Boiling point
- Flash point
Important rule for extraction : for Physical state, the value must be strictly one of the following : solid, liquid or gas.
If the document indicates something equivalent (e.g. "powder", "aqueous solution", "vapour"), you must map it to the closest category (powder → solid, aqueous solution → liquid, vapour → gas).
If you cannot determine the state unambiguously, return null.

Please analyze the document and locate section 7 (Handling and storage) and section 10 (Stability and reactivity).
You must copy the information from these sections 7 & 10, and store it in 'storage_and_handling'.

You may take your time to reason through the text if needed.


Respond with ONLY a valid JSON object using exactly these four keys : 
 - physical_state
 - boiling_point
 - flash_point
 - storage_and_handling

Use null (not a string) for any value you cannot find.

SDS document:
{sds_text}
"""


def extract_sds_data_via_llm(
    text: str,
    model_name: str,
    api_key: str,
    timeout: int = 120,
) -> SDSExtraction:
    """
    Single LLM call returning validated SDS data as an SDSExtraction object.

    Falls back to an all-None SDSExtraction on any error (network, malformed
    JSON, validation) — the caller can always rely on the four fields existing.
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type":  "application/json",
    }
    payload = {
        "model": model_name,
        "messages": [
            {"role": "user", "content": _EXTRACTION_PROMPT.format(sds_text=text)}
        ],
        "response_format": {"type": "json_object"},
    }

    try:
        resp = requests.post(
            _OPENROUTER_URL, headers=headers,
            data=json.dumps(payload), timeout=timeout,
        )
        resp.raise_for_status()
        #logging.info(f"RAW LLM content: {resp.json()["choices"][0]!r}")
        content = resp.json()["choices"][0]["message"]["content"]
    except Exception as exc:
        logging.error(f"❌ LLM call failed: {exc}")
        return SDSExtraction()

    parsed = _parse_json_content(content)
    if parsed is None:
        logging.error("❌ Could not parse JSON from LLM response.")
        return SDSExtraction()

    try:
        return SDSExtraction(**parsed)
    except Exception as exc:
        logging.error(f"❌ Pydantic validation failed: {exc}")
        return SDSExtraction()


def _parse_json_content(content: str) -> dict | None:
    """
    Parse a JSON object from the LLM content, tolerating ```json fences```
    and surrounding prose.
    """
    if not content:
        return None
    content = content.strip()
    # Retire les fences markdown éventuelles
    if content.startswith("```"):
        content = content.split("```", 2)[1]
        if content.startswith("json"):
            content = content[4:]
        content = content.strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        # Dernier recours : isoler le premier objet {...}
        start, end = content.find("{"), content.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(content[start:end + 1])
            except json.JSONDecodeError:
                return None
        return None

