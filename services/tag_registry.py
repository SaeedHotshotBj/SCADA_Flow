# =====================================================
# SCADA_FLOW TAG REGISTRY SERVICE
#
# Keeps the database Tags table synchronized with the
# tag definitions coming from the Flow Designer.
# =====================================================

from database import get_connection


class TagRegistry:

    @staticmethod
    def sync(company_id, definitions):
        """
        Register/update tags defined by TagMapper.

        TagMapper is the source of truth for runtime tag
        definitions. This does not insert historian values;
        SQLWriter/HistorianService still controls storage.
        """

        if company_id is None or not definitions:
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

        finally:
            cursor.close()
            conn.close()

        return changed
