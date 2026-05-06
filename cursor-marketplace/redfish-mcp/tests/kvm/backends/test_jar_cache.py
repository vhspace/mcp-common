"""Tests for the SHA-256 JAR cache."""

from __future__ import annotations

import gzip
import hashlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pytest_httpx import HTTPXMock

from redfish_mcp.kvm.backends._jar_cache import JarCache, JarCacheError

JAR_BYTES = b"PK\x03\x04" + b"fake jar contents\x00" * 200


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    return tmp_path / "jars"


class TestJarCache:
    def test_first_fetch_downloads_and_caches(self, httpx_mock: HTTPXMock, cache_dir: Path):
        httpx_mock.add_response(
            method="GET",
            url="https://10.0.0.1/iKVM.jar",
            content=JAR_BYTES,
            status_code=200,
        )
        cache = JarCache(root=cache_dir)
        path = cache.get_or_fetch(url="https://10.0.0.1/iKVM.jar", sid="abc", verify_tls=False)
        assert path.exists()
        assert path.read_bytes() == JAR_BYTES
        assert path.parent.parent == cache_dir
        expected_sha = hashlib.sha256(JAR_BYTES).hexdigest()
        assert expected_sha in str(path)

    def test_second_fetch_is_cache_hit(self, httpx_mock: HTTPXMock, cache_dir: Path):
        httpx_mock.add_response(
            method="GET",
            url="https://10.0.0.1/iKVM.jar",
            content=JAR_BYTES,
            status_code=200,
        )
        cache = JarCache(root=cache_dir)
        p1 = cache.get_or_fetch("https://10.0.0.1/iKVM.jar", sid="abc", verify_tls=False)
        p2 = cache.get_or_fetch("https://10.0.0.1/iKVM.jar", sid="abc", verify_tls=False)
        assert p1 == p2
        assert len(httpx_mock.get_requests()) == 1

    def test_cache_dir_is_0700(self, httpx_mock: HTTPXMock, cache_dir: Path):
        httpx_mock.add_response(
            method="GET",
            url="https://10.0.0.1/iKVM.jar",
            content=JAR_BYTES,
            status_code=200,
        )
        cache = JarCache(root=cache_dir)
        path = cache.get_or_fetch("https://10.0.0.1/iKVM.jar", sid="abc", verify_tls=False)
        assert (path.parent.stat().st_mode & 0o777) == 0o700
        assert (path.stat().st_mode & 0o777) == 0o600

    def test_tampered_cache_file_detected_and_refreshed(
        self, httpx_mock: HTTPXMock, cache_dir: Path
    ):
        httpx_mock.add_response(
            method="GET",
            url="https://10.0.0.1/iKVM.jar",
            content=JAR_BYTES,
            status_code=200,
        )
        cache = JarCache(root=cache_dir)
        path = cache.get_or_fetch("https://10.0.0.1/iKVM.jar", sid="abc", verify_tls=False)
        path.write_bytes(b"tampered")
        httpx_mock.add_response(
            method="GET",
            url="https://10.0.0.1/iKVM.jar",
            content=JAR_BYTES,
            status_code=200,
        )
        path2 = cache.get_or_fetch("https://10.0.0.1/iKVM.jar", sid="abc", verify_tls=False)
        assert path2.read_bytes() == JAR_BYTES

    def test_http_error_raises_jarcache_error(self, httpx_mock: HTTPXMock, cache_dir: Path):
        """404 on .jar and 404 on .jar.pack.gz both raise JarCacheError."""
        # Plain JAR 404 triggers fallback to .pack.gz
        httpx_mock.add_response(
            method="GET",
            url="https://10.0.0.1/iKVM.jar",
            status_code=404,
        )
        # pack.gz fallback also 404 → should raise JarCacheError
        httpx_mock.add_response(
            method="GET",
            url="https://10.0.0.1/iKVM.jar.pack.gz",
            status_code=404,
        )
        cache = JarCache(root=cache_dir)
        with pytest.raises(JarCacheError):
            cache.get_or_fetch("https://10.0.0.1/iKVM.jar", sid="abc", verify_tls=False)

    def test_404_jar_falls_back_to_pack200(self, httpx_mock: HTTPXMock, cache_dir: Path):
        """If the plain .jar returns 404, we fetch .jar.pack.gz and decode via unpack200."""
        # Fake pack.gz content — just gzip of some bytes (not real pack200, but we mock unpack200)
        fake_pack_gz = gzip.compress(b"fake pack200 content")

        httpx_mock.add_response(
            method="GET",
            url="https://10.0.0.1/iKVM.jar",
            status_code=404,
        )
        httpx_mock.add_response(
            method="GET",
            url="https://10.0.0.1/iKVM.jar.pack.gz",
            content=fake_pack_gz,
            status_code=200,
        )

        # Mock subprocess.run (unpack200) to write JAR_BYTES to the output path
        def fake_unpack200(cmd: list[str], **_kwargs: object) -> MagicMock:
            out_path = Path(cmd[-1])  # last arg is the output jar
            out_path.write_bytes(JAR_BYTES)
            result = MagicMock()
            result.returncode = 0
            return result

        cache = JarCache(root=cache_dir)
        with (
            patch(
                "redfish_mcp.kvm.backends._jar_cache.shutil.which",
                return_value="/usr/bin/unpack200",
            ),
            patch(
                "redfish_mcp.kvm.backends._jar_cache.subprocess.run",
                side_effect=fake_unpack200,
            ),
        ):
            path = cache.get_or_fetch("https://10.0.0.1/iKVM.jar", sid="abc", verify_tls=False)

        assert path.exists()
        assert path.read_bytes() == JAR_BYTES

    def test_pack200_without_unpack200_raises(self, httpx_mock: HTTPXMock, cache_dir: Path):
        """If unpack200 is not installed, a clear JarCacheError is raised."""
        fake_pack_gz = gzip.compress(b"fake pack200 content")

        httpx_mock.add_response(
            method="GET",
            url="https://10.0.0.1/iKVM.jar",
            status_code=404,
        )
        httpx_mock.add_response(
            method="GET",
            url="https://10.0.0.1/iKVM.jar.pack.gz",
            content=fake_pack_gz,
            status_code=200,
        )

        cache = JarCache(root=cache_dir)
        with (
            patch(
                "redfish_mcp.kvm.backends._jar_cache.shutil.which",
                return_value=None,
            ),
            patch(
                "redfish_mcp.kvm.backends._jar_cache.JarCache._find_unpack200",
                return_value=None,
            ),
        ):
            with pytest.raises(JarCacheError, match="unpack200"):
                cache.get_or_fetch("https://10.0.0.1/iKVM.jar", sid="abc", verify_tls=False)
