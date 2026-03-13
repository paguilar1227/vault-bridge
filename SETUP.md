# Vault Bridge — From-Scratch Setup Guide

This walks you through deploying Vault Bridge to Azure from nothing. Every command here is **one-time** — once deployed, the GitHub Actions CI/CD pipeline handles all future updates automatically.

## Prerequisites

Before you start, you need:

- **Azure subscription** with active credits ([check here](https://portal.azure.com/#view/Microsoft_Azure_Billing/SubscriptionsBlade))
- **Azure CLI** installed and logged in:
  ```bash
  # Install (macOS)
  brew install azure-cli

  # Install (Linux)
  curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash

  # Log in
  az login
  ```
- **Docker** installed (for local testing only — CI/CD handles production builds)
- **GitHub repo** at `paguilar1227/vault-bridge` (or update the OIDC script if different)

## Cost

| Resource | Monthly Cost |
|----------|-------------|
| Azure Container Registry (Basic) — shared across all projects | ~$5.00 |
| Azure Key Vault (Standard) | ~$0.01 |
| Azure Container Apps (Consumption, scale-to-zero) | $0.00 (free tier) |
| **Total** | **~$5/month** |

The ACR is the only real cost, and it's shared — you don't pay again for your next project.

---

## Step 1: Create Resource Groups

Two resource groups keep shared infra separate from project-specific resources. `pablo-rg` is your account-wide shared group. `vault-bridge-rg` is just for this project.

```bash
az group create --name pablo-rg --location centralus
az group create --name vault-bridge-rg --location centralus
```

## Step 2: Deploy Shared ACR

This creates your shared Azure Container Registry. Every project you build pushes images here — you only do this once, ever.

```bash
az deployment group create \
  --resource-group pablo-rg \
  --template-file infra/shared/azuredeploy.json \
  --parameters @infra/shared/azuredeploy.parameters.json
```

**What this creates:**
- `pabloregistry.azurecr.io` — Basic tier ACR, admin access disabled (images are pulled via managed identity)

## Step 3: Push the Initial Docker Image

The Container App needs an image in ACR before it can start. Push one manually this first time — CI/CD handles it after that.

```bash
# Log into the registry
az acr login --name pabloregistry

# Build and push (--platform is required on Apple Silicon Macs — Azure Container Apps runs linux/amd64)
docker build --platform linux/amd64 -t pabloregistry.azurecr.io/vault-bridge:latest .
docker push pabloregistry.azurecr.io/vault-bridge:latest
```

> **Apple Silicon note:** If you skip `--platform linux/amd64`, Docker builds a `linux/arm64` image by default. The push will succeed, but the Container App will fail in Step 4 with: `image OS/Arc must be linux/amd64 but found linux/arm64`.

## Step 4: Deploy Vault Bridge Infrastructure

This creates the Key Vault, Container Apps Environment, Container App, managed identity, and all the role assignments (Key Vault Secrets User + AcrPull) in one shot.

You'll be prompted for the bearer token — generate one and **save it somewhere safe** (you'll need it to connect Claude later).

```bash
# Generate a token
BRIDGE_TOKEN=$(openssl rand -hex 32)
echo "Save this token: $BRIDGE_TOKEN"

# Deploy everything
az deployment group create \
  --resource-group vault-bridge-rg \
  --template-file infra/vault-bridge/azuredeploy.json \
  --parameters @infra/vault-bridge/azuredeploy.parameters.json \
  --parameters vaultBridgeToken="$BRIDGE_TOKEN"
```

> **First-deploy timing issue:** The ARM template creates the Container App and its AcrPull role assignment in parallel. On first deploy, the role assignment may not propagate before the Container App tries to pull the image, causing an `unable to pull image using Managed identity` error. If this happens, just re-run the same `az deployment group create` command — the second attempt succeeds because the role assignment has had time to propagate. The re-run may report `RoleAssignmentExists` as an error, but that's harmless — the Container App will be running.

**What this creates:**
- `pablo-secrets` — Key Vault (Standard, RBAC-enabled, soft delete on)
- `vault-bridge-env` — Container Apps Environment
- `vault-bridge` — Container App with system-assigned managed identity
- Role assignment: Container App → Key Vault Secrets User (read-only secret access)
- Role assignment: Container App → AcrPull on `pabloregistry` (cross-resource-group)

**Get your app URL after deployment:**

```bash
az containerapp show \
  --name vault-bridge \
  --resource-group vault-bridge-rg \
  --query properties.configuration.ingress.fqdn -o tsv
```

Your MCP endpoint will be `https://<that-fqdn>/sse`.

## Step 5: Add Secrets to Key Vault

Before Claude can fetch anything, you need secrets in the vault. Use tags so `list_secrets()` returns useful context.

```bash
VAULT_NAME="pablo-secrets"

az keyvault secret set --vault-name $VAULT_NAME \
  --name "stripe-api-key" --value "sk_live_..." \
  --tags service=stripe purpose=payments

az keyvault secret set --vault-name $VAULT_NAME \
  --name "supabase-url" --value "https://xxx.supabase.co" \
  --tags service=supabase purpose=database

az keyvault secret set --vault-name $VAULT_NAME \
  --name "supabase-anon-key" --value "eyJ..." \
  --tags service=supabase purpose=database-auth

# Add as many as you need — no restart or redeployment required
```

**Naming convention:** `{service}-{type}`, lowercase, hyphens only (Key Vault doesn't allow underscores). Examples: `stripe-api-key`, `github-pat`, `openrouter-api-key`.

> **Note:** You need the "Key Vault Secrets Officer" role on your own account to set secrets. If `az keyvault secret set` fails with a 403, grant yourself that role:
> ```bash
> az role assignment create \
>   --role "Key Vault Secrets Officer" \
>   --assignee $(az ad signed-in-user show --query id -o tsv) \
>   --scope $(az keyvault show --name pablo-secrets --query id -o tsv)
> ```

## Step 6: Set Up GitHub Actions CI/CD

This is the part that makes future changes automatic. Push to `main` → image builds → deploys to Azure.

### 6a: Create the OIDC App Registration

Workload Identity Federation lets GitHub Actions authenticate to Azure without stored secrets — GitHub gets short-lived tokens via OIDC.

```bash
./infra/setup-github-oidc.sh
```

The script will print three values at the end. You need all three.

### 6b: Add GitHub Secrets

Go to **Settings → Secrets and variables → Actions** in your GitHub repo and add:

| Secret Name | Value |
|-------------|-------|
| `AZURE_CLIENT_ID` | The app (client) ID from step 6a |
| `AZURE_TENANT_ID` | Your Azure tenant ID from step 6a |
| `AZURE_SUBSCRIPTION_ID` | Your subscription ID from step 6a |

That's it. The workflow at `.github/workflows/deploy.yml` is already in the repo. On the next push to `main`, it will:

1. Authenticate to Azure via OIDC
2. Log into `pabloregistry.azurecr.io`
3. Build the Docker image and push it (tagged with commit SHA + `latest`)
4. Update the Container App to the new image

## Step 7: Connect Claude

Add Vault Bridge as a custom connector in Claude. Since connectors sync account-wide, this works on iOS, web, desktop, and CLI automatically.

In **Claude Settings → Connectors → Add Custom Connector**, add:

- **Name:** `Vault Bridge`
- **URL:** `https://<your-app-fqdn>/sse?token=<your-BRIDGE_TOKEN-from-step-4>`

Leave the OAuth fields empty — auth is handled via the token in the URL.

### Verify It Works

Open any Claude conversation and ask:

> Use the Vault Bridge to list available secrets.

You should see your secret names with their service/purpose tags.

---

## After Setup: Day-to-Day Operations

Everything below happens without touching any infrastructure.

### Adding a new secret

```bash
az keyvault secret set --vault-name pablo-secrets \
  --name "new-service-api-key" --value "..." \
  --tags service=new-service purpose=whatever
```

No restart, no redeployment. Claude can access it immediately.

### Rotating a secret

```bash
az keyvault secret set --vault-name pablo-secrets \
  --name "stripe-api-key" --value "sk_live_new..."
```

Key Vault versions automatically. The MCP server always fetches the latest.

### Updating the MCP server code

Just push to `main`. GitHub Actions builds, pushes, and redeploys automatically. The Container App picks up the new image within a minute or two.

### Rotating the bearer token

```bash
NEW_TOKEN=$(openssl rand -hex 32)

az containerapp update \
  --name vault-bridge \
  --resource-group vault-bridge-rg \
  --set-env-vars VAULT_BRIDGE_TOKEN="$NEW_TOKEN"

echo "Update your Claude connector URL with: $NEW_TOKEN"
```

Then update the `?token=` parameter in your Claude connector URL.

---

## Teardown

If you ever want to remove everything:

```bash
# Project resources
az group delete --name vault-bridge-rg --yes

# Shared ACR (only if no other projects use it)
az group delete --name pablo-rg --yes

# OIDC app registration
az ad app delete --id <AZURE_CLIENT_ID>
```

## Troubleshooting

**Container App won't start / image pull errors:**

Two common causes:

1. **Wrong architecture (Apple Silicon):** If you built the image on an M-series Mac without `--platform linux/amd64`, the image is `arm64` but Container Apps needs `amd64`. Rebuild with `docker build --platform linux/amd64 ...`, push again, and re-deploy.

2. **AcrPull role not yet propagated:** The managed identity's AcrPull role assignment can take 1-2 minutes to propagate after the ARM deployment. Re-run the same `az deployment group create` command from Step 4, or manually restart the revision:
```bash
az containerapp revision restart \
  --name vault-bridge \
  --resource-group vault-bridge-rg \
  --revision $(az containerapp revision list --name vault-bridge --resource-group vault-bridge-rg --query "[0].name" -o tsv)
```

**403 from Key Vault:**
The Key Vault Secrets User role assignment also takes a minute to propagate. If `get_secret` fails right after deploy, wait and retry. If it persists, verify the role assignment:
```bash
az role assignment list \
  --scope $(az keyvault show --name pablo-secrets --query id -o tsv) \
  --output table
```

**GitHub Actions deploy fails:**
Check that all three secrets (`AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`) are set in repo settings. Verify the OIDC federated credential is scoped to `repo:paguilar1227/vault-bridge:ref:refs/heads/main`.

**Cold starts:**
The app scales to zero when idle. First request after idle takes 5-15 seconds while the container boots. This is normal — Claude is making a tool call, not a human staring at a spinner. If it bothers you, set `--min-replicas 1` (~$2-4/month).
