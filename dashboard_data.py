# =====================================================
# SCADA_FLOW DASHBOARD DATA
# FLOW BASED TAG READER
# =====================================================


import json

from database import get_company_flow



# =====================================================
# GET TAGS FROM FLOW
# =====================================================

def get_flow_tags(company_id=1):


    tags = []



    try:


        flow_json = get_company_flow(company_id)



        if not flow_json:

            return tags



        flow = json.loads(flow_json)



        nodes = (

            flow

            .get("drawflow",{})

            .get("Home",{})

            .get("data",{})

        )



        for node in nodes.values():



            if node.get("name") != "TagMapper":

                continue



            mappings = (

                node

                .get("data",{})

                .get("mappings",[])

            )



            for item in mappings:



                name = item.get("name")



                if not name:

                    continue



                tags.append(

                    {

                        "tag": name,

                        "title": name,

                        "unit": item.get(

                            "unit",

                            ""

                        )

                    }

                )



            break




    except Exception as e:


        print(

            "FLOW TAG ERROR:",

            e

        )



    return tags