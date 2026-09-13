from database import get_connection


class ProductionContext:
    """Flow-defined production identity read from two PLC holding registers."""

    def __init__(self, config=None, *args, **kwargs):
        self.config = config or {}
        self.company_id = self._to_int(self.config.get("company_id"))

    @staticmethod
    def _to_int(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _register_value(registers, address):
        if address in (None, ""):
            return None
        keys = [str(address).strip()]
        try:
            keys.append(str(int(float(address))))
        except (TypeError, ValueError):
            pass
        for key in keys:
            if key in registers:
                return registers[key]
        return None

    @staticmethod
    def _numeric_code(value):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None

    def _resolve_code(self, table, column, raw_value):
        if self.company_id is None or raw_value in (None, ""):
            return None

        text = str(raw_value).strip()
        conn = get_connection()
        try:
            row = conn.execute(
                f"SELECT {column} AS Code FROM {table} "
                "WHERE CompanyID=? AND TRIM(CAST(" + column + " AS TEXT))=? "
                "ORDER BY rowid LIMIT 1",
                (self.company_id, text),
            ).fetchone()
            if row:
                return str(row["Code"]).strip()

            number = self._numeric_code(raw_value)
            if number is None:
                return None

            rows = conn.execute(
                f"SELECT {column} AS Code FROM {table} WHERE CompanyID=? ORDER BY rowid",
                (self.company_id,),
            ).fetchall()
            for candidate in rows:
                candidate_code = str(candidate["Code"]).strip()
                try:
                    if int(float(candidate_code)) == number:
                        return candidate_code
                except (TypeError, ValueError):
                    continue
            return None
        finally:
            conn.close()

    def _validate_pair(self, contract_code, product_code):
        if not contract_code or not product_code or self.company_id is None:
            return False
        conn = get_connection()
        try:
            row = conn.execute(
                """
                SELECT 1
                FROM Contracts c
                JOIN ContractProducts cp ON cp.ContractID=c.ContractID
                JOIN Products p ON p.ProductID=cp.ProductID
                WHERE c.CompanyID=?
                  AND LOWER(TRIM(c.ContractCode))=LOWER(TRIM(?))
                  AND LOWER(TRIM(p.ProductCode))=LOWER(TRIM(?))
                LIMIT 1
                """,
                (self.company_id, contract_code, product_code),
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    def execute(self, data=None):
        data = data or {}
        registers = data.get("Registers", {}) or data.get("registers", {}) or {}
        plc = data.get("PLC", {}) or {}
        runtime_plc_id = self._to_int(data.get("PLC_ID", plc.get("PLC_ID")))
        configured_plc_id = self._to_int(self.config.get("plc_id", self.config.get("PLC_ID")))

        if configured_plc_id is not None and runtime_plc_id is not None and configured_plc_id != runtime_plc_id:
            result = dict(data)
            result["ProductionContext"] = {"PLC_ID": runtime_plc_id, "ContractCode": None, "ProductCode": None}
            result["ProductionContextValid"] = False
            result["ProductionContextError"] = "ProductionContext PLC_ID does not match runtime PLC_ID."
            return result

        contract_raw = self._register_value(registers, self.config.get("contract_code_register"))
        product_raw = self._register_value(registers, self.config.get("product_code_register"))
        contract_code = self._resolve_code("Contracts", "ContractCode", contract_raw)
        product_code = self._resolve_code("Products", "ProductCode", product_raw)
        valid = self._validate_pair(contract_code, product_code)

        result = dict(data)
        tags = dict(result.get("Tags", {}) or {})
        if contract_code is not None:
            tags["ContractCode"] = contract_code
        if product_code is not None:
            tags["ProductCode"] = product_code

        result["Tags"] = tags
        result["ProductionContext"] = {
            "PLC_ID": runtime_plc_id if runtime_plc_id is not None else configured_plc_id,
            "ContractCode": contract_code,
            "ProductCode": product_code,
            "ContractRaw": contract_raw,
            "ProductRaw": product_raw,
        }
        result["ProductionContextValid"] = bool(valid)
        result["ProductionContextError"] = "" if valid else "PLC production context is not a valid contract/product pair."
        return result


__all__ = ["ProductionContext"]
