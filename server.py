import os
import hmac
import logging
from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
from azure.keyvault.secrets import SecretClient
from fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from vault_bridge.tools import (
    vault_get_secret,
    vault_help,
    vault_list_secrets,
    vault_set_env,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vault-bridge")

# --- Config ---
VAULT_URL = os.environ["AZURE_VAULT_URL"]  # e.g. https://pablo-secrets.vault.azure.net
BEARER_TOKEN = os.environ["VAULT_BRIDGE_TOKEN"]  # Auth token for MCP connection
ENV = os.environ.get("ENVIRONMENT", "production")

# --- Azure Key Vault client ---
# Uses Managed Identity in Azure, falls back to CLI creds locally
credential = (
    ManagedIdentityCredential()
    if ENV == "production"
    else DefaultAzureCredential()
)
vault_client = SecretClient(vault_url=VAULT_URL, credential=credential)


# --- Auth middleware: accepts token via header OR query param ---
class TokenAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        token = request.query_params.get("token")
        if not token:
            auth = request.headers.get("authorization", "")
            if auth.startswith("Bearer "):
                token = auth[7:]
        if not token or not hmac.compare_digest(token, BEARER_TOKEN):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


# --- MCP Server ---
mcp = FastMCP(
    "Vault Bridge",
    instructions="Securely retrieves API keys and secrets from Azure Key Vault.",
)


@mcp.tool()
def help() -> str:
    """Get help on what Vault Bridge is and how to use it.

    Call this tool to learn about the MCP server's purpose, available tools,
    and how to add new secrets to the vault.
    """
    return vault_help()


@mcp.tool()
def get_secret(name: str) -> str:
    """Retrieve a secret value by name.

    Use this to get API keys, connection strings, and other credentials
    needed to build, test, or run applications.

    IMPORTANT: The returned value is sensitive. Never display it in output,
    echo it to the terminal, log it, or include it in commits. Use it only
    by setting it as an environment variable or passing it directly to the
    service that needs it.

    Args:
        name: The secret name (lowercase, hyphens). Example: "stripe-api-key"
    """
    return vault_get_secret(name, vault_client=vault_client)


@mcp.tool()
def list_secrets() -> list[dict]:
    """List all available secrets with their metadata.

    Returns secret names and tags (service, purpose) but NOT values.
    Use this to discover what credentials are available before calling
    get_secret() or set_env().
    """
    return vault_list_secrets(vault_client=vault_client)


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
    return vault_set_env(name, env_var=env_var, vault_client=vault_client)


# --- ASGI app: FastMCP HTTP app + auth middleware ---
app = mcp.http_app(path="/sse")
app.add_middleware(TokenAuthMiddleware)
