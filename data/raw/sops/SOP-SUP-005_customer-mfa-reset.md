---
doc_id: "SOP-SUP-005"
title: "Customer MFA Reset"
doc_type: "sop"
domain: "support"
owner: "TEAM-05"
version: "1.9"
effective_date: "2025-03-18"
sensitivity: "internal"
systems: ["Auth Service", "Customer Portal"]
---

# Customer MFA Reset

## Purpose  
The purpose of this Standard Operating Procedure (SOP) is to outline the steps for resetting Multi-Factor Authentication (MFA) for customers at Acme Corp. This procedure ensures the security and integrity of customer accounts while providing necessary support in a timely manner.

## Scope  
This SOP applies to all personnel involved in handling customer requests for MFA resets using the Auth Service and Customer Portal. It is intended for use in scenarios where customers require assistance in regaining access to their accounts due to issues with their MFA settings.

## Procedure  

### 3.1 Identity Verification  
1. Upon receiving a request for an MFA reset, the agent must initiate identity verification.
2. Verify the customer's identity using 2 of the following 3 factors:
   - Registered email
   - Last 4 digits of the payment card
   - Amount of a recent transaction
3. Document the verification process in accordance with POL-COMP-001: Data Retention Policy.

### 3.2 MFA Reset Process  
1. After successful verification, generate an MFA reset link through the Auth Service.
2. Send the MFA reset link to the verified registered email address of the customer.
3. Notify the customer that the MFA reset link will expire in 24 hours and that they must complete the reset within this timeframe.

### 3.3 Prohibited Practices  
1. Never reset MFA over chat without completing the verification steps outlined in section 3.1.
2. Ensure that all communications with the customer are conducted in compliance with POL-COMP-002: Acceptable Use Policy.

### 3.4 Follow-Up  
1. If the customer reports any issues accessing the reset link or completing the MFA reset, escalate the matter as per the escalation guidelines in section 4.
2. Log the incident details and any communications with the customer to maintain a record of the interaction.

## Escalation  
If the identity verification fails or the customer is unable to reset their MFA after following the provided instructions, escalate the issue to the Incident Commander. The escalation must include:
- Customer's name
- Verification attempt details
- Any relevant incident numbers (e.g., INC-2025-031: Payment latency spike) that may assist in diagnosing the issue.

## Verification  
To ensure compliance with security protocols, the following verification steps must be adhered to:
- Maintain detailed logs of each MFA reset request and verification process.
- All agents must be trained to adhere to the SOP guidelines and understand the importance of safeguarding customer information.
- Review incidents related to authentication failures (e.g., INC-2025-003: Auth token validation failures) for ongoing training and improvement sessions.

## References  
- POL-COMP-001: Data Retention Policy  
- POL-COMP-002: Acceptable Use Policy  
- POL-COMP-003: Incident Response Policy  
- POL-COMP-004: Access Control Policy
