"""Tests for the seed script content sanitizer."""


class TestSanitize:
    """Test secret redaction in seed content."""

    def _sanitize(self, content: str) -> str:
        import sys

        sys.path.insert(0, "/workspaces/together/agent-memory")
        from scripts.seed_workspace import _sanitize

        return _sanitize(content)

    def test_clean_text_unchanged(self):
        text = "GPU node b65c909e-41 had NVLink CRC errors."
        assert "NVLink CRC errors" in self._sanitize(text)

    def test_anthropic_key_redacted(self):
        text = "key: sk-ant-api03-abc123def456ghi789jkl012mno345pqr678"
        result = self._sanitize(text)
        assert "sk-ant-api03" not in result
        assert "REDACTED" in result

    def test_github_token_redacted(self):
        text = "GITHUB_TOKEN=ghp_abcdefghijklmnopqrstuvwxyz0123456789"
        result = self._sanitize(text)
        assert "ghp_" not in result
        assert "REDACTED" in result

    def test_together_key_redacted(self):
        text = "TOGETHER_API_KEY=tgp_v1_abc123def456ghi789"
        result = self._sanitize(text)
        assert "tgp_v1_" not in result
        assert "REDACTED" in result

    def test_bearer_token_redacted(self):
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abc.def"
        result = self._sanitize(text)
        assert "Bearer eyJ" not in result

    def test_password_references_preserved(self):
        """Documentation about passwords should NOT be redacted."""
        text = "The IPMI password must be under 20 characters."
        assert "password" in self._sanitize(text)

    def test_multiline(self):
        text = "line 1\nkey=sk-ant-api03-longkeyvalue123456789\nline 3"
        result = self._sanitize(text)
        assert "line 1" in result
        assert "line 3" in result
        assert "sk-ant-api03" not in result
