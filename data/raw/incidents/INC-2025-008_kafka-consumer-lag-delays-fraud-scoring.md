---
doc_id: "INC-2025-008"
title: "Kafka consumer lag delays fraud scoring"
doc_type: "incident"
domain: "engineering"
owner: "TEAM-04"
version: "1.0"
effective_date: "2025-02-19"
sensitivity: "internal"
systems: ["Kafka Cluster", "Fraud Detection Engine"]
severity: "SEV2"
incident_date: "2025-02-19"
resolved_by: "EMP-65302"
---

# Kafka consumer lag delays fraud scoring

## Summary

On February 19, 2025, an incident occurred involving the Kafka Cluster and the Fraud Detection Engine, where consumer lag reached 380,000 messages due to a broker disk filling up. This situation resulted in fraud scoring being delayed by as much as 18 minutes. The incident was classified as a Severity 2 (SEV2) event, prompting immediate investigation and remediation efforts to minimize the impact on fraud detection operations.

The Kafka Cluster is a critical component of the Fraud Detection Engine, responsible for processing transactions in real-time. The delays caused by the consumer lag directly affected the scoring of transactions for potential fraud, thereby increasing the risk of undetected fraudulent activities during the period of lag.

## Timeline

- **2025-02-19T08:00Z**: Initial monitoring alerts indicate rising consumer lag in Kafka Cluster.
- **2025-02-19T08:15Z**: Lag reaches 100,000 messages; on-call engineer begins investigation.
- **2025-02-19T08:30Z**: Lag continues to increase; internal communication initiated regarding potential impacts on fraud scoring.
- **2025-02-19T08:45Z**: Lag reaches 200,000 messages; the owning team implements temporary mitigation strategies.
- **2025-02-19T09:00Z**: Lag hits 300,000 messages; the Incident Commander escalates the incident.
- **2025-02-19T09:15Z**: Lag reaches 380,000 messages; fraud scoring is delayed by up to 18 minutes.
- **2025-02-19T09:30Z**: Matthew Moore identifies the cause as a filled broker disk.
- **2025-02-19T09:45Z**: The broker disk is cleared, and consumer lag begins to decrease.
- **2025-02-19T10:00Z**: Lag returns to acceptable levels; normal fraud scoring operations resume.
- **2025-02-19T10:30Z**: Incident is officially marked as resolved.

## RCA

The primary cause of the incident was the filling of a broker disk within the Kafka Cluster, which led to a significant backlog of messages (consumer lag) that could not be processed in a timely manner. This situation arose due to a combination of factors including:

1. **Insufficient Disk Space**: The disk allocated for the Kafka broker did not have adequate space to accommodate the volume of messages being produced, leading to the failure of message processing.
   
2. **Monitoring Gaps**: Although there were monitoring alerts, they did not prompt proactive measures to address the disk space issue before it reached a critical state.

3. **Failure to Scale Resources**: A lack of foresight in scaling disk resources to match the increased load from transaction processing contributed to the incident.

The delay in fraud scoring during the incident highlighted the need for improved monitoring and incident response protocols to address such issues proactively in the future.

## Actions

- **Action 1**: Review and enhance monitoring thresholds for the Kafka Cluster to ensure timely alerts for disk space issues.  
  **Due Date**: 2025-02-26

- **Action 2**: Conduct a resource usage audit of the Kafka Cluster to determine if additional resources are needed to handle peak loads effectively.  
  **Due Date**: 2025-03-05

- **Action 3**: Update the relevant sections of the Observability Handbook (MAN-ENG-004) to include best practices for monitoring disk space and consumer lag.  
  **Due Date**: 2025-03-10

- **Action 4**: Conduct a post-incident review meeting to discuss lessons learned and update the Incident Response Policy (POL-COMP-003) accordingly.  
  **Due Date**: 2025-03-15
