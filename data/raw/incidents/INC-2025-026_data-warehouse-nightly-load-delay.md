---
doc_id: "INC-2025-026"
title: "Data Warehouse nightly load delay"
doc_type: "incident"
domain: "engineering"
owner: "TEAM-04"
version: "1.0"
effective_date: "2025-07-22"
sensitivity: "internal"
systems: ["Data Warehouse", "Reporting Service"]
severity: "SEV3"
incident_date: "2025-07-22"
resolved_by: "EMP-87397"
---

# Data Warehouse nightly load delay

## Summary

On July 22, 2025, Acme Corp experienced a delay in the nightly load of the Data Warehouse, which subsequently affected the Reporting Service. The load was delayed by 6 hours due to a schema change in an upstream Kafka topic that was not communicated in advance. This incident is classified as Severity 3 (SEV3) as it did not result in a complete service outage but did impact the availability of reporting data for a significant duration. The issue was identified promptly, and missing data that was not loaded during the delayed period was backfilled according to SOP-OPS-022: Data Warehouse Backfill.

## Timeline

- **2025-07-22 00:00 UTC** - Scheduled nightly load begins for the Data Warehouse.
- **2025-07-22 06:00 UTC** - Data load remains incomplete; initial investigation starts.
- **2025-07-22 08:00 UTC** - Incident Commander identifies schema change in upstream Kafka topic as the cause of the delay.
- **2025-07-22 12:00 UTC** - Linda Wolfe, the on-call engineer, implements a backfill of the missing data per SOP-OPS-022: Data Warehouse Backfill.
- **2025-07-22 18:00 UTC** - Nightly load completes after the backfill is successfully executed.
- **2025-07-22 18:30 UTC** - Incident officially resolved; retrospective initiated.

## RCA

The root cause of the incident was a schema change in an upstream Kafka topic that was not properly communicated to the team responsible for the Data Warehouse nightly load process. This lack of communication led to an unexpected failure in processing the data, resulting in a 6-hour delay in the nightly load. As per company policies, particularly POL-COMP-006: Change Management Policy, all changes to upstream systems must be documented and communicated effectively to prevent similar disruptions.

The delay impacted the Reporting Service, which relies on timely and accurate data from the Data Warehouse. Although the issue was eventually resolved through a backfill process, the incident highlighted a gap in the change management communication, underscoring the need for better coordination and notification protocols when changes affecting the data pipeline occur.

## Actions

- **Review and Improve Change Management Protocols**: Conduct a review of current change management procedures to ensure that all schema changes in upstream systems are communicated effectively to all relevant stakeholders. **Due Date: 2025-08-05**
  
- **Implement Notification System for Schema Changes**: Develop a notification system that alerts the owning team of any schema changes in upstream Kafka topics that may impact downstream processes. **Due Date: 2025-08-15**

- **Post-Incident Review Meeting**: Schedule a post-incident review meeting to discuss the incident, its impact, and potential improvements to the incident response process with all relevant parties. **Due Date: 2025-08-01**

- **Update SOPs and Policies**: Update SOP-OPS-022: Data Warehouse Backfill and any relevant documentation to include provisions for handling upstream changes that may affect data loads. **Due Date: 2025-08-10** 

By addressing these actions, Acme Corp aims to enhance its operational robustness and mitigate the risk of similar incidents in the future.
