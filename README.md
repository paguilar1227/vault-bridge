# Vault Bridge

An MCP server that bridges Azure Key Vault to Claude Code — giving you secure, on-demand secret access from your phone, browser, or terminal without copy-pasting credentials.

## The Problem

Claude Code cloud sessions (web/mobile) have no secure way to inject API keys. The environment variables UI explicitly warns against adding secrets. This means you can't build or test apps that need credentials without pasting them into chat — a terrible workflow.

## The Solution

A thin [FastMCP](https://github.com/jlowin/fastmcp) server hosted on Azure Container Apps that:

1. Reads secrets from Azure Key Vault via Managed Identity (zero stored credentials)
2. Exposes them to Claude via MCP tools (`get_secret`, `list_secrets`, `set_env`)
3. Authenticates Claude's connection with a bearer token over HTTPS
4. Scales to zero when idle — effectively free to run

Since remote MCP servers sync account-wide in Claude, connecting once makes your secrets available on iOS, web, desktop, and CLI automatically.

## Tools

| Tool | Description |
|------|------------|
| `list_secrets()` | List available secret names with service/purpose tags (no values) |
| `get_secret(name)` | Retrieve a secret value by name |
| `set_env(name, env_var?)` | Get an `export` command to set a secret as an env var (preferred) |

## Architecture

```
Claude Code (phone/web/cli)
    │
    │  HTTPS + Bearer Token
    ▼
Vault Bridge MCP (Azure Container Apps)
    │
    │  Managed Identity (no credentials)
    ▼
Azure Key Vault (Standard tier)
```

## Quickstart

See the full architecture plan and deployment instructions in [`agent-workspace/plans/`](./agent-workspace/plans/).

### Prerequisites

- Azure subscription with credits
- Azure CLI (`az`) installed and authenticated
- Docker (for local testing)

### Local Development

```bash
# Set environment variables
export AZURE_VAULT_URL="https://your-vault.vault.azure.net"
export VAULT_BRIDGE_TOKEN="any-dev-token"
export ENVIRONMENT="development"  # Uses DefaultAzureCredential (CLI login)

# Install dependencies
pip install -r requirements.txt

# Run the server
fastmcp run server.py --transport sse --host 0.0.0.0 --port 8080
```

### Deploy to Azure

```bash
# Create resources
az group create --name vault-bridge-rg --location centralus
az keyvault create --name your-vault-name --resource-group vault-bridge-rg --location centralus --sku standard --enable-rbac-authorization true

# Add secrets (with tags for discoverability)
az keyvault secret set --vault-name your-vault-name --name "stripe-api-key" --value "sk_..." --tags service=stripe purpose=payments

# Deploy container app
az containerapp env create --name vault-bridge-env --resource-group vault-bridge-rg --location centralus
az containerapp up --name vault-bridge --resource-group vault-bridge-rg --environment vault-bridge-env --source . --ingress external --target-port 8080 --min-replicas 0 --max-replicas 1

# Enable managed identity and grant vault access
az containerapp identity assign --name vault-bridge --resource-group vault-bridge-rg --system-assigned
# Then grant "Key Vault Secrets User" role — see full plan for details
```

### Connect to Claude

Add as a remote MCP server in Claude settings:

```json
{
  "name": "Vault Bridge",
  "url": "https://<your-app-url>/sse",
  "headers": {
    "Authorization": "Bearer <your-token>"
  }
}
```

## Design Philosophy

- **Vault Bridge is a dumb locker.** It fetches keys. It has no opinions about which service to use or when.
- **Service routing lives elsewhere.** Default model preferences, capabilities, and "when to use what" belong in your Context Injector or project-level `CLAUDE.md`.
- **Adding a new secret = one CLI command.** No redeployment, no code changes. Claude can access it immediately.

## Cost

Effectively $0/month for a solo developer. Key Vault Standard is $0.03/10K operations. Container Apps scales to zero with a generous free tier (180K vCPU-seconds + 2M requests/month).

## License

MIT
