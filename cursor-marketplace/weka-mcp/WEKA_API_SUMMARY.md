# Weka API Summary

This document summarizes the Weka REST API endpoints and authentication methods for building the Weka MCP server.

## Base URL
- Default: `https://weka01:14000/api/v2`
- Port: `14000` (HTTPS)

## Authentication

### Login Endpoint
**POST** `/api/v2/login`

Request body:
```json
{
  "username": "admin",
  "password": "your-password"
}
```

Response:
```json
{
  "data": [{
    "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    "expires_in": 300
  }]
}
```

### Refresh Token
**POST** `/api/v2/login/refresh`

Request body:
```json
{
  "refresh_token": "refresh-token-here"
}
```

### Using Access Token
Include in Authorization header:
```
Authorization: Bearer ACCESS-TOKEN-HERE
```

## Key API Endpoints

### Cluster Management

#### Get Cluster Status
**GET** `/api/v2/cluster`

Returns cluster health, hosts, capacity, and performance metrics.

#### Get Cluster Statistics
**GET** `/api/v2/stats`

Returns comprehensive performance and resource usage statistics.

#### List Containers
**GET** `/api/v2/containers`

Returns all containers running in the cluster with their status, hostname, IPs, and resource allocation.

### Filesystem Management

#### List All Filesystems
**GET** `/api/v2/fileSystems`

Returns a list of all filesystems in the cluster.

Query parameters:
- `data_reduction_ratio` (optional)

#### Get Filesystem Details
**GET** `/api/v2/fileSystems/{uid}`

Returns specific information about a filesystem including size, quota, and usage.

#### Create Filesystem
**POST** `/api/v2/fileSystems`

Request body:
```json
{
  "name": "new-fs",
  "capacity": "10TB",
  "tiering": {},
  "kms_vault_key_identifier": "optional-key-id",
  "kms_vault_namespace": "optional-namespace",
  "kms_vault_role_id": "optional-role-id",
  "kms_vault_secret_id": "optional-secret-id"
}
```

Response:
```json
{
  "uid": "fs-002",
  "name": "new-fs"
}
```

#### Delete Filesystem
**DELETE** `/api/v2/fileSystems/{uid}`

Removes a filesystem and its data from the cluster.

#### Restore Filesystem from Snapshot
**POST** `/api/v2/fileSystems/download`

Request body:
```json
{
  "source_bucket": "my-backup-bucket",
  "snapshot_name": "snapshot-20231027",
  "new_fs_name": "restored-fs"
}
```

### S3 Cluster Management

#### View S3 Cluster
**GET** `/api/v2/s3`

Returns details about the S3 cluster.

#### Create S3 Cluster
**POST** `/api/v2/s3`

#### Update S3 Cluster
**PUT** `/api/v2/s3`

#### Delete S3 Cluster
**DELETE** `/api/v2/s3`

## Python Example

```python
import requests

# Login to obtain access token
url = "https://weka01:14000/api/v2/login"
payload = {
    "username": "admin",
    "password": "admin"
}
headers = {
    'Content-Type': 'application/json'
}

response = requests.post(url, headers=headers, json=payload, verify=False)
auth_data = response.json()

access_token = auth_data['data'][0]['access_token']

# Get cluster status
url = "https://weka01:14000/api/v2/cluster"
headers = {
    'Authorization': f'Bearer {access_token}'
}

response = requests.get(url, headers=headers, verify=False)
cluster_status = response.json()
```

## Notes

- Access tokens expire in 300 seconds (5 minutes)
- Use refresh tokens to obtain new access tokens
- SSL verification can be disabled with `verify=False` for development
- All API calls require authentication except the login endpoint
