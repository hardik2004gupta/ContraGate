"""
Trigger and non-transactional extension detection.

Queries pg_trigger, pg_proc, AND pg_extension to find all triggers on a table
and classify each as transactional or non-transactional.

pg_extension cross-reference: an extension pattern in a function body is only
classified as non-transactional if the extension is actually installed (present
in pg_extension). This prevents false positives from extension names appearing
in comments, variable names, or dead code paths.

Non-transactional triggers invoke extensions (pg_net, dblink, or any caller-
supplied extension name) whose side effects persist even when the DB transaction
is rolled back — making the overall operation PERMANENT in at least one component.

SECURITY: table names are passed as query parameters. SQL identifiers are
never interpolated raw.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


_DEFAULT_NON_TRANSACTIONAL_EXTENSIONS: list[str] = ["pg_net", "dblink"]

# Patterns that indicate a non-transactional call for each known extension.
# These match actual function calls, not just identifier mentions.
_EXTENSION_PATTERNS: dict[str, list[str]] = {
    "pg_net": [
        r"\bnet\.http_post\s*\(",
        r"\bnet\.http_get\s*\(",
        r"\bnet\.http_delete\s*\(",
        r"\bnet\.http_collect_response\s*\(",
    ],
    "dblink": [
        r"\bdblink\s*\(",
        r"\bdblink_exec\s*\(",
        r"\bdblink_send_query\s*\(",
        r"\bdblink_connect\s*\(",
    ],
}

# Simple URL pattern for endpoint extraction (http/https only)
_URL_PATTERN = re.compile(r"https?://[^\s'\",)]+")


@dataclass
class TriggerInfo:
    trigger_name: str
    table_name: str
    event: str
    timing: str
    function_name: str
    invokes_non_transactional: bool
    non_transactional_extension: str | None
    estimated_external_calls: int | None
    target_endpoint: str | None


@dataclass
class TriggerAnalysis:
    table: str
    triggers: list[TriggerInfo]
    has_permanent_side_effects: bool
    non_transactional_triggers: list[TriggerInfo]


def _decode_timing(tgtype: int) -> str:
    if tgtype & 64:
        return "INSTEAD OF"
    if tgtype & 2:
        return "BEFORE"
    return "AFTER"


def _decode_event(tgtype: int) -> str:
    events = []
    if tgtype & 4:
        events.append("INSERT")
    if tgtype & 8:
        events.append("DELETE")
    if tgtype & 16:
        events.append("UPDATE")
    if tgtype & 32:
        events.append("TRUNCATE")
    return " OR ".join(events) if events else "UNKNOWN"


def _get_installed_extensions(conn: Any) -> set[str]:
    """
    Return the set of extension names currently installed in the database.

    Used to filter non_transactional_extensions to only those actually present,
    preventing false positives from extension names in comments or dead code.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT extname FROM pg_extension")
        rows = cur.fetchall()
    return {
        (row["extname"] if isinstance(row, dict) else row[0])
        for row in rows
    }


def _detect_extension(
    function_def: str,
    extensions: list[str],
    installed_extensions: set[str],
) -> str | None:
    """
    Return the first matching extension name found in the function definition,
    but only if that extension is currently installed in the database.

    Requiring the extension to be installed prevents false positives from
    extension names that appear in comments, variable names, or string literals.
    If the extension is not installed, its functions cannot be called at runtime,
    so the trigger cannot produce non-transactional side effects.
    """
    func_lower = function_def.lower()
    for ext in extensions:
        if ext not in installed_extensions:
            continue
        patterns = _EXTENSION_PATTERNS.get(ext, [rf"\b{re.escape(ext)}\s*\("])
        for pattern in patterns:
            if re.search(pattern, func_lower):
                return ext
    return None


def _extract_endpoint(function_def: str) -> str | None:
    """Attempt to extract the first HTTP URL literal from the function body."""
    match = _URL_PATTERN.search(function_def)
    return match.group(0).rstrip("'\"") if match else None


def detect_triggers(
    table: str,
    conn: Any,
    non_transactional_extensions: list[str] | None = None,
) -> TriggerAnalysis:
    """
    Detect all triggers on *table* and classify them as transactional or not.

    non_transactional_extensions defaults to ["pg_net", "dblink"].
    Add extension names to the list to expand coverage (e.g., ["pg_net", "dblink", "http"]).

    Classification requires BOTH:
      (a) the function source matches a known call pattern for the extension, AND
      (b) the extension is actually installed (present in pg_extension).

    SECURITY: table is used as a query parameter — never interpolated into SQL.
    """
    if non_transactional_extensions is None:
        non_transactional_extensions = _DEFAULT_NON_TRANSACTIONAL_EXTENSIONS

    installed_extensions = _get_installed_extensions(conn)

    query = """
        SELECT
            t.tgname        AS trigger_name,
            c.relname       AS table_name,
            t.tgtype        AS tgtype,
            p.proname       AS function_name,
            pg_get_functiondef(p.oid) AS function_def
        FROM pg_trigger t
        JOIN pg_class c ON t.tgrelid = c.oid
        JOIN pg_proc p  ON t.tgfoid  = p.oid
        WHERE c.relname = %s
          AND NOT t.tgisinternal
        ORDER BY t.tgname
    """

    with conn.cursor() as cur:
        cur.execute(query, (table,))
        rows = cur.fetchall()

    def _val(row: Any, key: str, idx: int) -> Any:
        return row[key] if isinstance(row, dict) else row[idx]

    infos: list[TriggerInfo] = []
    for row in rows:
        tgtype = _val(row, "tgtype", 2)
        timing = _decode_timing(tgtype)
        event = _decode_event(tgtype)
        func_name = _val(row, "function_name", 3)
        func_def = _val(row, "function_def", 4) or ""

        ext = _detect_extension(func_def, non_transactional_extensions, installed_extensions)
        endpoint = _extract_endpoint(func_def) if ext else None

        infos.append(TriggerInfo(
            trigger_name=_val(row, "trigger_name", 0),
            table_name=_val(row, "table_name", 1),
            event=event,
            timing=timing,
            function_name=func_name,
            invokes_non_transactional=ext is not None,
            non_transactional_extension=ext,
            estimated_external_calls=None,
            target_endpoint=endpoint,
        ))

    non_transactional = [t for t in infos if t.invokes_non_transactional]

    return TriggerAnalysis(
        table=table,
        triggers=infos,
        has_permanent_side_effects=len(non_transactional) > 0,
        non_transactional_triggers=non_transactional,
    )
