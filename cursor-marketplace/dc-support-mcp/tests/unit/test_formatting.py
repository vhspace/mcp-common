"""Tests for dc_support_mcp.formatting module."""

from unittest.mock import patch

from dc_support_mcp.formatting import (
    format_gpu_triage_summary,
    markdown_to_wiki,
    netbox_ensure_triage_status,
    sanitize_for_vendor,
)


class TestMarkdownToWiki:
    """Tests for the markdown -> Atlassian wiki markup converter."""

    # ── Headings ────────────────────────────────────────────────────

    def test_h1(self):
        assert markdown_to_wiki("# Title") == "h1. Title"

    def test_h2(self):
        assert markdown_to_wiki("## Section") == "h2. Section"

    def test_h3(self):
        assert markdown_to_wiki("### Subsection") == "h3. Subsection"

    def test_h6(self):
        assert markdown_to_wiki("###### Deep") == "h6. Deep"

    def test_heading_mid_text(self):
        result = markdown_to_wiki("intro\n## Heading\nbody")
        assert "h2. Heading" in result
        assert "intro" in result
        assert "body" in result

    # ── Bold ────────────────────────────────────────────────────────

    def test_bold(self):
        assert markdown_to_wiki("**bold text**") == "*bold text*"

    def test_bold_in_sentence(self):
        result = markdown_to_wiki("This is **important** stuff")
        assert result == "This is *important* stuff"

    def test_multiple_bold(self):
        result = markdown_to_wiki("**a** and **b**")
        assert result == "*a* and *b*"

    # ── Inline code ─────────────────────────────────────────────────

    def test_inline_code(self):
        assert markdown_to_wiki("`nvidia-smi`") == "{{nvidia-smi}}"

    def test_inline_code_in_sentence(self):
        result = markdown_to_wiki("Run `nvidia-smi -L` to check")
        assert result == "Run {{nvidia-smi -L}} to check"

    # ── Fenced code blocks ──────────────────────────────────────────

    def test_code_fence_no_lang(self):
        md = "```\nsome code\n```"
        assert markdown_to_wiki(md) == "{code}\nsome code\n{code}"

    def test_code_fence_with_lang(self):
        md = "```python\nprint('hi')\n```"
        assert markdown_to_wiki(md) == "{code:python}\nprint('hi')\n{code}"

    def test_code_fence_multiline(self):
        md = "```bash\nline1\nline2\nline3\n```"
        result = markdown_to_wiki(md)
        assert result.startswith("{code:bash}")
        assert "line1\nline2\nline3" in result
        assert result.endswith("{code}")

    def test_code_fence_contents_not_transformed(self):
        md = "```\n## not a heading\n**not bold**\n```"
        result = markdown_to_wiki(md)
        assert "## not a heading" in result
        assert "**not bold**" in result

    # ── Links ───────────────────────────────────────────────────────

    def test_link(self):
        md = "[Google](https://google.com)"
        assert markdown_to_wiki(md) == "[Google|https://google.com]"

    def test_link_in_text(self):
        md = "See [the docs](https://example.com) for details"
        result = markdown_to_wiki(md)
        assert "[the docs|https://example.com]" in result

    # ── Lists ───────────────────────────────────────────────────────

    def test_dash_bullets(self):
        md = "- item one\n- item two"
        assert markdown_to_wiki(md) == "* item one\n* item two"

    def test_asterisk_bullets_passthrough(self):
        md = "* already wiki\n* second"
        result = markdown_to_wiki(md)
        assert "* already wiki" in result

    def test_numbered_list(self):
        md = "1. first\n2. second\n3. third"
        result = markdown_to_wiki(md)
        assert "# first" in result
        assert "# second" in result
        assert "# third" in result

    # ── Horizontal rule ─────────────────────────────────────────────

    def test_hr(self):
        assert markdown_to_wiki("---") == "----"

    def test_hr_long(self):
        assert markdown_to_wiki("-----") == "----"

    # ── Blockquotes ─────────────────────────────────────────────────

    def test_blockquote_single(self):
        result = markdown_to_wiki("> quoted text")
        assert "{quote}" in result
        assert "quoted text" in result

    def test_blockquote_multiline(self):
        md = "> line one\n> line two"
        result = markdown_to_wiki(md)
        assert result.count("{quote}") == 2
        assert "line one" in result
        assert "line two" in result

    # ── Passthrough / idempotency ───────────────────────────────────

    def test_plain_text_passthrough(self):
        plain = "This is just plain text with no formatting."
        assert markdown_to_wiki(plain) == plain

    def test_empty_string(self):
        assert markdown_to_wiki("") == ""

    def test_wiki_markup_passthrough(self):
        wiki = "h2. Already Wiki\n*bold*\n{{code}}"
        result = markdown_to_wiki(wiki)
        assert "h2. Already Wiki" in result
        assert "*bold*" in result
        assert "{{code}}" in result

    # ── Combined ────────────────────────────────────────────────────

    def test_combined_formatting(self):
        md = (
            "## GPU Status\n\n"
            "**Node:** `us-south-3a-r01-05`\n"
            "- 3/4 GPUs visible\n"
            "- Bus [0018:01:00.0](https://example.com) failed\n\n"
            "---\n\n"
            "```\nNVRM: GPU has fallen off the bus\n```"
        )
        result = markdown_to_wiki(md)
        assert "h2. GPU Status" in result
        assert "*Node:*" in result
        assert "{{us-south-3a-r01-05}}" in result
        assert "* 3/4 GPUs visible" in result
        assert "[0018:01:00.0|https://example.com]" in result
        assert "----" in result
        assert "{code}" in result
        assert "NVRM: GPU has fallen off the bus" in result


class TestFormatGpuTriageSummary:
    """Tests for the structured GPU triage summary formatter."""

    def test_basic_summary(self):
        result = format_gpu_triage_summary(
            node="us-south-3a-r01-05",
            gpus_visible=3,
            gpus_expected=4,
            failed_bus_ids=["0018:01:00.0"],
            error_type="GPU-CONTNMT + AER Completion Timeout",
        )
        assert "3/4 GPUs visible" in result
        assert "1 missing" in result
        assert "0018:01:00.0" in result
        assert "GPU-CONTNMT" in result
        assert "Reboot attempted:** No" in result

    def test_with_reboot(self):
        result = format_gpu_triage_summary(
            node="r01-05",
            gpus_visible=3,
            gpus_expected=4,
            failed_bus_ids=["0018:01:00.0"],
            error_type="PCIe AER",
            reboot_attempted=True,
        )
        assert "Yes -- GPU did not recover" in result

    def test_with_dmesg(self):
        result = format_gpu_triage_summary(
            node="r01-05",
            gpus_visible=3,
            gpus_expected=4,
            failed_bus_ids=["0018:01:00.0"],
            error_type="PCIe AER",
            dmesg_excerpt="NVRM: GPU 0018:01:00.0 has fallen off the bus",
        )
        assert "dmesg excerpt" in result
        assert "> NVRM" in result

    def test_with_prior_ticket(self):
        result = format_gpu_triage_summary(
            node="r01-05",
            gpus_visible=3,
            gpus_expected=4,
            failed_bus_ids=["0018:01:00.0"],
            error_type="PCIe AER",
            prior_ticket="SRE-1072 (canceled Feb 24)",
        )
        assert "SRE-1072" in result

    def test_multiple_bus_ids(self):
        result = format_gpu_triage_summary(
            node="r12-11",
            gpus_visible=2,
            gpus_expected=4,
            failed_bus_ids=["0009:01:00.0", "0018:01:00.0"],
            error_type="GPU-CONTNMT x2",
        )
        assert "2 missing" in result
        assert "0009:01:00.0, 0018:01:00.0" in result

    def test_converts_cleanly_to_wiki(self):
        md = format_gpu_triage_summary(
            node="r01-05",
            gpus_visible=3,
            gpus_expected=4,
            failed_bus_ids=["0018:01:00.0"],
            error_type="PCIe AER",
            dmesg_excerpt="NVRM: fallen off the bus",
            reboot_attempted=True,
            prior_ticket="SRE-1072",
        )
        wiki = markdown_to_wiki(md)
        assert "*GPU Status:*" in wiki
        assert "{quote}" in wiki
        assert "**" not in wiki


class TestSanitizeForVendor:
    """Tests for the vendor-facing content sanitizer."""

    # ── Linear ticket IDs ───────────────────────────────────────────

    def test_strips_sre_ticket(self):
        assert "SRE-1572" not in sanitize_for_vendor("See SRE-1572 for details")
        assert "[internal ticket]" in sanitize_for_vendor("See SRE-1572 for details")

    def test_strips_eng_ticket(self):
        assert "ENG-49685" not in sanitize_for_vendor("Ref ENG-49685")

    def test_strips_ns_ticket(self):
        assert "NS-1206" not in sanitize_for_vendor("Node scanner NS-1206")

    def test_strips_becca_ticket(self):
        assert "BECCA-229" not in sanitize_for_vendor("RTB BECCA-229")

    def test_strips_multiple_tickets(self):
        text = "Prior: SRE-1072 / ENG-12345 (both canceled)"
        result = sanitize_for_vendor(text)
        assert "SRE-1072" not in result
        assert "ENG-12345" not in result

    # ── Internal hostnames ──────────────────────────────────────────

    def test_strips_internal_hostname(self):
        result = sanitize_for_vendor("Node us-south-3a-r01-05 is down")
        assert "us-south-3a-r01-05" not in result
        assert "[internal hostname]" in result

    def test_strips_fqdn(self):
        result = sanitize_for_vendor("SSH to us-south-3a-r03-14.cloud.together.ai")
        assert "us-south-3a-r03-14.cloud.together.ai" not in result

    def test_preserves_provider_node_name(self):
        result = sanitize_for_vendor("Node tn1-c1-01-node05 has GPU issues")
        assert "tn1-c1-01-node05" in result

    # ── Internal URLs ───────────────────────────────────────────────

    def test_strips_linear_url(self):
        url = "https://linear.app/together-ai/issue/SRE-1572/foo"
        assert "linear.app" not in sanitize_for_vendor(f"See {url}")

    def test_strips_slack_url(self):
        url = "https://togetherai.slack.com/archives/C09CNRUHEUA/p123"
        assert "slack.com" not in sanitize_for_vendor(f"Thread: {url}")

    def test_strips_pagerduty_url(self):
        url = "https://togetherai.pagerduty.com/incidents/Q123"
        assert "pagerduty.com" not in sanitize_for_vendor(url)

    def test_strips_grafana_url(self):
        url = "https://monitoring-admin.internal.together.ai/grafana/explore"
        assert "together.ai" not in sanitize_for_vendor(url)

    # ── Slack channels ──────────────────────────────────────────────

    def test_strips_slack_channel(self):
        result = sanitize_for_vendor("Posted in #alerts-us-south-3a")
        assert "#alerts-us-south-3a" not in result

    # ── @mentions ───────────────────────────────────────────────────

    def test_strips_at_mention(self):
        result = sanitize_for_vendor("CC @danil@together.ai")
        assert "danil@together.ai" not in result

    # ── Passthrough ─────────────────────────────────────────────────

    def test_empty_string(self):
        assert sanitize_for_vendor("") == ""

    def test_plain_text_passthrough(self):
        text = "GPU 0018:01:00.0 has fallen off the bus. Needs repair."
        assert sanitize_for_vendor(text) == text

    def test_preserves_pcie_addresses(self):
        text = "Failed PCIe: 0008:01:00.0, 0018:01:00.0"
        assert sanitize_for_vendor(text) == text

    def test_preserves_hypertec_ticket(self):
        text = "Hypertec ticket HTCSR-3391"
        assert sanitize_for_vendor(text) == text

    # ── Combined ────────────────────────────────────────────────────

    def test_realistic_mixed_content(self):
        text = (
            "Node us-south-3a-r01-05 (tn1-c1-01-node05) has 3/4 GPUs.\n"
            "Prior ticket SRE-1072 was canceled.\n"
            "See https://linear.app/together-ai/issue/SRE-1072 for history.\n"
            "CC @danil@together.ai in #svc-infrastructure"
        )
        result = sanitize_for_vendor(text)
        assert "tn1-c1-01-node05" in result
        assert "3/4 GPUs" in result
        assert "us-south-3a-r01-05" not in result
        assert "SRE-1072" not in result
        assert "linear.app" not in result
        assert "danil@together.ai" not in result
        assert "#svc-infrastructure" not in result


class TestNetboxEnsureTriageStatus:
    """Tests for the NetBox triage-status fallback."""

    @patch("dc_support_mcp.formatting.requests.patch")
    @patch.dict("os.environ", {"NETBOX_TOKEN": "test-token"})
    def test_success(self, mock_patch):
        mock_patch.return_value.status_code = 200
        result = netbox_ensure_triage_status(2475, "SRE-1572")
        assert result is True
        mock_patch.assert_called_once()
        call_kwargs = mock_patch.call_args
        assert call_kwargs.kwargs["json"]["status"] == "triage"
        assert call_kwargs.kwargs["json"]["custom_fields"]["Linear"] == "SRE-1572"

    @patch("dc_support_mcp.formatting.requests.patch")
    @patch.dict("os.environ", {"NETBOX_TOKEN": "test-token"})
    def test_without_linear(self, mock_patch):
        mock_patch.return_value.status_code = 200
        result = netbox_ensure_triage_status(2475)
        assert result is True
        assert "custom_fields" not in mock_patch.call_args.kwargs["json"]

    @patch("dc_support_mcp.formatting.requests.patch")
    @patch.dict("os.environ", {"NETBOX_TOKEN": "test-token"})
    def test_failure(self, mock_patch):
        mock_patch.return_value.status_code = 403
        mock_patch.return_value.text = "forbidden"
        result = netbox_ensure_triage_status(9999, "SRE-999")
        assert result is False

    @patch.dict("os.environ", {}, clear=True)
    def test_no_token(self):
        result = netbox_ensure_triage_status(2475)
        assert result is False

    @patch("dc_support_mcp.formatting.requests.patch")
    @patch.dict("os.environ", {"NETBOX_TOKEN": "test-token"})
    def test_network_error(self, mock_patch):
        import requests as req

        mock_patch.side_effect = req.ConnectionError("timeout")
        result = netbox_ensure_triage_status(2475)
        assert result is False
