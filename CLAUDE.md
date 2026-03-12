# CLAUDE.md — Vault Bridge

## Project Overview

This is a FastMCP server that bridges Azure Key Vault to Claude Code via MCP tools. It's a thin credential-fetching layer — intentionally simple.

## Tech Stack

- **Language:** Python 3.12
- **Framework:** FastMCP
- **Secret Backend:** Azure Key Vault (Standard tier)
- **Auth to Vault:** Azure Managed Identity (production) / DefaultAzureCredential (dev)
- **Auth from Claude:** Bearer token over HTTPS
- **Hosting:** Azure Container Apps (Consumption plan, scale-to-zero)

## Key Files

- `server.py` — The entire MCP server (~100 lines). Three tools: `get_secret`, `list_secrets`, `set_env`
- `Dockerfile` — Slim Python 3.12 image, runs FastMCP with SSE transport on port 8080
- `requirements.txt` — fastmcp, azure-identity, azure-keyvault-secrets, uvicorn

## Design Rules

- **Keep it dumb.** This server fetches keys. It does not know about models, services, or when to use what. That logic belongs in the Context Injector or project CLAUDE.md files.
- **No credentials in the server.** Managed Identity handles Vault auth. The bearer token is set via environment variable.
- **Tags are metadata.** Secrets in Key Vault use tags (`service`, `purpose`) so `list_secrets()` returns useful context.

## Secrets & Environment Variables

This project itself needs these env vars to run:
- `AZURE_VAULT_URL` — Key Vault URL (e.g., `https://pablo-secrets.vault.azure.net`)
- `VAULT_BRIDGE_TOKEN` — Bearer token for MCP connection auth
- `ENVIRONMENT` — `production` (uses ManagedIdentityCredential) or `development` (uses DefaultAzureCredential)

## Development

```bash
export AZURE_VAULT_URL="https://your-vault.vault.azure.net"
export VAULT_BRIDGE_TOKEN="dev-token"
export ENVIRONMENT="development"
pip install -r requirements.txt
fastmcp run server.py --transport sse --host 0.0.0.0 --port 8080
```
