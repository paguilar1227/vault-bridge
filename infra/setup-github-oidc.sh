#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# One-time setup: Workload Identity Federation for GitHub Actions → Azure
#
# This creates an Entra ID app registration with federated credentials so
# GitHub Actions can authenticate to Azure without stored secrets.
#
# Prerequisites:
#   - az cli installed and logged in (az login)
#   - Permissions to create app registrations in your Entra ID tenant
#
# Usage:
#   ./infra/setup-github-oidc.sh
# ============================================================================

# --- Configuration -----------------------------------------------------------
GITHUB_ORG="paguilar1227"
GITHUB_REPO="vault-bridge"
APP_NAME="github-actions-vault-bridge"
SHARED_RG="pablo-rg"
PROJECT_RG="vault-bridge-rg"

# --- Create App Registration ------------------------------------------------
echo "Creating Entra ID app registration: $APP_NAME"
APP_ID=$(az ad app create --display-name "$APP_NAME" --query appId -o tsv)
echo "App (client) ID: $APP_ID"

# Create the service principal
echo "Creating service principal..."
SP_OBJECT_ID=$(az ad sp create --id "$APP_ID" --query id -o tsv)
echo "Service principal object ID: $SP_OBJECT_ID"

# --- Add Federated Credential for main branch pushes ------------------------
echo "Adding federated credential for main branch..."
az ad app federated-credential create --id "$APP_ID" --parameters "{
  \"name\": \"github-actions-main\",
  \"issuer\": \"https://token.actions.githubusercontent.com\",
  \"subject\": \"repo:${GITHUB_ORG}/${GITHUB_REPO}:ref:refs/heads/main\",
  \"audiences\": [\"api://AzureADTokenExchange\"]
}"

# --- Grant roles on both resource groups -------------------------------------
SUBSCRIPTION_ID=$(az account show --query id -o tsv)

echo "Granting Contributor on $SHARED_RG..."
az role assignment create \
  --assignee-object-id "$SP_OBJECT_ID" \
  --assignee-principal-type ServicePrincipal \
  --role "AcrPush" \
  --scope "/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$SHARED_RG"

echo "Granting Contributor on $PROJECT_RG..."
az role assignment create \
  --assignee-object-id "$SP_OBJECT_ID" \
  --assignee-principal-type ServicePrincipal \
  --role "Contributor" \
  --scope "/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$PROJECT_RG"

# --- Print summary -----------------------------------------------------------
TENANT_ID=$(az account show --query tenantId -o tsv)

echo ""
echo "============================================"
echo "  Setup complete! Add these as GitHub secrets:"
echo "============================================"
echo ""
echo "  AZURE_CLIENT_ID      = $APP_ID"
echo "  AZURE_TENANT_ID      = $TENANT_ID"
echo "  AZURE_SUBSCRIPTION_ID = $SUBSCRIPTION_ID"
echo ""
echo "Go to: https://github.com/$GITHUB_ORG/$GITHUB_REPO/settings/secrets/actions"
echo ""
