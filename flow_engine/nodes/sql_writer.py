# =====================================================
# SCADA_FLOW SQL WRITER NODE
# HISTORIAN STORAGE ENGINE
# TIME + TRIGGER BASED
# =====================================================


from services.historian_service import HistorianService
from services.tag_registry import TagRegistry


class SQLWriter:

    def __init__(self, config):

        self.config = config or {}

        self.company_id = self.config.get(
            "company_id",
            1
        )

        self.historian = HistorianService()

    # =====================================================
    # EXECUTE
    # =====================================================

    def execute(self, data=None):

        if data is None:
            data = {}

        tags = data.get(
            "Tags",
            {}
        )

        definitions = data.get(
            "TagDefinitions",
            []
        )

        registers = data.get(
            "Registers",
            {}
        )

        if not definitions:
            print(
                "SQL WRITER: NO DEFINITIONS"
            )
            return data

        # ----------------------------------------------------
        # Register/update every tag defined by TagMapper.
        # This only updates the Tags metadata table. Actual
        # historian storage remains controlled by HistorianService.
        # ----------------------------------------------------

        registered = TagRegistry.sync(
            self.company_id,
            definitions
        )

        print()
        print(
            "TAG REGISTRY:",
            registered,
            "TAGS SYNCHRONIZED"
        )
        print()

        print()
        print("SQL DEFINITIONS:")
        print(definitions)
        print()

        written = self.historian.process(
            self.company_id,
            tags,
            definitions,
            registers
        )

        print()
        print(
            "SQL WRITER:"
        )
        print(
            written,
            "VALUES INSERTED"
        )
        print()

        data["SQL_Written"] = written
        data["Tags_Registered"] = registered

        return data
