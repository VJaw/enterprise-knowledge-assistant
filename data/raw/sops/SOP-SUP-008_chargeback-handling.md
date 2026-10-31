---
doc_id: "SOP-SUP-008"
title: "Chargeback Handling"
doc_type: "sop"
domain: "support"
owner: "TEAM-05"
version: "1.2"
effective_date: "2025-05-20"
sensitivity: "internal"
systems: ["Fraud Detection Engine", "Billing Service"]
---

# Chargeback Handling

## Purpose  
The purpose of this Standard Operating Procedure (SOP) is to outline the process for handling chargebacks at Acme Corp. This document aims to ensure a consistent and efficient approach to managing chargeback disputes, minimizing financial loss, and maintaining compliance with relevant policies.

## Scope  
This SOP applies to all chargeback notices received by Acme Corp through any payment platform. It encompasses interactions with the Fraud Detection Engine and the Billing Service, and it is relevant to all personnel involved in chargeback management.

## Procedure  

### 3.1 Initial Chargeback Notification  
1. Upon receiving a chargeback notice, the responsible party must log the chargeback in the internal tracking system.  
2. Review the details of the chargeback notice to ensure all necessary information is available for the dispute process.  
3. Respond to the chargeback notice within 7 days to comply with industry standards.

### 3.2 Gathering Evidence  
1. Collect all relevant documentation related to the transaction, including transaction ID, date, amount, and customer details.  
2. Retrieve the transaction's Fraud Detection Engine risk score associated with the chargeback.  
3. Compile evidence to support the dispute, including any communication with the customer, transaction history, and the Fraud Detection Engine risk score. This risk score must be attached to the dispute evidence.

### 3.3 Review Process  
1. For disputes involving amounts over $1,000, conduct a thorough review involving a fraud analyst before proceeding with submission.  
2. The fraud analyst will evaluate the risk score and supporting evidence to determine the validity of the chargeback.  
3. Document the findings and recommendations from the fraud analyst in the internal tracking system.

### 3.4 Submission of Dispute  
1. Once all evidence is gathered and reviewed, submit the dispute to the payment processor or relevant financial institution.  
2. Ensure that all documentation, including the Fraud Detection Engine risk score, is included in the submission.  
3. Track the submission status and any responses received from the payment processor.

### 3.5 Follow-Up Actions  
1. Monitor the status of the chargeback dispute regularly.  
2. If additional information is requested by the payment processor, respond promptly to ensure timely resolution.  
3. Document all communications and actions taken during the follow-up process.

## Escalation  
In cases where the chargeback dispute is escalated or requires further analysis, the on-call engineer should engage the Incident Commander. The Incident Commander will assess the situation and determine whether further investigation or action is necessary. All escalations must be documented in accordance with POL-COMP-003: Incident Response Policy.

## Verification  
To ensure compliance with this SOP, periodic audits of chargeback handling processes will be conducted. These audits will verify adherence to the outlined procedures and assess the effectiveness of the chargeback management strategy. Any discrepancies or areas for improvement must be reported to the relevant stakeholders.

## References  
- POL-COMP-001: Data Retention Policy  
- POL-COMP-002: Acceptable Use Policy  
- POL-COMP-008: Payment Card Data Handling Policy  
- SOP-SUP-003: Refund Escalation
