"""Tests for inventory module."""

from redfish_mcp.inventory import extract_odata_ids, looks_nvme_drive


class TestExtractOdataIds:
    def test_empty_input(self):
        assert extract_odata_ids({}) == []

    def test_single_member(self):
        data = {"Members": [{"@odata.id": "/a/b/c"}]}
        assert extract_odata_ids(data) == ["/a/b/c"]

    def test_nested_members(self):
        data = {"Members": [{"@odata.id": "/a"}, {"@odata.id": "/b"}]}
        assert extract_odata_ids(data) == ["/a", "/b"]

    def test_deduplication(self):
        data = {"Members": [{"@odata.id": "/a"}, {"@odata.id": "/a"}]}
        assert extract_odata_ids(data) == ["/a"]

    def test_nested_objects(self):
        data = {"Inner": {"@odata.id": "/nested"}}
        assert "/nested" in extract_odata_ids(data)

    def test_list_of_dicts(self):
        data = [{"@odata.id": "/x"}, {"@odata.id": "/y"}]
        ids = extract_odata_ids(data)
        assert "/x" in ids
        assert "/y" in ids


class TestLooksNvmeDrive:
    def test_nvme_protocol(self):
        assert looks_nvme_drive({"Protocol": "NVMe"}) is True

    def test_nvme_model(self):
        assert looks_nvme_drive({"Model": "Samsung NVMe 990 Pro"}) is True

    def test_nvme_name(self):
        assert looks_nvme_drive({"Name": "NVMe Disk 0"}) is True

    def test_non_nvme(self):
        assert looks_nvme_drive({"Protocol": "SATA", "Model": "WD Red", "Name": "Disk 0"}) is False

    def test_empty_drive(self):
        assert looks_nvme_drive({}) is False
