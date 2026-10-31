---
doc_id: "SOP-SUP-003"
title: "Refund Escalation"
doc_type: "sop"
domain: "support"
owner: "TEAM-05"
version: "2.3"
effective_date: "2025-02-10"
sensitivity: "internal"
systems: ["Billing Service"]
---

# Refund Escalation

## Purpose
The purpose of this Standard Operating Procedure (SOP) is to outline the process for handling refund requests at Acme Corp. This document provides clear guidelines for processing refunds, including escalation procedures and necessary approvals to ensure compliance and consistency.

## Scope
This SOP applies to all refund requests made by customers through any channel supported by Acme Corp. It specifically addresses the process within the Billing Service and is relevant to any personnel involved in refund processing, including support staff and financial analysts.

## Procedure

### 3.1 Refund Request Submission
1. Customers may request a refund within **30 days of purchase**.
2. The support staff will collect the necessary information from the customer, including:
   - Purchase date
   - Order number
   - Reason for the refund
3. All refund requests must be logged in the designated tracking system, ensuring that the details are recorded accurately for future reference.

### 3.2 Approval Process
1. For refund requests exceeding **$500**, the request must be approved by a **Tier-2 Support Lead**.
2. For refund requests exceeding **$5,000**, approval must also be obtained from the Finance department in addition to the Tier-2 Support Lead.
3. The support staff will notify the appropriate personnel for approvals, providing them with all relevant details from the refund request log.
4. Once approvals are obtained, the request will move to the next stage for processing.

### 3.3 Issuing Refunds
1. Approved refunds are issued from the **Billing Service refund console**.
2. The refund must be processed within **2 business days** of receiving the necessary approvals.
3. The support staff must ensure that the refund is accurately recorded in the system, including the transaction ID and date of issuance.
4. Customers must be informed once the refund has been processed, providing them with confirmation details.

### 3.4 Handling Denied Requests
1. If a refund request is denied, the customer must be notified with a clear explanation of the reason for denial.
2. The denial must also be documented in the tracking system to maintain a complete record of all requests.

## Escalation
In the event of complications during the refund process, the following escalation path should be followed:

1. If the Tier-2 Support Lead does not respond to an approval request within **1 business day**, the support staff should escalate the request to the next level of management.
2. If Finance does not respond within **2 business days** for refunds over **$5,000**, the support staff must escalate the issue to the Finance oversight team.
3. Any issues related to the Billing Service, such as system outages or processing errors, should be escalated to the on-call engineer as per the guidelines outlined in the **SOP-OPS-021: On-call Escalation** document.

## Verification
To ensure compliance with this SOP, the following verification steps should be undertaken:

1. Regular audits of refund requests and approvals must be conducted, ensuring that the process is adhered to.
2. The support staff should periodically review the refund tracking system for accuracy and completeness.
3. Feedback from customers regarding the refund process should be collected and analyzed to improve the procedure.

**Worked example (ticket TCK-00042):** customer Henry Mcconnell (terri86@hotmail.com, (299) 259-6008) requested a $750 refund 12 days after purchase, so it needed Tier-2 Support Lead approval. The agent pasted the card number 4111 1111 1111 1111 and the customer's SSN 709-48-8434 into the ticket notes, which breaches POL-COMP-008; the notes were redacted during review.

## References
- **POL-COMP-001: Data Retention Policy** - Ensure compliance with data retention requirements for refund records.
- **POL-COMP-002: Acceptable Use Policy** - Remain compliant with acceptable use guidelines during the processing of refunds.
- **POL-COMP-003: Incident Response Policy** - Follow incident response procedures if any issues arise during refund processing.
- **SOP-SUP-008: Chargeback Handling** - Refer to this SOP for guidance on handling chargebacks, which may be related to refund requests.
