# Deployment and Rollback Process

## Deployment pipeline

All services deploy through the CI/CD pipeline. A deployment builds an immutable container image tagged
with the git commit SHA, runs the integration test suite, and deploys to staging. Production deploys
require a passing staging soak of at least 30 minutes.

Production deployments use a rolling strategy with a minimum healthy percentage of 100% and a maximum of
200%, so capacity never drops during a deploy.

## Deployment windows

Production deployments are allowed Monday to Thursday between 09:00 and 16:00 UTC. Deployments outside
this window require approval from the Engineering Manager on duty. Friday deployments are not permitted
for Tier 1 services.

## Rollback policy

- Any engineer may *propose* a rollback, but executing a rollback in production requires explicit
  approval from the on-call engineer for the affected service.
- A rollback must target the most recent version that was healthy in production; skipping more than
  two versions back requires approval from the service owner.
- After approval, the rollback is executed through the deployment tool, which re-validates that the
  service is still running the version being rolled back from. If the running version changed after
  approval, the rollback is aborted and must be re-approved.
- Every rollback must be recorded in the incident report with the from-version, to-version, approver
  and outcome.

## Post-deployment verification

For 30 minutes after a production deploy, watch the service's error rate and latency dashboards. The
deployment is considered failed if the 5xx rate exceeds twice its pre-deploy baseline.
