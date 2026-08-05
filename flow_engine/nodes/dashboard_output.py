# =====================================================
# SCADA_FLOW DASHBOARD OUTPUT NODE
# LIVE TAG DISPLAY ENGINE
# =====================================================


from socket_manager import send_dashboard_data





class DashboardOutput:



    def __init__(self, config):


        self.config = config or {}



        self.widgets = self.config.get(

            "widgets",

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





        output = {

            "Online": True,

            "Tags": {}

        }





        if self.widgets:



            for widget in self.widgets:



                tag = widget.get(

                    "tag"

                )



                if tag in tags:



                    output["Tags"][tag] = tags[tag]






        else:


            output["Tags"] = tags







        try:



            send_dashboard_data(

                output

            )



            print()

            print(
                "DASHBOARD OUTPUT SENT"
            )

            print(
                output
            )

            print()



        except Exception as e:



            print(

                "DASHBOARD OUTPUT ERROR:",

                e

            )







        data["DashboardData"] = output





        return data