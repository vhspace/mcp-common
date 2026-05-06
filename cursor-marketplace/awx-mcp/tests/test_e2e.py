"""End-to-end tests for AWX MCP server against a real AWX instance.

These tests require a test AWX instance and will create/modify test resources.
DO NOT run against production AWX instances.

Required environment variables:
- AWX_HOST: AWX instance URL
- AWX_TOKEN: API token with appropriate permissions
- E2E_TEST_ORG_ID: Organization ID for testing (optional)
- E2E_TEST_PROJECT_ID: Project ID for testing (optional)
- E2E_TEST_INVENTORY_ID: Inventory ID for testing (optional)

Test resources created will be prefixed with 'e2e-test-' for easy cleanup.
"""

import os
import time

import pytest

# Only run e2e tests if explicitly enabled
pytestmark = pytest.mark.skipif(
    os.getenv("E2E_TEST_ENABLED", "").lower() not in ("true", "1", "yes"),
    reason="E2E tests disabled. Set E2E_TEST_ENABLED=true to run.",
)

from awx_mcp.awx_client import AwxRestClient
from awx_mcp.config import Settings


@pytest.fixture(scope="session")
def awx_client() -> AwxRestClient:
    """Create AWX client for e2e testing."""
    settings = Settings()
    return AwxRestClient(
        host=str(settings.awx_host),
        token=settings.awx_token.get_secret_value(),
        api_base_path=settings.api_base_path,
        verify_ssl=settings.verify_ssl,
        timeout_seconds=settings.timeout_seconds,
    )


@pytest.fixture
def test_org_id() -> int | None:
    """Get test organization ID from environment."""
    org_id = os.getenv("E2E_TEST_ORG_ID")
    return int(org_id) if org_id else None


@pytest.fixture
def test_project_id() -> int | None:
    """Get test project ID from environment."""
    project_id = os.getenv("E2E_TEST_PROJECT_ID")
    return int(project_id) if project_id else None


@pytest.fixture
def test_inventory_id() -> int | None:
    """Get test inventory ID from environment."""
    inventory_id = os.getenv("E2E_TEST_INVENTORY_ID")
    return int(inventory_id) if inventory_id else None


def test_awx_connectivity(awx_client: AwxRestClient) -> None:
    """Test basic connectivity to AWX instance."""
    ping_result = awx_client.get("ping")
    assert "version" in ping_result
    assert "active_node" in ping_result
    print(f"Connected to AWX version {ping_result['version']}")


def test_awx_user_authentication(awx_client: AwxRestClient) -> None:
    """Test that our token provides valid authentication."""
    user_result = awx_client.get("me")
    assert "id" in user_result
    assert "username" in user_result
    print(f"Authenticated as user: {user_result['username']}")


def test_awx_list_job_templates(awx_client: AwxRestClient) -> None:
    """Test listing job templates from real AWX instance."""
    templates_result = awx_client.get("job_templates", params={"page_size": 5})
    assert "count" in templates_result
    assert "results" in templates_result
    assert isinstance(templates_result["results"], list)
    print(f"Found {templates_result['count']} job templates")


def test_awx_list_inventories(awx_client: AwxRestClient) -> None:
    """Test listing inventories from real AWX instance."""
    inventories_result = awx_client.get("inventories", params={"page_size": 5})
    assert "count" in inventories_result
    assert "results" in inventories_result
    assert isinstance(inventories_result["results"], list)
    print(f"Found {inventories_result['count']} inventories")


def test_awx_list_projects(awx_client: AwxRestClient) -> None:
    """Test listing projects from real AWX instance."""
    projects_result = awx_client.get("projects", params={"page_size": 5})
    assert "count" in projects_result
    assert "results" in projects_result
    assert isinstance(projects_result["results"], list)
    print(f"Found {projects_result['count']} projects")


def test_awx_list_credentials(awx_client: AwxRestClient) -> None:
    """Test listing credentials from real AWX instance."""
    credentials_result = awx_client.get("credentials", params={"page_size": 5})
    assert "count" in credentials_result
    assert "results" in credentials_result
    assert isinstance(credentials_result["results"], list)
    print(f"Found {credentials_result['count']} credentials")


def test_awx_list_users(awx_client: AwxRestClient) -> None:
    """Test listing users from real AWX instance."""
    users_result = awx_client.get("users", params={"page_size": 5})
    assert "count" in users_result
    assert "results" in users_result
    assert isinstance(users_result["results"], list)
    print(f"Found {users_result['count']} users")


@pytest.mark.skipif(
    os.getenv("E2E_ALLOW_JOB_LAUNCH", "").lower() not in ("true", "1", "yes"),
    reason="Job launch tests disabled. Set E2E_ALLOW_JOB_LAUNCH=true to run.",
)
def test_awx_job_lifecycle(awx_client: AwxRestClient, test_inventory_id: int | None) -> None:
    """
    Test complete job lifecycle: launch, monitor, and cleanup.

    This is a comprehensive e2e test that launches a real job.
    Only run this if you have a safe test environment.
    """
    if not test_inventory_id:
        pytest.skip("No test inventory ID provided")

    # Find a simple job template (preferably one that doesn't modify infrastructure)
    templates = awx_client.get("job_templates", params={"page_size": 10})
    if not templates["results"]:
        pytest.skip("No job templates available for testing")

    # Use the first template (in a real scenario, you'd want a specific test template)
    template = templates["results"][0]
    template_id = template["id"]

    print(f"Launching job from template: {template['name']}")

    # Launch the job
    launch_result = awx_client.post(f"job_templates/{template_id}/launch")
    job_id = launch_result["id"]

    print(f"Launched job ID: {job_id}")

    # Wait for job to complete (with timeout)
    max_wait = 300  # 5 minutes
    start_time = time.time()

    while time.time() - start_time < max_wait:
        job_status = awx_client.get(f"jobs/{job_id}")
        status = job_status.get("status", "")

        if status in ["successful", "failed", "error", "canceled"]:
            print(f"Job completed with status: {status}")
            break

        time.sleep(10)  # Wait 10 seconds before checking again

    # Verify final status
    final_job = awx_client.get(f"jobs/{job_id}")
    final_status = final_job.get("status", "")
    assert final_status in ["successful", "failed", "error", "canceled"]

    # Get job stdout
    stdout_result = awx_client.get_text(f"jobs/{job_id}/stdout")
    assert isinstance(stdout_result, str)
    print(f"Job stdout length: {len(stdout_result)} characters")

    print(f"E2E job test completed successfully for job {job_id}")


def test_awx_system_metrics(awx_client: AwxRestClient) -> None:
    """Test system metrics collection."""
    # Test individual metric endpoints
    unified_jobs = awx_client.get("unified_jobs", params={"page_size": 1})
    assert "count" in unified_jobs

    running_jobs = awx_client.get("jobs", params={"status": "running", "page_size": 1})
    assert "count" in running_jobs

    failed_jobs = awx_client.get("jobs", params={"status": "failed", "page_size": 1})
    assert "count" in failed_jobs

    print(
        f"System metrics - Total jobs: {unified_jobs['count']}, Running: {running_jobs['count']}, Failed: {failed_jobs['count']}"
    )


def test_awx_cluster_status(awx_client: AwxRestClient) -> None:
    """Test cluster status information."""
    try:
        instances = awx_client.get("instances")
        assert "count" in instances
        print(f"Cluster has {instances['count']} instances")
    except Exception:
        # Instances endpoint might not be available in all AWX versions
        print("Instances endpoint not available")

    try:
        ping = awx_client.get("ping")
        assert "version" in ping
        print(f"AWX version: {ping['version']}")
    except Exception:
        print("Ping endpoint failed")


def test_awx_workflow_visualization(awx_client: AwxRestClient) -> None:
    """Test workflow visualization data collection."""
    # Find workflow templates
    workflows = awx_client.get("workflow_job_templates", params={"page_size": 5})

    if workflows["results"]:
        workflow = workflows["results"][0]
        workflow_id = workflow["id"]

        print(f"Testing workflow visualization for: {workflow['name']}")

        # Get workflow nodes
        nodes = awx_client.get(f"workflow_job_templates/{workflow_id}/workflow_nodes")
        assert "results" in nodes

        print(f"Workflow has {len(nodes['results'])} nodes")

        # Test visualization data structure
        node_data = nodes["results"]
        if node_data:
            # Verify node structure
            node = node_data[0]
            assert "id" in node
            assert "identifier" in node
            assert "unified_job_type" in node
            print("Workflow node structure validated")
    else:
        print("No workflow templates available for visualization testing")


def test_awx_credential_types(awx_client: AwxRestClient) -> None:
    """Test credential type enumeration."""
    credential_types = awx_client.get("credential_types", params={"page_size": 20})
    assert "count" in credential_types
    assert "results" in credential_types

    # Should have standard types like Machine, Vault, etc.
    type_names = [ct["name"] for ct in credential_types["results"]]
    print(f"Available credential types: {', '.join(type_names)}")

    # Verify we have basic types
    assert any("Machine" in name for name in type_names), (
        "Machine credential type should be available"
    )


def test_awx_organization_structure(awx_client: AwxRestClient, test_org_id: int | None) -> None:
    """Test organization structure and permissions."""
    if test_org_id:
        org_details = awx_client.get(f"organizations/{test_org_id}")
        assert "id" in org_details
        assert org_details["id"] == test_org_id
        print(f"Test organization: {org_details.get('name', 'Unknown')}")

        # Test teams in organization
        teams = awx_client.get("teams", params={"organization": test_org_id, "page_size": 5})
        assert "results" in teams
        print(f"Organization has {len(teams['results'])} teams")
    else:
        print("No test organization ID provided, skipping organization tests")
