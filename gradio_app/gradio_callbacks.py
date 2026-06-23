import json
import pandas as pd
from SDS_functions import analyser_material_id, build_additional_json, send_additional_json, get_oracle_connection, envoyer_json, disconnect_from_CISPro_API

# Import H_CODES_DICT here from the notebook
H_CODES_DICT = None  # dynamically injected

def set_h_codes_dict(h_dict):
    global H_CODES_DICT
    H_CODES_DICT = h_dict


def get_material_id_list():
    try:
        conn = get_oracle_connection()
        with conn.cursor() as cursor:
            cursor.execute("""SELECT 
	c.MATERIALID , bd.filename, bd.blobdata, g.labelcodes, g.pictograms, g.signalword, g.classifications, c.physicalstate, c.ppe, c.STORAGEANDHANDLING, c.STORAGECONDITION, c.FLASHPOINT, c.BOILINGPOINT 
FROM 
	CISPRO.BLOB_DATA bd 
	JOIN cispro.SDSDOCUMENT s ON s.FILE1_BLOBID = bd.BLOBDATAID 
	JOIN cispro.CHEMICAL c ON c.NODEID = s.OWNER_ID
	JOIN cispro.GHS g ON g.MATERIAL_ID = c.NODEID
WHERE 
	s.ARCHIVED = 'N' AND TO_CHAR(s.DATECREATED, 'YYYY-MM-DD') = to_char((sysdate - 1), 'YYYY-MM-DD')
	AND s.OBSOLETE = 0 AND c.OBSOLETE = 0
ORDER BY 
	s.DATECREATED DESC""")
            rows = cursor.fetchall()
            return [row[0] for row in rows]
    except Exception as e:
        return [f"Erreur lors de la récupération : {e}"]
        
def analyser_liste_ids(id_list_text, h_codes_dict):
    lines = id_list_text.strip().splitlines()
    ids = [line.strip() for line in lines if line.strip()]
    results = []
    
    for material_id in ids:
        try:
            result_json, full_text = analyser_material_id(material_id, h_codes_dict)
            #status = "✅ OK"
            status = result_json
        except Exception as e:
            status = f"❌ {e}"
        results.append((material_id, status))
    
    return pd.DataFrame(results, columns=["Material ID", "Statut"])

def analyze_and_get_additional(mid, use_llm, model_name):
    from config import OPENROUTER_API_KEY
    result, full_text = analyser_material_id(mid, H_CODES_DICT)
    
    if result is None:
        return full_text, "❌ No additional JSON because of error"
        
    try:
        parsed = json.loads(result)
    except Exception as e:
        return result, f"❌ Error parsing JSON: {e}"
        
    try:
        additional_json = build_additional_json(parsed, full_text, model_name, OPENROUTER_API_KEY if use_llm else None)
        return result, json.dumps(additional_json, indent=4, ensure_ascii=False)
    except Exception as e:
        return result, f"❌ Error building additional JSON: {e}"

def send_additional_wrapper(username, password, additional_json):
    try:
        status = send_additional_json(username, password, additional_json)
        disconnect_from_CISPro_API()
        return status
    except Exception as e:
        return f"Error during sending additional data : {e}"

def send_wrapper(username, password, json_data):
    try:
        status = envoyer_json(username, password, json_data)
        disconnect_from_CISPro_API()
        return status
    except Exception as e:
        return f"Error during sending data : {e}"

