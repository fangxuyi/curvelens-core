# Migrating a Legacy WTI Deployment

Use `agent/migrate_wti_history.py` to move a deployed single-product CurveLens
data root into CurveLens Core's isolated WTI runtime. The migration includes
raw inputs, bronze/silver/gold layers, CME PDFs, reports, monitor state,
manifests, and delivery deduplication history.

It does not move source code, `.env`, credentials, agent registrations, cron
jobs, or Telegram configuration. It never deletes the legacy data.

## 1. Preview while the legacy deployment is running

From the Core repository root, run:

```bash
CCVM_PRODUCT=wti ccvm/.venv/bin/python agent/migrate_wti_history.py \
  --source /absolute/path/to/legacy/ccvm/data
```

Dry-run is the default. It reports new, differing, and unchanged files;
delivery-ledger counts; and manifest-row counts without writing anything.

## 2. Cut over

Choose a quiet period after the legacy deployment finishes a daily run:

1. Disable both legacy and Core WTI schedules and wait for active runs to end.
2. Repeat the dry-run and review its source and destination paths.
3. Apply the migration:

   ```bash
   CCVM_PRODUCT=wti ccvm/.venv/bin/python agent/migrate_wti_history.py \
     --source /absolute/path/to/legacy/ccvm/data \
     --apply --confirm-runtimes-stopped
   ```

4. Read the JSON result. A successful result is `MIGRATED` and contains a
   `verification` block plus `backup_root` and `report_path`.
5. Point the WTI agent at the Core repository. Enable only the Core schedules
   after the usual explicit delivery approval; leave the legacy schedules off.
6. Keep the legacy repository as a read-only rollback copy until Core has run
   successfully for several settlement days.

## Safety behavior

- Source artifacts are authoritative when the same relative file differs.
- Every overwritten destination artifact is backed up below
  `ccvm/data/products/wti/.migration_backups/`.
- Delivery ledgers are merged by message id. Anything already delivered is
  removed from pending, preventing historical messages from being resent.
- Existing destination-only manifest rows and delivery ids are preserved.
- Manifest `raw_path` values are rewritten to the Core WTI data root, then
  checked against the migrated file and its stored SHA-256.
- All source files are checksum-verified after copying.
- If the source changes during migration, the command fails and can be rerun;
  individual writes are atomic and the overall migration is idempotent.
- A DuckDB WAL file blocks the cutover because it indicates an active or
  uncleanly closed manifest database.
