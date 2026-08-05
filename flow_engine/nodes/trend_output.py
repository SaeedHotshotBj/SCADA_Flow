# =====================================================
# SCADA_FLOW TREND OUTPUT NODE
# =====================================================

import jdatetime



class TrendOutput:


    def __init__(self, config):

        self.config = config or {}



    # =====================================================
    # TIME CONVERSION
    # =====================================================

    def convert_time(self, value):

        try:

            if value is None:

                return None



            # SQL datetime object

            if hasattr(value, "strftime"):

                return value.strftime(
                    "%Y-%m-%dT%H:%M:%S"
                )



            value = str(value)



            # Jalali string

            if "/" in value:


                jalali = jdatetime.datetime.strptime(

                    value,

                    "%Y/%m/%d %H:%M:%S"

                )


                gregorian = jalali.togregorian()


                return gregorian.strftime(

                    "%Y-%m-%dT%H:%M:%S"

                )



            return str(value)



        except Exception as e:


            print(
                "TIME CONVERSION ERROR:",
                e
            )


            return str(value)





    # =====================================================
    # EXECUTE
    # =====================================================

    def execute(self, data=None):


        if data is None:

            data = {}



        trend_data = data.get(

            "TrendData",

            []

        )



        request = data.get(

            "TrendRequest",

            {}

        )



        selected_tag = request.get(

            "Tag"

        )



        if not selected_tag:


            tags = request.get(

                "Tags",

                []

            )


            if len(tags) == 1:

                selected_tag = tags[0]




        output = []

        points = []




        # =============================================
        # SELECTED TAG
        # =============================================

        for item in trend_data:


            item_tag = item.get(

                "Tag"

            )


            if selected_tag and item_tag != selected_tag:

                continue



            points.append(

                {

                    "x": self.convert_time(

                        item.get(
                            "Timestamp"
                        )

                    ),

                    "y": float(

                        item.get(

                            "Value",

                            0

                        )

                    )

                }

            )





        if selected_tag:


            output.append(

                {

                    "tag": selected_tag,

                    "title": selected_tag,

                    "data": points

                }

            )



        else:


            grouped = {}



            for item in trend_data:


                tag = item.get(

                    "Tag"

                )


                if not tag:

                    continue



                grouped.setdefault(

                    tag,

                    []

                ).append(

                    {

                        "x": self.convert_time(

                            item.get(
                                "Timestamp"
                            )

                        ),

                        "y": float(

                            item.get(

                                "Value",

                                0

                            )

                        )

                    }

                )



            if grouped:


                first_tag = list(

                    grouped.keys()

                )[0]



                output.append(

                    {

                        "tag": first_tag,

                        "title": first_tag,

                        "data": grouped[first_tag]

                    }

                )





        data["ChartData"] = {


            "datasets": output


        }




        print()

        print(
            "========== TREND OUTPUT DEBUG =========="
        )

        print(
            "Selected tag:",
            selected_tag
        )

        print(
            "Datasets:",
            len(output)
        )

        print(
            "Points:",
            len(points)
        )

        if points:

            print(
                "FIRST POINT:",
                points[0]
            )


        print(
            "========================================"
        )

        print()



        return data