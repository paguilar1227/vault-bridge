"""Pure tool functions for Azure Key Vault access.

Each function accepts a ``vault_client`` keyword argument (an
``azure.keyvault.secrets.SecretClient`` instance) so callers can inject
their own client — no module-level globals required.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from azure.keyvault.secrets import SecretClient

logger = logging.getLogger("vault-bridge")


def vault_help() -> str:
    """Return help text describing Vault Bridge and its available tools."""
    return """
# Vault Bridge

An MCP server that provides secure access to API keys and secrets stored in Azure Key Vault.

## Available Tools

- **get_secret(name)** — Retrieve a secret value by name
- **list_secrets()** — List all secrets with their service/purpose tags (no values)
- **set_env(name)** — Get an export command to set a secret as an environment variable
- **help()** — This help text

## Adding a New Secret

Use the Azure CLI:

```
az keyvault secret set \\
  --vault-name <your-vault> \\
  --name <secret-name> \\
  --value "<secret-value>" \\
  --tags service="<service-name>" purpose="<comma-separated-purposes>"
```

**Naming:** lowercase with hyphens (e.g., `stripe-api-key`).
**Tags:** Always set `service` and `purpose` so `list_secrets()` returns useful context.

The new secret is available immediately — no server restart needed.
""".strip()


def vault_get_secret(name: str, *, vault_client: SecretClient) -> str:
    """Retrieve a secret value by name.

    Args:
        name: The secret name (lowercase, hyphens). Example: ``"stripe-api-key"``
        vault_client: An Azure ``SecretClient`` instance.
    """
    try:
        secret = vault_client.get_secret(name)
        logger.info(f"Secret retrieved: {name}")
        return secret.value
    except Exception as e:
        logger.error(f"Failed to retrieve secret '{name}': {e}")
        return f"ERROR: Secret '{name}' not found. Use list_secrets() to see available names."


def vault_list_secrets(*, vault_client: SecretClient) -> list[dict]:
    """List all available secrets with their metadata.

    Returns secret names and tags (service, purpose) but NOT values.

    Args:
        vault_client: An Azure ``SecretClient`` instance.
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


def vault_set_env(
    name: str, env_var: str | None = None, *, vault_client: SecretClient
) -> str:
    """Retrieve a secret and return a shell export command.

    Args:
        name: The secret name. Example: ``"stripe-api-key"``
        env_var: Optional env var name override. Defaults to uppercased name
                 with hyphens converted to underscores.
        vault_client: An Azure ``SecretClient`` instance.
    """
    try:
        secret = vault_client.get_secret(name)
        var_name = env_var or name.upper().replace("-", "_")
        return f'export {var_name}="{secret.value}"'
    except Exception:
        return f"ERROR: Secret '{name}' not found."
