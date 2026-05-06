"""Parse Supermicro iKVM JNLP XML into a structured JnlpSpec.

JNLP (Java Network Launch Protocol) is the legacy vehicle for Java Web Start
applications. Supermicro serves one at /cgi/url_redirect.cgi?url_name=man_ikvm
that describes the iKVM viewer:
  - codebase URL (base for resolving relative jar href)
  - one <jar href=...> we download once and cache
  - main class to invoke
  - ~22 positional arguments passed to that main class, including a rotated
    ephemeral credential for the RFB handshake.

We tolerate argument count variation across firmware versions; we only
validate that the arguments array is non-empty and the structural fields
are present.
"""

from __future__ import annotations

from dataclasses import dataclass
from xml.etree import ElementTree as ET


class JnlpParseError(Exception):
    """Raised when the JNLP XML cannot be parsed into a JnlpSpec."""


@dataclass(frozen=True)
class JnlpSpec:
    codebase: str
    jar_href: str
    main_class: str
    arguments: tuple[str, ...]

    def jar_url(self) -> str:
        """Absolute URL to the iKVM JAR on the BMC."""
        base = self.codebase.rstrip("/")
        return f"{base}/{self.jar_href}"


def parse_jnlp(xml_bytes: bytes) -> JnlpSpec:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise JnlpParseError(f"malformed JNLP XML: {exc}") from exc

    codebase = root.attrib.get("codebase")
    if not codebase:
        raise JnlpParseError("JNLP root missing codebase attribute")

    jar_elem = root.find(".//resources/jar")
    if jar_elem is None or not jar_elem.attrib.get("href"):
        raise JnlpParseError("JNLP missing <resources><jar href=...>")
    jar_href = jar_elem.attrib["href"]

    app_elem = root.find("application-desc")
    if app_elem is None:
        raise JnlpParseError("JNLP missing <application-desc>")
    main_class = app_elem.attrib.get("main-class")
    if not main_class:
        raise JnlpParseError("<application-desc> missing main-class attribute")

    args: list[str] = [a.text or "" for a in app_elem.findall("argument")]
    if not args:
        raise JnlpParseError("<application-desc> has no <argument> elements")

    return JnlpSpec(
        codebase=codebase,
        jar_href=jar_href,
        main_class=main_class,
        arguments=tuple(args),
    )
