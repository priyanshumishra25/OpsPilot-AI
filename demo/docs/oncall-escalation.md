# On-call and Escalation Policy

## Rotations

- Commerce Core on-call covers `orders-api` and `inventory`.
- Payments Platform on-call covers `checkout-api` and the payment provider integration.
- Rotations are weekly and hand over on Mondays at 10:00 UTC.

## Severity levels

- **SEV-1**: Customers cannot place orders at all. Page the incident commander immediately and open a
  bridge call. Status page update within 15 minutes.
- **SEV-2**: Significant degradation (for example error rate above 5% or checkout latency doubled).
  Page the owning team's on-call. Status page update within 30 minutes.
- **SEV-3**: Minor or internal-only impact. Handle during business hours.

## Escalation

If the primary on-call does not acknowledge a page within 10 minutes, the page escalates to the
secondary on-call. After a further 15 minutes it escalates to the Engineering Manager.
