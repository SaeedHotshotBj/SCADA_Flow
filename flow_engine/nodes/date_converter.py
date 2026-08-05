# =====================================================
# SCADA_FLOW DATE CONVERTER NODE
# FINAL TREND VERSION
# =====================================================

import jdatetime
from datetime import datetime



class DateConverterNode:


    def __init__(self, config=None):

        self.config = config or {}

        self.direction = self.config.get(
            "direction",
            "G2J"
        )



    # =====================================================
    # JALALI -> GREGORIAN
    # =====================================================

    def j2g(self, value):

        if not value:

            return None



        if isinstance(value, datetime):

            return value



        if isinstance(value, str):

            value = (
                value
                .replace(
                    "۰","0"
                )
                .replace(
                    "۱","1"
                )
                .replace(
                    "۲","2"
                )
                .replace(
                    "۳","3"
                )
                .replace(
                    "۴","4"
                )
                .replace(
                    "۵","5"
                )
                .replace(
                    "۶","6"
                )
                .replace(
                    "۷","7"
                )
                .replace(
                    "۸","8"
                )
                .replace(
                    "۹","9"
                )
            )



            formats = [

                "%Y/%m/%d %H:%M:%S",

                "%Y/%m/%d %H:%M",

                "%Y-%m-%d %H:%M:%S",

                "%Y-%m-%d %H:%M"

            ]



            for fmt in formats:

                try:

                    result = jdatetime.datetime.strptime(
                        value,
                        fmt
                    )

                    return result.togregorian()


                except:

                    pass



        return None



    # =====================================================
    # GREGORIAN -> JALALI
    # =====================================================

    def g2j(self, value):

        if not value:

            return None



        if isinstance(value,str):

            formats = [

                "%Y-%m-%d %H:%M:%S",

                "%Y-%m-%d %H:%M"

            ]


            for fmt in formats:

                try:

                    value = datetime.strptime(
                        value,
                        fmt
                    )

                    break

                except:

                    pass



        result = jdatetime.datetime.fromgregorian(
            datetime=value
        )



        return result.strftime(
            "%Y/%m/%d %H:%M:%S"
        )



    # =====================================================
    # EXECUTE
    # =====================================================

    def execute(self,data=None):


        if data is None:

            data = {}



        request = data.get(
            "TrendRequest",
            {}
        )



        # =================================================
        # JALALI INPUT TO DATABASE
        # =================================================

        if self.direction == "J2G":



            if request.get("Calendar") == "Jalali":



                if request.get("Start"):


                    request["Start"] = self.j2g(
                        request["Start"]
                    )



                if request.get("End"):


                    request["End"] = self.j2g(
                        request["End"]
                    )



                request["Calendar"] = "Gregorian"



            data["TrendRequest"] = request



            return data




        # =================================================
        # DATABASE TIME TO JALALI OUTPUT
        # =================================================


        trend = data.get(
            "TrendData",
            []
        )



        converted = []



        for item in trend:



            converted.append(

                {

                    "Tag":
                    item.get(
                        "Tag"
                    ),


                    "Timestamp":
                    self.g2j(
                        item.get(
                            "Timestamp"
                        )
                    ),


                    "Value":
                    item.get(
                        "Value"
                    )

                }

            )



        data["TrendData"] = converted



        if "TrendRequest" not in data:

            data["TrendRequest"] = {}



        data["TrendRequest"]["Calendar"] = "Jalali"

        data["TrendRequest"]["DatePicker"] = "JalaliPicker"



        return data