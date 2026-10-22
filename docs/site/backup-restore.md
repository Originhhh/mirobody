# Backups

A complete recovery needs the database, uploaded files and matching encryption
keys. Application and model images can be downloaded again.

## Create a backup

Run from the checkout that owns the Docker deployment, with Postgres running:

```bash
shell/backup.sh
# Or choose another destination:
BACKUP_DIR=/mnt/nas/mirobody shell/backup.sh
```

The script creates a Postgres dump and, when present, an archive of local
uploads. Keep a copy off the application host.

## Keep these separately

| Item | Reason |
| --- | --- |
| `.env`, active config overlays, secret YAML files and Compose overrides | The backup script does not include configuration or encryption keys. |
| External storage buckets | Files in S3 or OSS are not copied by the local-upload backup. |
| Running version or commit | Needed to choose compatible code for recovery. |

Newly generated keys cannot decrypt existing content. Protect keys alongside
the backup while keeping them out of source control.

## Verify and recover

An archive listing check does not prove the whole application can be restored.
Rehearse recovery on an isolated deployment, then open a representative record
and uploaded file. Keep production volumes out of that test.

For the recovery commands, use
[Verify and Restore a Backup](https://docs.mirobody.ai/en/deployment/restore).
The [technical reference](../backup-restore.md) explains volume names,
retention and version-specific migrations.
