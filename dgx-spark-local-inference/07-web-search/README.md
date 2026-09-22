# Step 7 — web search, without giving the model a socket

`opencode.json` is the agent configuration: models served through the router,
and a SearXNG MCP server launched as a local child process over stdio. The
agent decides to search, the MCP process performs it against a self-hosted
SearXNG instance, and the results come back as text. The model never opens a
connection.

Replace `ROUTER-HOST` and `SEARXNG-HOST`, and export `LLM_API_KEY`. Run SearXNG
from its [official container image](https://github.com/searxng/searxng-docker).
Nothing redacts the query the agent composes; the MCP process's logs are where
to review what left.
