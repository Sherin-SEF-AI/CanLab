"""
UDS (ISO 14229) / OBD-II (SAE J1979) scanner over CAN.

Functional request ID : 0x7DF
Response IDs          : 0x7E8 – 0x7EF (ECU 0 – ECU 7)
"""
from PyQt6.QtCore import QThread, pyqtSignal

# OBD-II Mode 01 PIDs live in core.obd2_pids (single source of truth, with
# correct one- and two-byte decoders). This module used to keep a second,
# narrower copy in which every multi-byte PID decoded as a single byte.

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
}

FUNCTIONAL_REQUEST_ID = 0x7DF

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


def _printable(payload: bytes) -> str:
    """Render a DID payload as ASCII, keeping only printable characters.

    VIN/part-number DIDs are ASCII; version DIDs are often packed binary. Using
    ``errors="replace"`` alone filled the UI with U+FFFD for binary payloads.
    """
    text = "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in payload).strip()
    return text if any(c.isalnum() for c in text) else ""


_DTC_PREFIX = {0: "P", 1: "C", 2: "B", 3: "U"}


def decode_dtc(high: int, mid: int, low: int) -> str:
    """Decode a 3-byte ISO 15031-6 / ISO 14229 DTC into its printable code.

    Bits 7-6 of the high byte select the system letter (P/C/B/U); bits 5-4 are
    the first digit; the remaining nibble and the middle byte are the last three
    digits. The low byte is the fault-type/failure byte, not part of the code.
    """
    prefix = _DTC_PREFIX[(high >> 6) & 0x03]
    return f"{prefix}{(high >> 4) & 0x03}{high & 0x0F:X}{mid:02X}"


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

    def run(self):
        if self._mode == "PID":
            self._scan_pids()
        elif self._mode == "DTC":
            self._read_dtc()
        elif self._mode == "DEEP":
            self._deep_scan()
        elif self._mode == "SERVICES":
            self._scan_services()
        self.finished.emit()

    def _send_to(self, arb_id: int, data: bytes, timeout: float = 0.5):
        """
        Send a *service payload* to a specific ECU and receive via ISO-TP.

        ``data`` carries no ISO-TP PCI byte and no padding — e.g. ``b"\\x3E\\x00"``
        for TesterPresent. The session builds the PCI. Returns a _FakeMsg whose
        ``.data`` is the assembled *service payload* of the response (also PCI-less),
        so ``resp.data[0]`` is the response SID.
        """
        if not self._running:
            return None
        rx_id = arb_id + 0x08
        try:
            from core.isotp import ISOTPSession
            session = ISOTPSession(self._bus, tx_id=arb_id, rx_id=rx_id)
            payload = session.send(data, timeout=timeout)
            if payload:
                return _FakeMsg(rx_id, payload)
        except Exception as e:
            self.error.emit(str(e))
        return None

    def _send_and_recv(self, data: bytes, timeout: float = 0.5):
        """
        Broadcast a *service payload* to the functional address 0x7DF and return
        the first response from 0x7E8–0x7EF.

        Like :meth:`_send_to`, ``data`` is PCI-less and the returned ``.data`` is
        the assembled PCI-less service payload. Both helpers used to disagree
        about whether the PCI byte was present, which shifted every field index
        by one on the multi-frame path.
        """
        if not self._running:
            return None
        try:
            import can, time
            from core.isotp import ISOTPSession

            n = len(data)
            if n > 7:
                # Multi-frame functional requests need a per-ECU FC handshake,
                # which broadcast addressing cannot provide.
                self.error.emit("Functional (broadcast) requests must fit in a "
                                "single frame; use a physical ECU address.")
                return None
            frame = bytes([n & 0x0F]) + data
            frame = frame + bytes(8 - len(frame))
            self._bus.send(can.Message(
                arbitration_id=FUNCTIONAL_REQUEST_ID,
                data=frame,
                is_extended_id=False,
            ))

            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                resp = self._bus.recv(timeout=0.05)
                if not resp or not (0x7E8 <= resp.arbitration_id <= 0x7EF):
                    continue
                raw = bytes(resp.data)
                if not raw:
                    continue
                pci = (raw[0] >> 4) & 0x0F
                rx_id = resp.arbitration_id

                if pci == 0x0:                      # Single Frame
                    length = raw[0] & 0x0F
                    return _FakeMsg(rx_id, raw[1:1 + length])

                if pci == 0x1:                      # First Frame — reassemble
                    if len(raw) < 2:
                        continue
                    session = ISOTPSession(self._bus, tx_id=rx_id - 0x08, rx_id=rx_id)
                    session._send_fc()
                    length  = ((raw[0] & 0x0F) << 8) | raw[1]
                    payload = bytearray(raw[2:])
                    while time.monotonic() < deadline and len(payload) < length:
                        cf = self._bus.recv(timeout=0.05)
                        if cf and cf.arbitration_id == rx_id and cf.data:
                            payload += bytearray(bytes(cf.data)[1:])
                    return _FakeMsg(rx_id, bytes(payload[:length]))
        except Exception as e:
            self.error.emit(str(e))
        return None

    def _scan_pids(self):
        """Query every known Mode 01 PID and emit decoded physical values.

        Uses the canonical :mod:`core.obd2_pids` table (26 PIDs with correct
        one- and two-byte decoders) rather than a second, narrower copy that
        decoded every multi-byte PID as a single byte.
        """
        from core.obd2_pids import PID_TABLE, decode_pid

        self.status.emit("Scanning OBD-II PIDs…")
        for pid, entry in PID_TABLE.items():
            if not self._running:
                break
            resp = self._send_and_recv(bytes([0x01, pid]))
            if resp is None:
                continue
            raw = bytes(resp.data)
            # Positive Mode 01 response: 41 <pid> <A> [B ...]
            if len(raw) < 3 or raw[0] != 0x41 or raw[1] != pid:
                continue
            value = decode_pid(pid, raw[2:])
            if value is not None:
                self.pid_result.emit(pid, entry["name"], round(value, 2),
                                     entry.get("unit", ""))

    def _read_dtc(self):
        """UDS ReadDTCInformation, subfunction 0x02 (reportDTCByStatusMask)."""
        self.status.emit("Reading DTCs (service 0x19)…")
        resp = self._send_and_recv(bytes([0x19, 0x02, 0xFF]), timeout=0.5)
        dtcs = []
        if resp:
            raw = bytes(resp.data)
            # Response: 59 02 <statusAvailabilityMask> then 4-byte records of
            # [DTC_high, DTC_mid, DTC_low, statusOfDTC]. The old code started at
            # the mask byte and strode 3, so every code after the first was
            # decoded from misaligned bytes.
            if len(raw) >= 3 and raw[0] == 0x59:
                i = 3
                while i + 2 < len(raw):
                    hi, mid, lo = raw[i], raw[i + 1], raw[i + 2]
                    if hi == 0 and mid == 0 and lo == 0:
                        break
                    dtcs.append(decode_dtc(hi, mid, lo))
                    i += 4
        self.dtc_result.emit(dtcs)

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
            resp_id = ecu_id + 0x08   # physical response ID

            # Open extended session (0x10 0x03)
            self.status.emit(f"Opening extended session on 0x{ecu_id:03X}…")
            self._send_to(ecu_id, bytes([0x10, 0x03]))
            time.sleep(0.1)

            # Read each DataIdentifier
            for did, did_name in UDS_DATA_IDS.items():
                if not self._running:
                    return
                hi = (did >> 8) & 0xFF
                lo = did & 0xFF
                resp = self._send_to(ecu_id, bytes([0x22, hi, lo]), timeout=0.3)
                if resp and len(resp.data) >= 3:
                    raw = bytes(resp.data)
                    # Positive response: 62 <DID_hi> <DID_lo> <data...>. The
                    # payload is PCI-less, so the SID is at index 0 — the old
                    # code checked index 1 and never matched, which is why the
                    # ECU-info scan reported nothing on real hardware.
                    if raw[0] == 0x62 and raw[1] == hi and raw[2] == lo:
                        payload = raw[3:]
                        hex_str = payload.hex().upper()
                        decoded = _printable(payload)
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
        for svc_id in range(0x10, 0x3F):
            if not self._running:
                break
            if not self._allow_unsafe and svc_id in DESTRUCTIVE_SERVICES:
                self.status.emit(f"  ⚠ skipped {DESTRUCTIVE_SERVICES[svc_id]} "
                                 f"(0x{svc_id:02X}) — enable unsafe scan to probe")
                self.service_result.emit(FUNCTIONAL_REQUEST_ID, svc_id, False, b"")
                continue
            resp = self._send_and_recv(bytes([svc_id, 0x00]), timeout=0.15)
            supported = False
            resp_data = b""
            if resp:
                raw = bytes(resp.data)
                # Negative response is 7F <sid> <nrc>; NRC 0x11 = serviceNotSupported.
                # Indices are PCI-less now, so the NRC sits at raw[2], not raw[3].
                not_supported = (len(raw) >= 3 and raw[0] == 0x7F
                                 and raw[2] in (0x11, 0x7F))
                if not not_supported:
                    supported = True
                    resp_data = raw
            self.service_result.emit(
                FUNCTIONAL_REQUEST_ID, svc_id, supported, resp_data
            )
            svc_name = UDS_SERVICES.get(svc_id, f"0x{svc_id:02X}")
            status = "✓" if supported else "✗"
            self.status.emit(f"  {status} {svc_name}")
            time.sleep(0.05)
