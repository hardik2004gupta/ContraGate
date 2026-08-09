"""
Structured logging for ContraGate MCP servers.

All tool calls, errors, and server events are logged as structured JSON
to stdout. The Docker log driver aggregates these across containers.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any


class StructuredLogger:
    def __init__(self, server_name: str) -> None:
        self.server_name = server_name
        self._logger = logging.getLogger(server_name)
        if not self._logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            handler.setFormatter(logging.Formatter("%(message)s"))
            self._logger.addHandler(handler)
            self._logger.setLevel(logging.INFO)

    def _emit(self, level: str, event: str, **fields: Any) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
            "level": level,
            "server": self.server_name,
            "event": event,
            **fields,
        }
        self._logger.info(json.dumps(record))

    def log_tool_call(
        self,
        tool: str,
        arguments: dict,
        success: bool,
        duration_ms: float,
        error: str | None = None,
    ) -> None:
        self._emit(
            "INFO" if success else "ERROR",
            "tool_call",
            tool=tool,
            success=success,
            duration_ms=round(duration_ms, 2),
            error=error,
        )

    def info(self, message: str, **fields: Any) -> None:
        self._emit("INFO", message, **fields)

    def error(self, message: str, **fields: Any) -> None:
        self._emit("ERROR", message, **fields)

    def warning(self, message: str, **fields: Any) -> None:
        self._emit("WARNING", message, **fields)
