---
doc_id: "SOP-OPS-007"
title: "Kafka Broker Replacement"
doc_type: "sop"
domain: "operations"
owner: "TEAM-04"
version: "1.3"
effective_date: "2025-02-03"
sensitivity: "internal"
systems: ["Kafka Cluster"]
---

# Kafka Broker Replacement

## Purpose

The purpose of this Standard Operating Procedure (SOP) is to outline the steps required to safely replace a Kafka broker within the Kafka Cluster. This process aims to minimize downtime and ensure data integrity throughout the replacement.

## Scope

This SOP applies to all personnel involved in maintaining and operating the Kafka Cluster. It is intended for use when a Kafka broker needs to be decommissioned and replaced, either due to planned maintenance or in response to operational incidents.

## Procedure

### 3.1 Preparation

1. Review the current state of the Kafka Cluster to ensure that all brokers are functioning normally.
2. Confirm that the number of under-replicated partitions is 0. This is crucial for preventing data loss during the broker replacement.
3. Identify the broker that needs to be replaced and notify relevant stakeholders of the planned maintenance window.

### 3.2 Moving Partitions

1. Use the `kafka-reassign-partitions` command to move all partitions off the broker designated for replacement. This step is essential to ensure that no data is lost during the decommissioning process.
   - Command example: `kafka-reassign-partitions --zookeeper <zookeeper_host> --reassignment-json-file <reassignment_file.json> --execute`

### 3.3 Decommissioning the Broker

1. Once all partitions have been successfully moved off the broker, proceed to decommission the broker from the Kafka Cluster.
2. Monitor the Kafka Cluster to confirm that the partitions are no longer assigned to the broker being replaced.

### 3.4 Replacement

1. Install the new broker hardware or software as necessary, ensuring that it meets all configuration standards outlined in the MAN-ENG-003: Kafka Operations Guide.
2. Add the new broker to the Kafka Cluster configuration.
3. Restart the Kafka services on the new broker.

### 3.5 Recovery

1. After adding the new broker, wait for the In-Sync Replica (ISR) to recover fully. This involves monitoring the health of the new broker and ensuring that it has caught up with the current data.
2. Confirm that all data is being replicated correctly and that the new broker is functioning as expected.

### 3.6 Finalization

1. After successful recovery, update any relevant documentation to reflect the changes made to the Kafka Cluster.
2. Inform stakeholders that the broker replacement is complete and that normal operations can resume.

## Escalation

In the event of any issues during the broker replacement process, the on-call engineer should follow the guidelines outlined in POL-COMP-003: Incident Response Policy. Escalate to the Incident Commander if necessary. Ensure that all incidents, such as INC-2025-008: Kafka consumer lag delays fraud scoring, are logged and addressed promptly.

## Verification

Once the new broker is operational, conduct a verification process to ensure that all functionality is intact. This includes:

1. Monitoring partition assignments to verify that all partitions are distributed evenly across the brokers.
2. Checking the performance metrics to ensure that the Kafka Cluster is operating within acceptable parameters.
3. Confirming that no under-replicated partitions exist and that the replication factor is maintained as per POL-COMP-001: Data Retention Policy.

## References

- POL-COMP-001: Data Retention Policy
- POL-COMP-003: Incident Response Policy
- MAN-ENG-003: Kafka Operations Guide
- SOP-OPS-011: Production Deployment and Rollback
