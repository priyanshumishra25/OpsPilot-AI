#!/usr/bin/env bash
# Writes application secret values into the Secrets Manager containers created by Terraform, so values never
# enter Terraform state. Run once per environment (and again to rotate). Requires the AWS CLI with permission to
# call secretsmanager:PutSecretValue on these secrets.
#
#   ENV=dev ANTHROPIC_API_KEY=... GITHUB_TOKEN=... ./infra/scripts/put-secrets.sh
#
# Unset values are generated where that is safe (JWT secret, MCP key pair, control-plane tokens); the Anthropic key
# and GitHub token are only written when provided.
set -euo pipefail
: "${ENV:?set ENV=dev|prod}"
PREFIX="opspilot-${ENV}"

put() {  # name value
  aws secretsmanager put-secret-value --secret-id "${PREFIX}/$1" --secret-string "$2" >/dev/null
  echo "wrote ${PREFIX}/$1"
}
rand() { python3 -c 'import secrets; print(secrets.token_urlsafe(48))'; }

put jwt-secret "${OPSPILOT_JWT_SECRET:-$(rand)}"
if [[ -z "${MCP_SIGNING_PRIVATE_KEY:-}" ]]; then
  read -r MCP_SIGNING_PRIVATE_KEY MCP_VERIFY_PUBLIC_KEY < <(python3 - <<'PY'
import base64
from cryptography.hazmat.primitives import serialization as s
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
k = Ed25519PrivateKey.generate()
print(base64.b64encode(k.private_bytes(s.Encoding.Raw, s.PrivateFormat.Raw, s.NoEncryption())).decode(),
      base64.b64encode(k.public_key().public_bytes(s.Encoding.Raw, s.PublicFormat.Raw)).decode())
PY
)
fi
put mcp-signing-private-key "$MCP_SIGNING_PRIVATE_KEY"
put mcp-verify-public-key "$MCP_VERIFY_PUBLIC_KEY"
for t in read deploy admin; do put "control-plane-${t}-token" "$(rand)"; done
# Optional credentials: the sentinel "unset" lets tasks start; the app treats it as not configured.
put anthropic-api-key "${ANTHROPIC_API_KEY:-unset}"
put github-token "${GITHUB_TOKEN:-unset}"
