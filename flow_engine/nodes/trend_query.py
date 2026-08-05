# =====================================================
# SCADA_FLOW TREND QUERY NODE
# =====================================================

from database import get_trend_data, row_value



class TrendQuery:


    def __init__(self, config):

        self.config = config



    # =====================================================
    # EXECUTE
    # =====================================================

    def execute(self, data=None):


        if data is None:

            data = {}



        request = data.get(

            "TrendRequest",

            {}

        )



        dates = data.get(

            "ConvertedDate",

            {}

        )



        start = dates.get(

            "Start"

        )


        end = dates.get(

            "End"

        )



        company_id = self.config.get(

            "company_id",

            1

        )



        tags = request.get(

            "Tags",

            []

        )



        result = {}



        for tag in tags:



            rows = get_trend_data(

                company_id,

                tag,

                start,

                end

            )



            values = []


            for row in rows:


                values.append(

                    {

                        "Timestamp":

                            row_value(row, "Timestamp", 0),


                        "Value":

                            float(row_value(row, "Value", 1))

                    }

                )



            result[tag] = values





        data["TrendResult"] = result



        print()

        print("==============================")

        print("TREND QUERY")

        print(result)

        print("==============================")

        print()



        return data