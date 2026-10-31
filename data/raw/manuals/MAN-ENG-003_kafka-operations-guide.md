---
doc_id: "MAN-ENG-003"
title: "Kafka Operations Guide"
doc_type: "manual"
domain: "engineering"
owner: "TEAM-02"
version: "2.4"
effective_date: "2025-03-05"
sensitivity: "internal"
systems: ["Kafka Cluster"]
---

# Kafka Operations Guide

## Overview

The Kafka Operations Guide (MAN-ENG-003, version 2.4) provides essential procedures and best practices for managing and operating the Kafka Cluster at Acme Corp. This document outlines the architecture, components, dependencies, operations, common alerts, troubleshooting steps, and recovery strategies associated with the Kafka Cluster.

## Architecture and Components

The Kafka Cluster consists of the following key components:

- **6 Brokers**: These are responsible for storing and serving messages.
- **KRaft Controllers**: Manages the metadata and the cluster state.
- **Schema Registry**: Ensures that the data written to topics complies with the defined schemas.
- **MirrorMaker 2**: Used for replicating topics across different Kafka clusters.

### Topics Configuration

- **Replication Factor**: 3
- **Minimum In-Sync Replicas**: 2
- **Default Topic Retention**: 7 days

This configuration ensures data durability and availability, aligning with the company’s policies on data retention (refer to POL-COMP-001: Data Retention Policy).

## Dependencies

The Kafka Cluster relies on several internal and external systems for its operations:

- **Zookeeper**: While transitioning to KRaft, Zookeeper may still be involved in some legacy configurations.
- **Monitoring Tools**: Tools that provide observability (refer to MAN-ENG-004: Observability Handbook) and alerting for Kafka metrics.
- **Data Producers and Consumers**: Various applications and services that produce and consume messages from the Kafka Cluster.
- **Network Infrastructure**: Reliable network connections are essential for communication between brokers and clients.

## Operations

### Starting the Kafka Cluster

1. Ensure all brokers and KRaft controllers are up and running.
2. Use the following command to start each broker:

   ```bash
   bin/kafka-server-start.sh config/server.properties
   ```

3. Verify the status of the Kafka Cluster using:

   ```bash
   bin/kafka-topics.sh --bootstrap-server localhost:9092 --describe
   ```

### Managing Topics

- To create a new topic:

   ```bash
   bin/kafka-topics.sh --create --topic <topic_name> --bootstrap-server localhost:9092 --replication-factor 3 --partitions 3 --config min.insync.replicas=2
   ```

- To delete a topic:

   ```bash
   bin/kafka-topics.sh --delete --topic <topic_name> --bootstrap-server localhost:9092
   ```

### Monitoring and Alerts

- Set up alerts to notify when consumer lag exceeds 50,000 messages for 10 minutes.
- Use the following command to check consumer lag:

   ```bash
   bin/kafka-consumer-groups.sh --bootstrap-server localhost:9092 --describe --group <consumer_group>
   ```

## Common Alerts and Troubleshooting

### Common Alerts

- **Consumer Lag**: Alert when consumer lag exceeds 50,000 messages for 10 minutes.
- **Schema Compatibility Issues**: Alerts when data does not comply with the schema in the Schema Registry.
- **Broker Down**: Alerts when a broker becomes unresponsive.

### Troubleshooting Steps

- **Consumer Lag Delays**: If consumer lag exceeds the threshold, check for:

  - Slow consumers or application performance issues.
  - Network connectivity problems.
  - Review logs for any errors related to consumer processing (refer to INC-2025-008: Kafka consumer lag delays fraud scoring).

- **Broker Failures**: In case of broker failure, follow the steps in SOP-OPS-007: Kafka Broker Replacement.

- **Schema Registry Issues**: Check for schema compatibility and recent changes. Refer to logs for any discrepancies.

## Recovery

In the event of a failure, the following recovery procedures should be followed:

1. **Broker Recovery**: If a broker goes down, attempt to restart it using:

   ```bash
   bin/kafka-server-stop.sh
   bin/kafka-server-start.sh config/server.properties
   ```

2. **Schema Registry Recovery**: Ensure that the Schema Registry is reachable and that schemas are correctly registered.

3. **Data Recovery**: If data loss is suspected, refer to the retention policy and assess if the data falls within the retention period outlined in POL-COMP-001: Data Retention Policy.

4. **Incident Handling**: In case of significant failures, refer to POL-COMP-003: Incident Response Policy for procedures on escalation and resolution.

## References

- **Data Retention Policy**: POL-COMP-001
- **Incident Response Policy**: POL-COMP-003
- **Observability Handbook**: MAN-ENG-004
- **Kafka Broker Replacement**: SOP-OPS-007

This guide should serve as a reliable reference for all operational aspects of the Kafka Cluster, ensuring that processes are followed consistently and efficiently. Always refer to the latest versions of the referenced documents for the most current practices and policies.
