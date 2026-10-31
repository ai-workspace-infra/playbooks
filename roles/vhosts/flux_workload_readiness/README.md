# Flux workload readiness

Observe only the GitRepository, Kustomization and HelmRelease objects explicitly
selected in the environment declaration. The role waits for Ready, confirms
observed generation, and records non-secret per-object revisions in a separate
receipt. It does not change Flux resources or establish application, data,
DNS, or user-flow acceptance. Missing, suspended, unhealthy or stale objects
fail the run; retries are bounded. Cluster initialization and three-identity
verification must have completed first. Repeated execution is read-only apart
from replacing the caller-selected receipt on the controller.
