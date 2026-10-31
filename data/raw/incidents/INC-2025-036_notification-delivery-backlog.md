---
doc_id: "INC-2025-036"
title: "Notification delivery backlog"
doc_type: "incident"
domain: "operations"
owner: "TEAM-04"
version: "1.0"
effective_date: "2025-09-03"
sensitivity: "internal"
systems: ["Notification Service", "Kafka Cluster"]
severity: "SEV3"
incident_date: "2025-09-03"
resolved_by: "EMP-36062"
---

# Notification delivery backlog

## Summary

On September 3, 2025, Acme Corp experienced a significant backlog in the Notification Service, resulting from a throttling issue with a third-party SMS provider. This incident led to the accumulation of approximately 1.2 million email and SMS notifications, causing a delivery delay of up to 3 hours. The incident was classified as SEV3 due to the impact on user experience, but no critical system functionality was compromised. The Notification Service relies on the Kafka Cluster for message processing, and this incident highlighted the need for better monitoring and alerting mechanisms regarding third-party service performance.

## Timeline

- 2025-09-03 10:00 UTC: Incident detected; Notification Service reports delays in message deliveries.
- 2025-09-03 10:05 UTC: On-call engineer begins investigation; identifies third-party SMS provider throttling.
- 2025-09-03 10:15 UTC: Backlog confirmed at 1.2 million notifications.
- 2025-09-03 10:30 UTC: Incident escalated to Incident Commander for coordination.
- 2025-09-03 11:00 UTC: Initial communication sent to affected stakeholders regarding the backlog and expected delays.
- 2025-09-03 12:00 UTC: Temporary mitigation measures implemented; adjustments made to reduce notification dispatch rates.
- 2025-09-03 12:30 UTC: Incident Manager Jeremy Roberts assumes oversight of resolution efforts.
- 2025-09-03 13:00 UTC: Third-party SMS provider confirms resolution of throttling issue.
- 2025-09-03 13:30 UTC: Normal notification delivery rate restored; backlog begins to clear.
- 2025-09-03 15:00 UTC: All notifications delivered; backlog fully cleared.
- 2025-09-03 15:30 UTC: Post-incident review initiated.

## RCA

The root cause of the incident was identified as throttling by the third-party SMS provider, which limited the number of requests that could be processed at any given time. This throttling resulted in a backlog of notifications that built up in the Notification Service. The Kafka Cluster, utilized for processing these notifications, was unable to keep pace with the incoming messages due to the constraints imposed by the third-party provider. The delay of up to 3 hours in delivering notifications impacted user engagement and communication effectiveness, although the core functionalities of the Notification Service remained operational.

Furthermore, the incident exposed gaps in monitoring capabilities regarding third-party service performance. There were insufficient alerts configured to notify the owning team of potential throttling conditions, resulting in delayed response times. The lack of proactive measures to manage third-party dependencies contributed to the severity of the incident.

## Actions

- Review and enhance monitoring and alerting rules for third-party service performance to ensure timely detection of throttling issues. **Due Date: 2025-09-10**
- Conduct a post-incident review with the owning team to discuss lessons learned and refine the incident response process. **Due Date: 2025-09-15**
- Update the Incident Response Policy (POL-COMP-003) to include specific protocols for handling third-party service issues. **Due Date: 2025-09-20**
- Implement a contingency plan for notification delivery that includes fallback mechanisms in case of future throttling incidents. **Due Date: 2025-09-25**
