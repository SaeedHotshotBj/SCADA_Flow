# =====================================================
# SCADA_FLOW ALARM NODE
# =====================================================


from database import get_connection
from datetime import datetime






def insert_alarm_safe(

        company_id,

        tag,

        value,

        message

):


    conn = get_connection()

    cursor = conn.cursor()



    cursor.execute(

        """

        INSERT INTO AlarmHistory

        (

            CompanyID,

            AlarmText,

            AlarmValue,

            Timestamp

        )

        VALUES

        (?,?,?,?)

        """,

        (

            company_id,

            message,

            value,

            datetime.now()

        )

    )



    conn.commit()

    conn.close()







class AlarmNode:



    def __init__(self, config):


        self.config = config or {}


        self.alarms = self.config.get(

            "alarms",

            []

        )


        self.memory = {}







    def execute(self, data=None):


        if data is None:

            data = {}



        tags = data.get(

            "Tags",

            {}

        )



        company_id = self.config.get(

            "company_id",

            1

        )





        for alarm in self.alarms:



            tag = alarm.get("tag")


            condition = alarm.get("condition")


            limit = alarm.get("limit")


            message = alarm.get(

                "message",

                "Alarm"

            )




            if tag not in tags:

                continue



            value = tags[tag]

            active = False




            try:


                if condition == ">":

                    active = value > float(limit)


                elif condition == "<":

                    active = value < float(limit)


                elif condition == "==":

                    active = value == float(limit)



            except:


                continue






            previous = self.memory.get(

                tag,

                False

            )



            self.memory[tag] = active





            if active and not previous:



                try:


                    insert_alarm_safe(

                        company_id,

                        tag,

                        value,

                        message

                    )


                    print(

                        "ALARM:",

                        message

                    )



                except Exception as e:


                    print(

                        "ALARM DATABASE ERROR:",

                        e

                    )





        return data