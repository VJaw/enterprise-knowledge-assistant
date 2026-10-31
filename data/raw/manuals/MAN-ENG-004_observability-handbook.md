---
doc_id: "MAN-ENG-004"
title: "Observability Handbook"
doc_type: "manual"
domain: "engineering"
owner: "TEAM-04"
version: "1.8"
effective_date: "2025-05-12"
sensitivity: "internal"
systems: ["Monitoring Stack"]
---

# Observability Handbook

## Overview

The Observability Handbook provides guidelines and procedures to ensure effective monitoring, alerting, and troubleshooting of Acme Corp's services. The primary focus is on the Monitoring Stack, which is composed of Prometheus, Grafana, Alertmanager, and Loki. This stack enables the collection, visualization, and alerting of metrics and logs, facilitating proactive identification of issues and performance bottlenecks within our systems.

## Architecture and Components

The Monitoring Stack consists of four main components:

- **Prometheus**: A time-series database that collects metrics from configured targets at specified intervals.
- **Grafana**: A visualization tool that displays metrics collected by Prometheus in customizable dashboards.
- **Alertmanager**: A component responsible for managing alerts sent by Prometheus, routing them based on severity levels.
- **Loki**: A log aggregation system designed to work seamlessly with Prometheus, allowing for the querying of logs based on labels.

### Metrics Retention

- Metrics are retained for **30 days** at a **15-second resolution**. This retention period ensures that sufficient historical data is available for trend analysis and debugging.

### RED Metrics

Every tier-1 service must expose the following RED metrics:

- **Rate**: The number of requests processed over a specified timeframe.
- **Errors**: The total number of failed requests.
- **Duration**: The time taken to process requests.

## Dependencies

The effective functioning of the Monitoring Stack relies on several dependencies, including:

- Network connectivity to all monitored services.
- Proper configuration of service endpoints to expose metrics.
- Availability of storage for metrics and logs.

It is essential that any changes to service configurations or infrastructure adhere to the guidelines outlined in the **POL-COMP-006: Change Management Policy** to mitigate the risk of disruptions.

## Operations

### Setting Up the Monitoring Stack

To set up the Monitoring Stack, the following steps should be followed:

1. **Install Prometheus**:
   ```bash
   # Install Prometheus
   sudo apt-get install prometheus
   ```

2. **Configure Prometheus**:
   Ensure that the `prometheus.yml` configuration file is set to scrape metrics from the appropriate endpoints.

3. **Install Grafana**:
   ```bash
   # Install Grafana
   sudo apt-get install grafana
   ```

4. **Configure Alertmanager**:
   Ensure that alert routes for SEV1 and SEV2 alerts are correctly configured to route to the paging rotation.

5. **Install Loki**:
   ```bash
   # Install Loki
   sudo apt-get install loki
   ```

### Common Alerts and Troubleshooting

The Monitoring Stack is configured to detect and alert on several common issues. Below are examples of alerts along with potential troubleshooting steps:

- **SEV1 Alerts**:
  - **Payment latency spike** (INC-2025-031): Investigate service load and database performance.
  - **Customer Portal outage** (INC-2025-017): Check service health and connectivity.

- **SEV2 Alerts**:
  - **Expired TLS certificate on API Gateway** (INC-2025-022): Follow procedures in **SOP-OPS-018: TLS Certificate Renewal**.
  - **Audit log ingestion gap** (INC-2025-044): Verify log pipeline status and check for errors in Loki.

### Alert Management

Alertmanagement is crucial for maintaining operational integrity. The Alertmanager routes SEV1 and SEV2 alerts to the paging rotation to ensure timely responses. The on-call engineer should acknowledge alerts promptly and follow the protocols defined in **POL-COMP-003: Incident Response Policy**.

## Recovery

In case of incidents identified via the Monitoring Stack, the following recovery steps should be taken:

1. **Incident Assessment**: The Incident Commander should assess the impact and categorize the incident severity.
2. **Remedial Action**:
   - For issues like **Ledger DB primary failure** (INC-2025-012), refer to **SOP-OPS-014: Database Failover**.
   - For **Kafka consumer lag delays fraud scoring** (INC-2025-008), consult **SOP-OPS-007: Kafka Broker Replacement**.

3. **Post-Incident Review**: After recovery, a post-incident review should be conducted to analyze the root cause and improve monitoring practices.

## References

- **POL-COMP-001: Data Retention Policy**
- **POL-COMP-002: Acceptable Use Policy**
- **POL-COMP-003: Incident Response Policy**
- **POL-COMP-004: Access Control Policy**
- **MAN-ENG-001: Payment Gateway Runbook**
- **SOP-OPS-021: On-call Escalation** 

This documentation will be updated as necessary to reflect changes in procedures, tools, or best practices within Acme Corp’s observability framework.
