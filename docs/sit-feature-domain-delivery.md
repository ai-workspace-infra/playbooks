# SIT feature domain delivery boundary

This contract documents the stable mapping and owner split for eventual per-Feature-ID SIT domains. It does not itself provision DNS, create a namespace, change ingress watch scope, or certify application reachability.

- GitOps owns the declarative Feature ID, namespace and hostname mapping.
- IaC owns exact Cloudflare A records and must use the fresh Dev ingress address from CMDB; conflicts with records not tagged to the same Feature ID are blocked.
- Playbooks owns the trusted lane materialization, ingress/certificate prerequisites and HTTP/HTTPS health evidence.
- Toolkit must bind the same Feature ID, fixed GitOps commit, namespace UID, CMDB revision, DNS receipts and test report into the release receipt.

Current implementation status: DNS owner mapping and a contract-only GitOps schema are under review. The deployed GitOps/Playbooks/Toolkit path still fixes `feature-xconnect-referral-sit`, one source directory and three referral hostnames. Dynamic namespace creation, platform Ingress watcher/RBAC lifecycle, HTTPS application probes and Toolkit caller wiring remain NOT_IMPLEMENTED. A DNS A record alone is not acceptance.

Do not enable a lane until the ordered owner PRs are reviewed and merged. Then provision only the exact lane records through the IaC owner workflow, materialize the reviewed namespace and ingress scope through trusted Playbooks, and require DNS-to-current-CMDB, HTTP challenge reachability, TLS certificate and application health receipts before reporting the lane accessible. Cleanup must retain test evidence and remove only resources owned by the exact Feature ID and namespace UID.
