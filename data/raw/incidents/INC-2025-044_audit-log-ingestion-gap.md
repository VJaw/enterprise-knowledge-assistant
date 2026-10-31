---
doc_id: "INC-2025-044"
title: "Audit log ingestion gap"
doc_type: "incident"
domain: "engineering"
owner: "TEAM-04"
version: "1.0"
effective_date: "2025-11-18"
sensitivity: "confidential"
systems: ["Audit Log Store", "Kafka Cluster", "Auth Service"]
severity: "SEV2"
incident_date: "2025-11-18"
resolved_by: "EMP-22156"
---

# Audit log ingestion gap

## Summary

On November 18, 2025, an incident identified as "Audit log ingestion gap" (INC-2025-044) occurred, categorized as a severity level 2 (SEV2). The incident was triggered by an expired Kafka Access Control List (ACL) that resulted in the Auth Service audit events not being ingested into the Audit Log Store for a duration of 5 hours. This gap in audit logging has implications for compliance, security monitoring, and operational integrity. Upon discovery, the missing events were successfully replayed from the Kafka Cluster, which retains messages for 7 days, thus restoring the integrity of the audit logs.

The incident highlights the importance of maintaining robust ACL configurations within the Kafka Cluster, as well as ensuring that all access controls are monitored and updated in accordance with the policies outlined in POL-COMP-004: Access Control Policy. Furthermore, this incident serves as a reminder of the necessity of adherence to the protocols established in POL-COMP-003: Incident Response Policy, to ensure timely identification and resolution of similar issues in the future.

## Timeline

- 2025-11-18 12:00 UTC: The issue was initially detected when audit events were found to be missing from the Audit Log Store.
- 2025-11-18 12:05 UTC: The on-call engineer began investigating the cause of the missing audit events.
- 2025-11-18 12:30 UTC: The engineer identified that the Kafka ACL for the Auth Service had expired.
- 2025-11-18 12:45 UTC: The owning team was notified of the issue and began remediation steps.
- 2025-11-18 17:00 UTC: The expired ACL was renewed, and the Auth Service resumed normal operation.
- 2025-11-18 17:05 UTC: The missing audit events were replayed from the Kafka Cluster to the Audit Log Store.
- 2025-11-18 17:15 UTC: Incident Commander Robin Ellis confirmed that the ingestion gap was resolved and all missing events were accounted for.

## RCA

The root cause of the incident was the expiration of the Kafka ACL associated with the Auth Service, which prevented the successful ingestion of audit events into the Audit Log Store. This failure was a result of insufficient monitoring and alerting mechanisms around ACL expiry, leading to a lack of awareness of the operational status of the Auth Service's access controls. The importance of proactive management of access controls is emphasized in POL-COMP-004: Access Control Policy. 

The incident exposed a gap in the oversight of Kafka ACLs and highlighted the need for better operational practices to ensure timely renewals and updates. Additionally, the incident reinforces the necessity for regular audits of access control policies and adherence to the guidelines set forth in POL-COMP-006: Change Management Policy.

## Actions

- Implement a monitoring solution to track the status of all Kafka ACLs and alert the owning team 7 days prior to expiry. **Due Date: 2026-01-15**
- Conduct a review of current access controls for all services interacting with the Kafka Cluster to ensure compliance with POL-COMP-004: Access Control Policy. **Due Date: 2026-02-01**
- Develop and document a procedure for regular audits of access controls to prevent future expirations, in alignment with POL-COMP-006: Change Management Policy. **Due Date: 2026-01-30**
- Schedule a training session for relevant personnel on the importance of maintaining audit logs and the implications of ACL management. **Due Date: 2026-02-15**
