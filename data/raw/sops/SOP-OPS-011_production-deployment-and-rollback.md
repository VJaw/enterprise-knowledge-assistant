---
doc_id: "SOP-OPS-011"
title: "Production Deployment and Rollback"
doc_type: "sop"
domain: "operations"
owner: "TEAM-04"
version: "3.2"
effective_date: "2025-04-02"
sensitivity: "internal"
systems: ["API Gateway", "Payment Gateway"]
---

# Production Deployment and Rollback

## Purpose
The purpose of this Standard Operating Procedure (SOP) is to provide a systematic approach for deploying and rolling back changes in the API Gateway and Payment Gateway systems at Acme Corp. This document ensures that all deployments are conducted safely and efficiently, minimizing disruption to services and maintaining system integrity.

## Scope
This procedure applies to all deployments and rollbacks involving the API Gateway and Payment Gateway systems. It is intended for use by any personnel involved in the deployment process, including but not limited to engineers, on-call support staff, and Incident Commanders.

## Procedure

### 3.1 Deployment Process
1. **Pre-Deployment Checks**  
   - Ensure that the code has passed all necessary quality assurance tests.
   - Confirm that no deployments are scheduled during the change freeze as defined in POL-COMP-006: Change Management Policy.

2. **Initiate Deployment**  
   - Begin the deployment process by pushing the code to the designated environment.
   - The deployment will start as a canary release, targeting 5% of incoming traffic for the first 15 minutes.

3. **Monitor Canary Release**  
   - During the canary phase, closely monitor the error rate across the API Gateway and Payment Gateway systems.
   - If the error rate exceeds 1% during this period, the deployment will automatically roll back to the previous stable version.

4. **Full Deployment**  
   - If the canary release is successful (error rate ≤ 1%), proceed to complete the deployment to all traffic after the canary period.

### 3.2 Rollback Process
1. **Automatic Rollback**  
   - In the event that the canary deployment fails (error rate > 1%), the system will initiate an automatic rollback to the last stable version. 
   - Ensure that the rollback is completed and verify that services are restored to their previous functioning state.

2. **Manual Rollback**  
   - If a manual rollback is necessary outside of the canary phase, follow the rollback instructions outlined in MAN-ENG-001: Payment Gateway Runbook.
   - Document the reasons for the rollback and any anomalies encountered during the deployment.

### 3.3 Post-Deployment Verification
1. **Verification Checks**  
   - After a successful deployment, conduct verification checks as per the guidelines in MAN-ENG-002: Auth Service Architecture and MAN-ENG-006: API Gateway Configuration Guide.
   - Ensure that all services are operational and that there are no outstanding incidents.

2. **Monitoring and Alerts**  
   - Set up monitoring and alerting as described in MAN-ENG-004: Observability Handbook to track the performance and stability of the API Gateway and Payment Gateway post-deployment.

## Escalation
If issues arise during the deployment or rollback that cannot be resolved by the on-call engineer, escalate the situation to the Incident Commander. The Incident Commander will follow the procedures outlined in POL-COMP-003: Incident Response Policy to manage the incident effectively.

## Verification
Post-deployment, the following verification steps must be completed:
- Review logs for any anomalies, including audit logs as per INC-2025-044: Audit log ingestion gap.
- Confirm that all dependent services are functioning, with specific attention to any incidents that may have been reported, including but not limited to INC-2025-031: Payment latency spike and INC-2025-026: Data Warehouse nightly load delay.
- Ensure compliance with data handling policies as outlined in POL-COMP-008: Payment Card Data Handling Policy.

## References
- POL-COMP-001: Data Retention Policy
- POL-COMP-006: Change Management Policy
- POL-COMP-003: Incident Response Policy
- MAN-ENG-001: Payment Gateway Runbook
