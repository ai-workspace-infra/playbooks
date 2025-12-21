# Playbook roles planning

This document clarifies what should live under `/playbooks/roles/` for host-level automation (Ansible) versus what should be delivered through Helm charts, and ensures we cover the five tiers across data platforms: data warehouse → big data → ML → DL → large models.

## Scope rules
- **Ansible roles**: host-coupled configuration that is not itself a cloud resource (GPU driver/runtime, OS tuning, user/SSH prep, rendering on-host config files, database bootstrapping, etc.).
- **Helm charts**: anything that runs as a Kubernetes workload (operators, clusters, services running in pods).

## Base roles shared across tiers (Ansible)
- GPU driver and CUDA stack installation.
- Docker/Containerd runtime setup.
- System parameter tuning (kernel limits, hugepages, network stack), plus user home/SSH layout.
- Database initialization tasks (e.g., bootstrap PostgreSQL/ClickHouse on hosts) and rendering templated configs such as `ClickHouse/users.xml`.

## Coverage by capability tier
| Tier | Host-focused roles (Ansible) | Kubernetes services (Helm) |
| --- | --- | --- |
| Data warehouse | ClickHouse host bootstrap & config render; PostgreSQL init where needed. |  — |
| Big data | JVM/runtime, local disks, and OS tuning for data nodes. | Spark Operator; Flink Operator; Kafka/Redpanda; MinIO. |
| ML | GPU runtime base (drivers, container runtime), Python ML base image prep; user workspace/SSH. | Ray Cluster; MLflow; JupyterHub. |
| DL | Same GPU/system tuning plus inference node bootstrap (tensorRT/cuDNN as needed). | Triton Inference Server; LMDeploy (for deployment runtimes). |
| Large models | Secure SSH/user profiles and config templating for model storage/IO. | vLLM serving; model-specific Helm releases atop Ray/K8s. |

## Suggested role layout under `/playbooks/roles/`
- `common/` (new): shared tasks for system tuning, users/SSH, and package repos for GPU/runtime support.
- `gpu/`: install GPU drivers + CUDA toolkit.
- `container_runtime/`: install and configure Docker/Containerd with GPU runtime integration.
- `database_init/`: bootstrap on-host databases (e.g., PostgreSQL, ClickHouse), render config files (`users.xml`, etc.).
- `bigdata_node_prep/`: OS/disk tuning for Spark/Flink/Kafka/Redpanda/MinIO hosts.
- `ml_node_prep/`: Python/conda base, SSH workspace prep for ML workloads.
- `dl_inference_node/`: tensorRT/cuDNN dependencies and runtime checks for Triton/LMDeploy nodes.

Helm-delivered components should live under `playbooks/roles/charts/` or the repo’s Helm release structure and include Spark/Flink Operators, Kafka/Redpanda/MinIO, Ray Cluster, Triton, vLLM/LMDeploy, MLflow, and JupyterHub.
