from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class HealthStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    HEALTH_STATUS_UNKNOWN: _ClassVar[HealthStatus]
    HEALTH_STATUS_HEALTHY: _ClassVar[HealthStatus]
    HEALTH_STATUS_WARNING: _ClassVar[HealthStatus]
    HEALTH_STATUS_CRITICAL: _ClassVar[HealthStatus]
HEALTH_STATUS_UNKNOWN: HealthStatus
HEALTH_STATUS_HEALTHY: HealthStatus
HEALTH_STATUS_WARNING: HealthStatus
HEALTH_STATUS_CRITICAL: HealthStatus

class FabricSummary(_message.Message):
    __slots__ = ("total_nodes", "switches", "channel_adapters", "total_links", "adaptive_routing_switches")
    TOTAL_NODES_FIELD_NUMBER: _ClassVar[int]
    SWITCHES_FIELD_NUMBER: _ClassVar[int]
    CHANNEL_ADAPTERS_FIELD_NUMBER: _ClassVar[int]
    TOTAL_LINKS_FIELD_NUMBER: _ClassVar[int]
    ADAPTIVE_ROUTING_SWITCHES_FIELD_NUMBER: _ClassVar[int]
    total_nodes: int
    switches: int
    channel_adapters: int
    total_links: int
    adaptive_routing_switches: int
    def __init__(self, total_nodes: _Optional[int] = ..., switches: _Optional[int] = ..., channel_adapters: _Optional[int] = ..., total_links: _Optional[int] = ..., adaptive_routing_switches: _Optional[int] = ...) -> None: ...

class ProblematicPort(_message.Message):
    __slots__ = ("lid", "port_name", "guid", "link_down_count", "symbol_error_count", "total_errors", "issues")
    LID_FIELD_NUMBER: _ClassVar[int]
    PORT_NAME_FIELD_NUMBER: _ClassVar[int]
    GUID_FIELD_NUMBER: _ClassVar[int]
    LINK_DOWN_COUNT_FIELD_NUMBER: _ClassVar[int]
    SYMBOL_ERROR_COUNT_FIELD_NUMBER: _ClassVar[int]
    TOTAL_ERRORS_FIELD_NUMBER: _ClassVar[int]
    ISSUES_FIELD_NUMBER: _ClassVar[int]
    lid: str
    port_name: str
    guid: str
    link_down_count: int
    symbol_error_count: int
    total_errors: int
    issues: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, lid: _Optional[str] = ..., port_name: _Optional[str] = ..., guid: _Optional[str] = ..., link_down_count: _Optional[int] = ..., symbol_error_count: _Optional[int] = ..., total_errors: _Optional[int] = ..., issues: _Optional[_Iterable[str]] = ...) -> None: ...

class ErrorCount(_message.Message):
    __slots__ = ("error_type", "count")
    ERROR_TYPE_FIELD_NUMBER: _ClassVar[int]
    COUNT_FIELD_NUMBER: _ClassVar[int]
    error_type: str
    count: int
    def __init__(self, error_type: _Optional[str] = ..., count: _Optional[int] = ...) -> None: ...

class FabricHealthResponse(_message.Message):
    __slots__ = ("status", "score", "timestamp", "collection_id", "fabric_summary", "total_errors", "total_warnings", "errors_by_type", "problematic_ports", "issue_summary", "az_id")
    STATUS_FIELD_NUMBER: _ClassVar[int]
    SCORE_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    COLLECTION_ID_FIELD_NUMBER: _ClassVar[int]
    FABRIC_SUMMARY_FIELD_NUMBER: _ClassVar[int]
    TOTAL_ERRORS_FIELD_NUMBER: _ClassVar[int]
    TOTAL_WARNINGS_FIELD_NUMBER: _ClassVar[int]
    ERRORS_BY_TYPE_FIELD_NUMBER: _ClassVar[int]
    PROBLEMATIC_PORTS_FIELD_NUMBER: _ClassVar[int]
    ISSUE_SUMMARY_FIELD_NUMBER: _ClassVar[int]
    AZ_ID_FIELD_NUMBER: _ClassVar[int]
    status: HealthStatus
    score: int
    timestamp: str
    collection_id: str
    fabric_summary: FabricSummary
    total_errors: int
    total_warnings: int
    errors_by_type: _containers.RepeatedCompositeFieldContainer[ErrorCount]
    problematic_ports: _containers.RepeatedCompositeFieldContainer[ProblematicPort]
    issue_summary: _containers.RepeatedScalarFieldContainer[str]
    az_id: str
    def __init__(self, status: _Optional[_Union[HealthStatus, str]] = ..., score: _Optional[int] = ..., timestamp: _Optional[str] = ..., collection_id: _Optional[str] = ..., fabric_summary: _Optional[_Union[FabricSummary, _Mapping]] = ..., total_errors: _Optional[int] = ..., total_warnings: _Optional[int] = ..., errors_by_type: _Optional[_Iterable[_Union[ErrorCount, _Mapping]]] = ..., problematic_ports: _Optional[_Iterable[_Union[ProblematicPort, _Mapping]]] = ..., issue_summary: _Optional[_Iterable[str]] = ..., az_id: _Optional[str] = ...) -> None: ...

class GetFabricHealthRequest(_message.Message):
    __slots__ = ("collection_id", "az_id")
    COLLECTION_ID_FIELD_NUMBER: _ClassVar[int]
    AZ_ID_FIELD_NUMBER: _ClassVar[int]
    collection_id: str
    az_id: str
    def __init__(self, collection_id: _Optional[str] = ..., az_id: _Optional[str] = ...) -> None: ...

class CollectionSummary(_message.Message):
    __slots__ = ("id", "collected_at", "total_nodes", "switches", "total_links", "error_count", "warning_count", "health_status", "health_score", "az_id")
    ID_FIELD_NUMBER: _ClassVar[int]
    COLLECTED_AT_FIELD_NUMBER: _ClassVar[int]
    TOTAL_NODES_FIELD_NUMBER: _ClassVar[int]
    SWITCHES_FIELD_NUMBER: _ClassVar[int]
    TOTAL_LINKS_FIELD_NUMBER: _ClassVar[int]
    ERROR_COUNT_FIELD_NUMBER: _ClassVar[int]
    WARNING_COUNT_FIELD_NUMBER: _ClassVar[int]
    HEALTH_STATUS_FIELD_NUMBER: _ClassVar[int]
    HEALTH_SCORE_FIELD_NUMBER: _ClassVar[int]
    AZ_ID_FIELD_NUMBER: _ClassVar[int]
    id: str
    collected_at: str
    total_nodes: int
    switches: int
    total_links: int
    error_count: int
    warning_count: int
    health_status: HealthStatus
    health_score: int
    az_id: str
    def __init__(self, id: _Optional[str] = ..., collected_at: _Optional[str] = ..., total_nodes: _Optional[int] = ..., switches: _Optional[int] = ..., total_links: _Optional[int] = ..., error_count: _Optional[int] = ..., warning_count: _Optional[int] = ..., health_status: _Optional[_Union[HealthStatus, str]] = ..., health_score: _Optional[int] = ..., az_id: _Optional[str] = ...) -> None: ...

class ListCollectionsRequest(_message.Message):
    __slots__ = ("limit", "offset", "az_id")
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    OFFSET_FIELD_NUMBER: _ClassVar[int]
    AZ_ID_FIELD_NUMBER: _ClassVar[int]
    limit: int
    offset: int
    az_id: str
    def __init__(self, limit: _Optional[int] = ..., offset: _Optional[int] = ..., az_id: _Optional[str] = ...) -> None: ...

class ListCollectionsResponse(_message.Message):
    __slots__ = ("collections", "total_count")
    COLLECTIONS_FIELD_NUMBER: _ClassVar[int]
    TOTAL_COUNT_FIELD_NUMBER: _ClassVar[int]
    collections: _containers.RepeatedCompositeFieldContainer[CollectionSummary]
    total_count: int
    def __init__(self, collections: _Optional[_Iterable[_Union[CollectionSummary, _Mapping]]] = ..., total_count: _Optional[int] = ...) -> None: ...

class GetCollectionRequest(_message.Message):
    __slots__ = ("id",)
    ID_FIELD_NUMBER: _ClassVar[int]
    id: str
    def __init__(self, id: _Optional[str] = ...) -> None: ...

class IbdiagnetError(_message.Message):
    __slots__ = ("device_id", "port_name", "error_type", "message", "value", "threshold")
    DEVICE_ID_FIELD_NUMBER: _ClassVar[int]
    PORT_NAME_FIELD_NUMBER: _ClassVar[int]
    ERROR_TYPE_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    THRESHOLD_FIELD_NUMBER: _ClassVar[int]
    device_id: str
    port_name: str
    error_type: str
    message: str
    value: int
    threshold: int
    def __init__(self, device_id: _Optional[str] = ..., port_name: _Optional[str] = ..., error_type: _Optional[str] = ..., message: _Optional[str] = ..., value: _Optional[int] = ..., threshold: _Optional[int] = ...) -> None: ...

class StageSummary(_message.Message):
    __slots__ = ("stage_name", "warnings", "errors")
    STAGE_NAME_FIELD_NUMBER: _ClassVar[int]
    WARNINGS_FIELD_NUMBER: _ClassVar[int]
    ERRORS_FIELD_NUMBER: _ClassVar[int]
    stage_name: str
    warnings: int
    errors: int
    def __init__(self, stage_name: _Optional[str] = ..., warnings: _Optional[int] = ..., errors: _Optional[int] = ...) -> None: ...

class GetCollectionResponse(_message.Message):
    __slots__ = ("id", "collected_at", "fabric_summary", "errors", "stage_summaries", "health")
    ID_FIELD_NUMBER: _ClassVar[int]
    COLLECTED_AT_FIELD_NUMBER: _ClassVar[int]
    FABRIC_SUMMARY_FIELD_NUMBER: _ClassVar[int]
    ERRORS_FIELD_NUMBER: _ClassVar[int]
    STAGE_SUMMARIES_FIELD_NUMBER: _ClassVar[int]
    HEALTH_FIELD_NUMBER: _ClassVar[int]
    id: str
    collected_at: str
    fabric_summary: FabricSummary
    errors: _containers.RepeatedCompositeFieldContainer[IbdiagnetError]
    stage_summaries: _containers.RepeatedCompositeFieldContainer[StageSummary]
    health: FabricHealthResponse
    def __init__(self, id: _Optional[str] = ..., collected_at: _Optional[str] = ..., fabric_summary: _Optional[_Union[FabricSummary, _Mapping]] = ..., errors: _Optional[_Iterable[_Union[IbdiagnetError, _Mapping]]] = ..., stage_summaries: _Optional[_Iterable[_Union[StageSummary, _Mapping]]] = ..., health: _Optional[_Union[FabricHealthResponse, _Mapping]] = ...) -> None: ...

class TriggerIbdiagnetRequest(_message.Message):
    __slots__ = ("az_id",)
    AZ_ID_FIELD_NUMBER: _ClassVar[int]
    az_id: str
    def __init__(self, az_id: _Optional[str] = ...) -> None: ...

class TriggerIbdiagnetResponse(_message.Message):
    __slots__ = ("success", "message", "collection_id")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    COLLECTION_ID_FIELD_NUMBER: _ClassVar[int]
    success: bool
    message: str
    collection_id: str
    def __init__(self, success: bool = ..., message: _Optional[str] = ..., collection_id: _Optional[str] = ...) -> None: ...

class TriggerTopologyRequest(_message.Message):
    __slots__ = ("az_id",)
    AZ_ID_FIELD_NUMBER: _ClassVar[int]
    az_id: str
    def __init__(self, az_id: _Optional[str] = ...) -> None: ...

class TriggerTopologyResponse(_message.Message):
    __slots__ = ("success", "message", "topology_changed")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    TOPOLOGY_CHANGED_FIELD_NUMBER: _ClassVar[int]
    success: bool
    message: str
    topology_changed: bool
    def __init__(self, success: bool = ..., message: _Optional[str] = ..., topology_changed: bool = ...) -> None: ...

class PortCounterData(_message.Message):
    __slots__ = ("port", "lid", "guid", "device_id", "port_name", "node_desc", "node_type", "link_down_counter", "link_error_recovery_counter", "symbol_error_counter", "port_rcv_remote_physical_errors", "port_rcv_errors", "port_xmit_discard", "port_rcv_switch_relay_errors", "excessive_buffer_errors", "local_link_integrity_errors", "port_rcv_constraint_errors", "port_xmit_constraint_errors", "vl15_dropped", "port_xmit_data", "port_rcv_data", "port_xmit_pkts", "port_rcv_pkts", "port_xmit_wait", "port_xmit_data_extended", "port_rcv_data_extended", "port_xmit_pkts_extended", "port_rcv_pkts_extended", "fec_corrected_symbol_counter_total", "port_fec_correctable_block_counter", "port_fec_uncorrectable_block_counter", "sync_header_error_counter", "unknown_block_counter", "port_local_physical_errors", "port_malformed_packet_errors", "port_buffer_overrun_errors", "port_dlid_mapping_errors", "total_errors", "link_state", "phy_state", "link_width", "link_speed", "fec_mode", "retransmission", "raw_ber", "effective_ber", "symbol_ber", "remote_guid", "remote_port", "remote_node_desc", "remote_lid")
    PORT_FIELD_NUMBER: _ClassVar[int]
    LID_FIELD_NUMBER: _ClassVar[int]
    GUID_FIELD_NUMBER: _ClassVar[int]
    DEVICE_ID_FIELD_NUMBER: _ClassVar[int]
    PORT_NAME_FIELD_NUMBER: _ClassVar[int]
    NODE_DESC_FIELD_NUMBER: _ClassVar[int]
    NODE_TYPE_FIELD_NUMBER: _ClassVar[int]
    LINK_DOWN_COUNTER_FIELD_NUMBER: _ClassVar[int]
    LINK_ERROR_RECOVERY_COUNTER_FIELD_NUMBER: _ClassVar[int]
    SYMBOL_ERROR_COUNTER_FIELD_NUMBER: _ClassVar[int]
    PORT_RCV_REMOTE_PHYSICAL_ERRORS_FIELD_NUMBER: _ClassVar[int]
    PORT_RCV_ERRORS_FIELD_NUMBER: _ClassVar[int]
    PORT_XMIT_DISCARD_FIELD_NUMBER: _ClassVar[int]
    PORT_RCV_SWITCH_RELAY_ERRORS_FIELD_NUMBER: _ClassVar[int]
    EXCESSIVE_BUFFER_ERRORS_FIELD_NUMBER: _ClassVar[int]
    LOCAL_LINK_INTEGRITY_ERRORS_FIELD_NUMBER: _ClassVar[int]
    PORT_RCV_CONSTRAINT_ERRORS_FIELD_NUMBER: _ClassVar[int]
    PORT_XMIT_CONSTRAINT_ERRORS_FIELD_NUMBER: _ClassVar[int]
    VL15_DROPPED_FIELD_NUMBER: _ClassVar[int]
    PORT_XMIT_DATA_FIELD_NUMBER: _ClassVar[int]
    PORT_RCV_DATA_FIELD_NUMBER: _ClassVar[int]
    PORT_XMIT_PKTS_FIELD_NUMBER: _ClassVar[int]
    PORT_RCV_PKTS_FIELD_NUMBER: _ClassVar[int]
    PORT_XMIT_WAIT_FIELD_NUMBER: _ClassVar[int]
    PORT_XMIT_DATA_EXTENDED_FIELD_NUMBER: _ClassVar[int]
    PORT_RCV_DATA_EXTENDED_FIELD_NUMBER: _ClassVar[int]
    PORT_XMIT_PKTS_EXTENDED_FIELD_NUMBER: _ClassVar[int]
    PORT_RCV_PKTS_EXTENDED_FIELD_NUMBER: _ClassVar[int]
    FEC_CORRECTED_SYMBOL_COUNTER_TOTAL_FIELD_NUMBER: _ClassVar[int]
    PORT_FEC_CORRECTABLE_BLOCK_COUNTER_FIELD_NUMBER: _ClassVar[int]
    PORT_FEC_UNCORRECTABLE_BLOCK_COUNTER_FIELD_NUMBER: _ClassVar[int]
    SYNC_HEADER_ERROR_COUNTER_FIELD_NUMBER: _ClassVar[int]
    UNKNOWN_BLOCK_COUNTER_FIELD_NUMBER: _ClassVar[int]
    PORT_LOCAL_PHYSICAL_ERRORS_FIELD_NUMBER: _ClassVar[int]
    PORT_MALFORMED_PACKET_ERRORS_FIELD_NUMBER: _ClassVar[int]
    PORT_BUFFER_OVERRUN_ERRORS_FIELD_NUMBER: _ClassVar[int]
    PORT_DLID_MAPPING_ERRORS_FIELD_NUMBER: _ClassVar[int]
    TOTAL_ERRORS_FIELD_NUMBER: _ClassVar[int]
    LINK_STATE_FIELD_NUMBER: _ClassVar[int]
    PHY_STATE_FIELD_NUMBER: _ClassVar[int]
    LINK_WIDTH_FIELD_NUMBER: _ClassVar[int]
    LINK_SPEED_FIELD_NUMBER: _ClassVar[int]
    FEC_MODE_FIELD_NUMBER: _ClassVar[int]
    RETRANSMISSION_FIELD_NUMBER: _ClassVar[int]
    RAW_BER_FIELD_NUMBER: _ClassVar[int]
    EFFECTIVE_BER_FIELD_NUMBER: _ClassVar[int]
    SYMBOL_BER_FIELD_NUMBER: _ClassVar[int]
    REMOTE_GUID_FIELD_NUMBER: _ClassVar[int]
    REMOTE_PORT_FIELD_NUMBER: _ClassVar[int]
    REMOTE_NODE_DESC_FIELD_NUMBER: _ClassVar[int]
    REMOTE_LID_FIELD_NUMBER: _ClassVar[int]
    port: int
    lid: str
    guid: str
    device_id: int
    port_name: str
    node_desc: str
    node_type: str
    link_down_counter: int
    link_error_recovery_counter: int
    symbol_error_counter: int
    port_rcv_remote_physical_errors: int
    port_rcv_errors: int
    port_xmit_discard: int
    port_rcv_switch_relay_errors: int
    excessive_buffer_errors: int
    local_link_integrity_errors: int
    port_rcv_constraint_errors: int
    port_xmit_constraint_errors: int
    vl15_dropped: int
    port_xmit_data: int
    port_rcv_data: int
    port_xmit_pkts: int
    port_rcv_pkts: int
    port_xmit_wait: int
    port_xmit_data_extended: int
    port_rcv_data_extended: int
    port_xmit_pkts_extended: int
    port_rcv_pkts_extended: int
    fec_corrected_symbol_counter_total: int
    port_fec_correctable_block_counter: int
    port_fec_uncorrectable_block_counter: int
    sync_header_error_counter: int
    unknown_block_counter: int
    port_local_physical_errors: int
    port_malformed_packet_errors: int
    port_buffer_overrun_errors: int
    port_dlid_mapping_errors: int
    total_errors: int
    link_state: str
    phy_state: str
    link_width: str
    link_speed: str
    fec_mode: str
    retransmission: bool
    raw_ber: float
    effective_ber: float
    symbol_ber: float
    remote_guid: str
    remote_port: str
    remote_node_desc: str
    remote_lid: str
    def __init__(self, port: _Optional[int] = ..., lid: _Optional[str] = ..., guid: _Optional[str] = ..., device_id: _Optional[int] = ..., port_name: _Optional[str] = ..., node_desc: _Optional[str] = ..., node_type: _Optional[str] = ..., link_down_counter: _Optional[int] = ..., link_error_recovery_counter: _Optional[int] = ..., symbol_error_counter: _Optional[int] = ..., port_rcv_remote_physical_errors: _Optional[int] = ..., port_rcv_errors: _Optional[int] = ..., port_xmit_discard: _Optional[int] = ..., port_rcv_switch_relay_errors: _Optional[int] = ..., excessive_buffer_errors: _Optional[int] = ..., local_link_integrity_errors: _Optional[int] = ..., port_rcv_constraint_errors: _Optional[int] = ..., port_xmit_constraint_errors: _Optional[int] = ..., vl15_dropped: _Optional[int] = ..., port_xmit_data: _Optional[int] = ..., port_rcv_data: _Optional[int] = ..., port_xmit_pkts: _Optional[int] = ..., port_rcv_pkts: _Optional[int] = ..., port_xmit_wait: _Optional[int] = ..., port_xmit_data_extended: _Optional[int] = ..., port_rcv_data_extended: _Optional[int] = ..., port_xmit_pkts_extended: _Optional[int] = ..., port_rcv_pkts_extended: _Optional[int] = ..., fec_corrected_symbol_counter_total: _Optional[int] = ..., port_fec_correctable_block_counter: _Optional[int] = ..., port_fec_uncorrectable_block_counter: _Optional[int] = ..., sync_header_error_counter: _Optional[int] = ..., unknown_block_counter: _Optional[int] = ..., port_local_physical_errors: _Optional[int] = ..., port_malformed_packet_errors: _Optional[int] = ..., port_buffer_overrun_errors: _Optional[int] = ..., port_dlid_mapping_errors: _Optional[int] = ..., total_errors: _Optional[int] = ..., link_state: _Optional[str] = ..., phy_state: _Optional[str] = ..., link_width: _Optional[str] = ..., link_speed: _Optional[str] = ..., fec_mode: _Optional[str] = ..., retransmission: bool = ..., raw_ber: _Optional[float] = ..., effective_ber: _Optional[float] = ..., symbol_ber: _Optional[float] = ..., remote_guid: _Optional[str] = ..., remote_port: _Optional[str] = ..., remote_node_desc: _Optional[str] = ..., remote_lid: _Optional[str] = ...) -> None: ...

class SwitchSummary(_message.Message):
    __slots__ = ("node_guid", "node_desc", "num_ports", "device_id", "vendor_id", "ports_with_errors", "total_errors", "total_link_down", "total_fec_uncorrectable", "port_counters", "fw_version", "fw_date", "psid", "uptime_secs", "sw_version", "worst_raw_ber", "down_ports")
    NODE_GUID_FIELD_NUMBER: _ClassVar[int]
    NODE_DESC_FIELD_NUMBER: _ClassVar[int]
    NUM_PORTS_FIELD_NUMBER: _ClassVar[int]
    DEVICE_ID_FIELD_NUMBER: _ClassVar[int]
    VENDOR_ID_FIELD_NUMBER: _ClassVar[int]
    PORTS_WITH_ERRORS_FIELD_NUMBER: _ClassVar[int]
    TOTAL_ERRORS_FIELD_NUMBER: _ClassVar[int]
    TOTAL_LINK_DOWN_FIELD_NUMBER: _ClassVar[int]
    TOTAL_FEC_UNCORRECTABLE_FIELD_NUMBER: _ClassVar[int]
    PORT_COUNTERS_FIELD_NUMBER: _ClassVar[int]
    FW_VERSION_FIELD_NUMBER: _ClassVar[int]
    FW_DATE_FIELD_NUMBER: _ClassVar[int]
    PSID_FIELD_NUMBER: _ClassVar[int]
    UPTIME_SECS_FIELD_NUMBER: _ClassVar[int]
    SW_VERSION_FIELD_NUMBER: _ClassVar[int]
    WORST_RAW_BER_FIELD_NUMBER: _ClassVar[int]
    DOWN_PORTS_FIELD_NUMBER: _ClassVar[int]
    node_guid: str
    node_desc: str
    num_ports: int
    device_id: int
    vendor_id: int
    ports_with_errors: int
    total_errors: int
    total_link_down: int
    total_fec_uncorrectable: int
    port_counters: _containers.RepeatedCompositeFieldContainer[PortCounterData]
    fw_version: str
    fw_date: str
    psid: str
    uptime_secs: int
    sw_version: str
    worst_raw_ber: float
    down_ports: int
    def __init__(self, node_guid: _Optional[str] = ..., node_desc: _Optional[str] = ..., num_ports: _Optional[int] = ..., device_id: _Optional[int] = ..., vendor_id: _Optional[int] = ..., ports_with_errors: _Optional[int] = ..., total_errors: _Optional[int] = ..., total_link_down: _Optional[int] = ..., total_fec_uncorrectable: _Optional[int] = ..., port_counters: _Optional[_Iterable[_Union[PortCounterData, _Mapping]]] = ..., fw_version: _Optional[str] = ..., fw_date: _Optional[str] = ..., psid: _Optional[str] = ..., uptime_secs: _Optional[int] = ..., sw_version: _Optional[str] = ..., worst_raw_ber: _Optional[float] = ..., down_ports: _Optional[int] = ...) -> None: ...

class ListSwitchesRequest(_message.Message):
    __slots__ = ("az_id", "collection_id", "errors_only", "limit", "offset")
    AZ_ID_FIELD_NUMBER: _ClassVar[int]
    COLLECTION_ID_FIELD_NUMBER: _ClassVar[int]
    ERRORS_ONLY_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    OFFSET_FIELD_NUMBER: _ClassVar[int]
    az_id: str
    collection_id: str
    errors_only: bool
    limit: int
    offset: int
    def __init__(self, az_id: _Optional[str] = ..., collection_id: _Optional[str] = ..., errors_only: bool = ..., limit: _Optional[int] = ..., offset: _Optional[int] = ...) -> None: ...

class ListSwitchesResponse(_message.Message):
    __slots__ = ("switches", "total_count")
    SWITCHES_FIELD_NUMBER: _ClassVar[int]
    TOTAL_COUNT_FIELD_NUMBER: _ClassVar[int]
    switches: _containers.RepeatedCompositeFieldContainer[SwitchSummary]
    total_count: int
    def __init__(self, switches: _Optional[_Iterable[_Union[SwitchSummary, _Mapping]]] = ..., total_count: _Optional[int] = ...) -> None: ...

class ListPortCountersRequest(_message.Message):
    __slots__ = ("az_id", "collection_id", "errors_only", "guid_filter", "sort_by", "sort_desc", "limit", "offset")
    AZ_ID_FIELD_NUMBER: _ClassVar[int]
    COLLECTION_ID_FIELD_NUMBER: _ClassVar[int]
    ERRORS_ONLY_FIELD_NUMBER: _ClassVar[int]
    GUID_FILTER_FIELD_NUMBER: _ClassVar[int]
    SORT_BY_FIELD_NUMBER: _ClassVar[int]
    SORT_DESC_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    OFFSET_FIELD_NUMBER: _ClassVar[int]
    az_id: str
    collection_id: str
    errors_only: bool
    guid_filter: str
    sort_by: str
    sort_desc: bool
    limit: int
    offset: int
    def __init__(self, az_id: _Optional[str] = ..., collection_id: _Optional[str] = ..., errors_only: bool = ..., guid_filter: _Optional[str] = ..., sort_by: _Optional[str] = ..., sort_desc: bool = ..., limit: _Optional[int] = ..., offset: _Optional[int] = ...) -> None: ...

class ListPortCountersResponse(_message.Message):
    __slots__ = ("port_counters", "total_count")
    PORT_COUNTERS_FIELD_NUMBER: _ClassVar[int]
    TOTAL_COUNT_FIELD_NUMBER: _ClassVar[int]
    port_counters: _containers.RepeatedCompositeFieldContainer[PortCounterData]
    total_count: int
    def __init__(self, port_counters: _Optional[_Iterable[_Union[PortCounterData, _Mapping]]] = ..., total_count: _Optional[int] = ...) -> None: ...

class CableInfo(_message.Message):
    __slots__ = ("port", "lid", "guid", "port_name", "identifier", "vendor", "oui", "part_number", "serial_number", "revision", "date_code", "length_m", "cable_type", "fw_version", "power_class", "nominal_bitrate", "temperature_c", "supply_voltage_uv", "rx_power_mw", "rx_power_dbm", "tx_power_mw", "tx_power_dbm", "tx_bias_ma", "alarm_temp_high", "alarm_temp_low", "alarm_voltage_high", "alarm_voltage_low", "rx_power_high_thresh", "rx_power_low_thresh", "tx_power_high_thresh", "tx_power_low_thresh", "tx_bias_high_thresh", "tx_bias_low_thresh", "latched_alarms")
    PORT_FIELD_NUMBER: _ClassVar[int]
    LID_FIELD_NUMBER: _ClassVar[int]
    GUID_FIELD_NUMBER: _ClassVar[int]
    PORT_NAME_FIELD_NUMBER: _ClassVar[int]
    IDENTIFIER_FIELD_NUMBER: _ClassVar[int]
    VENDOR_FIELD_NUMBER: _ClassVar[int]
    OUI_FIELD_NUMBER: _ClassVar[int]
    PART_NUMBER_FIELD_NUMBER: _ClassVar[int]
    SERIAL_NUMBER_FIELD_NUMBER: _ClassVar[int]
    REVISION_FIELD_NUMBER: _ClassVar[int]
    DATE_CODE_FIELD_NUMBER: _ClassVar[int]
    LENGTH_M_FIELD_NUMBER: _ClassVar[int]
    CABLE_TYPE_FIELD_NUMBER: _ClassVar[int]
    FW_VERSION_FIELD_NUMBER: _ClassVar[int]
    POWER_CLASS_FIELD_NUMBER: _ClassVar[int]
    NOMINAL_BITRATE_FIELD_NUMBER: _ClassVar[int]
    TEMPERATURE_C_FIELD_NUMBER: _ClassVar[int]
    SUPPLY_VOLTAGE_UV_FIELD_NUMBER: _ClassVar[int]
    RX_POWER_MW_FIELD_NUMBER: _ClassVar[int]
    RX_POWER_DBM_FIELD_NUMBER: _ClassVar[int]
    TX_POWER_MW_FIELD_NUMBER: _ClassVar[int]
    TX_POWER_DBM_FIELD_NUMBER: _ClassVar[int]
    TX_BIAS_MA_FIELD_NUMBER: _ClassVar[int]
    ALARM_TEMP_HIGH_FIELD_NUMBER: _ClassVar[int]
    ALARM_TEMP_LOW_FIELD_NUMBER: _ClassVar[int]
    ALARM_VOLTAGE_HIGH_FIELD_NUMBER: _ClassVar[int]
    ALARM_VOLTAGE_LOW_FIELD_NUMBER: _ClassVar[int]
    RX_POWER_HIGH_THRESH_FIELD_NUMBER: _ClassVar[int]
    RX_POWER_LOW_THRESH_FIELD_NUMBER: _ClassVar[int]
    TX_POWER_HIGH_THRESH_FIELD_NUMBER: _ClassVar[int]
    TX_POWER_LOW_THRESH_FIELD_NUMBER: _ClassVar[int]
    TX_BIAS_HIGH_THRESH_FIELD_NUMBER: _ClassVar[int]
    TX_BIAS_LOW_THRESH_FIELD_NUMBER: _ClassVar[int]
    LATCHED_ALARMS_FIELD_NUMBER: _ClassVar[int]
    port: int
    lid: str
    guid: str
    port_name: str
    identifier: str
    vendor: str
    oui: str
    part_number: str
    serial_number: str
    revision: str
    date_code: str
    length_m: float
    cable_type: str
    fw_version: str
    power_class: str
    nominal_bitrate: float
    temperature_c: float
    supply_voltage_uv: int
    rx_power_mw: _containers.RepeatedScalarFieldContainer[float]
    rx_power_dbm: _containers.RepeatedScalarFieldContainer[float]
    tx_power_mw: _containers.RepeatedScalarFieldContainer[float]
    tx_power_dbm: _containers.RepeatedScalarFieldContainer[float]
    tx_bias_ma: _containers.RepeatedScalarFieldContainer[float]
    alarm_temp_high: float
    alarm_temp_low: float
    alarm_voltage_high: int
    alarm_voltage_low: int
    rx_power_high_thresh: float
    rx_power_low_thresh: float
    tx_power_high_thresh: float
    tx_power_low_thresh: float
    tx_bias_high_thresh: float
    tx_bias_low_thresh: float
    latched_alarms: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, port: _Optional[int] = ..., lid: _Optional[str] = ..., guid: _Optional[str] = ..., port_name: _Optional[str] = ..., identifier: _Optional[str] = ..., vendor: _Optional[str] = ..., oui: _Optional[str] = ..., part_number: _Optional[str] = ..., serial_number: _Optional[str] = ..., revision: _Optional[str] = ..., date_code: _Optional[str] = ..., length_m: _Optional[float] = ..., cable_type: _Optional[str] = ..., fw_version: _Optional[str] = ..., power_class: _Optional[str] = ..., nominal_bitrate: _Optional[float] = ..., temperature_c: _Optional[float] = ..., supply_voltage_uv: _Optional[int] = ..., rx_power_mw: _Optional[_Iterable[float]] = ..., rx_power_dbm: _Optional[_Iterable[float]] = ..., tx_power_mw: _Optional[_Iterable[float]] = ..., tx_power_dbm: _Optional[_Iterable[float]] = ..., tx_bias_ma: _Optional[_Iterable[float]] = ..., alarm_temp_high: _Optional[float] = ..., alarm_temp_low: _Optional[float] = ..., alarm_voltage_high: _Optional[int] = ..., alarm_voltage_low: _Optional[int] = ..., rx_power_high_thresh: _Optional[float] = ..., rx_power_low_thresh: _Optional[float] = ..., tx_power_high_thresh: _Optional[float] = ..., tx_power_low_thresh: _Optional[float] = ..., tx_bias_high_thresh: _Optional[float] = ..., tx_bias_low_thresh: _Optional[float] = ..., latched_alarms: _Optional[_Iterable[str]] = ...) -> None: ...

class ListCablesRequest(_message.Message):
    __slots__ = ("az_id", "collection_id", "alarms_only", "limit", "offset")
    AZ_ID_FIELD_NUMBER: _ClassVar[int]
    COLLECTION_ID_FIELD_NUMBER: _ClassVar[int]
    ALARMS_ONLY_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    OFFSET_FIELD_NUMBER: _ClassVar[int]
    az_id: str
    collection_id: str
    alarms_only: bool
    limit: int
    offset: int
    def __init__(self, az_id: _Optional[str] = ..., collection_id: _Optional[str] = ..., alarms_only: bool = ..., limit: _Optional[int] = ..., offset: _Optional[int] = ...) -> None: ...

class ListCablesResponse(_message.Message):
    __slots__ = ("cables", "total_count")
    CABLES_FIELD_NUMBER: _ClassVar[int]
    TOTAL_COUNT_FIELD_NUMBER: _ClassVar[int]
    cables: _containers.RepeatedCompositeFieldContainer[CableInfo]
    total_count: int
    def __init__(self, cables: _Optional[_Iterable[_Union[CableInfo, _Mapping]]] = ..., total_count: _Optional[int] = ...) -> None: ...

class GetCollectionStatusRequest(_message.Message):
    __slots__ = ("az_id",)
    AZ_ID_FIELD_NUMBER: _ClassVar[int]
    az_id: str
    def __init__(self, az_id: _Optional[str] = ...) -> None: ...

class GetCollectionStatusResponse(_message.Message):
    __slots__ = ("is_running", "collection_type", "started_at", "expected_duration_secs", "error", "last_completed_at")
    IS_RUNNING_FIELD_NUMBER: _ClassVar[int]
    COLLECTION_TYPE_FIELD_NUMBER: _ClassVar[int]
    STARTED_AT_FIELD_NUMBER: _ClassVar[int]
    EXPECTED_DURATION_SECS_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    LAST_COMPLETED_AT_FIELD_NUMBER: _ClassVar[int]
    is_running: bool
    collection_type: str
    started_at: str
    expected_duration_secs: int
    error: str
    last_completed_at: str
    def __init__(self, is_running: bool = ..., collection_type: _Optional[str] = ..., started_at: _Optional[str] = ..., expected_duration_secs: _Optional[int] = ..., error: _Optional[str] = ..., last_completed_at: _Optional[str] = ...) -> None: ...

class ImportCollectionRequest(_message.Message):
    __slots__ = ("collection_json", "raw_data", "az_id")
    COLLECTION_JSON_FIELD_NUMBER: _ClassVar[int]
    RAW_DATA_FIELD_NUMBER: _ClassVar[int]
    AZ_ID_FIELD_NUMBER: _ClassVar[int]
    collection_json: bytes
    raw_data: bytes
    az_id: str
    def __init__(self, collection_json: _Optional[bytes] = ..., raw_data: _Optional[bytes] = ..., az_id: _Optional[str] = ...) -> None: ...

class ImportCollectionResponse(_message.Message):
    __slots__ = ("success", "message", "collection_id")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    COLLECTION_ID_FIELD_NUMBER: _ClassVar[int]
    success: bool
    message: str
    collection_id: str
    def __init__(self, success: bool = ..., message: _Optional[str] = ..., collection_id: _Optional[str] = ...) -> None: ...

class UploadIbdiagnetRequest(_message.Message):
    __slots__ = ("tarball_data", "az_id", "filename")
    TARBALL_DATA_FIELD_NUMBER: _ClassVar[int]
    AZ_ID_FIELD_NUMBER: _ClassVar[int]
    FILENAME_FIELD_NUMBER: _ClassVar[int]
    tarball_data: bytes
    az_id: str
    filename: str
    def __init__(self, tarball_data: _Optional[bytes] = ..., az_id: _Optional[str] = ..., filename: _Optional[str] = ...) -> None: ...
