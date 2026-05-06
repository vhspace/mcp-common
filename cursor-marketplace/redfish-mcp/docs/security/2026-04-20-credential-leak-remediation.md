# Credential Leak Remediation

**Status**: Pending -- scrubbed from HEAD, still in git history  
**Date discovered**: 2026-04-20  
**PR that scrubbed current tree**: #78  
**PR that adds gitleaks guardrails**: #79  

## Leaked credentials

Three real BMC passwords were committed to git history across 11 commits (first introduced 2026-02-10):

| Secret | Username | Site | Risk |
|--------|----------|------|------|
| `Ta1supp0rt` | `taiuser` | ORI | Active BMC credential |
| `owtXmNcN6b` | `tai` | 5C | Active BMC credential |
| `Extenuate` | `together_bmc` | IREN B300 | Active BMC credential |

## TODO

### 1. Rotate credentials (CRITICAL -- do first)
- [ ] Rotate ORI BMC password for `taiuser`
- [ ] Rotate 5C BMC password for `tai`
- [ ] Rotate IREN B300 BMC password for `together_bmc`
- [ ] Update 1Password vault entries
- [ ] Update any CI/CD env vars that use these

### 2. Delete stale remote branches
```bash
git push origin --delete fix/cli-site-aware-credentials
git push origin --delete fix/issue-40-env-credential-resolution
git push origin --delete fix/issue-40-netbox-credential-resolver
```

### 3. Rewrite git history with git-filter-repo
```bash
# Fresh mirror clone
git clone --mirror https://github.com/vhspace/redfish-mcp.git redfish-mcp-purge.git
cd redfish-mcp-purge.git

# Create replacements.txt:
#   Ta1supp0rt==>[REDACTED]
#   owtXmNcN6b==>[REDACTED]
#   Extenuate==>[REDACTED]

pip install git-filter-repo
git filter-repo --sensitive-data-removal \
  --replace-text replacements.txt \
  --replace-message replacements.txt

# Verify
git log -S'Ta1supp0rt' --all -p  # should find nothing
git log -S'owtXmNcN6b' --all -p
git log -S'Extenuate' --all -p

# Force push
git remote add origin https://github.com/vhspace/redfish-mcp.git
git push --force --mirror origin
```

### 4. Post-rewrite cleanup
- [ ] Contact GitHub Support to purge cached commit objects
- [ ] Notify all contributors to delete old clones and re-clone
- [ ] Verify no forks retain the old history
- [ ] Clean up `taiuser` username from `docs/REMOTE_MULTI_USER_DESIGN.md`

### Gotchas
- Coordinate a push freeze during the rewrite
- All 31 tags will be rewritten (breaks GPG signatures)
- Branch protection on main must allow force-push (currently unprotected)
- `Extenuate` is also an English word -- verify the replacement doesn't mangle prose
