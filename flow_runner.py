import time
import traceback


from flow_engine.registry import get_node_class

from flow_status import flow_status






class FlowRunner:



    def __init__(self, flow_data):


        self.nodes = {}

        self.connections = {}

        self.running = True

        self.flow_data = flow_data


        self.load_flow()






    # =====================================================
    # CONFIG
    # =====================================================


    def get_node_config(self, node):


        data = node.get(

            "data",

            {}

        )


        if "config" in data:


            return data["config"]



        return data






    # =====================================================
    # LOAD FLOW
    # =====================================================


    def load_flow(self):


        flow = self.flow_data



        if "drawflow" in flow:



            print(

                "Loading Drawflow format"

            )



            home = flow["drawflow"]["Home"]["data"]





            for node_id,node in home.items():



                node_type = node.get(

                    "name"

                )



                config = self.get_node_config(

                    node

                )



                node_class = get_node_class(

                    node_type

                )



                if node_class:



                    self.nodes[str(node_id)] = {


                        "instance":

                            node_class(config),



                        "type":

                            node_type


                    }




                    print(

                        "Loaded Node:",

                        node_type

                    )






            for node_id,node in home.items():



                self.connections[str(node_id)] = []



                outputs = node.get(

                    "outputs",

                    {}

                )



                for output in outputs.values():



                    for connection in output.get(

                        "connections",

                        []

                    ):



                        self.connections[str(node_id)].append(

                            str(connection["node"])

                        )






        else:


            print(

                "Invalid flow format"

            )







    # =====================================================
    # START NODES
    # =====================================================


    def get_start_nodes(self):



        all_nodes = set(

            self.nodes.keys()

        )



        targets = set()



        for source,nodes in self.connections.items():



            for node in nodes:


                targets.add(node)




        return list(

            all_nodes-targets

        )







    # =====================================================
    # NEXT
    # =====================================================


    def next_nodes(self,node_id):


        return self.connections.get(

            str(node_id),

            []

        )







    # =====================================================
    # EXECUTE NODE
    # =====================================================


    def execute_node(self,node_id,data,visited=None):



        if visited is None:


            visited=set()



        node_id=str(node_id)





        if node_id in visited:



            return data




        visited.add(node_id)





        if node_id not in self.nodes:


            return data






        node = self.nodes[node_id]["instance"]






        try:



            result = node.execute(

                data

            )



            if result is not None:


                data=result





            flow_status.node_ok(

                node_id

            )





        except Exception as e:



            print()

            print(

                "NODE ERROR:",

                self.nodes[node_id]["type"]

            )

            print(e)



            traceback.print_exc()




            flow_status.node_error(

                node_id,

                e

            )






        children = self.next_nodes(

            node_id

        )





        for child in children:



            data = self.execute_node(

                child,

                data,

                visited.copy()

            )






        return data







    # =====================================================
    # REALTIME ENGINE
    # =====================================================


    def run(self):



        print()

        print(

            "FLOW ENGINE RUNNING"

        )

        print()



        flow_status.start()



        start_nodes = self.get_start_nodes()



        print(

            "START NODES:",

            start_nodes

        )







        while self.running:



            try:



                flow_status.update_scan()



                for node in start_nodes:



                    self.execute_node(

                        node,

                        {},

                        set()

                    )





            except Exception as e:



                print(

                    "FLOW ENGINE ERROR:",

                    e

                )



                traceback.print_exc()





            time.sleep(1)







    # =====================================================
    # TREND REQUEST ENGINE
    # =====================================================


    def execute_request(self, request):


        print()

        print(

            "TREND REQUEST:",

            request

        )

        print()



        result = request



        start_nodes = self.get_start_nodes()



        for node in start_nodes:



            result = self.execute_node(

                node,

                result,

                set()

            )



        return result







    # =====================================================
    # STOP
    # =====================================================


    def stop(self):


        self.running=False


        flow_status.stop()