# =====================================================
# SCADA_FLOW TREND DATABASE READER NODE
# =====================================================

from datetime import datetime
import jdatetime

from database import get_trend_data, row_value



class TrendDatabaseReader:


    def __init__(self, config):

        self.config = config or {}

        self.company_id = self.config.get(
            "company_id",
            1
        )



    # =====================================================
    # DATE NORMALIZATION
    # =====================================================

    def normalize_date(self, value, calendar):


        if not value:

            return None



        try:


            # =============================================
            # JALALI INPUT
            # =============================================

            if calendar == "Jalali":


                value = value.replace(
                    "-",
                    "/"
                )


                jalali = jdatetime.datetime.strptime(

                    value,

                    "%Y/%m/%d %H:%M"

                )


                return jalali.togregorian()



            # =============================================
            # GREGORIAN INPUT
            # =============================================

            else:


                value = value.replace(

                    "T",

                    " "

                )


                return datetime.strptime(

                    value,

                    "%Y-%m-%d %H:%M"

                )



        except Exception as e:


            print(

                "DATE NORMALIZE ERROR:",

                e

            )


            return value



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



        selected_tag = request.get(

            "Tag"

        )



        tags = request.get(

            "Tags",

            []

        )



        # =============================================
        # SINGLE TAG PRIORITY
        # =============================================

        if selected_tag:


            tags = [

                selected_tag

            ]


        elif len(tags) == 1:


            selected_tag = tags[0]



        start = request.get(

            "Start"

        )


        end = request.get(

            "End"

        )



        calendar = request.get(

            "Calendar",

            "Gregorian"

        )



        # =============================================
        # NORMALIZE DATE BEFORE SQL QUERY
        # =============================================

        start = self.normalize_date(

            start,

            calendar

        )


        end = self.normalize_date(

            end,

            calendar

        )



        trend = []



        print()

        print(

            "TREND DATABASE READER"

        )

        print(

            "Company :",

            self.company_id

        )

        print(

            "Selected:",

            selected_tag

        )

        print(

            "Tags    :",

            tags

        )

        print(

            "Start   :",

            start

        )

        print(

            "End     :",

            end

        )

        print()



        for tag in tags:


            if not tag:

                continue



            try:


                rows = get_trend_data(

                    self.company_id,

                    tag,

                    start,

                    end

                )



                print(

                    tag,

                    "->",

                    len(rows),

                    "rows"

                )



                for row in rows:


                    trend.append(

                        {

                            "Tag": tag,

                            "Timestamp": row_value(row, "Timestamp", 0),

                            "Value": row_value(row, "Value", 1)

                        }

                    )



            except Exception as e:


                print(

                    "TREND DATABASE ERROR:",

                    e

                )




        # =============================================
        # RESTORE TREND REQUEST
        # =============================================

        data["TrendRequest"] = {


            "Tag": selected_tag,


            "Tags": tags,


            "Start": start,


            "End": end,


            "Calendar": calendar


        }



        data["TrendData"] = trend



        print()

        print(

            "TOTAL TREND POINTS:",

            len(trend)

        )

        print()



        return data