---
name: weka-storage-ops
description: Use when performing Weka storage operations — creating filesystems, managing orgs, snapshots, S3, user accounts, or quota changes. Triggers on Weka provisioning, filesystem create, org create, snapshot, S3 bucket, quota update, user management.
---

# Weka Storage Operations

This skill covers write operations on Weka clusters. For read-only triage, see `storage-management`.

## Common Operations

### Create a filesystem
```python
# MCP
weka_create_filesystem(name="training-data", capacity="50TB", group_name="ml-group")

# CLI
weka-cli create-fs training-data 50TB --group ml-group
```

### Resize a filesystem
```python
weka_update_filesystem(uid="fs-0001", total_capacity="100TB")
# CLI: weka-cli update-fs fs-0001 --total-capacity 100TB
```

### Create an org with quotas
```python
weka_create_organization(name="ml-team", ssd_quota_gb=500, total_quota_gb=2000)
# CLI: weka-cli create-org ml-team --ssd-quota 500 --total-quota 2000
```

### Update org quotas
```python
weka_update_org_quota(org_uid="org-0002", ssd_quota="520TB", total_quota="1PB")
# CLI: weka-cli update-org-quota org-0002 --ssd-quota 520TB --total-quota 1PB
```

### Create an org user
```python
weka_create_user(username="alice", password="secure-pass", role="OrgAdmin")
# CLI: weka-cli create-user alice --password secure-pass --role OrgAdmin
```

### Snapshot workflow
```python
# Create snapshot
weka_create_snapshot(filesystem_uid="fs-0001", name="pre-migration-v1")
# Upload to object storage for DR
weka_upload_snapshot(uid="snap-0001", locator="s3://backup-bucket")
# Restore from backup
weka_restore_filesystem(source_bucket="backup-bucket", snapshot_name="pre-migration-v1", new_fs_name="restored-data")
```

### Mute alerts during maintenance
```python
weka_manage_alert(action="mute", alert_type="NodeDown", duration_secs=7200)
# Unmute when done
weka_manage_alert(action="unmute", alert_type="NodeDown")
```

## Multi-Site Operations

All operations accept an optional `site` parameter:
```python
weka_create_filesystem(name="data", capacity="10TB", site="dfw01")
weka_list_filesystems(site="ori")
```

CLI equivalent: `weka-cli create-fs data 10TB --site dfw01`

## Gotchas for Write Operations

### Org and User Management
1. **`weka_create_user` targets the session org**: It creates users in the org of the logged-in session, NOT a target org. To create a user in org "ml-team", set `WEKA_ORG=ml-team` in the environment first.

2. **Usernames are globally unique across orgs**: You cannot have "admin" in both root and ml-team orgs.

3. **First OrgAdmin bootstrap problem**: If you delete the only admin of an org, you must delete and recreate the org: `weka org create <name> <username> <password>`.

4. **Org-scoped filesystem visibility**: Filesystems created by root org are invisible to org-scoped users. If an org user needs access to a filesystem, it must be created while authenticated as that org.

### Filesystem Operations
5. **Non-tiered FS and ssd_capacity**: For non-tiered filesystems, SSD capacity equals total capacity. Attempting to set `ssd_capacity` separately will error.

6. **Capacity strings**: Both MCP and CLI accept human-readable strings like "50TB", "1PB". The MCP `weka_create_filesystem` converts to bytes internally.

### CSI Integration
7. **CSI requires host weka client**: `weka local setup container` must be run on each node before CSI can mount filesystems.

8. **CSI secret org mismatch**: The CSI secret's org user must belong to the same org that owns the filesystem. Cross-org access is not supported.

9. **mount_opts net= param**: Only needed for RDMA. ORI site runs Weka over Ethernet UDP — don't set `net=ib`.
