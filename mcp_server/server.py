#!/usr/bin/env python3
"""MCP server for Beacon Protocol integration."""

import asyncio
import json
import sys
from typing import Any, Optional

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from .beacon_lookup import agentfolio_beacon_lookup, BeaconInfo


# Server instance
server = Server("beacon-protocol-mcp")


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools on this MCP server."""
    return [
        Tool(
            name="agentfolio_beacon_lookup",
            description="Look up a beacon ID by agentfolio ID and retrieve beacon status",
            inputSchema={
                "type": "object",
                "properties": {
                    "agentfolio_id": {
                        "type": "string",
                        "description": "The agentfolio ID to look up",
                    },
                },
                "required": ["agentfolio_id"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(
    name: str,
    arguments: dict[str, Any],
) -> list[TextContent]:
    """Handle tool calls from the MCP client."""
    if name == "agentfolio_beacon_lookup":
        agentfolio_id = arguments.get("agentfolio_id")
        if not agentfolio_id:
            return [
                TextContent(
                    type="text",
                    text=json.dumps({"error": "agentfolio_id is required"}),
                )
            ]

        try:
            beacon_id = agentfolio_beacon_lookup(agentfolio_id)
            result = {
                "agentfolio_id": agentfolio_id,
                "beacon_id": beacon_id,
                "lookup_successful": beacon_id is not None,
            }
            return [TextContent(type="text", text=json.dumps(result, indent=2))]
        except Exception as e:
            return [
                TextContent(
                    type="text",
                    text=json.dumps({"error": str(e), "agentfolio_id": agentfolio_id}),
                )
            ]

    return [TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]


async def main() -> None:
    """Run the MCP server with stdio transport."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())