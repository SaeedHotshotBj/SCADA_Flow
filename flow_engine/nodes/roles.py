# =====================================================
# SCADA_FLOW
# ROLES NODE
# =====================================================

class Roles:

    def __init__(self, config=None):

        self.config = config or {}

        self.company_id = self.config.get(
            "company_id"
        )

        self.roles = self.config.get(
            "roles",
            []
        )

    # =================================================
    # EXECUTE
    # =================================================

    def execute(self, data=None):

        return {
            "company_id": self.company_id,
            "roles": self.get_roles(),
            "data": data
        }

    # =================================================
    # GET ROLES
    # =================================================

    def get_roles(self):

        result = []

        for item in self.roles:

            if isinstance(item, str):

                result.append({
                    "role": item
                })

                continue

            if not isinstance(item, dict):
                continue

            role = str(
                item.get("role", "")
            ).strip()

            if not role:
                continue

            result.append({

                "role": role,

                "username":
                    str(
                        item.get(
                            "username",
                            ""
                        )
                    ).strip(),

                "password":
                    item.get(
                        "password",
                        ""
                    ),

                "enabled":
                    bool(
                        item.get(
                            "enabled",
                            True
                        )
                    )

            })

        return result

    # =================================================
    # ROLE NAMES
    # =================================================

    def get_role_names(self):

        return [

            item["role"]

            for item in self.get_roles()

            if item.get("enabled", True)

        ]