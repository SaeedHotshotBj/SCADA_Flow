# ======================================
# SCADA FLOW SERVER CONFIGURATION
# ======================================


FLASK_CONFIG = {

    "SECRET_KEY": "SCADA_FLOW_SECRET_KEY"

}



SOCKETIO_CONFIG = {

    "cors_allowed_origins": "*"

}



FLOW_CONFIG = {

    "flow_file": "flow.json"

}



DB_CONFIG = {

    "path": "data/scada_flow.db"

}



PLC_CONFIG = {

    "PLC_MODEL": "Kinco K608",

    "PLC_IP": "192.168.1.100",

    "PLC_PORT": 502,

    "SLAVE_ID": 1

}



PLC_ID = 1



REGISTERS = {

    125: "Motor_Hour",

    128: "Mixer_Hour",

    131: "Press_Hour",

    135: "Voltage_12",

    136: "Voltage_13",

    137: "Voltage_23",

    138: "Voltage_L1",

    139: "Voltage_L2",

    140: "Voltage_L3",

    141: "Current_L1",

    142: "Current_L2",

    143: "Current_L3"

}