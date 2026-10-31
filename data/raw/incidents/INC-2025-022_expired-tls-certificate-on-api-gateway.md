---
doc_id: "INC-2025-022"
title: "Expired TLS certificate on API Gateway"
doc_type: "incident"
domain: "operations"
owner: "TEAM-04"
version: "1.0"
effective_date: "2025-06-16"
sensitivity: "internal"
systems: ["API Gateway"]
severity: "SEV2"
incident_date: "2025-06-16"
resolved_by: "EMP-64987"
---

# Expired TLS certificate on API Gateway

## Summary

On June 16, 2025, an expired TLS certificate on the API Gateway resulted in 3% of clients experiencing failures in TLS handshakes for a duration of 2 hours. The issue was exacerbated by the fact that the renewal automation for the TLS certificates had been failing silently, preventing timely updates and leading to the service disruption. This incident was classified as a Severity 2 (SEV2) due to the moderate impact on client connections, which affected service availability and user experience.

The owning team initiated an investigation upon detection of the issue, leading to a swift resolution through manual intervention. The incident highlighted the importance of monitoring and alerting mechanisms related to certificate management.

## Timeline

- 2025-06-16 12:00 UTC: Expired TLS certificate detected on API Gateway.
- 2025-06-16 12:05 UTC: Initial reports of TLS handshake failures begin to surface.
- 2025-06-16 12:15 UTC: Monitoring systems confirm a 3% failure rate in client connections.
- 2025-06-16 12:30 UTC: Incident Commander is notified of the issue.
- 2025-06-16 12:45 UTC: Investigation begins; root cause analysis initiated.
- 2025-06-16 13:00 UTC: Confirmation that renewal automation had been failing silently.
- 2025-06-16 14:00 UTC: Cynthia Diaz (Senior SRE) manually renews the expired TLS certificate.
- 2025-06-16 14:05 UTC: TLS handshake failures resolved; client connections restored.
- 2025-06-16 14:30 UTC: Incident officially closed.

## RCA

The root cause of the incident was the expiration of a TLS certificate for the API Gateway. The certificate renewal automation, which is designed to manage the lifecycle of TLS certificates, had been failing silently. This failure went unnoticed until expiration occurred, resulting in a disruption of service for approximately 3% of clients due to TLS handshake failures.

The following factors contributed to the incident:
- **Silent failure of renewal automation:** The automation tool responsible for renewing the TLS certificate did not report errors, leading to a lack of awareness regarding its operational status.
- **Lack of proactive monitoring:** There were insufficient monitoring and alerting mechanisms in place to notify the owning team of impending certificate expirations or failures in the renewal process.

This incident underscores the need for enhanced monitoring of automated systems, particularly those related to security and client connectivity.

## Actions

- **Implement enhanced monitoring for TLS certificate renewal processes**: Review and update existing monitoring tools to ensure they can detect and alert on failures in certificate renewal automation. Due date: 2025-07-01.
- **Conduct a review of existing certificate management policies**: Align with relevant documentation, specifically POL-COMP-005: Encryption and Key Management Policy, to ensure compliance and best practices are followed. Due date: 2025-07-15.
- **Establish a manual verification process for critical certificates**: Develop a procedure to manually verify the status of critical TLS certificates on a regular basis to mitigate risks associated with automated failures. Due date: 2025-07-30.
- **Update the Incident Response Policy**: Review and revise the current Incident Response Policy (POL-COMP-003) to include specific guidelines for handling incidents related to TLS certificate management. Due date: 2025-08-15.
