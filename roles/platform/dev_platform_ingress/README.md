# Fixed Dev shared ingress owner

This role only reconciles the reviewed `services/platform-ingress/dev` tree. The adapter checks exact raw file hashes before Kustomize, then the four-resource rendered hash; inputs cannot approve a different platform manifest. The fixed official Traefik chart and image are checked again at runtime.

The existing Dev bootstrap identity and shared strict SSH binding supply transport. CMDB instance identity, node UID, cluster UID and unchanged foundation source/root are required. The role first creates the two compatible app namespaces, then the platform namespace, scoped reconciler and source. The source Kustomization filters its Namespace object because namespace creation belongs to this management owner.

The reconciler has finite namespaced permissions in platform-ingress and watcher Role/RoleBinding permissions in the two app namespaces. It has no cluster resources, CRDs, wildcard verbs or escalation. The reviewed chart renders nine resources. Only the approved 80/443 hostPorts are exempted from restricted pod policy in the owned platform namespace; the two app namespaces remain restricted. Existing ownership or occupied hostPorts fail before writes.

Verification checks current source/Helm readiness, exact chart/values, finite RBAC, one nonroot Traefik pod on the approved node, immutable runtime imageID, ingress class and watch namespaces, and a local unmatched-host HTTP 404. It never reads ACME storage. TLS issuance and business acceptance remain NOT_RUN. No workflow in this change has been applied to a host.

The reusable caller must provide same-run protected Toolkit candidate and CMDB artifacts. Modes are plan, reconcile and verify. Platform candidate receipts use stage `platform-ingress`; the source is fixed to the reviewed commit. The platform owner does not deploy either lane's business candidate or change the foundation root source.
