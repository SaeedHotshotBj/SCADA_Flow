# =====================================================
# SCADA_FLOW EXPRESSION NODE
# CALCULATED TAG ENGINE
# =====================================================


class ExpressionNode:



    def __init__(self, config):


        self.config = config or {}


        self.expressions = self.config.get(

            "expressions",

            []

        )





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





        for item in self.expressions:



            name = item.get(

                "name"

            )



            expression = item.get(

                "expression"

            )





            if not name or not expression:


                continue






            try:



                result = eval(

                    expression,

                    {},

                    tags

                )



                tags[name] = result





            except Exception as e:



                print(

                    "EXPRESSION ERROR:",

                    name,

                    e

                )







        data["Tags"] = tags





        print()

        print(
            "EXPRESSION OUTPUT:"
        )

        print(
            tags
        )

        print()



        return data