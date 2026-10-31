---
doc_id: "INC-2025-017"
title: "Customer Portal outage"
doc_type: "incident"
domain: "operations"
owner: "TEAM-04"
version: "1.0"
effective_date: "2025-05-08"
sensitivity: "confidential"
systems: ["Customer Portal", "API Gateway"]
severity: "SEV1"
incident_date: "2025-05-08"
resolved_by: "EMP-13478"
---

# Customer Portal outage

## Summary

On May 8, 2025, Acme Corp experienced a severe outage of the Customer Portal, classified as SEV1 (INC-2025-017). The outage lasted for 51 minutes and was caused by a deployment of a new route configuration in the API Gateway that contained a malformed rule. This incident impacted all users attempting to access the Customer Portal, preventing them from logging in and completing transactions. The change that caused the outage was rolled back immediately following the procedures outlined in SOP-OPS-011: Production Deployment and Rollback.

## Timeline

- **2025-05-08T14:00:00Z**: Deployment of new API Gateway route configuration.
- **2025-05-08T14:00:45Z**: Reports of Customer Portal unavailability begin to surface.
- **2025-05-08T14:01:00Z**: Incident Commander is notified of the outage.
- **2025-05-08T14:05:00Z**: Initial investigation reveals a malformed rule in the API Gateway configuration.
- **2025-05-08T14:10:00Z**: Rollback initiated as per SOP-OPS-011.
- **2025-05-08T14:51:00Z**: Customer Portal becomes available again after rollback completion.
- **2025-05-08T14:55:00Z**: Incident resolved by Michelle Ray (Senior Software Engineer).
- **2025-05-08T15:00:00Z**: Post-incident review scheduled.

## RCA

The root cause of the incident was determined to be a malformed routing rule introduced during the deployment of the API Gateway's configuration. This misconfiguration prevented valid requests to the Customer Portal, causing a complete service outage for all users. The issue underscores the need for improved validation procedures during deployment processes. While the rollback procedure (SOP-OPS-011) was successfully executed, the rapid deployment without adequate pre-deployment testing contributed to the incident's severity and duration.

The severity of this incident was heightened due to the critical nature of the Customer Portal in facilitating customer transactions and interactions. As such, the incident triggered a review of existing deployment practices and highlighted opportunities for improvement in monitoring and error detection during deployments.

## Actions

- **Review deployment procedures to enhance validation checks**: Due by 2025-05-22.
- **Conduct a post-incident review meeting to discuss lessons learned**: Due by 2025-05-15.
- **Update API Gateway configuration guidelines to include stricter testing protocols**: Due by 2025-05-30.
- **Evaluate the current incident response framework (POL-COMP-003: Incident Response Policy) to ensure readiness for similar incidents**: Due by 2025-05-25.
