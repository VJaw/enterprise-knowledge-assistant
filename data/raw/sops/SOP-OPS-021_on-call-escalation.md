---
doc_id: "SOP-OPS-021"
title: "On-call Escalation"
doc_type: "sop"
domain: "operations"
owner: "TEAM-04"
version: "2.0"
effective_date: "2025-03-03"
sensitivity: "internal"
systems: ["Monitoring Stack"]
---

# On-call Escalation

## Purpose

The purpose of this Standard Operating Procedure (SOP) is to outline the escalation process for on-call incidents related to the Monitoring Stack. This document serves to ensure a rapid and effective response to incidents, thereby minimizing downtime and maintaining service reliability.

## Scope

This SOP applies to all incidents that arise within the Monitoring Stack. It is relevant to all on-call engineers and applicable personnel involved in incident management. This document complements other relevant policies and procedures governing incident response and operational excellence.

## Procedure

### 3.1 Incident Acknowledgment

1. Upon receiving a page related to the Monitoring Stack, the primary on-call engineer must acknowledge the page within **5 minutes**.
2. If the page is not acknowledged within this timeframe, the incident will escalate automatically to the secondary on-call engineer after **10 minutes**.

### 3.2 Incident Command Structure

1. For every SEV1 incident, a named Incident Commander will be assigned to oversee the incident management process.
2. The Incident Commander is responsible for coordinating the response efforts, communicating updates to relevant stakeholders, and ensuring that the incident is resolved efficiently.

### 3.3 Incident Investigation and Resolution

1. The primary on-call engineer, or the secondary if escalated, will begin an investigation into the incident, utilizing the Monitoring Stack to identify the root cause.
2. If necessary, refer to the relevant sections in SOP-OPS-014: Database Failover, SOP-OPS-007: Kafka Broker Replacement, and MAN-ENG-004: Observability Handbook for guidance on specific issues.
3. Document all findings and actions taken in the incident tracking system to maintain a clear record.

### 3.4 Communication Protocols

1. Throughout the incident, the Incident Commander will provide regular updates to affected stakeholders.
2. Ensure that all communications adhere to POL-COMP-002: Acceptable Use Policy to maintain professionalism and clarity.

### 3.5 Incident Closure

1. Once the incident has been resolved, the Incident Commander will ensure that all documentation is updated.
2. Conduct a post-incident review to analyze the response and identify areas for improvement in line with POL-COMP-003: Incident Response Policy.

## Escalation

1. If the incident cannot be resolved by the primary or secondary on-call engineers within a reasonable timeframe, the incident should be escalated to the next level of support.
2. Escalation should include notifying the Incident Commander and any other relevant parties who may assist in resolution.
3. Ensure that all escalation steps are documented within the incident tracking system.

## Verification

1. After resolution, the Monitoring Stack should be monitored closely to verify that the incident has been fully resolved and that no residual issues remain.
2. Verify that all systems are functioning as expected and that KPIs are being met.

## References

- POL-COMP-001: Data Retention Policy
- POL-COMP-002: Acceptable Use Policy
- POL-COMP-003: Incident Response Policy
- SOP-OPS-014: Database Failover
- SOP-OPS-007: Kafka Broker Replacement
