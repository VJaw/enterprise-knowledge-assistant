---
doc_id: "INC-2025-040"
title: "Fraud model false-positive surge"
doc_type: "incident"
domain: "engineering"
owner: "TEAM-04"
version: "1.0"
effective_date: "2025-10-09"
sensitivity: "internal"
systems: ["Fraud Detection Engine", "Payment Gateway"]
severity: "SEV2"
incident_date: "2025-10-09"
resolved_by: "EMP-46048"
---

# Fraud model false-positive surge

## Summary

On October 9, 2025, Acme Corp experienced a surge in false-positive declines related to fraudulent transactions caused by a new version of the Fraud Detection Engine model. The updated model resulted in false-positive declines reaching 4.8% of transactions for a duration of 95 minutes. This incident impacted the Payment Gateway and led to a significant number of legitimate transactions being incorrectly flagged as fraudulent. In response to the situation, the Fraud Detection Engine model was rolled back to the previous version that had a more accurate performance profile. 

## Timeline
- 2025-10-09T14:00Z: New fraud model version deployed to the Fraud Detection Engine.
- 2025-10-09T14:05Z: Initial reports of increased false-positive declines are received.
- 2025-10-09T14:30Z: False-positive declines peak at 4.8% of transactions.
- 2025-10-09T14:45Z: Incident Commander is notified and begins escalation.
- 2025-10-09T14:55Z: Rollback procedure initiated for the Fraud Detection Engine model.
- 2025-10-09T15:35Z: Rollback completed; false-positive rates begin to return to normal.
- 2025-10-09T15:50Z: Final confirmation received that false-positive rates are stable.
- 2025-10-09T16:00Z: Incident resolved by Jill Rhodes (Software Engineer).

## RCA

The root cause of the incident was traced back to the deployment of a new version of the Fraud Detection Engine model. Due to insufficient testing under conditions simulating peak transaction volumes, the new model's performance was not adequately validated. The model's algorithm, designed to identify fraudulent transactions, inadvertently flagged a higher percentage of legitimate transactions as fraudulent, resulting in false-positive declines peaking at 4.8%. 

The spike in false positives led to a significant operational impact, as legitimate transactions were declined, affecting customer experience and potentially jeopardizing revenue. The incident was exacerbated by the lack of immediate monitoring alerts that could have prompted a faster response. 

A review of the deployment process indicated that the change management protocols outlined in POL-COMP-006: Change Management Policy were not fully adhered to, particularly in relation to the comprehensive testing of new model versions before deployment. 

## Actions
- Review and enhance testing protocols for fraud model deployments to ensure they simulate peak transaction volumes effectively. **Due Date: 2025-10-23**
- Conduct a post-incident review meeting to discuss the false-positive surge and gather insights for improving future model deployments. **Due Date: 2025-10-16**
- Update the monitoring system to include more robust alert mechanisms for fraud detection performance metrics. **Due Date: 2025-10-30**
- Reinforce adherence to the POL-COMP-006: Change Management Policy during future updates to the Fraud Detection Engine model. **Due Date: 2025-10-20**
