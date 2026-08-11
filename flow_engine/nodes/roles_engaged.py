# =====================================================
# SCADA_FLOW
# ROLES ENGAGED NODE
# =====================================================

class RolesEngaged:

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

            "company_id":
                self.company_id,

            "allowed_roles":
                self.get_allowed_roles(),

            "data":
                data

        }

    # =================================================
    # GET ALLOWED ROLES
    # =================================================

    def get_allowed_roles(self):

        result = []

        for item in self.roles:

            if isinstance(item, str):

                role = item.strip()

                if role:
                    result.append(role)

                continue

            if isinstance(item, dict):

                role = str(
                    item.get(
                        "role",
                        ""
                    )
                ).strip()

                if role:
                    result.append(role)

        return list(
            dict.fromkeys(result)
        )

    # =================================================
    # ROLE ALLOWED
    # =================================================

    def is_allowed(self, role):

        if not role:
            return False

        allowed = self.get_allowed_roles()

        return any(
            str(item).strip().lower()
            ==
            str(role).strip().lower()
            for item in allowed
        )