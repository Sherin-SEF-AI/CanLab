"""
UDS (ISO 14229) / OBD-II (SAE J1979) scanner over CAN.

Functional request ID : 0x7DF
Response IDs          : 0x7E8 – 0x7EF (ECU 0 – ECU 7)
"""
from PyQt6.QtCore import QThread, pyqtSignal
import logging

log = logging.getLogger(__name__)

# OBD-II PIDs come from the single J1979 table in core.obd2_pids — this module
# used to carry a second, inconsistent copy with its own decode arithmetic.
from canlab.core.obd2_pids import PID_TABLE, decode_pid   # noqa: E402

# UDS service names
UDS_SERVICES = {
    0x10: "DiagnosticSessionControl",
    0x11: "ECUReset",
    0x14: "ClearDTCInfo",
    0x19: "ReadDTCByStatusMask",
    0x22: "ReadDataByIdentifier",
    0x27: "SecurityAccess",
    0x2E: "WriteDataByIdentifier",
    0x31: "RoutineControl",
    0x34: "RequestDownload",
    0x3E: "TesterPresent",
    0x85: "ControlDTCSetting",
    0x87: "LinkControl",
}

FUNCTIONAL_REQUEST_ID = 0x7DF

# Keep a non-default diagnostic session alive during long sweeps (the ISO 14229
# S3 timer is nominally 5 s).
TESTER_PRESENT_INTERVAL = 2.0

# Per-service wait during the service scan (short: most IDs never answer).
SERVICE_PROBE_TIMEOUT = 0.15
SERVICE_PROBE_GAP = 0.05

# UDS services that can change ECU/vehicle state. Probing these — even with a
# reserved subfunction — can reset ECUs, clear diagnostics, start routines,
# begin a firmware download, or (via SecurityAccess) trip an attempt lockout.
# The service scan SKIPS these unless explicitly run with allow_unsafe=True.
DESTRUCTIVE_SERVICES = {
    0x11: "ECUReset",
    0x14: "ClearDiagnosticInformation",
    0x27: "SecurityAccess (lockout risk)",
    0x28: "CommunicationControl",
    0x2C: "DynamicallyDefineDataIdentifier",
    0x2E: "WriteDataByIdentifier",
    0x2F: "InputOutputControlByIdentifier",
    0x31: "RoutineControl",
    0x34: "RequestDownload",
    0x35: "RequestUpload",
    0x36: "TransferData",
    0x37: "RequestTransferExit",
    0x38: "RequestFileTransfer",
    0x3D: "WriteMemoryByAddress",
    0x85: "ControlDTCSetting",
    0x87: "LinkControl",
}

# UDS Data Identifiers for ECU information
UDS_DATA_IDS = {
    0xF186: "ActiveDiagnosticSession",
    0xF187: "VehicleManufacturerSparePartNumber",
    0xF188: "VehicleManufacturerECUSoftwareNumber",
    0xF189: "VehicleManufacturerECUSoftwareVersionNumber",
    0xF18A: "SystemSupplierIdentifier",
    0xF18B: "ECUManufacturingDate",
    0xF18C: "ECUSerialNumber",
    0xF190: "VIN",
    0xF191: "VehicleManufacturerECUHardwareNumber",
    0xF192: "SystemSupplierECUHardwareNumber",
    0xF193: "SystemSupplierECUHardwareVersionNumber",
    0xF194: "SystemSupplierECUSoftwareNumber",
    0xF195: "SystemSupplierECUSoftwareVersionNumber",
    0xF197: "VehicleManufacturerKitAssemblyPartNumber",
}

# UDS session types
UDS_SESSIONS = {
    0x01: "Default",
    0x02: "Programming",
    0x03: "Extended",
}


def decode_dtc_records(payload: bytes) -> list[str]:
    """Decode a ReadDTCInformation (0x19 sub-function 0x02) response payload.

    Layout: ``59 02 <statusAvailabilityMask>`` then 4-byte records of
    ``<DTC high> <DTC mid> <DTC low> <status>``.
    """
    if len(payload) < 3 or payload[0] != 0x59:
        return []
    codes: list[str] = []
    i = 3
    while i + 3 < len(payload):
        hi, mid, lo, _status = payload[i:i + 4]
        if hi == 0 and mid == 0 and lo == 0:
            break
        prefix = "PCBU"[(hi >> 6) & 0x03]
        codes.append(f"{prefix}{(hi >> 4) & 0x03}{hi & 0x0F:X}{mid >> 4:X}"
                     f"{mid & 0x0F:X}-{lo:02X}")
        i += 4
    return codes


class _FakeMsg:
    """Lightweight stand-in for can.Message with arbitration_id and data."""
    __slots__ = ("arbitration_id", "data")
    def __init__(self, arb_id: int, data: bytes):
        self.arbitration_id = arb_id
        self.data           = data


class UDSScanner(QThread):
    """
    Scans for supported OBD-II PIDs and optionally reads DTC codes.
    Emits pid_result and dtc_result signals.
    """
    pid_result   = pyqtSignal(int, str, float, str)    # pid, name, value, unit
    dtc_result   = pyqtSignal(list)                    # list of DTC strings
    ecu_result   = pyqtSignal(int, str, str, str)      # ecu_addr, did_name, value_hex, decoded
    service_result = pyqtSignal(int, int, bool, bytes) # ecu_addr, service_id, supported, response
    status       = pyqtSignal(str)
    finished     = pyqtSignal()
    error        = pyqtSignal(str)

    def __init__(self, bus, mode: str = "PID", ecu_addr: int = 0x7DF,
                 parent=None, allow_unsafe: bool = False):
        super().__init__(parent)
        self._bus      = bus
        self._mode     = mode       # "PID" | "DTC" | "DEEP" | "SERVICES"
        self._ecu_addr = ecu_addr   # 0x7DF = functional, 0x7E0-0x7EF = physical
        self._running  = True
        # When False (default) the SERVICES scan skips DESTRUCTIVE_SERVICES so a
        # "which services are supported" probe cannot reset ECUs or clear DTCs
        # on a live bus.
        self._allow_unsafe = allow_unsafe

    def stop(self):
        self._running = False
        self.wait(2000)

    def run(self):
        from canlab.core import safety
        safety.register_tx_worker(self)
        try:
            if self._mode == "PID":
                self._scan_pids()
            elif self._mode == "DTC":
                self._read_dtc()
            elif self._mode == "DEEP":
                self._deep_scan()
            elif self._mode == "SERVICES":
                self._scan_services()
        finally:
            safety.unregister_tx_worker(self)
        self.finished.emit()

    def _send_to(self, arb_id: int, data: bytes, timeout: float = 0.5):
        """Physically addressed request. ``data`` is the service payload only
        (no PCI byte); the reply is likewise the service payload."""
        if not self._running:
            return None
        rx_id = arb_id + 0x08
        try:
            from canlab.core.isotp import ISOTPSession
            session = ISOTPSession(self._bus, tx_id=arb_id, rx_id=rx_id)
            payload = session.request(data, timeout=timeout)
            if payload:
                return _FakeMsg(rx_id, payload)
        except Exception as e:
            self.error.emit(str(e))
        return None

    def _send_and_recv(self, data: bytes, timeout: float = 0.5):
        """Functionally addressed request (0x7DF), answered by any of
        0x7E8-0x7EF. ``data`` is the service payload only.

        This used to hand-roll its own consecutive-frame reassembly with no
        sequence-number check; ISOTPSession is the one transport now.
        """
        if not self._running:
            return None
        try:
            from canlab.core.isotp import ISOTPSession
            session = ISOTPSession(self._bus, tx_id=FUNCTIONAL_REQUEST_ID,
                                   rx_id=range(0x7E8, 0x7F0))
            payload = session.request(data, timeout=timeout)
            if payload:
                return _FakeMsg(session.last_rx_id or 0x7E8, payload)
        except Exception as e:
            self.error.emit(str(e))
        return None

    def _scan_pids(self):
        self.status.emit("Scanning OBD-II PIDs…")
        for pid, entry in PID_TABLE.items():
            if not self._running:
                break
            resp = self._send_and_recv(bytes([0x01, pid]))
            if resp is None:
                continue
            raw = resp.data
            if len(raw) < 3 or raw[0] != 0x41 or raw[1] != pid:
                continue
            value = decode_pid(pid, raw[2:])
            if value is None:
                continue
            self.pid_result.emit(pid, entry["name"], round(value, 2),
                                 entry.get("unit", "") or "")

    def _read_dtc(self):
        self.status.emit("Reading DTCs (service 0x19)…")
        resp = self._send_and_recv(bytes([0x19, 0x02, 0xFF]), timeout=0.5)
        self.dtc_result.emit(decode_dtc_records(resp.data) if resp else [])

    def _deep_scan(self):
        """
        Deep UDS scan:
        1. Probe each ECU address 0x7E0–0x7E7 for presence (TesterPresent)
        2. Open extended diagnostic session
        3. Read all known DataIdentifiers (VIN, software version, ECU serial, etc.)
        """
        import time
        active_ecus = []

        self.status.emit("Probing ECU addresses 0x7E0–0x7E7…")
        for ecu_id in range(0x7E0, 0x7E8):
            if not self._running:
                return
            # TesterPresent (0x3E 0x00)
            resp = self._send_to(ecu_id, bytes([0x3E, 0x00]))
            if resp:
                active_ecus.append(ecu_id)
                self.status.emit(f"  ECU found: 0x{ecu_id:03X} → response 0x{resp.arbitration_id:03X}")
            time.sleep(0.05)

        if not active_ecus:
            self.status.emit("No ECUs responded. Check connection and ignition.")
            return

        for ecu_id in active_ecus:
            if not self._running:
                return
            # Open extended session (0x10 0x03)
            self.status.emit(f"Opening extended session on 0x{ecu_id:03X}…")
            self._send_to(ecu_id, bytes([0x10, 0x03]))
            time.sleep(0.1)
            last_tp = time.monotonic()

            # Read each DataIdentifier
            for did, did_name in UDS_DATA_IDS.items():
                if not self._running:
                    return
                # A long DID sweep must keep the extended session alive or
                # later reads come back as negative responses.
                if time.monotonic() - last_tp > TESTER_PRESENT_INTERVAL:
                    self._send_to(ecu_id, bytes([0x3E, 0x80]), timeout=0.1)
                    last_tp = time.monotonic()
                hi = (did >> 8) & 0xFF
                lo = did & 0xFF
                resp = self._send_to(ecu_id, bytes([0x22, hi, lo]), timeout=0.3)
                if resp and len(resp.data) >= 3:
                    raw = bytes(resp.data)
                    if raw[0] == 0x62:   # positive response
                        payload = raw[3:]
                        hex_str = payload.hex().upper()
                        try:
                            decoded = payload.decode("ascii", errors="replace").strip()
                        except Exception:
                            decoded = ""
                        self.ecu_result.emit(ecu_id, did_name, hex_str, decoded)
                time.sleep(0.05)

            # Return to default session
            self._send_to(ecu_id, bytes([0x10, 0x01]))
            time.sleep(0.05)

    def _scan_services(self):
        """
        Probe which UDS services are supported by scanning 0x10–0x3E
        against the functional address.
        """
        import time
        if self._allow_unsafe:
            self.status.emit("Scanning ALL UDS services (0x10–0x3E) — UNSAFE mode…")
        else:
            self.status.emit("Scanning read-only UDS services (0x10–0x3E; "
                             "destructive services skipped)…")
        last_tp = time.monotonic()
        for svc_id in range(0x10, 0x3F):
            if not self._running:
                break
            if time.monotonic() - last_tp > TESTER_PRESENT_INTERVAL:
                self._send_and_recv(bytes([0x3E, 0x80]), timeout=0.1)
                last_tp = time.monotonic()
            if not self._allow_unsafe and svc_id in DESTRUCTIVE_SERVICES:
                self.status.emit(f"  ⚠ skipped {DESTRUCTIVE_SERVICES[svc_id]} "
                                 f"(0x{svc_id:02X}) — enable unsafe scan to probe")
                self.service_result.emit(FUNCTIONAL_REQUEST_ID, svc_id, False, b"")
                continue
            resp = self._send_and_recv(bytes([svc_id, 0x00]),
                                       timeout=SERVICE_PROBE_TIMEOUT)
            supported = False
            resp_data = b""
            if resp:
                raw = bytes(resp.data)
                # Not a "service not supported" negative response (7F xx 11)
                if not (len(raw) >= 3 and raw[0] == 0x7F and raw[2] == 0x11):
                    supported = True
                    resp_data = raw
            self.service_result.emit(
                FUNCTIONAL_REQUEST_ID, svc_id, supported, resp_data
            )
            svc_name = UDS_SERVICES.get(svc_id, f"0x{svc_id:02X}")
            status = "✓" if supported else "✗"
            self.status.emit(f"  {status} {svc_name}")
            time.sleep(SERVICE_PROBE_GAP)
