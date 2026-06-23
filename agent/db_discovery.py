# agent/db_discovery.py
# Step 1 of the daily agent pipeline:
# Queries Oracle/CISPro to retrieve the Z-numbers of materials created today.
from __future__ import annotations

import logging

from SDS_functions import get_oracle_connection

# ── SQL placeholder ────────────────────────────────────────────────────────────
#
_DEFAULT_SQL = """
    SELECT
    c.MATERIALID
    FROM cispro.CHEMICAL c
    JOIN cispro.SDSDOCUMENT s ON s.OWNER_ID = c.NODEID
    WHERE
    s.ARCHIVED = 'N' AND TO_CHAR(c.DATECREATED, 'YYYY-MM-DD') = to_char((sysdate - 1), 'YYYY-MM-DD')
    AND s.OBSOLETE = 0 AND c.OBSOLETE = 0
    AND s.FILETYPE_TEXT = 'File'
    AND c.NODEID NOT IN (SELECT g.MATERIAL_ID FROM CISPRO.GHS g)
"""


def discover_today_materials(sql_query: str | None = None) -> list[str]:
    """
    Query Oracle/CISPro for Material IDs (Z-numbers) created today.

    Args:
        sql_query: Custom SQL override. Falls back to _DEFAULT_SQL when None.
                   Must return a single column containing MATERIALID values.

    Returns:
        Sorted, deduplicated list of Z-numbers, e.g. ["Z0132456", "Z0132457"].

    Raises:
        Exception: On DB connection failure or query error.
    """
    query = sql_query or _DEFAULT_SQL
    logging.info("🔍 Querying Oracle for today's new materials…")

    conn = get_oracle_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(query)
            rows = cursor.fetchall()
    finally:
        conn.close()

    ids = sorted({row[0].strip() for row in rows if row[0]})
    logging.info(f"   → {len(ids)} material(s) discovered: {ids or '(none)'}")
    return ids
