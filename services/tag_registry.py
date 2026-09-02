# =====================================================
# SCADA_FLOW TAG REGISTRY SERVICE
# PLC-aware TagMapper registry.
# =====================================================

import json
import hashlib

from database import get_connection


class TagRegistry:

    _signature_cache = {}

    @staticmethod
    def _signature(definitions):
        try:
            payload = json.dumps(definitions, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        except (TypeError, ValueError):
            payload = repr(definitions)
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def sync(company_id, definitions):
        if company_id is None or not definitions:
            return 0

        try:
            company_id = int(company_id)
        except (TypeError, ValueError):
            return 0

        signature = TagRegistry._signature(definitions)
        cache_key = company_id
        if TagRegistry._signature_cache.get(cache_key) == signature:
            return 0

        conn = get_connection()
        cursor = conn.cursor()
        changed = 0

        try:
            for definition in definitions:
                if not isinstance(definition, dict):
                    continue

                name = str(definition.get("name") or "").strip()
                register = definition.get("register")
                plc_id = definition.get("plc_id", definition.get("PLC_ID"))
                datatype = str(definition.get("datatype") or definition.get("data_type") or "INT").strip()
                description = str(definition.get("description") or "").strip()

                if not name or register is None or plc_id in (None, ""):
                    continue

                try:
                    register = int(register)
                    plc_id = int(plc_id)
                except (TypeError, ValueError):
                    continue

                cursor.execute("""
                    SELECT TagID
                    FROM Tags
                    WHERE CompanyID = ?
                      AND PLC_ID = ?
                      AND LOWER(TagName) = LOWER(?)
                    LIMIT 1
                """, (company_id, plc_id, name))
                row = cursor.fetchone()

                if row:
                    cursor.execute("""
                        UPDATE Tags
                        SET RegisterAddress = ?, DataType = ?, Description = ?
                        WHERE TagID = ?
                    """, (register, datatype, description, row["TagID"]))
                else:
                    cursor.execute("""
                        INSERT INTO Tags
                        (CompanyID, PLC_ID, TagName, RegisterAddress, DataType, Description)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (company_id, plc_id, name, register, datatype, description))

                changed += 1

            conn.commit()
            TagRegistry._signature_cache[cache_key] = signature
        finally:
            cursor.close()
            conn.close()

        return changed
