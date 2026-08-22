# =====================================================
# SCADA_FLOW TAG REGISTRY SERVICE
#
# Keeps the database Tags table synchronized with the
# tag definitions coming from the Flow Designer.
# =====================================================

import json
import hashlib

from database import get_connection


class TagRegistry:

    _signature_cache = {}

    @staticmethod
    def _signature(definitions):
        try:
            payload = json.dumps(
                definitions,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False
            )
        except (TypeError, ValueError):
            payload = repr(definitions)

        return hashlib.sha1(
            payload.encode("utf-8")
        ).hexdigest()

    @staticmethod
    def sync(company_id, definitions):
        """
        Register/update tags defined by TagMapper.

        The database is synchronized only when the definitions actually
        change. This avoids repeated SELECT/UPDATE/COMMIT work on every
        realtime Flow scan while keeping the Flow Designer as source of truth.
        """

        if company_id is None or not definitions:
            return 0

        try:
            company_id = int(company_id)
        except (TypeError, ValueError):
            return 0

        signature = TagRegistry._signature(
            definitions
        )

        cache_key = company_id

        if TagRegistry._signature_cache.get(cache_key) == signature:
            return 0

        conn = get_connection()
        cursor = conn.cursor()
        changed = 0

        try:
            for definition in definitions:
                name = str(definition.get("name") or "").strip()
                register = definition.get("register")
                datatype = str(
                    definition.get("datatype")
                    or definition.get("data_type")
                    or "INT"
                ).strip()
                description = str(
                    definition.get("description") or ""
                ).strip()

                if not name or register is None:
                    continue

                try:
                    register = int(register)
                except (TypeError, ValueError):
                    continue

                cursor.execute(
                    """
                    SELECT TagID
                    FROM Tags
                    WHERE CompanyID = ?
                      AND LOWER(TagName) = LOWER(?)
                    LIMIT 1
                    """,
                    (company_id, name)
                )

                row = cursor.fetchone()

                if row:
                    cursor.execute(
                        """
                        UPDATE Tags
                        SET RegisterAddress = ?,
                            DataType = ?,
                            Description = ?
                        WHERE TagID = ?
                        """,
                        (
                            register,
                            datatype,
                            description,
                            row["TagID"]
                        )
                    )
                else:
                    cursor.execute(
                        """
                        INSERT INTO Tags
                        (
                            CompanyID,
                            TagName,
                            RegisterAddress,
                            DataType,
                            Description
                        )
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            company_id,
                            name,
                            register,
                            datatype,
                            description
                        )
                    )

                changed += 1

            conn.commit()
            TagRegistry._signature_cache[cache_key] = signature

        finally:
            cursor.close()
            conn.close()

        return changed
