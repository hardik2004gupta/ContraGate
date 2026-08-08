"""
Common MCP server base class.

All six ContraGate MCP servers share this base. Each server subclasses
ContraGateMCPServer, registers its tools with @server.tool(), and calls run().

Tool calls arrive via:
  POST /call  {"tool": "<name>", "arguments": {...}}

Every tool call is logged via the structured logging module. Auth is validated
via the auth module for non-local calls.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .auth import validate_api_key
from .logging import StructuredLogger


class ToolCallRequest(BaseModel):
    tool: str
    arguments: dict[str, Any] = {}


class ContraGateMCPServer:
    """Base class for all ContraGate MCP servers."""

    def __init__(
        self,
        server_name: str,
        port: int,
        require_auth: bool = True,
    ) -> None:
        self.server_name = server_name
        self.port = port
        self.require_auth = require_auth
        self._tools: dict[str, Callable] = {}
        self._schemas: dict[str, dict] = {}
        self.logger = StructuredLogger(server_name)
        self.app = FastAPI(title=f"ContraGate {server_name}", version="2.0.0")
        self._register_routes()

    def tool(self, name: str, schema: dict | None = None):
        """Decorator to register a callable as an MCP tool."""
        def decorator(fn: Callable) -> Callable:
            self._tools[name] = fn
            if schema:
                self._schemas[name] = schema
            return fn
        return decorator

    def _register_routes(self) -> None:
        @self.app.get("/health")
        async def health() -> dict:
            return {"status": "ok", "server": self.server_name}

        @self.app.get("/tools")
        async def list_tools() -> dict:
            return {
                "server": self.server_name,
                "tools": list(self._tools.keys()),
                "schemas": self._schemas,
            }

        @self.app.post("/call")
        async def call_tool(request: Request, body: ToolCallRequest) -> JSONResponse:
            if self.require_auth:
                api_key = request.headers.get("X-ContraGate-Key", "")
                validate_api_key(api_key)

            tool_fn = self._tools.get(body.tool)
            if tool_fn is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Tool '{body.tool}' not found on server '{self.server_name}'",
                )

            start = time.monotonic()
            try:
                if inspect.iscoroutinefunction(tool_fn):
                    result = await tool_fn(**body.arguments)
                else:
                    result = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: tool_fn(**body.arguments)
                    )
                duration_ms = (time.monotonic() - start) * 1000
                self.logger.log_tool_call(
                    tool=body.tool,
                    arguments=body.arguments,
                    success=True,
                    duration_ms=duration_ms,
                )
                return JSONResponse(content={"result": result, "error": None})
            except Exception as exc:
                duration_ms = (time.monotonic() - start) * 1000
                self.logger.log_tool_call(
                    tool=body.tool,
                    arguments=body.arguments,
                    success=False,
                    duration_ms=duration_ms,
                    error=str(exc),
                )
                raise HTTPException(status_code=500, detail=str(exc)) from exc

    def run(self) -> None:
        import uvicorn
        uvicorn.run(self.app, host="0.0.0.0", port=self.port)
