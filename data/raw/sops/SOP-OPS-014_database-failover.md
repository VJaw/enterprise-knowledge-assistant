---
doc_id: "SOP-OPS-014"
title: "Database Failover"
doc_type: "sop"
domain: "operations"
owner: "TEAM-04"
version: "2.1"
effective_date: "2025-03-12"
sensitivity: "internal"
systems: ["Ledger DB", "Payment Gateway", "Billing Service"]
---

# Database Failover

## Purpose

The purpose of this Standard Operating Procedure (SOP) is to outline the steps for executing a database failover for the Ledger DB primary/replica cluster. This ensures minimal disruption to services reliant on the Ledger DB, such as the Payment Gateway and Billing Service, while maintaining data integrity and operational continuity during an outage.

## Scope

This SOP applies to the Ledger DB primary/replica cluster. It encompasses the actions required to initiate both automatic and manual failover processes, ensuring adherence to the target Recovery Time Objective (RTO) of 30 minutes and Recovery Point Objective (RPO) of 5 minutes. This document is relevant to incidents where the primary database instance is deemed unhealthy.

## Procedure

### 3.1 Automatic Failover

1. The system continuously monitors the health of the Ledger DB primary instance.
2. If the primary instance is detected as unhealthy for 60 seconds, the automatic failover process is triggered.
3. The system promotes the replica instance to primary status without manual intervention.
4. Notifications regarding the failover event are sent to the on-call engineer and relevant stakeholders.
5. The Payment Gateway and Billing Service will automatically redirect to the new primary.

### 3.2 Manual Promotion

In scenarios where automatic failover does not occur or additional verification is required, manual promotion can be executed by following these steps:

1. Put the Payment Gateway and Billing Service into maintenance mode to freeze writes. This prevents any data inconsistency during the failover process.
2. Confirm that the replica lag is under 5 seconds. This ensures that the data on the new primary is sufficiently up-to-date.
3. Run the command `ledgerctl promote --replica ledger-db-replica-1` to promote the designated replica as the new primary.
4. Repoint the DNS record `ledger-db.internal` to the new primary to ensure services can access the updated database instance.
5. Run the ledger smoke test to validate the functionality and stability of the new primary.
6. Take the Payment Gateway and Billing Service out of maintenance mode to resume normal operations.

## Escalation

In the event of a database failover, if further issues arise during the process or if the failover does not complete successfully, escalate the situation as follows:

1. Notify the on-call database engineer immediately.
2. If the issue persists, escalate to the Incident Commander for further investigation and resolution.

## Verification

After the failover process, it is critical to verify that the services are functioning correctly. This includes:

1. Monitoring the health and performance of the new primary Ledger DB instance.
2. Checking the logs for any errors or warnings related to the failover.
3. Ensuring that data integrity is maintained as per the Data Retention Policy (POL-COMP-001).
4. Conducting performance checks on the Payment Gateway and Billing Service to confirm they are operating without issues.
5. Documenting the incident and outcomes in accordance with the Incident Response Policy (POL-COMP-003).

## References

- POL-COMP-001: Data Retention Policy
- POL-COMP-003: Incident Response Policy
- SOP-OPS-007: Kafka Broker Replacement
- SOP-OPS-021: On-call Escalation
