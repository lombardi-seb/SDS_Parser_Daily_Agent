# euh_codes.py
"""
Detection of EUH supplemental hazard statements (EU CLP), kept deliberately
separate from the UN GHS H-code machinery.

Why a separate module and a separate reference file
---------------------------------------------------
`ghscode_10.txt` is sourced from the UN GHS Rev. 10 publication. EUH statements
come from Regulation (EC) No 1272/2008 (CLP), Annexes II and III — a different
regulator and a different document. Mixing the two in one file would break the
provenance trail that makes the GHS dictionary auditable.

EUH statements are *supplemental information*, not a classification. Under CLP
they carry no pictogram and no signal word. Consequently this module never
contributes to pictogram selection, to the signal-word priority rule, or to the
GHS07 suppression rules — it only adds codes to `labelCodes`.

Three operating modes (config.EUH_MODE)
---------------------------------------
  "off"    Nothing is detected. Identical to the pre-EUH behaviour.
  "audit"  Codes are detected, logged and written to EUH_AUDIT_LOG_PATH, but
           NOT added to labelCodes. Nothing changes in what is POSTed to CISPro.
           This is the default: it lets you measure occurrence frequency and
           validate the CISPro vocabulary before changing production output.
  "on"     Codes are detected AND added to labelCodes.

Move to "on" only once a manual POST has confirmed that the CISPro GHS
jurisdiction accepts an EUH value in `labelCodes`.
"""
from __future__ import annotations

import csv
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import chardet

# ── Matching ──────────────────────────────────────────────────────────────────
# Tolerates "EUH019", "EUH 019" and "EUH-019" (OCR and layout artefacts), and
# the letter suffix that is part of the code for EUH201A / EUH209A.
#
# Note: no whitespace is allowed before the suffix, otherwise "EUH210 Available"
# could be read as "EUH210A". The trailing \b prevents a following word from
# being absorbed.
EUH_PATTERN = re.compile(r"\bEUH[\s\-]?(\d{3})([A-Z])?\b")

# Audit trail, same spirit as shadow_section2.jsonl
EUH_AUDIT_LOG_PATH = Path("euh_audit.jsonl")


def _canonical(digits: str, suffix: Optional[str]) -> str:
    """'019', None -> 'EUH019'   |   '201', 'A' -> 'EUH201A'"""
    return f"EUH{digits}{suffix or ''}"


# ── Reference file ────────────────────────────────────────────────────────────

def read_euh_tsv_file(tsv_path: str | Path) -> dict:
    """
    Load the CLP EUH reference file.

    Returns a dict keyed by canonical code, restricted to rows whose `Include`
    column is TRUE:

        {"EUH019": {"statement": "May form explosive peroxides",
                    "type": "Physical",
                    "reference": "Annex III Part 2, Table 2.1",
                    "notes": "..."}, ...}

    Lines starting with '#' are comments. A missing or unreadable file is not
    fatal: an empty dict is returned and a warning is logged, so a packaging
    mistake degrades to the previous behaviour instead of killing a nightly run.
    """
    tsv_path = Path(tsv_path)
    if not tsv_path.exists():
        logging.warning(f"EUH: reference file not found ({tsv_path}) — EUH detection disabled")
        return {}

    try:
        with open(tsv_path, mode="rb") as fh:
            encoding = chardet.detect(fh.read())["encoding"] or "utf-8"

        euh_dict: dict = {}
        with open(tsv_path, mode="r", encoding=encoding) as fh:
            lines = [ln for ln in fh if not ln.lstrip().startswith("#")]

        reader = csv.DictReader(lines, delimiter="\t")
        for row in reader:
            code = (row.get("EUH-Code") or "").strip()
            if not code.startswith("EUH"):
                continue
            if (row.get("Include") or "").strip().upper() != "TRUE":
                continue
            euh_dict[code] = {
                "statement": (row.get("Hazard Statement (EN)") or "").strip(),
                "type":      (row.get("Type") or "").strip(),
                "reference": (row.get("CLP Reference") or "").strip(),
                "notes":     (row.get("Notes") or "").strip(),
            }
    except (OSError, csv.Error, UnicodeDecodeError) as exc:
        logging.error(f"EUH: cannot read {tsv_path} — {exc}. EUH detection disabled.")
        return {}

    logging.info(f"EUH: {len(euh_dict)} codes enabled from {tsv_path}")
    return euh_dict


# ── Operating mode ────────────────────────────────────────────────────────────
# Resolved at call time, not at import time, so a CLI flag can override the
# .env value. Reading `from config import EUH_MODE` in the consumer module would
# bind the value at import and make --euh-mode a no-op.
VALID_EUH_MODES = ("off", "audit", "on")

_EUH_MODE_OVERRIDE: Optional[str] = None


def set_euh_mode(mode: Optional[str]) -> None:
    """
    Override the configured mode for the lifetime of the process.

    Passing None clears the override and falls back to config.EUH_MODE.
    Raises ValueError on an unknown mode: a silent fallback to "off" here would
    mean a run believing it audits EUH codes while doing nothing.
    """
    global _EUH_MODE_OVERRIDE
    if mode is None:
        _EUH_MODE_OVERRIDE = None
        return
    normalised = mode.strip().lower()
    if normalised not in VALID_EUH_MODES:
        raise ValueError(
            f"invalid EUH mode {mode!r} — expected one of {', '.join(VALID_EUH_MODES)}"
        )
    _EUH_MODE_OVERRIDE = normalised
    logging.info(f"EUH: mode forced to '{normalised}' (CLI override)")


def get_euh_mode() -> str:
    """
    Current mode: the CLI override when set, otherwise config.EUH_MODE.

    An unreadable or unrecognised configured value degrades to "off" with a
    warning, so a typo in .env cannot silently push EUH codes into labelCodes.
    """
    if _EUH_MODE_OVERRIDE is not None:
        return _EUH_MODE_OVERRIDE
    try:
        from config import EUH_MODE
    except ImportError:
        logging.warning("EUH: EUH_MODE missing from config — falling back to 'off'")
        return "off"
    mode = (EUH_MODE or "off").strip().lower()
    if mode not in VALID_EUH_MODES:
        logging.warning(
            f"EUH: unrecognised EUH_MODE={EUH_MODE!r} in configuration — "
            f"falling back to 'off' (expected one of {', '.join(VALID_EUH_MODES)})"
        )
        return "off"
    return mode


# ── Lazy singleton ────────────────────────────────────────────────────────────
# Avoids threading a second dictionary through every call site. The explicit
# parameter of find_euh_codes() still takes precedence when supplied.
_EUH_DICT_CACHE: Optional[dict] = None

def get_euh_dict() -> dict:
    """Load the EUH reference once per process, from config.EUH_TSV_PATH."""
    global _EUH_DICT_CACHE
    if _EUH_DICT_CACHE is None:
        try:
            from config import EUH_TSV_PATH
        except ImportError:
            logging.warning("EUH: EUH_TSV_PATH missing from config — EUH detection disabled")
            _EUH_DICT_CACHE = {}
        else:
            _EUH_DICT_CACHE = read_euh_tsv_file(EUH_TSV_PATH)
    return _EUH_DICT_CACHE


def reset_euh_dict_cache() -> None:
    """Force a reload on the next call. Useful in tests and long-lived Gradio."""
    global _EUH_DICT_CACHE
    _EUH_DICT_CACHE = None


# ── Detection ─────────────────────────────────────────────────────────────────

def find_euh_codes(text: str, euh_dict: Optional[dict] = None) -> tuple[set[str], set[str]]:
    """
    Find EUH codes in *text*.

    Returns:
        (known, unknown)
        - known   : codes present in the reference file with Include=TRUE.
        - unknown : codes matching the EUH pattern but absent from the reference
                    (or set to Include=FALSE). Never emitted downstream; surfaced
                    so a newly-published EUH statement appearing in the corpus
                    does not go unnoticed.
    """
    if not text:
        return set(), set()

    if euh_dict is None:
        euh_dict = get_euh_dict()
    if not euh_dict:
        return set(), set()

    known: set[str] = set()
    unknown: set[str] = set()
    for digits, suffix in EUH_PATTERN.findall(text):
        code = _canonical(digits, suffix)
        (known if code in euh_dict else unknown).add(code)

    return known, unknown


# ── Audit trail ───────────────────────────────────────────────────────────────

def log_euh_findings(
    material_id: str,
    known: set[str],
    unknown: set[str],
    mode: str,
    scope: str = "section2",
) -> None:
    """
    Record what was found for one material.

    Writes one `logging` line and one JSON line in EUH_AUDIT_LOG_PATH. Called on
    every run regardless of mode, so "audit" mode produces a directly countable
    dataset:

        jq -r '.known[]' euh_audit.jsonl | sort | uniq -c | sort -rn
    """
    if not known and not unknown:
        return

    if known:
        logging.info(
            f"EUH {material_id}: {sorted(known)} detected in {scope} (mode={mode})"
        )
    if unknown:
        logging.warning(
            f"EUH {material_id}: {sorted(unknown)} matched the EUH pattern but are not "
            f"enabled in the reference file — check euh_codes_clp.txt"
        )

    record = {
        "ts":          datetime.now().isoformat(timespec="seconds"),
        "material_id": material_id,
        "mode":        mode,
        "scope":       scope,
        "known":       sorted(known),
        "unknown":     sorted(unknown),
    }
    try:
        with open(EUH_AUDIT_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        logging.error(f"EUH: write {EUH_AUDIT_LOG_PATH} impossible — {exc}")
