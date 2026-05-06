"""Tests for JNLP XML parsing."""

from __future__ import annotations

from pathlib import Path

import pytest

from redfish_mcp.kvm.backends._jnlp import JnlpParseError, JnlpSpec, parse_jnlp

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class TestParseJnlp:
    def test_parses_supermicro_x13_fixture(self):
        spec = parse_jnlp(_load("jnlp_supermicro_x13.xml"))
        assert isinstance(spec, JnlpSpec)
        assert spec.codebase == "https://10.0.0.1:443"
        assert spec.jar_href == "iKVM__V1.69.42.0x0.jar"
        assert spec.main_class == "tw.com.aten.ikvm.KVMMain"
        assert len(spec.arguments) >= 22
        assert spec.arguments[0] == "10.0.0.1"
        assert spec.arguments[9] == "EphemeralUser"
        assert spec.arguments[10] == "EphemeralPass"

    def test_jar_absolute_url_computed(self):
        spec = parse_jnlp(_load("jnlp_supermicro_x13.xml"))
        assert spec.jar_url() == "https://10.0.0.1:443/iKVM__V1.69.42.0x0.jar"

    def test_missing_jar_raises(self):
        bad_xml = b"""<?xml version="1.0"?>
<jnlp codebase="https://x/"><resources/>
<application-desc main-class="x"><argument>a</argument></application-desc></jnlp>"""
        with pytest.raises(JnlpParseError) as exc_info:
            parse_jnlp(bad_xml)
        assert "jar" in str(exc_info.value).lower()

    def test_missing_main_class_raises(self):
        bad_xml = b"""<?xml version="1.0"?>
<jnlp codebase="https://x/">
<resources><jar href="x.jar"/></resources>
<application-desc><argument>a</argument></application-desc></jnlp>"""
        with pytest.raises(JnlpParseError) as exc_info:
            parse_jnlp(bad_xml)
        assert "main-class" in str(exc_info.value).lower()

    def test_malformed_xml_raises(self):
        with pytest.raises(JnlpParseError):
            parse_jnlp(b"<<not xml>>")

    def test_no_arguments_raises(self):
        bad_xml = b"""<?xml version="1.0"?>
<jnlp codebase="https://x/">
<resources><jar href="x.jar"/></resources>
<application-desc main-class="x"></application-desc></jnlp>"""
        with pytest.raises(JnlpParseError):
            parse_jnlp(bad_xml)
