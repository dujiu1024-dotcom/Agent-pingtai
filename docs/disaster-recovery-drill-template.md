# Disaster Recovery Drill Record

> Copy this file for each tabletop, component restore or full recovery exercise.
> Do not include credentials, secret values, customer data or sensitive endpoint
> details. Store detailed evidence in the approved restricted incident system.

## Exercise metadata

- Exercise ID:
- Date / timezone:
- Facilitator / incident commander:
- Participants / independent reviewer:
- Scope: tabletop / component restore / full platform / failover / failback
- Environment: isolated recovery environment (name/reference only):
- Related change / incident:
- Approved recovery objectives (RPO / RTO):

## Scenario and assumptions

- Failure injected or scenario:
- Components in scope:
- Primary-site fencing method:
- Backup set selected (manifest references and timestamps):
- Encryption identity availability confirmed: yes / no
- Application writers and consumers frozen at (UTC):

## Timeline (UTC)

| Event | Start | End | Elapsed | Evidence reference |
| --- | --- | --- | --- | --- |
| Incident declared | | | | |
| Primary fenced / writers stopped | | | | |
| Dependencies and configuration ready | | | | |
| PostgreSQL restored and checked | | | | |
| Redis restored and checked | | | | |
| Objects restored and checked | | | | |
| Application validation passed | | | | |
| Traffic resumed | | | | |

## Results

- Last known good write / snapshot watermark:
- Actual recovery point:
- **Measured RPO:**
- **Measured RTO:**
- Target RPO / RTO met: yes / no
- PostgreSQL integrity / migration checks:
- Redis state / ownership checks:
- Object inventory / sampled hash checks:
- Cross-component reference and outbox reconciliation:
- Tenant isolation / authorization smoke tests:
- Data loss, duplicate effects or manual repairs:
- Failover/failback fencing and routing evidence:

## Findings and actions

| Finding / gap | Severity | Owner | Due date | Tracking issue | Closure evidence |
| --- | --- | --- | --- | --- | --- |
| | | | | | |

- Overall result: pass / pass with actions / fail
- Reviewer sign-off:
- Follow-up exercise date:
