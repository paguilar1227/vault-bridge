import os
import hmac
import logging
from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
from azure.keyvault.secrets import SecretClient
from fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

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
    description="Securely retrieves API keys and secrets from Azure Key Vault.",
)
mcp._app.add_middleware(TokenAuthMiddleware)


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
