# =====================================================
# SCADA_FLOW EDITOR NODE REGISTRY
# PROPERTY DEFINITIONS
# =====================================================


NODE_REGISTRY = {



# =====================================================
# PLC READER
# =====================================================


"PLCReader": {

    "config": [

        {
            "name": "ip",
            "label": "PLC IP",
            "type": "text"
        },

        {
            "name": "port",
            "label": "PLC Port",
            "type": "number"
        },

        {
            "name": "slave",
            "label": "Slave ID",
            "type": "number"
        },

        {
            "name": "register",
            "label": "Start Register",
            "type": "number"
        },

        {
            "name": "count",
            "label": "Register Count",
            "type": "number"
        }

    ]
},





# =====================================================
# TAG MAPPER
# =====================================================


"TagMapper": {


"config":[


{

"name":"mappings",

"label":"Tag Definitions",

"type":"table",


"columns":[


    {
        "name":"register",
        "label":"Register",
        "type":"number"
    },


    {
        "name":"name",
        "label":"Tag Name",
        "type":"text"
    },


    {
        "name":"datatype",
        "label":"Data Type",
        "type":"select",
        "options":[
            "FLOAT",
            "INT",
            "BOOL"
        ]
    },


    {
        "name":"scale",
        "label":"Scale",
        "type":"number"
    },


    {
        "name":"storage",
        "label":"Storage",
        "type":"select",
        "options":[
            "TIME",
            "TRIGGER"
        ]
    },


    {
        "name":"interval",
        "label":"Time Interval (sec)",
        "type":"number"
    },


    {
        "name":"trigger_register",
        "label":"Trigger Register",
        "type":"number"
    },


    {
        "name":"trigger_value",
        "label":"Trigger Value",
        "type":"number"
    }


]

}


]

},






# =====================================================
# EXPRESSION
# =====================================================


"ExpressionNode":{


"config":[


{

"name":"expressions",

"label":"Expressions",

"type":"table",


"columns":[


{
"name":"name",
"label":"Result Name",
"type":"text"
},


{
"name":"expression",
"label":"Expression",
"type":"text"
}


]

}


]


},








# =====================================================
# SQL WRITER
# =====================================================


"SQLWriter":{


"config":[


{

"name":"company_id",

"label":"Company ID",

"type":"number",

"default":1

}

]


},








# =====================================================
# DASHBOARD
# =====================================================


"DashboardOutput":{


"config":[


{

"name":"widgets",

"label":"Dashboard Widgets",

"type":"table",


"columns":[


{
"name":"tag",
"label":"Tag",
"type":"text"
},


{
"name":"title",
"label":"Title",
"type":"text"
},


{
"name":"unit",
"label":"Unit",
"type":"text"
}


]

}


]


},








# =====================================================
# ALARM
# =====================================================


"AlarmNode":{


"config":[


{

"name":"alarms",

"label":"Alarm Rules",

"type":"table",


"columns":[


{
"name":"tag",
"label":"Tag",
"type":"text"
},


{
"name":"condition",
"label":"Condition",
"type":"select",

"options":[
">",
"<",
"=="
]

},


{
"name":"limit",
"label":"Limit",
"type":"number"
},


{
"name":"message",
"label":"Message",
"type":"text"
}


]

}


]


},








# =====================================================
# TREND READER
# =====================================================


"TrendReader":{


"config":[


{

"name":"company_id",

"label":"Company ID",

"type":"number",

"default":1

}


]


},







# =====================================================
# TREND DATABASE READER
# =====================================================


"TrendDatabaseReader":{


"config":[


{

"name":"company_id",

"label":"Company ID",

"type":"number",

"default":1

}


]


},






# =====================================================
# TREND OUTPUT
# =====================================================


"TrendOutput":{


"config":[


{

"name":"DatePicker",

"label":"Date Type",

"type":"select",

"options":[

"GregorianPicker",

"JalaliPicker"

],

"default":"GregorianPicker"

}


]


},





# =====================================================
# DATE CONVERTER
# =====================================================


"DateConverter":{


"config":[


{

"name":"direction",

"label":"Direction",

"type":"select",

"options":[

"J2G",

"G2J"

],

"default":"G2J"

}


]


}



}