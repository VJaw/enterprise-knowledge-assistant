---
doc_id: "SOP-OPS-009"
title: "Secrets Rotation"
doc_type: "sop"
domain: "operations"
owner: "TEAM-04"
version: "1.6"
effective_date: "2025-01-28"
sensitivity: "internal"
systems: ["Secrets Vault"]
---

# Secrets Rotation

## Purpose
The purpose of this Standard Operating Procedure (SOP) is to outline the processes and responsibilities for the rotation of secrets stored in the Secrets Vault. Regular rotation of secrets is essential for maintaining security and minimizing the risk of unauthorized access to sensitive information.

## Scope
This SOP applies to all personnel involved in the management of secrets within the Secrets Vault. It covers the routine rotation of secrets every 90 days and the procedures for emergency rotations in the event of a suspected compromise.

## Procedure

### 3.1 Routine Secret Rotation
1. **Schedule Rotation**: Secrets in the Secrets Vault are rotated every 90 days. The owning team must establish a schedule for routine secret rotations.
2. **Confirm Existing Secrets**: Before rotation, confirm the current secrets in the vault to ensure that all are accounted for.
3. **Execute Rotation**: Use the command `vaultctl rotate --path <secret-path>` to rotate the secret. Replace `<secret-path>` with the appropriate path for the secret being rotated.
4. **Notify Stakeholders**: After rotation, notify all relevant stakeholders that the secret has been updated. This includes updating any documentation that references the previous secret.
5. **Monitor for Issues**: After the rotation process, monitor systems for any issues that may arise from the change in secrets.

### 3.2 Emergency Secret Rotation
1. **Identify Compromise**: If there is a suspicion of a compromise, an emergency rotation must happen within 1 hour.
2. **Initiate Rotation**: Execute the command `vaultctl rotate --path <secret-path>` immediately to rotate the compromised secret.
3. **Implement Dual-Key Window**: Ensure that the old and new keys overlap for a 24-hour dual-key window before the old key is revoked. This allows for a seamless transition and minimizes potential service disruptions.
4. **Notify Incident Commander**: Inform the Incident Commander of the rotation and any relevant details regarding the suspected compromise.
5. **Review Access Logs**: Conduct a thorough review of access logs to identify any unauthorized access that may have occurred prior to the rotation.

## Escalation
In the event of any issues arising during the secret rotation process, the on-call engineer must escalate the situation to the Incident Commander. The Incident Commander will determine if further escalation to management is required based on the severity and potential impact of the incident.

## Verification
Verification of successful secret rotation is critical. Following each routine and emergency rotation, the owning team must:
1. **Check System Functionality**: Ensure that all systems utilizing the rotated secret are functioning correctly.
2. **Confirm Access**: Validate that access to services and applications relying on the secret is operating as expected without any unauthorized access.
3. **Document the Process**: Record the rotation process, including any anomalies or issues encountered, in the incident management system for future reference.

## References
- Refer to POL-COMP-004: Access Control Policy for guidelines on managing and controlling access to sensitive information.
- Consult POL-COMP-002: Acceptable Use Policy to ensure that all personnel are adhering to the standards for using sensitive data.
- Review POL-COMP-005: Encryption and Key Management Policy for best practices related to the management of cryptographic keys.
- For incident management processes, refer to POL-COMP-003: Incident Response Policy, which outlines steps to take in the event of a security incident.
