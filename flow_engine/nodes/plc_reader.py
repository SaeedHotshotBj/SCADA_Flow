# =====================================================
# SCADA_FLOW PLC READER
# EDGE-FIRST PLC DATA SOURCE
# =====================================================

import os
import time

try:
    from pymodbus.client import ModbusTcpClient
except Exception:
    ModbusTcpClient = None

from database import get_edge_latest_registers


class PLCReader:
    """Read PLC data from EDGE and never manufacture zero values for the dashboard."""

    def __init__(self, node=None, config=None):
        self.node = node or {}
        self.config = config or {}

    def _get_config(self, key, default=None):
        data = self.node.get("data", {}) if isinstance(self.node, dict) else {}
        if key in data:
            return data.get(key)
        return self.config.get(key, default)

    def _edge_timeout(self):
        try:
            return float(os.environ.get("SCADA_EDGE_TIMEOUT", "5"))
        except Exception:
            return 5.0

    def _get_company_id(self):
        value = self._get_config("company_id")
        if value in (None, ""):
            value = self._get_config("CompanyID")
        try:
            return int(value) if value not in (None, "") else None
        except Exception:
            return None

    def _read_edge_registers(self, register, count):
        company_id = self._get_company_id()
        if company_id is None:
            return {}

        try:
            registers = get_edge_latest_registers(company_id, register, count)
        except Exception as exc:
            print("PLCReader EDGE READ ERROR:", exc)
            return {}

        if not registers:
            return {}

        return registers

    def _read_direct_modbus(self, ip, port, slave, register, count):
        if ModbusTcpClient is None:
            return {}

        client = ModbusTcpClient(ip, port=port)
        try:
            if not client.connect():
                return {}
            result = client.read_holding_registers(
                address=register,
                count=count,
                slave=slave,
            )
            if result.isError():
                return {}
            return {
                register + index: value
                for index, value in enumerate(result.registers)
            }
        except Exception as exc:
            print("PLCReader MODBUS ERROR:", exc)
            return {}
        finally:
            try:
                client.close()
            except Exception:
                pass

    def execute(self, data=None):
        if data is None:
            data = {}

        ip = self._get_config("ip")
        port = self._get_config("port")
        slave = self._get_config("slave")
        register = self._get_config("register")
        count = self._get_config("count")

        required = {"ip": ip, "port": port, "slave": slave, "register": register, "count": count}
        missing = [name for name, value in required.items() if value is None or value == ""]
        if missing:
            raise ValueError("PLCReader configuration is incomplete. Missing: " + ", ".join(missing))

        try:
            port = int(port)
            slave = int(slave)
            register = int(register)
            count = int(count)
        except (TypeError, ValueError) as exc:
            raise ValueError("PLCReader configuration contains invalid numeric values.") from exc

        if port <= 0 or slave < 0 or register < 0 or count <= 0:
            raise ValueError("Invalid PLCReader numeric configuration.")

        # EDGE is the normal source. A temporary missing/late EDGE sample is
        # represented by an empty result, NOT by zero values. This prevents
        # the dashboard from alternating between the real value and zero.
        registers = self._read_edge_registers(register, count)

        if registers:
            print("PLC READER: EDGE DATA")
            print(f"Company: {self._get_company_id()}")
            print(f"Registers Available: {len(registers)}")
        elif os.environ.get("SCADA_DIRECT_MODBUS", "0").strip().lower() in {"1", "true", "yes", "on"}:
            registers = self._read_direct_modbus(ip, port, slave, register, count)
        else:
            print("PLC READER: WAITING FOR EDGE DATA")

        result = dict(data)
        result["PLC"] = {
            "ip": ip,
            "port": port,
            "slave": slave,
            "register": register,
            "count": count,
        }
        result["Registers"] = registers
        result["registers"] = registers
        return result
