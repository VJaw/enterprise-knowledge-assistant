---
doc_id: "INC-2025-003"
title: "Auth token validation failures"
doc_type: "incident"
domain: "engineering"
owner: "TEAM-04"
version: "1.0"
effective_date: "2025-01-14"
sensitivity: "internal"
systems: ["Auth Service", "Secrets Vault"]
severity: "SEV2"
incident_date: "2025-01-14"
resolved_by: "EMP-13478"
---

# Auth token validation failures

## Summary

On January 14, 2025, an incident occurred involving the Auth Service and Secrets Vault resulting in authentication token validation failures. The issue arose from a signing-key rotation in the Secrets Vault that was not properly propagated to all Token Issuer replicas. As a consequence, token validation failed for 22 minutes, causing service disruption for users attempting to authenticate. This incident is classified as Severity 2 (SEV2), indicating a significant but not critical impact on service availability.

The Auth Service is crucial for user authentication across Acme Corp's platforms, and the inability to validate tokens led to an increase in failed login attempts and user frustration. The on-call engineer responded promptly, and the incident was resolved within the established timeframe, restoring normal functionality to the affected systems.

## Timeline

- 2025-01-14 14:00 UTC: Token validation failures begin after signing-key rotation in the Secrets Vault.
- 2025-01-14 14:22 UTC: Incident identified and reported by the on-call engineer.
- 2025-01-14 14:25 UTC: Incident Commander engaged to coordinate response efforts.
- 2025-01-14 14:30 UTC: Initial assessment indicates failure due to lack of key propagation.
- 2025-01-14 14:50 UTC: Michelle Ray resolves the issue by manually propagating the signing key to all Token Issuer replicas.
- 2025-01-14 14:55 UTC: Token validation resumes successfully, and service is restored.
- 2025-01-14 15:00 UTC: Post-incident review scheduled to analyze the failure and improve processes.

## RCA

The root cause analysis (RCA) of this incident indicates that the failure in token validation was directly linked to a signing-key rotation that did not propagate correctly across all Token Issuer replicas. The Secrets Vault performed its operation as intended; however, the communication mechanism responsible for ensuring that all replicas received the updated signing key experienced a delay.

This issue was compounded by the lack of a monitoring alert that could have proactively identified the failure in key propagation, leading to a longer period of user disruption. The incident highlights a gap in the existing processes for managing key rotations and the need for improved observability in the Auth Service and Secrets Vault systems.

This incident also underscores the importance of adhering to established policies, such as POL-COMP-002: Acceptable Use Policy, which emphasizes the need for robust security practices, and POL-COMP-006: Change Management Policy, which outlines the protocols for managing changes in production environments.

## Actions

- Review and enhance the key propagation mechanism within the Secrets Vault to ensure reliable updates across all Token Issuer replicas.  
  **Due Date:** 2025-01-28

- Implement additional monitoring and alerting systems to detect failures in token validation and key propagation in real-time.  
  **Due Date:** 2025-02-15

- Conduct a training session for the owning team to reinforce best practices regarding key management and incident response, referencing POL-COMP-003: Incident Response Policy.  
  **Due Date:** 2025-02-20

- Schedule a follow-up review of the incident with relevant stakeholders to discuss findings and improvements to the incident response process.  
  **Due Date:** 2025-02-25
