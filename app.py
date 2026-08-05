import json
import threading
import os
import time


from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    send_file
)



from extensions import socketio

from dashboard_data import get_flow_tags

from config import (

    FLASK_CONFIG,

    SOCKETIO_CONFIG,

    FLOW_CONFIG

)



from flow_runner import FlowRunner


from flow_engine.node_registry import NODE_REGISTRY


from socket_manager import init_socketio


from services.dashboard_service import get_dashboard_widgets


from database import (
    get_connection,
    get_company_flow,
    get_trend_data,
    cleanup_old_trend_data,
    init_database,
    get_latest_tag_values,
)







# =====================================================
# FLASK APPLICATION
# =====================================================


app = Flask(__name__)

app.config["SECRET_KEY"] = FLASK_CONFIG["SECRET_KEY"]

init_database()


flow_runner_instance = None
trend_runtime_tags = []




# =====================================================
# SOCKET.IO INITIALIZATION
# =====================================================


socketio.init_app(

    app,

    cors_allowed_origins=

        SOCKETIO_CONFIG["cors_allowed_origins"]

)



init_socketio(socketio)









# =====================================================
# LOAD FLOW
# =====================================================


def _read_flow_file():

    flow_path = FLOW_CONFIG["flow_file"]

    if not os.path.isabs(flow_path):
        flow_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            flow_path,
        )

    if not os.path.exists(flow_path):
        return None

    with open(flow_path, encoding="utf-8") as f:
        return f.read()


def get_flow_data(company_id=1):

    flow_json = get_company_flow(company_id)

    if not flow_json:
        flow_json = _read_flow_file()

    if not flow_json:
        return None

    return json.loads(flow_json)


def load_flow(company_id=1):


    try:


        flow = get_flow_data(company_id)

        if not flow:

            print(

                "NO FLOW FOUND FOR COMPANY:",

                company_id

            )

            return None



        print(

            "FLOW LOADED"

        )



        return flow





    except Exception as e:


        print(

            "DATABASE FLOW LOAD ERROR:",

            e

        )


        return None







# =====================================================
# FLOW ENGINE THREAD
# =====================================================


def start_flow_engine():



    print(

        "FLOW ENGINE STARTING"

    )

    from database import cleanup_old_trend_data

    cleanup_old_trend_data()


    company_id = 1


    flow = load_flow(

        company_id

    )




    if flow is None:



        print(

            "NO FLOW LOADED"

        )


        return






    global flow_runner_instance


    flow_runner_instance = FlowRunner(flow)


    flow_runner_instance.run()










# =====================================================
# SOCKET.IO EVENTS
# =====================================================


@socketio.on("connect")

def socket_connect():



    print(

        "Dashboard Connected"

    )






@socketio.on("disconnect")

def socket_disconnect():



    print(

        "Dashboard Disconnected"

    )








@app.route("/date_filter")
def date_filter_page():

    return render_template(
        "date_filter.html"
    )




@app.route("/trend_config")
def trend_config():

    result = {

        "calendar": "Gregorian",

        "date_picker": "GregorianPicker",

        "tags": []

    }


    flow_json = get_company_flow(1)

    if not flow_json:
        flow_json = _read_flow_file()

    if not flow_json:

        return jsonify(result)


    try:

        flow = json.loads(flow_json)


        nodes = flow["drawflow"]["Home"]["data"]


        for node in nodes.values():


            if node.get("name") != "TrendOutput":

                continue


            config = node.get(
                "data",
                {}
            )


            result["date_picker"] = config.get(
                "DatePicker",
                "GregorianPicker"
            )


            if result["date_picker"] == "JalaliPicker":

                result["calendar"] = "Jalali"


            else:

                result["calendar"] = "Gregorian"



        # GET TREND TAGS FROM TAGMAPPER

        for node in nodes.values():


            if node.get("name") != "TagMapper":

                continue


            mappings = node.get(
                "data",
                {}
            ).get(
                "mappings",
                []
            )


            for item in mappings:


                if item.get("storage","").upper() == "TIME":


                    result["tags"].append(

                        {
                            "tag": item.get("name"),
                            "title": item.get("name"),
                            "unit": item.get("unit","")
                        }

                    )



            break



    except Exception as e:


        print(
            "TREND CONFIG ERROR:",
            e
        )



    return jsonify(result)






@app.route("/trend_tags")
def trend_tags():

    tags = []

    try:

        flow = get_flow_data(1)

        if not flow:
            return jsonify([])


        nodes = flow["drawflow"]["Home"]["data"]


        for node in nodes.values():


            if node.get("name") != "TagMapper":
                continue


            mappings = node.get(
                "data",
                {}
            ).get(
                "mappings",
                []
            )


            for item in mappings:


                name = item.get("name")


                if not name:
                    continue



                storage = item.get(
                    "storage",
                    ""
                )



                # ONLY TIME TAGS FOR TREND
                if storage.upper() != "TIME":

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



        print(
            "TREND TAGS:",
            tags
        )


        return jsonify(tags)



    except Exception as e:

        print(
            "TREND TAG ERROR:",
            e
        )

        return jsonify([])








@app.route("/trend")
def trend():

    return render_template(
        "trend.html"
    )




@app.route("/flow_trend", methods=["POST"])
def flow_trend():

    try:

        flow_json = get_company_flow(1)

        flow = json.loads(flow_json)

        runner = FlowRunner(flow)


        request_data = request.get_json() or {}


        result = runner.execute_request(
            request_data
        )


        return jsonify(
            result.get(
                "ChartData",
                {
                    "datasets":[]
                }
            )
        )


    except Exception as e:

        print(
            "FLOW TREND ERROR:",
            e
        )

        return jsonify(
            {
                "datasets":[]
            }
        )





@app.route("/trend_request", methods=["POST"])
def trend_request():

    try:

        data = request.get_json() or {}

        print()
        print("TREND REQUEST RECEIVED:")
        print(data)
        print()


        tag = data.get("tag")

        start = data.get("start")

        end = data.get("end")

        print("START RECEIVED =", start)
        print("END RECEIVED =", end)

        calendar = data.get(
            "calendar",
            "Gregorian"
        )


        if not tag:

            return jsonify({
                "datasets":[]
            })



        flow = get_flow_data(1)

        if not flow:

            return jsonify({
                "datasets":[]
            })


        runner = FlowRunner(flow)



        request_data = {

            "TrendRequest":{

                "Tag": tag,

                "Tags":[tag],

                "Start": start,

                "End": end,

                "Calendar": calendar,

                "DatePicker":
                    "JalaliPicker" if calendar == "Jalali"
                    else "GregorianPicker"

            }

        }



        result = runner.execute_request(
            request_data
        )



        chart_data = result.get(
            "ChartData",
            {
                "datasets":[]
            }
        )



        # limit chart points

        for ds in chart_data.get("datasets",[]):

            points = ds.get("data",[])


            if len(points)>2000:

                step = len(points)//2000

                ds["data"] = points[::step]




        print()

        print(
            "FINAL CHART DATASETS:",
            len(chart_data["datasets"])
        )


        for ds in chart_data["datasets"]:

            print(
                ds["tag"],
                len(ds["data"])
            )


        print()



        return jsonify(chart_data)



    except Exception as e:


        import traceback

        traceback.print_exc()


        return jsonify({

            "error":str(e)

        }),500




# =====================================================
# DASHBOARD LATEST VALUES
# =====================================================


@app.route("/dashboard/latest")
def dashboard_latest():

    widgets = get_dashboard_widgets()
    tag_names = [
        widget.get("tag")
        for widget in widgets
        if widget.get("tag")
    ]

    latest = get_latest_tag_values(1, tag_names)
    tags = {}

    for tag, item in latest.items():
        tags[tag] = item["value"]

    return jsonify({
        "Online": bool(tags),
        "Tags": tags,
        "Timestamps": {
            tag: item["timestamp"]
            for tag, item in latest.items()
        },
    })




# =====================================================
# DASHBOARD
# =====================================================


@app.route("/dashboard")

def dashboard():



    widgets = get_dashboard_widgets()



    return render_template(

        "dashboard.html",

        widgets=widgets

    )











# =====================================================
# HOME
# =====================================================


@app.route("/")

def home():



    widgets = get_dashboard_widgets()



    return render_template(

        "dashboard.html",

        widgets=widgets

    )









# =====================================================
# FLOW JSON
# =====================================================


@app.route("/flow.json")
def get_flow_json():


    try:


        flow_json = get_company_flow(1)


        if not flow_json:

            flow_json = _read_flow_file()


        if not flow_json:


            return jsonify({})


        return jsonify(

            json.loads(flow_json)

        )


    except Exception as e:


        print(
            "FLOW JSON LOAD ERROR:",
            e
        )


        return jsonify({})









# =====================================================
# FLOW EDITOR
# =====================================================


@app.route("/flow")

def flow_editor():



    return render_template(

        "flow_editor.html"

    )









# =====================================================
# NODE REGISTRY API
# =====================================================


@app.route("/node_registry")

def node_registry():



    return jsonify(

        NODE_REGISTRY

    )









# =====================================================
# SAVE FLOW
# =====================================================


@app.route("/save_flow", methods=["POST"])
def save_flow():


    try:


        data = request.get_json()



        print("\n========== FLOW RECEIVED ==========")


        print(
            json.dumps(
                data,
                indent=4,
                ensure_ascii=False
            )
        )


        print(
            "===================================\n"
        )




        if not data:


            return jsonify(

                {
                    "status":"error",
                    "message":"No flow data received"

                }

            ),400

        # SAVE ALSO TO LOCAL JSON FILE

        try:

            with open(
                FLOW_CONFIG["flow_file"],
                "w",
                encoding="utf-8"
            ) as f:

                json.dump(
                    data,
                    f,
                    indent=4,
                    ensure_ascii=False
                )


            print(
                "FLOW SAVED TO JSON FILE"
            )


        except Exception as e:

            print(
                "JSON SAVE ERROR:",
                e
            )





        flow_json = json.dumps(

            data,

            ensure_ascii=False

        )




        company_id = 1





        conn = get_connection()


        cursor = conn.cursor()





        # Check existing flow


        cursor.execute(

            """

            SELECT FlowID

            FROM Flows

            WHERE CompanyID=?

            """,

            (company_id,)

        )



        row = cursor.fetchone()





        if row:


            print(
                "Updating existing flow"
            )



            cursor.execute(

                """

                UPDATE Flows

                SET

                    FlowJson=?,

                    LastModified=datetime('now', 'localtime')


                WHERE CompanyID=?


                """,

                (

                    flow_json,

                    company_id

                )

            )




        else:


            print(
                "Creating new flow"
            )



            cursor.execute(

                """

                INSERT INTO Flows

                (

                    CompanyID,

                    FlowJson,

                    LastModified

                )

                VALUES

                (

                    ?,

                    ?,

                    datetime('now', 'localtime')

                )

                """,

                (

                    company_id,

                    flow_json

                )

            )






        conn.commit()



        cursor.close()


        conn.close()





        print(

            "FLOW SAVED SUCCESSFULLY"

        )




        return jsonify(

            {

                "status":"ok",

                "message":"Flow saved"

            }

        )







    except Exception as e:



        import traceback


        traceback.print_exc()



        return jsonify(

            {

                "status":"error",

                "message":str(e)

            }

        ),500







# =====================================================
# TREND CLEANUP THREAD
# =====================================================

def trend_cleanup_worker():

    while True:

        try:

            cleanup_old_trend_data()

        except Exception as e:

            print(
                "TREND CLEANUP ERROR:",
                e
            )


        # run once every 24 hours

        time.sleep(86400)








# =====================================================
# START APPLICATION
# =====================================================


if __name__ == "__main__":


    cleanup_thread = threading.Thread(
        target=trend_cleanup_worker,
        daemon=True
    )

    cleanup_thread.start()


    engine_thread = threading.Thread(

        target=start_flow_engine,

        daemon=True

    )



    engine_thread.start()






    print(

        "SCADA_FLOW STARTED"

    )





    socketio.run(

        app,

        host=os.environ.get("SCADA_HOST", "0.0.0.0"),

        port=int(os.environ.get("SCADA_PORT", "5000")),

        allow_unsafe_werkzeug=True,

    )