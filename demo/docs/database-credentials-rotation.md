# Database Credential Rotation (Restricted: SRE only)

Access scope: sre-restricted.

## Schedule

Database credentials for `orders-db` and `checkout-db` are rotated every 30 days by the SRE team.

## Procedure

1. Trigger rotation in AWS Secrets Manager for the secret `prod/orders-db/app-user`.
2. Wait for the rotation Lambda to report success (typically under 2 minutes).
3. Perform a rolling restart of the consuming service so new connections use the new password.
4. Confirm the old credential has been revoked by checking `pg_stat_activity` for sessions using it.

The break-glass database administrator account is `dba_breakglass`; its use must be approved by the
Head of SRE and is audited.
