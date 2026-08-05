from datetime import datetime





class FlowStatus:



    def __init__(self):


        self.running = False


        self.last_scan = None


        self.nodes = {}


        self.errors = []






    # =====================================================
    # ENGINE STATUS
    # =====================================================


    def start(self):


        self.running = True


        self.errors.clear()





    def stop(self):


        self.running = False







    # =====================================================
    # SCAN UPDATE
    # =====================================================


    def update_scan(self):


        self.last_scan = datetime.now()







    # =====================================================
    # NODE STATUS
    # =====================================================


    def node_ok(

            self,

            node_id

    ):


        self.nodes[node_id] = {


            "status": "OK",


            "time": datetime.now()


        }







    def node_error(

            self,

            node_id,

            error

    ):


        self.nodes[node_id] = {


            "status": "ERROR",


            "time": datetime.now(),


            "error": str(error)

        }




        self.errors.append({


            "node": node_id,


            "error": str(error),


            "time": datetime.now()


        })







    # =====================================================
    # EXPORT STATUS
    # =====================================================


    def get_status(self):


        return {


            "running": self.running,


            "last_scan": str(self.last_scan),


            "nodes": self.nodes,


            "errors": self.errors


        }







# Global status object


flow_status = FlowStatus()