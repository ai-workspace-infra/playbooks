# PR: fix/pgvector-native-install

## Summary
Dynamically detect the installed PostgreSQL major version on Linux systems and install the corresponding `pgvector` extension package.

## Context
When deploying PostgreSQL natively in standalone mode on Linux (Ubuntu/Debian), standard database base packages (`postgresql`, `postgresql-contrib`) are installed. However, `x_memory_hub` requires the `vector` extension (supplied by `pgvector`).

Because standard packages do not include `pgvector` out of the box, attempting to enable it leads to:
```
ERROR:  extension "vector" is not available
DETAIL:  Could not open extension control file "/usr/share/postgresql/17/extension/vector.control": No such file or directory.
```

The fix adds a dynamic package installer for pgvector:
1. Query the running PostgreSQL server version using `psql --version`.
2. Extract the major version number (e.g., `17`).
3. Install `postgresql-<major_version>-pgvector` from the active apt repository.

## Changes
| File | Change |
|------|--------|
| `roles/vhosts/postgres/tasks/native.yml` | Add PG version detection and dynamic `pgvector` apt package installation. |

## Related
- GHA Run: 29151394588
- Conversation: `2f521e13-c13e-4df8-b2d9-cd8883afff30`

## Verification
- CI/CD workflow `deploy-ai-workspace-iac.yaml` should deploy successfully.
- `x_memory_hub` should successfully run `CREATE EXTENSION IF NOT EXISTS vector;` on native database installations.
