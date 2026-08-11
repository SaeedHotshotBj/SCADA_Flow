# ============================================================
# SCADA_FLOW
# TAG MAPPER NODE
#
# Converts absolute PLC register addresses into tags.
#
# Example:
#
# Register 135 -> voltage1
# Register 141 -> current1
#
# Configuration comes ONLY from Drawflow.
# ============================================================


class TagMapper:

    # ========================================================
    # INIT
    # ========================================================

    def __init__(self, config=None, *args, **kwargs):

        self.config = config or {}

        self.mappings = self.config.get(
            "mappings",
            []
        )

    # ========================================================
    # EXECUTE
    # ========================================================

    def execute(self, data=None):

        if data is None:

            data = {}

        # ----------------------------------------------------
        # Get register map from PLCReader
        # ----------------------------------------------------

        registers = data.get(
            "Registers",
            {}
        )

        # Compatibility with older lowercase output.
        if not registers:

            registers = data.get(
                "registers",
                {}
            )

        tags = {}

        # ----------------------------------------------------
        # Process editor mappings
        # ----------------------------------------------------

        for item in self.mappings:

            register = item.get(
                "register"
            )

            name = item.get(
                "name"
            )

            scale = item.get(
                "scale",
                1
            )

            if register is None:
                continue

            if not name:
                continue

            # ------------------------------------------------
            # Register address normalization
            # ------------------------------------------------

            register_key = str(
                register
            )

            # ------------------------------------------------
            # Find register value
            # ------------------------------------------------

            if register_key not in registers:

                continue

            value = registers[
                register_key
            ]

            # ------------------------------------------------
            # Apply scale
            # ------------------------------------------------

            try:

                value = (
                    float(value)
                    *
                    float(scale)
                )

            except (
                TypeError,
                ValueError
            ):

                pass

            # ------------------------------------------------
            # Store tag
            # ------------------------------------------------

            tags[name] = value

        # ----------------------------------------------------
        # Output tags
        # ----------------------------------------------------

        data["Tags"] = tags

        # ----------------------------------------------------
        # Keep definitions available for SQLWriter
        # ----------------------------------------------------

        data["TagDefinitions"] = self.mappings

        # ----------------------------------------------------
        # Log
        # ----------------------------------------------------

        print()
        print(
            "TAG MAPPER:"
        )
        print(
            tags
        )
        print()

        return data