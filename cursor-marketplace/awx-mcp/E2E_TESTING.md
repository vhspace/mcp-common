# E2E Testing Guide

This document explains how to set up and run end-to-end tests for the AWX MCP server against a real AWX instance.

## ⚠️ Safety Warning

**DO NOT run e2e tests against production AWX instances!** These tests will create, modify, and potentially delete resources in your AWX environment.

## Prerequisites

### 1. Test AWX Instance

You need a dedicated AWX instance for testing. Options:

#### Option A: Local AWX Development Setup
```bash
# Using awx-operator (Kubernetes)
kubectl apply -f https://raw.githubusercontent.com/ansible/awx-operator/devel/deploy/awx-demo.yml

# Or using docker-compose
git clone https://github.com/ansible/awx
cd awx
make docker-compose
```

#### Option B: AWX Demo Instance
Use the official AWX demo environment or set up a temporary instance in a sandbox environment.

#### Option C: Isolated Test Environment
Set up AWX in a separate namespace/project/cluster dedicated for testing.

### 2. Test Resources

Create test resources in your AWX instance (these will be used by e2e tests):

```bash
# Example test resources to create:
# - Organization: "E2E Test Org"
# - Project: "E2E Test Project" (Git repo: https://github.com/ansible/ansible-tower-samples)
# - Inventory: "E2E Test Inventory"
# - Job Template: "E2E Test Job" (using a simple playbook)
```

### 3. API Token

Create a Personal Access Token in AWX with appropriate permissions for testing:
- Read access to all resources
- Write access to jobs, inventories, projects
- Execute permissions for job templates

## Configuration

### Environment Variables

Set these environment variables for e2e testing:

```bash
# Required
export AWX_HOST="https://your-test-awx-instance.com"
export AWX_TOKEN="your_personal_access_token"

# Optional - specify test resources (if not set, tests will skip some operations)
export E2E_TEST_ORG_ID="123"
export E2E_TEST_PROJECT_ID="456"
export E2E_TEST_INVENTORY_ID="789"

# Enable e2e tests
export E2E_TEST_ENABLED="true"

# Allow job launching (dangerous - only for safe test environments)
export E2E_ALLOW_JOB_LAUNCH="false"
```

### GitHub Actions Secrets

For CI/CD, set these as repository secrets:
- `AWX_HOST`: Test AWX instance URL
- `AWX_TOKEN`: API token for test instance
- `E2E_TEST_ORG_ID`: Organization ID for testing
- `E2E_TEST_PROJECT_ID`: Project ID for testing
- `E2E_TEST_INVENTORY_ID`: Inventory ID for testing

## Running E2E Tests

### Local Development

```bash
# Set environment variables (see above)
export AWX_HOST="https://test-awx.example.com"
export AWX_TOKEN="your_token_here"
export E2E_TEST_ENABLED="true"

# Run all e2e tests
cd awx-mcp
uv run pytest tests/test_e2e.py -v

# Run specific test
uv run pytest tests/test_e2e.py::test_awx_connectivity -v
```

### GitHub Actions

E2E tests run automatically when:
1. **Manual trigger**: Go to Actions → "CI" → "Run workflow" → Select branch
2. **PR labeled**: Add `e2e-test` label to pull request

### Test Categories

#### Safe Tests (Always Run)
- `test_awx_connectivity` - Basic connectivity
- `test_awx_user_authentication` - Token validation
- `test_awx_list_*` - Read-only operations
- `test_awx_system_metrics` - System information
- `test_awx_cluster_status` - Cluster health

#### Destructive Tests (Optional)
- `test_awx_job_lifecycle` - Launches actual jobs (disabled by default)

## Test Data Management

### Cleanup Strategy

E2E tests create resources with predictable names:

```bash
# Find test resources
awx organizations list | grep "e2e-test"
awx job_templates list | grep "e2e-test"
awx credentials list | grep "e2e-test"

# Clean up manually if needed
awx job_templates delete <id>
awx credentials delete <id>
```

### Test Resource Naming

All test-created resources use prefixes:
- `e2e-test-*` - Resources created by e2e tests
- `E2E Test *` - Pre-configured test resources

## Troubleshooting

### Common Issues

#### Authentication Errors
```bash
# Check token validity
curl -H "Authorization: Bearer $AWX_TOKEN" $AWX_HOST/api/v2/ping

# Verify token permissions
curl -H "Authorization: Bearer $AWX_TOKEN" $AWX_HOST/api/v2/me
```

#### Connection Issues
```bash
# Test basic connectivity
curl $AWX_HOST/api/v2/ping

# Check SSL certificate
curl --insecure $AWX_HOST/api/v2/ping
```

#### Permission Errors
```bash
# Check token permissions in AWX UI
# Ensure token has:
# - Read access to organizations, projects, inventories
# - Execute access to job templates (for job launch tests)
```

### Debug Mode

Enable debug logging:
```bash
export LOG_LEVEL="DEBUG"
uv run pytest tests/test_e2e.py::test_awx_connectivity -v -s
```

## CI/CD Integration

### Automated E2E Testing

The `.github/workflows/ci.yml` includes an optional e2e job that:

1. Runs only on manual trigger or when PR is labeled `e2e-test`
2. Uses secrets for AWX connection
3. Runs all safe e2e tests
4. Reports results to GitHub

### Test Environment Setup

For automated CI, consider:

1. **Ephemeral AWX instances** using awx-operator
2. **Docker Compose setup** for local testing
3. **Cloud-based test instances** (AWS, GCP, etc.)
4. **Shared test environment** with resource isolation

## Best Practices

### Test Environment Isolation

1. **Dedicated resources**: Use separate org/project/inventory for e2e tests
2. **Resource cleanup**: Delete test resources after each test run
3. **Idempotent tests**: Tests should work regardless of existing state
4. **Failure recovery**: Tests should clean up even if they fail

### Security Considerations

1. **Never use production tokens** in e2e tests
2. **Rotate test tokens** regularly
3. **Limit test permissions** to minimum required
4. **Audit test activity** in AWX logs

### Performance

1. **Parallel execution**: Run tests in parallel when possible
2. **Timeouts**: Set appropriate timeouts for operations
3. **Resource limits**: Limit concurrent jobs and resource usage
4. **Caching**: Cache expensive operations where safe

## Contributing

When adding new e2e tests:

1. Follow the existing patterns in `test_e2e.py`
2. Use the `pytestmark` skip decorator for safety
3. Document any new environment variables required
4. Include cleanup logic for created resources
5. Test against multiple AWX versions when possible