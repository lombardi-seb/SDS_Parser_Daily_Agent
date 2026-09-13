# section2_isolation.py
"""
Determinist isolation of SECTION 2 (Hazards identification) of a SDS,
+ audit "shadow" comparing with the extraction H-code section-2 vs full document.

Reason : H-codes are only valid in section 2 (PRODUCT classification).
Scanning the entire document captures the codes for COMPONENTS
(section 3) and the glossary (section 16) → over-classification of product.

Method : we rely on the section NUMBER (regulatory invariant 
GHS/CLP : 16 sections numbered sequentially), not on the NAME (which varies
depending on the language). Multilingual EN/FR/IT/DE, OCR-compatible.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── Settings ──────────────────────────────────────────────────────────────────
# Below this threshold, we revert to the full document scan.
CONFIDENCE_THRESHOLD = 0.7

# "Shadow" audit: always compare section-2 vs full doc and logs.
SHADOW_ENABLED  = True
SHADOW_LOG_PATH = Path("shadow_section2.jsonl")   # one line JSON per material

# ── Multilingual anchors ───────────────────────────────────────────────────────
# Word "section" (OCR-compatible : "SECT ION"). Extend if necessary.
_SECTION_WORD = r"(?:SECT\s*ION|RUBRIQUE|SEZIONE|ABSCHNITT|SEKTION)"

# Keywords from the TITLE, by section number: net when the word "section"
# is absent (ex. "2. HAZARDS IDENTIFICATION").
_TITLE_KW = {
    2: r"(?:HAZARD|DANGER|PERICOL|GEFAHR)",
    3: r"(?:COMPOSITION|COMPOSIZIONE|COMPOSICI|ZUSAMMENSETZUNG|"
       r"INGREDIENT|COMPONENT|COMPOSANT|BESTANDTEIL|INFORMAZ)",
}


def _find_header(text: str, n: int, start: int = 0) -> Optional[int]:
    """
    Offset of the beginning of the header of section *n*, searched from *start*.
    Two signals, in order :
      A · structural : word "SECTION/SEZIONE/…" + number
      B · semantic : number at the beginning of the line + title keyword
    """
    kw = _TITLE_KW.get(n, "")
    patterns = [
        rf"(?im)^\s*{_SECTION_WORD}\s*[:.\-]?\s*0?{n}\b",
        rf"(?im)^\s*0?{n}[.\s):\-]+.*{kw}" if kw else None,
    ]
    for pat in patterns:
        if not pat:
            continue
        m = re.search(pat, text[start:])
        if m:
            return start + m.start()
    return None


def isolate_section_2(full_text: str) -> tuple[Optional[str], float]:
    """
    Isolate text of section 2 of a SDS.

    Returns:
        (section2_text, confidence)
        - section2_text : block [start S2 ; start S3[, or None if fail.
        - confidence    : 0.0 → 1.0.

    The end (section 3) is searched AFTER the start of section 2, 
    that ignores return "cf section 3" in section 1.

    Confidence (structural, WITH NO length limit) :
        - 0.0         if anchor is missing or if order is incoherent.
        - 0.6 base if noth anchors are found and ordered.
        - +0.2        if block size is plausible (≥ 40).
        - +0.2        if block contains at least 1 H-code.
      The ABSENCE of an H-code does not disqualify (an unclassified product has
      a section 2 entry that legitimately does not have an H-code). This avoids
      having to go though the full doc for a non-hazardous product — which would
      re-capture unwanted codes from the gloassary/components.
    """
    start = _find_header(full_text, 2)
    if start is None:
        return None, 0.0
    end = _find_header(full_text, 3, start=start + 1)
    if end is None or end <= start:
        return None, 0.0

    block = full_text[start:end]
    conf = 0.6
    if len(block) >= 40:
        conf += 0.2
    # (?:EUH|H) : a section 2 carrying only EUH statements (a solvent labelled
    # just EUH019, say) is a properly classified section 2 and should score the
    # same as one carrying H-codes.
    if re.search(r"\b(?:EUH|H)\d{3}", block):
        conf += 0.2
    return block, round(min(conf, 1.0), 2)


# ── Audit "shadow" ────────────────────────────────────────────────────────────

def _hcodes_of(result: object) -> set[str]:
    """
    Hazard codes of a search_h_codes_in_pdf result, P-codes excluded.

    EUH codes are included when present (EUH_MODE == "on"), so the shadow audit
    covers them too: a section-2 scan and a full-document scan can disagree on
    an EUH statement exactly as they can on an H-code.
    """
    if not isinstance(result, dict):
        return set()
    label = result.get("labelCodes", "")
    return {t for t in label.split(",") if t.startswith("H") or t.startswith("EUH")}


def log_shadow_diff(
    material_id: str,
    result_section2: Optional[dict],
    result_fulldoc: dict,
    confidence: float,
    used_section2: bool,
) -> bool:
    """
    Compare result of section-2 vs result of full document and store
    the difference WITHOUT modify. Return True if differs.

    Write :
      - one line `logging` (INFO if similar, WARNING if different),
      - one line JSON in SHADOW_LOG_PATH (if SHADOW_ENABLED).
    """
    if not SHADOW_ENABLED:
        return False

    sec2_codes = _hcodes_of(result_section2) if result_section2 is not None else None
    full_codes = _hcodes_of(result_fulldoc)

    if sec2_codes is None:
        differs, added, removed = False, [], []
    else:
        differs = (result_section2 != result_fulldoc)
        added   = sorted(full_codes - sec2_codes)   # over-classification avoided
        removed = sorted(sec2_codes - full_codes)    # should be empty

    record = {
        "ts":                   datetime.now().isoformat(timespec="seconds"),
        "material_id":          material_id,
        "isolation_ok":         sec2_codes is not None,
        "confidence":           confidence,
        "used_section2":        used_section2,
        "section2_hcodes":      sorted(sec2_codes) if sec2_codes is not None else None,
        "fulldoc_hcodes":       sorted(full_codes),
        "added_by_fulldoc":     added,
        "missing_from_fulldoc": removed,
        "differs":              differs,
    }

    if differs:
        logging.warning(
            f"SHADOW_SECTION2 {material_id}: DIFFERS — full doc would have "
            f"added {added}"
            + (f" and lost {removed}" if removed else "")
            + f" (confidence={confidence}, section2_utilisée={used_section2})"
        )
    else:
        logging.info(
            f"SHADOW_SECTION2 {material_id}: identical "
            f"(isolation_ok={record['isolation_ok']}, confidence={confidence})"
        )

    try:
        with open(SHADOW_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        logging.error(f"SHADOW_SECTION2 : write {SHADOW_LOG_PATH} impossible — {exc}")

    return differs
