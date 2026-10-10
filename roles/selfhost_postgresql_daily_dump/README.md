# Selfhost PostgreSQL daily logical dump

This role manages the production Web SaaS `pg_dumpall` backup runner and its
cron entry. It writes date-named gzip dumps to the `/data` persistent disk,
validates gzip and SHA-256 before accepting a backup, and retains the latest
seven Shanghai calendar dates. Expiry removes only matching date-named dump
files and their `.sha256` sidecars.

The dump is plaintext before gzip and relies only on the persistent disk's
at-rest encryption. Host administrators can read it. The role does not restore
data, configure incremental backups, change PostgreSQL settings, or include
database credentials in files. The runner uses the local Docker PostgreSQL
container and refuses to run if `/data` is not mounted or the container is not
healthy. It skips an already-existing same-day dump only after validating its
gzip stream and checksum; it never overwrites that file.

The role is disabled by default. The PROD Web SaaS host enables it through its
host variables and the `setup-web-saas-domain.yml` caller.
