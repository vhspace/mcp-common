from maas_mcp.maas_client import MaasRestClient


def test_api_urls_have_trailing_slash() -> None:
    client = MaasRestClient(url="http://maas.example.com:5240/MAAS", api_key="a:b:c")
    assert client._get_api_url("version").endswith("/api/2.0/version/")
    assert client._get_api_url("machines").endswith("/api/2.0/machines/")
    assert client._get_api_url("machines/abc123").endswith("/api/2.0/machines/abc123/")
