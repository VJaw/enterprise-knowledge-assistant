---
doc_id: "SOP-OPS-022"
title: "Data Warehouse Backfill"
doc_type: "sop"
domain: "operations"
owner: "TEAM-04"
version: "1.0"
effective_date: "2025-07-30"
sensitivity: "internal"
systems: ["Data Warehouse", "Kafka Cluster", "Reporting Service"]
---

# Data Warehouse Backfill

## Purpose
The purpose of this Standard Operating Procedure (SOP) is to outline the steps for performing a backfill operation in the Data Warehouse. This is necessary to ensure the completeness and accuracy of historical data used for reporting and analytics. 

## Scope
This SOP applies to all operations involving backfills in the Data Warehouse, including the coordination of data ingestion from the Kafka Cluster and verification of data integrity in the Reporting Service. It is intended for use by operational staff involved in managing data processes.

## Procedure

### 3.1 Pre-Backfill Preparation
1. **Schedule Backfill**: Confirm that the backfill is scheduled within the 01:00-05:00 UTC window to minimize impact on the production environment.
2. **Review Data Retention**: Ensure that the data to be backfilled complies with the guidelines set forth in the POL-COMP-001: Data Retention Policy. Verify that the data is still within the retention period.
3. **Confirm Data Range**: Determine the date range for the backfill. A single run may cover at most 30 days of data.
4. **Notify Stakeholders**: Inform all relevant stakeholders of the scheduled backfill operation, including the expected start time and duration.

### 3.2 Execute Backfill
5. **Run Backfill Command**: Execute the backfill process using the command:
   ```
   dwctl backfill --from <date> --to <date>
   ```
   Replace `<date>` with the appropriate start and end dates for the backfill.
6. **Monitor Execution**: Monitor the command execution for any errors or warnings. Document any issues encountered during the process.

### 3.3 Post-Backfill Validation
7. **Validate Row Counts**: After the backfill operation completes, validate the row counts in the Data Warehouse against the expected counts for the specified date range. This step is crucial to ensure data integrity.
8. **Log Results**: Record the results of the validation process, including any discrepancies noted. If discrepancies arise, escalate the issue as outlined in the Escalation section.

### 3.4 Reporting and Documentation
9. **Update Documentation**: Update relevant documentation to reflect the completion of the backfill, including the date range processed and any issues encountered.
10. **Notify Stakeholders**: Inform stakeholders of the completion of the backfill operation, including any validation results and actions taken.

## Escalation
In the event of a failure during the backfill or significant discrepancies in row counts, the on-call engineer must escalate the issue according to the SOP-OPS-021: On-call Escalation. The Incident Commander will determine further action, which may include initiating an investigation as per the POL-COMP-003: Incident Response Policy.

## Verification
Verification of the backfill process is essential. The following steps should be taken:
- Ensure the backfill operation was completed within the scheduled window.
- Confirm that the row counts match expected values for the backfilled data range.
- Review logs for any errors or warnings during execution. 
- Document any findings and resolutions in the incident record if applicable.

## References
- POL-COMP-001: Data Retention Policy
- POL-COMP-003: Incident Response Policy
- SOP-OPS-021: On-call Escalation
- INC-2025-026: Data Warehouse nightly load delay
