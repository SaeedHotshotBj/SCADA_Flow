# =====================================================
# SCADA_FLOW DASHBOARD DATA
# FLOW BASED TAG READER / MULTI-COMPANY / MULTI-PLC
# =====================================================

import json
from database import get_company_flow, get_connection


def get_flow_tags(company_id):
    tags=[]
    plc_names={}
    try:
        conn=get_connection()
        try:
            for row in conn.execute("SELECT PLC_ID,PLC_Name FROM PLCs WHERE CompanyID=?",(company_id,)).fetchall():
                plc_names[int(row["PLC_ID"])]=str(row["PLC_Name"] or "PLC %s" % row["PLC_ID"])
        finally: conn.close()
        flow_json=get_company_flow(company_id)
        if not flow_json:return tags
        nodes=json.loads(flow_json).get("drawflow",{}).get("Home",{}).get("data",{})
        for node in nodes.values():
            if node.get("name")!="TagMapper":continue
            mappings=node.get("data",{}).get("mappings",[])
            for item in mappings if isinstance(mappings,list) else []:
                if not isinstance(item,dict) or not item.get("name"):continue
                try: plc_id=int(item.get("plc_id",item.get("PLC_ID")))
                except (TypeError,ValueError): plc_id=None
                tags.append({"tag":item["name"],"title":item.get("title",item["name"]),"unit":item.get("unit",""),"PLC_ID":plc_id,"plc_id":plc_id,"PLC_Name":plc_names.get(plc_id,"PLC %s"%plc_id if plc_id is not None else "PLC"),"register":item.get("register")})
            break
    except Exception as exc: print("FLOW TAG ERROR:",exc)
    return tags


def get_flow_roles(company_id):
    roles=[]
    try:
        flow_json=get_company_flow(company_id)
        if not flow_json:return roles
        nodes=json.loads(flow_json).get("drawflow",{}).get("Home",{}).get("data",{})
        for node in nodes.values():
            if node.get("name")!="Roles":continue
            for item in node.get("data",{}).get("roles",[]) or []:
                role=str(item.get("role","")).strip(); username=str(item.get("username","")).strip()
                if role and username:roles.append({"role":role,"username":username})
    except Exception as exc:print("FLOW ROLE ERROR:",exc)
    return roles


def get_flow_role_names(company_id):
    return list(dict.fromkeys(item["role"] for item in get_flow_roles(company_id)))

try:
    from services.edge_timeout_service import start_worker as start_edge_timeout_worker
    start_edge_timeout_worker()
except Exception as exc:
    print("EDGE TIMEOUT START ERROR FROM DASHBOARD DATA:",exc)
