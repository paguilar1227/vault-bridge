# Vault Bridge — MCP Secrets Server Architecture Plan

**Purpose:** Give Claude Code (web/mobile/desktop) on-demand access to your API keys and secrets without copy-pasting, stored securely in Azure Key Vault and served via a lightweight MCP server.

**Bottom line:** You open Claude Code on your phone, start building an app, and when Claude needs a Stripe key or Supabase URL, it silently fetches it. You never touch a secret.

---

## 1. Architecture Overview

```
┌──────────────────┐       HTTPS + Bearer Token        ┌────────────────────────┐
│  Claude Code     │ ──────────────────────────────────▶│  Vault Bridge MCP      │
│  (phone/web/cli) │◀────────────────────────────────── │  (Azure Container Apps)│
└──────────────────┘       MCP SSE Protocol             │                        │
                                                        │  FastMCP (Python)      │
                                                        │  ~50 lines of code     │
                                                        └───────────┬────────────┘
                                                                    │
                                                          Managed Identity
                                                          (no credentials)
                                                                    │
                                                                    ▼
                                                        ┌────────────────────────┐
                                                        │  Azure Key Vault       │
                                                        │  (Standard tier)       │
                                                        │                        │
                                                        │  stripe-api-key        │
                                                        │  supabase-url          │
                                                        │  supabase-anon-key     │
                                                        │  openrouter-api-key    │
                                                        │  hevy-api-key          │
                                                        │  ...                   │
                                                        └────────────────────────┘
```

**Key design decisions:**
- Managed Identity for Vault access — the MCP server itself holds zero credentials
- Bearer token to authenticate Claude's MCP connection — simple, same pattern as Open Brain
- Scale-to-zero on Container Apps — costs nothing when idle
- Standard tier Key Vault — $0.03 per 10,000 operations, effectively free for your usage

---

## 2. Azure Resources Needed

| Resource | SKU / Tier | Estimated Monthly Cost | Notes |
|----------|-----------|----------------------|-------|
| Azure Key Vault | Standard | ~$0.01 | $0.03/10K ops. You'll do maybe 100-200 ops/month |
| Azure Container Apps | Consumption | $0.00 (free tier) | 180K vCPU-sec + 2M requests free/month. Scale to zero when idle |
| Azure Container Registry | Basic | ~$5.00 | Stores your Docker image. Can also use GitHub Container Registry for free |
| **Total** | | **~$5/month** | Well within your $150/month credit |

**Alternative to save the $5:** Skip ACR entirely. Build and deploy directly from GitHub using Container Apps' GitHub integration, or use a free GitHub Container Registry (ghcr.io).

---

## 3. Azure Key Vault Setup

### 3.1 Create the Vault

```bash
# Set variables
RG="vault-bridge-rg"
LOCATION="centralus"           # Pick closest region to you
VAULT_NAME="pablo-secrets"     # Must be globally unique

# Create resource group
az group create --name $RG --location $LOCATION

# Create Key Vault (Standard tier)
az keyvault create \
  --name $VAULT_NAME \
  --resource-group $RG \
  --location $LOCATION \
  --sku standard \
  --enable-rbac-authorization true
```

### 3.2 Add Your Secrets

Use a consistent naming convention. Lowercase, hyphens only (Key Vault doesn't allow underscores).

```bash
# Add secrets with tags for discoverability
# Tags let list_secrets() return context alongside names

az keyvault secret set --vault-name $VAULT_NAME \
  --name "stripe-api-key" --value "sk_live_..." \
  --tags service=stripe purpose=payments

az keyvault secret set --vault-name $VAULT_NAME \
  --name "supabase-url" --value "https://xxx.supabase.co" \
  --tags service=supabase purpose=database

az keyvault secret set --vault-name $VAULT_NAME \
  --name "supabase-anon-key" --value "eyJ..." \
  --tags service=supabase purpose=database-auth

az keyvault secret set --vault-name $VAULT_NAME \
  --name "openrouter-api-key" --value "sk-or-..." \
  --tags service=openrouter purpose=llm-api

az keyvault secret set --vault-name $VAULT_NAME \
  --name "google-vertex-api-key" --value "..." \
  --tags service=google-vertex purpose=image-generation

az keyvault secret set --vault-name $VAULT_NAME \
  --name "hevy-api-key" --value "..." \
  --tags service=hevy purpose=fitness-tracking

az keyvault secret set --vault-name $VAULT_NAME \
  --name "github-pat" --value "ghp_..." \
  --tags service=github purpose=source-control

# Verify
az keyvault secret list --vault-name $VAULT_NAME --query "[].name" -o tsv
```

### 3.3 Secret Naming Convention

Stick to this pattern so the MCP tool is predictable:

```
{service}-{type}
```

Examples:
- `stripe-api-key`
- `supabase-url`
- `supabase-anon-key`
- `supabase-service-role-key`
- `openai-api-key`
- `github-pat`
- `azure-openai-key`
- `azure-openai-endpoint`

---

## 4. MCP Server Code

### 4.1 Project Structure

```
vault-bridge/
├── server.py              # MCP server (~60 lines)
├── requirements.txt
├── Dockerfile
└── .github/
    └── workflows/
        └── deploy.yml     # CI/CD (optional)
```

### 4.2 `server.py`

```python
import os
import logging
from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
from azure.keyvault.secrets import SecretClient
from fastmcp import FastMCP

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vault-bridge")

# --- Config ---
VAULT_URL = os.environ["AZURE_VAULT_URL"]          # e.g. https://pablo-secrets.vault.azure.net
BEARER_TOKEN = os.environ["VAULT_BRIDGE_TOKEN"]     # Auth token for MCP connection
ENV = os.environ.get("ENVIRONMENT", "production")

# --- Azure Key Vault client ---
# Uses Managed Identity in Azure, falls back to CLI creds locally
credential = (
    ManagedIdentityCredential() if ENV == "production"
    else DefaultAzureCredential()
)
vault_client = SecretClient(vault_url=VAULT_URL, credential=credential)

# --- MCP Server ---
mcp = FastMCP(
    "Vault Bridge",
    description="Securely retrieves API keys and secrets from Azure Key Vault.",
    auth_token=BEARER_TOKEN,
)


@mcp.tool()
def get_secret(name: str) -> str:
    """Retrieve a secret value by name.

    Use this to get API keys, connection strings, and other credentials
    needed to build, test, or run applications.

    IMPORTANT: The returned value is sensitive. Never display it in output,
    echo it to the terminal, log it, or include it in commits. Use it only
    by setting it as an environment variable or passing it directly to the
    service that needs it.

    Common secret names:
    - stripe-api-key
    - supabase-url, supabase-anon-key, supabase-service-role-key
    - openrouter-api-key
    - github-pat
    - openai-api-key

    Args:
        name: The secret name (lowercase, hyphens). Example: "stripe-api-key"
    """
    try:
        secret = vault_client.get_secret(name)
        logger.info(f"Secret retrieved: {name}")
        return secret.value
    except Exception as e:
        logger.error(f"Failed to retrieve secret '{name}': {e}")
        return f"ERROR: Secret '{name}' not found. Use list_secrets() to see available names."


@mcp.tool()
def list_secrets() -> list[dict]:
    """List all available secrets with their metadata.

    Returns secret names and tags (service, purpose) but NOT values.
    Use this to discover what credentials are available before calling
    get_secret() or set_env().
    """
    try:
        secrets = vault_client.list_properties_of_secrets()
        result = []
        for s in secrets:
            if not s.enabled:
                continue
            entry = {"name": s.name}
            if s.tags:
                entry["service"] = s.tags.get("service", "")
                entry["purpose"] = s.tags.get("purpose", "")
            result.append(entry)
        logger.info(f"Listed {len(result)} secrets")
        return sorted(result, key=lambda x: x["name"])
    except Exception as e:
        logger.error(f"Failed to list secrets: {e}")
        return [{"error": str(e)}]


@mcp.tool()
def set_env(name: str, env_var: str | None = None) -> str:
    """Retrieve a secret and return a shell command to export it as an
    environment variable.

    This is the PREFERRED way to use secrets — run the returned export
    command so the secret is available to subsequent processes without
    being displayed.

    Args:
        name: The secret name. Example: "stripe-api-key"
        env_var: Optional env var name override. Defaults to uppercased
                 name with hyphens converted to underscores.
                 Example: "stripe-api-key" becomes "STRIPE_API_KEY"
    """
    try:
        secret = vault_client.get_secret(name)
        var_name = env_var or name.upper().replace("-", "_")
        # Return the command for Claude to execute
        return f'export {var_name}="{secret.value}"'
    except Exception as e:
        return f"ERROR: Secret '{name}' not found."
```

### 4.3 `requirements.txt`

```
fastmcp>=2.0.0
azure-identity>=1.17.0
azure-keyvault-secrets>=4.8.0
uvicorn>=0.30.0
```

### 4.4 `Dockerfile`

```dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .

EXPOSE 8080

CMD ["fastmcp", "run", "server.py", "--transport", "sse", "--host", "0.0.0.0", "--port", "8080"]
```

---

## 5. Azure Container Apps Deployment

### 5.1 Create the Container App Environment

```bash
# Variables
RG="vault-bridge-rg"
LOCATION="centralus"
ENV_NAME="vault-bridge-env"
APP_NAME="vault-bridge"

# Create Container Apps environment
az containerapp env create \
  --name $ENV_NAME \
  --resource-group $RG \
  --location $LOCATION

# Build and deploy in one step (no ACR needed!)
# This uses Azure's built-in build service
az containerapp up \
  --name $APP_NAME \
  --resource-group $RG \
  --environment $ENV_NAME \
  --source . \
  --ingress external \
  --target-port 8080 \
  --min-replicas 0 \
  --max-replicas 1
```

### 5.2 Enable Managed Identity + Grant Vault Access

```bash
# Enable system-assigned managed identity
az containerapp identity assign \
  --name $APP_NAME \
  --resource-group $RG \
  --system-assigned

# Get the identity's principal ID
PRINCIPAL_ID=$(az containerapp identity show \
  --name $APP_NAME \
  --resource-group $RG \
  --query principalId -o tsv)

# Get Key Vault resource ID
VAULT_ID=$(az keyvault show --name $VAULT_NAME --query id -o tsv)

# Grant "Key Vault Secrets User" role (read-only access to secret values)
az role assignment create \
  --role "Key Vault Secrets User" \
  --assignee $PRINCIPAL_ID \
  --scope $VAULT_ID
```

### 5.3 Set Environment Variables

```bash
# Generate a strong bearer token
BRIDGE_TOKEN=$(openssl rand -hex 32)
echo "Save this token: $BRIDGE_TOKEN"

# Set env vars on the container app
az containerapp update \
  --name $APP_NAME \
  --resource-group $RG \
  --set-env-vars \
    AZURE_VAULT_URL="https://pablo-secrets.vault.azure.net" \
    VAULT_BRIDGE_TOKEN="$BRIDGE_TOKEN" \
    ENVIRONMENT="production"
```

### 5.4 Get Your App URL

```bash
APP_URL=$(az containerapp show \
  --name $APP_NAME \
  --resource-group $RG \
  --query properties.configuration.ingress.fqdn -o tsv)

echo "Your MCP endpoint: https://$APP_URL/sse"
```

---

## 6. Cold Start Consideration

Since the app scales to zero, the first request after idle will have a cold start (~5-15 seconds while the container boots). This is fine for your use case — Claude is making a tool call, not a human staring at a loading spinner.

If cold starts bother you, set `--min-replicas 1` to keep it warm. At idle rates, that's roughly $2-4/month — still well within your credits.

---

## 7. Connecting to Claude

### 7.1 Add as Remote MCP Server

In Claude Desktop or claude.ai Settings → Connectors → MCP Servers, add:

```json
{
  "name": "Vault Bridge",
  "url": "https://<your-app-url>/sse",
  "headers": {
    "Authorization": "Bearer <your-BRIDGE_TOKEN>"
  }
}
```

Since this is a remote MCP server, it syncs account-wide — available on iOS, web, and desktop automatically.

### 7.2 Verify Connection

Open any Claude conversation and ask:
```
Use the Vault Bridge to list available secrets.
```

You should get back your secret names.

---

## 8. Layered Intelligence — Separating Keys from Knowledge

The Vault Bridge is intentionally dumb — it's a key locker. It should never know
which model to use for image generation or when to pick OpenRouter over Azure OpenAI.
That "taste" layer lives elsewhere.

### 8.1 The Three Layers

```
┌─────────────────────────────────────────────────────────┐
│  Layer 3: Your explicit instruction                     │
│  "Use DALL-E for this one"                              │
│  ↑ overrides everything                                 │
├─────────────────────────────────────────────────────────┤
│  Layer 2: Project CLAUDE.md                             │
│  "This project uses Supabase + Stripe"                  │
│  Lists which secrets this specific repo needs           │
│  ↑ overrides global defaults                            │
├─────────────────────────────────────────────────────────┤
│  Layer 1: Context Injector (ai-services.md)             │
│  Global defaults, model capabilities, service routing   │
│  "For image gen, default to Google Vertex Nano Banana 2"│
│  "For embeddings, default to OpenRouter"                │
└─────────────────────────────────────────────────────────┘
         ↕ all layers call ↕
┌─────────────────────────────────────────────────────────┐
│  Vault Bridge MCP                                       │
│  Pure credential fetch — no opinions, no routing        │
└─────────────────────────────────────────────────────────┘
```

### 8.2 Context Injector File: `ai-services.md`

This lives in your Personal Context Injector MCP server (already hosted on Azure).
It gets injected into every Claude session, giving Claude global knowledge about
your AI service preferences and how to find the right keys.

```markdown
# AI Services — Defaults & Capabilities

When you need to call an external AI service, use these defaults unless I say
otherwise or a project's CLAUDE.md specifies a different service.

All credentials are available via the Vault Bridge MCP server.
Use `list_secrets()` to discover keys, `set_env()` to load them.

## Image Generation

**Default: Google Imagen (Nano Banana 2) via Vertex AI**
- Secret: `google-vertex-api-key`
- Capabilities: text-to-image, image editing with prompt, reference images,
  style transfer, inpainting
- Endpoint: Vertex AI `imagegeneration@006` (or latest)
- Best for: general image generation, product mockups, concept art
- Notes: Supports negative prompts, aspect ratio control, and
  seed-based reproducibility

**Alternative: OpenAI DALL-E 3**
- Secret: `openai-api-key`
- Capabilities: text-to-image only (no editing, no inpainting, no reference images)
- Best for: when I specifically request DALL-E or want its distinct style
- Notes: 1024x1024, 1024x1792, or 1792x1024 only

## LLM API Calls (when calling models programmatically)

**Default: OpenRouter**
- Secret: `openrouter-api-key`
- Use for: general LLM calls where model choice is flexible
- Notes: OpenAI-compatible API, auto-routing to best model for cost

**Alternative: Azure OpenAI**
- Secrets: `azure-openai-key` + `azure-openai-endpoint`
- Use when: I need GPT-4o specifically, or the project requires Azure's SLA
- Notes: Deployed models only, not all models available

## Embeddings

**Default: OpenRouter**
- Secret: `openrouter-api-key`
- Model: whatever is cost-optimal for the task

**Alternative: OpenAI text-embedding-3-small**
- Secret: `openai-api-key`
- Use when: I need OpenAI-compatible embeddings specifically, or the project
  already uses OpenAI's embedding format (e.g., Open Brain uses pgvector
  with OpenRouter embeddings — don't mix providers without asking)

## Payments

**Default: Stripe**
- Secrets: `stripe-api-key` (also check for `stripe-webhook-secret` if handling webhooks)
- Notes: Use test mode keys for development unless I say otherwise

## Database / Backend

**Default: Supabase**
- Secrets: `supabase-url`, `supabase-anon-key`, `supabase-service-role-key`
- Notes: Service role key bypasses RLS — only use server-side, never expose to client

## Source Control

- Secret: `github-pat`
- Notes: Scoped token, check permissions before assuming it can do everything
```

### 8.3 Project-Level CLAUDE.md — Per-Repo Overrides

Each repo gets a CLAUDE.md that specifies only what's different from the globals.
This keeps it short and avoids repeating the full service catalog.

```markdown
## Secrets & Environment Variables

This project requires API keys at runtime. **Never ask me to paste credentials.**

When you need an API key, connection string, or secret:

1. Use `list_secrets()` from the Vault Bridge MCP server to see what's available
2. Use `set_env("secret-name")` to get an export command, then execute it
3. The secret is now available as an environment variable for the session

### Rules for handling secrets:
- NEVER echo, print, log, or display secret values
- NEVER include secrets in git commits, comments, or documentation
- NEVER hardcode secrets in source files — always use environment variables
- Use `set_env()` over `get_secret()` when possible — it keeps the value out of
  your visible output
- If a test or build fails due to a missing credential, check `list_secrets()`
  and set the appropriate env var before retrying

### Secrets used by this project:
- `stripe-api-key` → `STRIPE_API_KEY`
- `supabase-url` → `SUPABASE_URL`
- `supabase-anon-key` → `SUPABASE_ANON_KEY`

### Service overrides for this project:
- Image generation: Use DALL-E 3 (this project's style guide is built around it)
- LLM calls: Use Azure OpenAI GPT-4o (client requires Azure SLA)
```

### 8.4 Why This Layering Matters

- **Vault Bridge never changes** when you switch models or providers. It's pure
  infrastructure — stable and boring.
- **`ai-services.md` is your single source of truth** for "how should Claude use
  my services." Update one file, every session picks it up.
- **Project CLAUDE.md handles exceptions.** Most repos use the defaults. The ones
  that don't say so explicitly.
- **Your voice overrides everything.** "Use DALL-E for this" always wins.

This also means adding a new service is two steps: add the secret to Key Vault,
add the service entry to `ai-services.md`. No code changes, no redeployments.

---

## 9. Security Model

### What's protected:

| Layer | Protection |
|-------|-----------|
| **Secrets at rest** | Azure Key Vault encryption (FIPS 140 validated) |
| **Server ↔ Vault** | Managed Identity — no credentials stored anywhere |
| **Claude ↔ Server** | Bearer token over HTTPS |
| **Access scope** | "Key Vault Secrets User" role = read-only, no delete/create |

### What's NOT protected (acceptable risks for a solo dev):

| Risk | Mitigation |
|------|-----------|
| Secret passes through Claude's context window | Instruct Claude to never display values; use `set_env()` pattern |
| Bearer token stored in Claude's MCP config | Rotate periodically; only you have access to your Claude account |
| Someone with your Claude account can access secrets | Same risk as someone with your laptop — secure your account |

### If you want to harden later:

- **IP restriction:** Lock the Container App to only accept requests from Anthropic's IP ranges (if they publish them)
- **Audit logging:** Enable Key Vault diagnostic logging to a Log Analytics workspace — see who accessed what, when
- **Token rotation:** Script a monthly token rotation via `az containerapp update`
- **Secret-level access:** Use Key Vault access policies to restrict which secrets the managed identity can read (e.g., separate vaults for prod vs dev)

---

## 10. Day-to-Day Workflow

### Adding a new secret:
```bash
az keyvault secret set --vault-name pablo-secrets \
  --name "new-service-api-key" --value "..." \
  --tags service=new-service purpose=whatever
```
No MCP server restart, no redeployment. Claude can immediately access it.

If the new service needs routing logic (defaults, capabilities, when to use it),
add an entry to `ai-services.md` in your Context Injector. Two steps total.

### Rotating a secret:
```bash
az keyvault secret set --vault-name pablo-secrets --name "stripe-api-key" --value "sk_live_new..."
```
Key Vault automatically versions it. The MCP server always fetches the latest.

### Viewing audit trail:
```bash
az monitor diagnostic-settings create \
  --resource $(az keyvault show --name pablo-secrets --query id -o tsv) \
  --name "vault-logs" \
  --logs '[{"category":"AuditEvent","enabled":true}]' \
  --workspace <log-analytics-workspace-id>
```

### Checking what Claude has access to:
Ask Claude: "List my available secrets from Vault Bridge"

---

## 11. What This Unlocks

With this running, here's what your mobile workflow looks like:

1. **Phone → Claude Code → "Build me a Stripe checkout page for my side project"**
   - Claude connects your repo
   - Runs `set_env("stripe-api-key")` automatically
   - Builds the feature, runs tests, creates a PR
   - You review from your phone

2. **Phone → Claude Code → "Fix the failing Supabase integration tests"**
   - Claude sees tests need `SUPABASE_URL` and `SUPABASE_ANON_KEY`
   - Fetches both via `set_env()`, runs tests, identifies the bug, fixes it
   - You approve the PR from the couch

3. **Phone → Claude Code → "Set up a new Next.js project with Clerk auth"**
   - Claude scaffolds the project
   - Checks `list_secrets()` for any Clerk keys
   - If missing, tells you: "I need a `clerk-publishable-key` — add it to your vault and I'll continue"
   - You run one `az keyvault secret set` command, Claude picks it up

No laptop required. No secret pasting. Fire and forget.

---

## 12. Implementation Checklist

### Phase 1: Infrastructure
- [ ] Create Azure resource group (`vault-bridge-rg`)
- [ ] Create Azure Key Vault (`pablo-secrets`)
- [ ] Add initial secrets to vault (with tags for service/purpose)

### Phase 2: MCP Server
- [ ] Write MCP server code (`server.py`)
- [ ] Create Dockerfile
- [ ] Create Container Apps environment
- [ ] Deploy container app
- [ ] Enable managed identity on container app
- [ ] Grant Key Vault Secrets User role to managed identity
- [ ] Set environment variables on container app
- [ ] Generate and save bearer token

### Phase 3: Connect & Test
- [ ] Add MCP server to Claude settings (remote, syncs everywhere)
- [ ] Test from Claude web: `list_secrets()` and `set_env()`
- [ ] Test from Claude iOS: same tools
- [ ] Verify `list_secrets()` returns tags/metadata alongside names

### Phase 4: Intelligence Layer
- [ ] Create `ai-services.md` in your Personal Context Injector
- [ ] Add service defaults (image gen, LLM, embeddings, etc.)
- [ ] Add capability notes (what each service supports)
- [ ] Add CLAUDE.md secret instructions to your active repos
- [ ] Add per-project service overrides where needed

### Phase 5: Hardening (Optional)
- [ ] Enable Key Vault audit logging
- [ ] Set up CI/CD for the MCP server itself
- [ ] Script monthly bearer token rotation
