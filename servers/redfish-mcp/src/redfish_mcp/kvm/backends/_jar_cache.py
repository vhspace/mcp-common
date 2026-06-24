"""SHA-256 content-addressable cache for the Supermicro iKVM JAR.

The JAR is vendor-supplied and tied to the BMC's firmware version. We never
redistribute it — each BMC serves its own. Caching by content hash means the
cache is correct across BMCs without us having to guess firmware identifiers.

Layout:
    <root>/
        <sha256>/
            iKVM.jar     (mode 0600, file)

Directory <sha256> has mode 0700. The root directory is created with whatever
mode its parents provide; on first use we chmod it to 0700 too.

Newer Supermicro X13 firmware (with jnlp.packEnabled=true) serves the JAR
only as a gzip-compressed pack200 file at <jar-url>.pack.gz. If the plain JAR
URL returns 404, we automatically fall back to the .pack.gz URL and decompress
it using the `unpack200` tool (available in Java 8-13 JDK; in Java 11 JDK).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import httpx

logger = logging.getLogger("redfish_mcp.kvm.backends.jar_cache")


class JarCacheError(Exception):
    """Raised when a JAR cannot be fetched or validated."""


@dataclass
class JarCache:
    root: Path

    def _subdir(self, sha: str) -> Path:
        return self.root / sha

    def _jar_path(self, sha: str) -> Path:
        return self._subdir(sha) / "iKVM.jar"

    def _manifest_path(self) -> Path:
        return self.root / "manifest.json"

    def _ensure_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)

    def _load_manifest(self) -> dict[str, str]:
        """Load URL -> SHA mapping from manifest."""
        manifest_file = self._manifest_path()
        if manifest_file.exists():
            return json.loads(manifest_file.read_text())  # type: ignore[no-any-return]
        return {}

    def _save_manifest(self, manifest: dict[str, str]) -> None:
        """Save URL -> SHA mapping to manifest (0600 perms to match cache policy)."""
        manifest_file = self._manifest_path()
        manifest_file.write_text(json.dumps(manifest))
        os.chmod(manifest_file, 0o600)

    def _validate(self, path: Path, sha: str) -> bool:
        if not path.exists():
            return False
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(64 * 1024), b""):
                h.update(chunk)
        return h.hexdigest() == sha

    def get_or_fetch(self, url: str, *, sid: str, verify_tls: bool = False) -> Path:
        """Return the path to the cached JAR, downloading if needed.

        The URL is fetched; the response body is hashed; cache path is derived
        from the hash. Subsequent calls with the same content hit the cache
        even if the URL changes (which it does on firmware updates).

        On newer Supermicro X13 firmware the plain JAR URL returns 404 because
        only the gzip-compressed pack200 variant (<url>.pack.gz) is served.
        We detect this 404 and retry with the .pack.gz suffix, then decode via
        `unpack200` (present in Java 8-13 JDK installations).
        """
        self._ensure_root()
        manifest = self._load_manifest()

        # Check if we've already cached this URL
        if url in manifest:
            sha = manifest[url]
            jar_path = self._jar_path(sha)
            if self._validate(jar_path, sha):
                logger.debug("JAR cache hit: %s", sha[:12])
                return jar_path

        try:
            with httpx.Client(verify=verify_tls, timeout=httpx.Timeout(30.0, connect=5.0)) as c:
                resp = c.get(url, cookies={"SID": sid})
        except httpx.HTTPError as exc:
            raise JarCacheError(f"JAR download failed: {exc}") from exc

        if resp.status_code == 404:
            # Newer Supermicro X13 firmware only serves the JAR in gzip-compressed
            # pack200 format at <url>.pack.gz (jnlp.packEnabled=true).
            logger.info("JAR URL %s returned 404; retrying as pack200 (%s.pack.gz)", url, url)
            body = self._fetch_and_decode_pack200(url + ".pack.gz", sid=sid, verify_tls=verify_tls)
        elif resp.status_code != 200:
            raise JarCacheError(f"JAR download returned HTTP {resp.status_code}: {resp.text[:200]}")
        else:
            body = resp.content

        sha = hashlib.sha256(body).hexdigest()
        jar_path = self._jar_path(sha)

        if self._validate(jar_path, sha):
            logger.debug("JAR cache hit: %s", sha[:12])
            manifest[url] = sha
            self._save_manifest(manifest)
            return jar_path

        subdir = self._subdir(sha)
        subdir.mkdir(parents=True, exist_ok=True)
        os.chmod(subdir, 0o700)

        tmp = subdir / "iKVM.jar.tmp"
        tmp.write_bytes(body)
        os.chmod(tmp, 0o600)
        os.replace(tmp, jar_path)

        manifest[url] = sha
        self._save_manifest(manifest)

        logger.info("JAR cached: %s (%d bytes)", sha[:12], len(body))
        return jar_path

    def _fetch_and_decode_pack200(self, pack_gz_url: str, *, sid: str, verify_tls: bool) -> bytes:
        """Download a .pack.gz file and convert it to a plain JAR.

        Uses the ``unpack200`` binary (part of Java 8-13 JDK). On Java 14+
        the tool was removed, so we fall back to checking several known JDK
        installation paths.

        Returns the raw JAR bytes.
        Raises JarCacheError if the download fails or unpack200 is not found.
        """
        try:
            with httpx.Client(verify=verify_tls, timeout=httpx.Timeout(60.0, connect=5.0)) as c:
                resp = c.get(pack_gz_url, cookies={"SID": sid})
        except httpx.HTTPError as exc:
            raise JarCacheError(f"pack200 JAR download failed: {exc}") from exc

        if resp.status_code != 200:
            raise JarCacheError(
                f"pack200 JAR download returned HTTP {resp.status_code}: {resp.text[:200]}"
            )

        pack_gz_bytes = resp.content
        logger.info("Downloaded pack200 JAR: %d bytes", len(pack_gz_bytes))

        # Find unpack200 binary.
        unpack200 = shutil.which("unpack200") or self._find_unpack200()
        if not unpack200:
            raise JarCacheError(
                "pack200 JAR served but 'unpack200' not found. "
                "Install a Java 8-13 JDK (e.g. openjdk-11-jdk) to enable pack200 decoding."
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            pack_gz_path = Path(tmpdir) / "ikvm.jar.pack.gz"
            jar_path = Path(tmpdir) / "ikvm.jar"
            pack_gz_path.write_bytes(pack_gz_bytes)

            try:
                result = subprocess.run(
                    [unpack200, str(pack_gz_path), str(jar_path)],
                    capture_output=True,
                    timeout=60,
                )
            except subprocess.TimeoutExpired as exc:
                raise JarCacheError("unpack200 timed out") from exc
            except OSError as exc:
                raise JarCacheError(f"unpack200 execution failed: {exc}") from exc

            if result.returncode != 0:
                stderr = result.stderr.decode(errors="replace")
                raise JarCacheError(f"unpack200 failed (exit {result.returncode}): {stderr[:500]}")

            jar_bytes = jar_path.read_bytes()

        logger.info("pack200 decoded to JAR: %d bytes", len(jar_bytes))
        return jar_bytes

    @staticmethod
    def _find_unpack200() -> str | None:
        """Search known JDK installation directories for unpack200."""
        candidates = [
            # Java 11 JDK on Ubuntu/Debian arm64
            "/usr/lib/jvm/java-11-openjdk-arm64/bin/unpack200",
            # Java 11 JDK on Ubuntu/Debian amd64
            "/usr/lib/jvm/java-11-openjdk-amd64/bin/unpack200",
            # Java 8 JDK on Ubuntu/Debian
            "/usr/lib/jvm/java-8-openjdk-arm64/bin/unpack200",
            "/usr/lib/jvm/java-8-openjdk-amd64/bin/unpack200",
            # macOS via Homebrew or SDKMAN
            "/usr/local/opt/openjdk@11/bin/unpack200",
            "/opt/homebrew/opt/openjdk@11/bin/unpack200",
        ]
        for path in candidates:
            if os.path.isfile(path) and os.access(path, os.X_OK):
                return path
        return None
