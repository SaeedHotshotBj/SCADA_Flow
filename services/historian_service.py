# =====================================================
# SCADA_FLOW HISTORIAN SERVICE
# TIME + TRIGGER STORAGE ENGINE
# =====================================================


import time

from database import insert_tag_value






class HistorianService:



    def __init__(self):


        self.time_memory = {}


        self.trigger_memory = {}






    # =====================================================
    # TIME CHECK
    # =====================================================


    def check_time(self, definition):


        name = definition.get(
            "name"
        )


        interval = definition.get(
            "interval",
            0
        )



        if not interval:


            return False





        now = time.time()



        last = self.time_memory.get(
            name,
            0
        )





        if now - last >= float(interval):


            self.time_memory[name] = now


            print(
                "TIME TRIGGER:",
                name,
                "interval:",
                interval
            )


            return True





        return False







    # =====================================================
    # TRIGGER CHECK
    # =====================================================


    def check_trigger(
            self,
            definition,
            registers
    ):


        trigger_register = definition.get(
            "trigger_register"
        )


        trigger_value = definition.get(
            "trigger_value"
        )





        if trigger_register is None:


            return False





        current = None





        if str(trigger_register) in registers:


            current = registers[str(trigger_register)]



        elif trigger_register in registers:


            current = registers[trigger_register]



        else:


            return False







        name = definition.get(
            "name"
        )





        previous = self.trigger_memory.get(
            name
        )



        self.trigger_memory[name] = current






        # rising edge 0 -> 1


        if previous == 0 and current == trigger_value:


            print(
                "TRIGGER EVENT:",
                name,
                "Register:",
                trigger_register,
                "Value:",
                current
            )


            return True





        return False










    # =====================================================
    # PROCESS TAG STORAGE
    # =====================================================


    def process(
            self,
            company_id,
            tags,
            definitions,
            registers
    ):



        written = 0






        for definition in definitions:




            name = definition.get(
                "name"
            )



            if name not in tags:


                continue





            value = tags[name]



            if value is None:


                continue







            mode = str(
                definition.get(
                    "storage",
                    "TIME"
                )
            ).upper()






            save = False





            if mode == "TIME":


                save = self.check_time(
                    definition
                )





            elif mode == "TRIGGER":


                save = self.check_trigger(
                    definition,
                    registers
                )









            if save:



                insert_tag_value(

                    company_id,

                    name,

                    value,

                    mode

                )


                print(
                    "HISTORIAN INSERT:",
                    name,
                    "=",
                    value,
                    "TIME:",
                    time.strftime("%Y-%m-%d %H:%M:%S")
                )


                written += 1





        return written