class FlowConverter:



    def __init__(self, drawflow):

        self.drawflow = drawflow






    def convert(self):


        nodes = []

        connections = []



        data = (

            self.drawflow

            .get("drawflow",{})

            .get("Home",{})

            .get("data",{})

        )





        for node_id,node in data.items():



            node_data = node.get(

                "data",

                {}

            )




            nodes.append(

                {

                    "id":

                        node_id,


                    "type":

                        node_data.get(

                            "type",

                            node.get(

                                "name"

                            )

                        ),



                    "config":

                        node_data.get(

                            "config",

                            {}

                        )

                }

            )





            outputs = node.get(

                "outputs",

                {}

            )





            for output in outputs.values():



                connections_list = output.get(

                    "connections",

                    []

                )



                for connection in connections_list:



                    connections.append(

                        {

                            "source":

                                node_id,



                            "target":

                                str(

                                    connection["node"]

                                )

                        }

                    )







        return {


            "nodes":

                nodes,


            "connections":

                connections


        }