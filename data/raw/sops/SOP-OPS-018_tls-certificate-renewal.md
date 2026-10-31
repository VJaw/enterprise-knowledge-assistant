---
doc_id: "SOP-OPS-018"
title: "TLS Certificate Renewal"
doc_type: "sop"
domain: "operations"
owner: "TEAM-04"
version: "1.1"
effective_date: "2025-06-30"
sensitivity: "internal"
systems: ["API Gateway"]
---

# TLS Certificate Renewal

## Purpose

The purpose of this Standard Operating Procedure (SOP) is to outline the steps required for the timely renewal of TLS certificates for the API Gateway at Acme Corp. Ensuring that TLS certificates are renewed before their expiration is critical for maintaining secure communications and compliance with industry standards.

## Scope

This SOP applies to all personnel involved in the management and operation of the API Gateway. It is relevant to the roles responsible for monitoring, maintaining, and updating the security infrastructure of Acme Corp’s systems. This document must be adhered to whenever a TLS certificate is due for renewal.

## Procedure

### 3.1 Certificate Renewal Timeline

1. **Monitor Certificate Expiry Dates:** The owning team must regularly monitor the expiry dates of TLS certificates associated with the API Gateway.
2. **Initiate Renewal Process:** Certificates are to be renewed at least 30 days before expiry to allow sufficient time for testing and deployment.
3. **Obtain New Certificate:** Follow the established process for obtaining a new TLS certificate from the Certificate Authority (CA).

### 3.2 Certificate Installation

1. **Install New Certificate:** Once the new certificate is received, install it on the API Gateway according to the guidelines outlined in the MAN-ENG-006: API Gateway Configuration Guide.
2. **Update Configuration:** Ensure that the API Gateway configuration is updated to reference the new certificate.

### 3.3 Verification of Certificate

1. **Verify Certificate Chain:** After installation, verify the new certificate chain from outside the network to confirm that the certificate is correctly configured and trusted.
2. **Conduct Functional Testing:** Perform functional tests on the API Gateway to ensure that all services are operational and that there are no disruptions in service due to the certificate change.

### 3.4 Documentation Update

1. **Record Renewal in Logs:** Document the renewal in the appropriate logs, ensuring that all relevant details, such as the date of renewal and the certificate’s expiration date, are captured.
2. **Update Internal Documentation:** Ensure that any internal documentation referencing the TLS certificates is updated to reflect the changes.

### 3.5 Incident Management

1. **Monitor for Issues:** The on-call engineer should monitor the API Gateway for any issues that may arise post-renewal, such as service outages or errors related to the new certificate.
2. **Escalate Issues:** If issues are detected, follow the procedures outlined in the POL-COMP-003: Incident Response Policy to escalate and resolve the incident.

## Escalation

In the event of an incident related to TLS certificate renewal, such as the one documented in INC-2025-022: Expired TLS certificate on API Gateway, the Incident Commander must be notified immediately. The Incident Commander will follow the escalation procedures defined in the POL-COMP-003: Incident Response Policy to ensure timely resolution.

## Verification

Following the renewal and installation of the TLS certificate, verification steps must be conducted to ensure compliance and functionality. This includes:

1. **External Verification:** Confirming the new certificate chain from outside the network.
2. **Functional Testing:** Ensuring that all API calls are successful and there are no disruptions in service.

If any discrepancies are noted, they must be documented and addressed promptly, with further escalation as necessary.

## References

- POL-COMP-003: Incident Response Policy
- MAN-ENG-006: API Gateway Configuration Guide
- INC-2025-022: Expired TLS certificate on API Gateway
- SOP-OPS-009: Secrets Rotation

This SOP may be revised in future updates to incorporate changes in technology, policy, or lessons learned from incidents. All personnel involved should familiarize themselves with this document and follow its procedures to maintain the security and integrity of Acme Corp’s API Gateway.
