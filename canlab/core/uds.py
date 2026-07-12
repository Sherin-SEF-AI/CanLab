"""
UDS (ISO 14229) / OBD-II (SAE J1979) scanner over CAN.

Functional request ID : 0x7DF
Response IDs          : 0x7E8 – 0x7EF (ECU 0 – ECU 7)
"""
from PyQt6.QtCore import QThread, pyqtSignal

# OBD-II PID names (Mode 01)
OBD2_PIDS = {
    0x00: ("Supported PIDs 01-20", None, None),
    0x04: ("Engine Load",          1/2.55, "%"),
    0x05: ("Coolant Temp",         -40,    "°C"),   # offset
    0x0B: ("MAP Pressure",         1,      "kPa"),
    0x0C: ("Engine RPM",           0.25,   "rpm"),
    0x0D: ("Vehicle Speed",        1,      "km/h"),
    0x0F: ("Intake Air Temp",      -40,    "°C"),
    0x11: ("Throttle Position",    1/2.55, "%"),
    0x1C: ("OBD Standard",         1,      ""),
    0x1F: ("Run Time Since Start", 1,      "s"),
    0x21: ("MIL Distance",         1,      "km"),
    0x2F: ("Fuel Level",           1/2.55, "%"),
}

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
        Send to specific ECU and receive via ISO-TP reassembly.
        Returns a _FakeMsg with .data = full assembled payload, .arbitration_id = rx_id.
        Single-frame replies behave identically to the previous version.
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
        Send to functional address 0x7DF and receive via ISO-TP reassembly.
        Scans rx IDs 0x7E8–0x7EF; reassembles multi-frame responses.
        """
        if not self._running:
            return None
        try:
            import can, time
            msg = can.Message(
                arbitration_id=FUNCTIONAL_REQUEST_ID,
                data=data,
                is_extended_id=False,
            )
            self._bus.send(msg)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                resp = self._bus.recv(timeout=0.05)
                if resp and 0x7E8 <= resp.arbitration_id <= 0x7EF:
                    pci = (resp.data[0] >> 4) & 0x0F if resp.data else 0xFF
                    if pci == 0x1:   # First Frame — reassemble via ISO-TP
                        from core.isotp import ISOTPSession
                        rx_id   = resp.arbitration_id
                        session = ISOTPSession(self._bus, tx_id=rx_id - 0x08, rx_id=rx_id)
                        session._send_fc()
                        length  = ((resp.data[0] & 0x0F) << 8) | resp.data[1]
                        payload = bytearray(resp.data[2:])
                        cf_idx  = 1
                        while time.monotonic() < deadline and len(payload) < length:
                            cf = self._bus.recv(timeout=0.05)
                            if cf and cf.arbitration_id == rx_id:
                                payload += bytearray(cf.data[1:])
                                cf_idx  += 1
                        return _FakeMsg(rx_id, bytes(payload[:length]))
                    return resp  # single-frame — backward compatible
        except Exception as e:
            self.error.emit(str(e))
        return None

    def _scan_pids(self):
        self.status.emit("Scanning OBD-II PIDs…")
        for pid, (name, scale, unit) in OBD2_PIDS.items():
            if not self._running:
                break
            if pid == 0x00:
                continue
            data   = bytes([0x02, 0x01, pid, 0x00, 0x00, 0x00, 0x00, 0x00])
            resp   = self._send_and_recv(data)
            if resp is None:
                continue
            raw = resp.data
            if len(raw) < 4 or raw[1] != 0x41 or raw[2] != pid:
                continue
            try:
                a = raw[3]
                b = raw[4] if len(raw) > 4 else 0
                if pid == 0x05 or pid == 0x0F:
                    value = float(a) + float(scale)
                elif pid == 0x0C:
                    value = ((a * 256 + b) * 0.25)
                else:
                    value = float(a) * (scale if isinstance(scale, float) else 1.0)
                self.pid_result.emit(pid, name, round(value, 2), unit or "")
            except Exception:
                pass

    def _read_dtc(self):
        self.status.emit("Reading DTCs (service 0x19)…")
        data = bytes([0x03, 0x19, 0x02, 0xFF, 0x00, 0x00, 0x00, 0x00])
        resp = self._send_and_recv(data, timeout=0.5)
        dtcs = []
        if resp:
            raw = resp.data
            i   = 3
            while i + 1 < len(raw):
                hi, lo = raw[i], raw[i + 1]
                if hi == 0 and lo == 0:
                    break
                prefix = {0: "P", 1: "C", 2: "B", 3: "U"}[(hi >> 6) & 0x03]
                code   = f"{prefix}{(hi & 0x3F):02X}{lo:02X}"
                dtcs.append(code)
                i += 3
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
            resp = self._send_to(ecu_id, bytes([0x02, 0x3E, 0x00, 0, 0, 0, 0, 0]))
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
            self._send_to(ecu_id, bytes([0x02, 0x10, 0x03, 0, 0, 0, 0, 0]))
            time.sleep(0.1)

            # Read each DataIdentifier
            for did, did_name in UDS_DATA_IDS.items():
                if not self._running:
                    return
                hi = (did >> 8) & 0xFF
                lo = did & 0xFF
                resp = self._send_to(
                    ecu_id,
                    bytes([0x03, 0x22, hi, lo, 0, 0, 0, 0]),
                    timeout=0.3,
                )
                if resp and len(resp.data) >= 4:
                    raw = bytes(resp.data)
                    if raw[1] == 0x62:   # positive response
                        payload = raw[4:]
                        hex_str = payload.hex().upper()
                        try:
                            decoded = payload.decode("ascii", errors="replace").strip()
                        except Exception:
                            decoded = ""
                        self.ecu_result.emit(ecu_id, did_name, hex_str, decoded)
                time.sleep(0.05)

            # Return to default session
            self._send_to(ecu_id, bytes([0x02, 0x10, 0x01, 0, 0, 0, 0, 0]))
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
            resp = self._send_and_recv(
                bytes([0x02, svc_id, 0x00, 0, 0, 0, 0, 0]),
                timeout=0.15,
            )
            supported = False
            resp_data = b""
            if resp:
                raw = bytes(resp.data)
                # Not a "service not supported" negative response (0x7F xx 0x11)
                if not (len(raw) >= 3 and raw[1] == 0x7F and raw[3] == 0x11):
                    supported = True
                    resp_data = raw
            self.service_result.emit(
                FUNCTIONAL_REQUEST_ID, svc_id, supported, resp_data
            )
            svc_name = UDS_SERVICES.get(svc_id, f"0x{svc_id:02X}")
            status = "✓" if supported else "✗"
            self.status.emit(f"  {status} {svc_name}")
            time.sleep(0.05)
