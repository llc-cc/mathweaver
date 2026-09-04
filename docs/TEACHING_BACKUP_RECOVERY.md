# Teaching data backup and recovery

The teaching service has three durable stores. A complete recovery point must
contain all three artifacts produced by `scripts/backup_teaching_data.sh`:

- `mysql.sql.gz`: relational users, classes, assignments, submissions, progress,
  history, and graph registry records;
- `neo4j-graphs.json.gz`: graph metadata, nodes, relationships, and payloads;
- `data-teaching.tar.gz`: uploaded source files and recoverable job artifacts.

Each backup also contains `METADATA` and `SHA256SUMS`. Backups are written to a
private partial directory, verified, and then atomically renamed under
`/opt/mathweaver/backups/teaching-<timestamp>`. The `latest-teaching` symlink
points to the most recent verified backup.

## Create and verify a backup

Run as root on the application server during a quiet period:

```bash
/opt/mathweaver/current-teaching/scripts/backup_teaching_data.sh
cd /opt/mathweaver/backups/latest-teaching
sha256sum -c SHA256SUMS
```

The installed timer runs the same online logical backup once each night. MySQL
uses a single transaction and includes the application schema, triggers, and all
rows. Stored routines and MySQL Events are intentionally excluded because this
application does not define them and the production application account does not
need elevated routine/event privileges. Every Neo4j graph is checked against the
MySQL graph registry, and the persistent file tree is archived without changing
application data.

## Recovery order

Recovery is an explicit maintenance operation; never run it against a live
production service.

1. Stop the frontend and all three teaching backend units.
2. Verify `SHA256SUMS` and confirm that `METADATA` names the `mathweaver`
   database and `/opt/mathweaver/data-teaching` directory.
3. Restore `mysql.sql.gz` into an empty `mathweaver` schema using a root-only
   MySQL client defaults file. Run the database upgrade command from the target
   release afterward.
4. Move the damaged `/opt/mathweaver/data-teaching` aside, extract
   `data-teaching.tar.gz` under `/opt/mathweaver`, and restore ownership to
   `nginx:nginx`.
5. Start Neo4j, load the environment file, and restore the logical graph bundle:

   ```bash
   set -a
   source /opt/mathweaver/.env.teaching
   set +a
   cd /opt/mathweaver/current-teaching/backend
   ../.venv/bin/python scripts/export_graph_backup.py \
     --restore /opt/mathweaver/backups/<backup>/neo4j-graphs.json.gz --apply
   ```

6. Start all three teaching backends and the frontend. Require HTTP 200 from
   ports 5002, 5003, and 5004 at `/health/ready`, then verify the public frontend.
7. Keep the previous data directory and database snapshot until row counts,
   graph checksums, and uploaded file access have been independently confirmed.

The logical graph restore replaces only graph ids present in the bundle. Restore
into an empty Neo4j database when recovering from a complete loss so that stale
extra graphs cannot survive unnoticed.
