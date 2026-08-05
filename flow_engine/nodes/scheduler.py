import threading
import time





class Scheduler:



    def __init__(self, config):

        self.config = config

        self.running = False






    def execute_task(self):


        print(

            "Scheduled task executed"

        )



        # Future:
        #
        # trigger another node
        #
        # run report
        #
        # write PLC command
        #
        # etc.





    def loop(self):


        interval = self.config.get(

            "interval",

            60

        )



        while self.running:



            time.sleep(

                interval

            )


            self.execute_task()








    def execute(

        self,

        data=None

    ):



        print(

            "Scheduler Started"

        )



        if not self.running:



            self.running = True



            thread = threading.Thread(

                target=self.loop,

                daemon=True

            )


            thread.start()






        return data