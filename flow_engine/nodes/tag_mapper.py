# =====================================================
# SCADA_FLOW TAG MAPPER NODE
# REGISTER -> TAG DEFINITIONS
# =====================================================


class TagMapper:


    def __init__(self, config):

        self.config = config or {}

        self.mappings = self.config.get(
            "mappings",
            []
        )



    # =====================================================
    # EXECUTE
    # =====================================================

    def execute(self, data=None):


        if data is None:

            data = {}



        registers = data.get(

            "Registers",

            {}

        )



        tags = {}




        for item in self.mappings:



            register = str(

                item.get(

                    "register"

                )

            )



            name = item.get(

                "name"

            )



            scale = item.get(

                "scale",

                1

            )



            if register not in registers:

                continue



            value = registers[register]



            try:

                value = float(value) * float(scale)


            except:

                pass



            tags[name] = value





        data["Tags"] = tags



        # send definitions to SQLWriter

        data["TagDefinitions"] = self.mappings





        print()

        print(
            "TAG MAPPER:"
        )

        print(
            tags
        )

        print()



        return data