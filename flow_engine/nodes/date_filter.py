import datetime



class DateFilterNode:


    def __init__(self, config):

        self.config = config



    # =====================================================
    # EXECUTE
    # =====================================================

    def execute(self, data=None):


        if data is None:

            data = {}



        calendar = self.config.get(

            "calendar",

            "Gregorian"

        )



        picker = self.config.get(

            "picker",

            "Gregorian"

        )



        start = self.config.get(

            "start",

            None

        )



        end = self.config.get(

            "end",

            None

        )



        data["DateFilter"] = {


            "Calendar":

                calendar,



            "Picker":

                picker,



            "Start":

                start,



            "End":

                end


        }



        print()


        print(
            "=============================="
        )


        print(
            "DATE FILTER NODE"
        )


        print(
            data["DateFilter"]
        )


        print(
            "=============================="
        )


        print()



        return data