import re
import csv
import logging
import requests
import json
from io import BytesIO
import fitz
import pytesseract
from PIL import Image
import chardet
import oracledb
from config import (
    DB_USERNAME, DB_PASSWORD, DB_HOST, DB_PORT, DB_SERVICE,
    LOGIN_URL, LOGOUT_URL, DELETE_URL, UPLOAD_URL, UPLOAD_ADDITIONAL_URL, TSV_PATH, TESSERACT_PATH
)
from llm_extraction import extract_sds_data_via_llm
from section2_isolation import isolate_section_2, log_shadow_diff, CONFIDENCE_THRESHOLD
# get_euh_mode() is called, never imported as a value: the mode must be resolved
# at call time so that --euh-mode can override the .env setting.
from euh_codes import find_euh_codes, log_euh_findings, get_euh_mode

pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH

fitz.TOOLS.mupdf_display_errors(False)

# Global variable temporary
LAST_RESULT_JSON = None
CISPRO_TOKEN = None

"""
    List of functions

    connect_to_CISPro_api(username, password):
    disconnect_from_CISPro_API():

    get_oracle_connection():
    get_nodeid_from_material_id(connection, znumber):
    get_pdf_blob_from_db(connection, nodeid):

    perform_ocr_on_pdf(pdf_bytes):
    read_tsv_file(tsv_path):
    expand_hcodes(hcode_str):
    search_h_codes_in_pdf(text, h_codes_dict, nodeid):

    analyser_material_id(material_id, h_codes_dict):
    envoyer_json(username, password, json_data):

    build_additional_json(result_json, full_text=None, model_name=None, use_llm=False):
    send_additional_json(username, password, additional_json):
"""

def connect_to_CISPro_api(username, password):
    """
    Login to CISPro API and return bearer token
    
    Args:
        username (str): username CISPro
        password (str): password CISPro
    
    Returns:
        str: Bearer token if success
        None: if failed
    
    Raises:
        Exception: if connection error
    """
    global CISPRO_TOKEN

    login_data = {
        'client_id': 'foundation-hub',
        'username': username,
        'password': password
    }

    try:
        logging.info(f"🔐 Try to login to CISPro: {username}")
        login_response = requests.post(LOGIN_URL, json=login_data, verify=False)

        if login_response.status_code != 200:
            error_msg = f"API connection failed: {login_response.status_code} - {login_response.text}"
            logging.error(error_msg)
            raise Exception(error_msg)

        CISPRO_TOKEN = login_response.json().get('access_token')
        if not CISPRO_TOKEN:
            raise Exception(
            f"Bearer token not found in API response"
            f"Response: {login_response.text}"
        )

        logging.info("✅ Connection successful, token found")
        return CISPRO_TOKEN
        
    except Exception as e:
        logging.error(f"❌ Error during the connection : {e}")
        raise

def disconnect_from_CISPro_API():
    """
    logout the user from CISPro.

    Args:
        delete_url (str): endpoint URL of deleting session
    """
    global CISPRO_TOKEN

    if CISPRO_TOKEN:
        headers = {"Authorization": f"Bearer {CISPRO_TOKEN}"}
        try:
            response = requests.delete(DELETE_URL, headers=headers, verify=False)
            response.raise_for_status()
            logging.info("logout successful.")
        except requests.exceptions.RequestException as e:
            logging.error(f"Logout failed: {e}")
        finally:
            CISPRO_TOKEN = None
    else:
        logging.warning("No token found. Already disconnected ?")

# Oracle connection
def get_oracle_connection():
    return oracledb.connect(
        user=DB_USERNAME,
        password=DB_PASSWORD,
        dsn=f"{DB_HOST}:{DB_PORT}/{DB_SERVICE}"
    )

# Get nodeid
def get_nodeid_from_material_id(connection, znumber):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT NODEID FROM CISPRO.CHEMICAL WHERE MATERIALID = :Znumber",
            {'Znumber': znumber}
        )
        row = cursor.fetchone()
        if not row:
            raise ValueError("Material ID not found.")
        return int(row[0])

# Get PDF file from DB
def get_pdf_blob_from_db(connection, nodeid):
    """
    Get the BLOB PDF content from Id
    """
    nodeid = int(nodeid)

    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT 
                filename, blobdata 
            FROM 
                CISPRO.BLOB_DATA bd 
                JOIN cispro.SDSDOCUMENT s ON s.FILE1_BLOBID = bd.BLOBDATAID 
            WHERE 
                s.OWNER_ID = :nodeid AND s.ARCHIVED = 'N' AND ROWNUM = 1
            ORDER BY 
                s.DATECREATED DESC""", {'nodeid': nodeid})
        row = cursor.fetchone()
        if row:
            filename, blob_bytes = row[0], row[1].read()
            return filename, blob_bytes
        else:
            raise ValueError("PDF not found for nodeid =", nodeid)

def perform_ocr_on_pdf(pdf_bytes):
    pdf_file = BytesIO(pdf_bytes)
    doc = fitz.open(stream=pdf_file, filetype="pdf")
    
    full_text = ""
    for page in doc:
        # Convertir la page en image (pixmap)
        pix = page.get_pixmap(dpi=300)
        img = Image.open(BytesIO(pix.tobytes("png")))

        # OCR via pytesseract
        text = pytesseract.image_to_string(img, lang="eng")
        full_text += text + "\n"

    doc.close()
    return full_text

# Function to read H-codes and their corresponding values from a TSV file
def read_tsv_file(tsv_path):
    h_codes_dict = {}
    with open(tsv_path, mode='rb') as file:
        result = chardet.detect(file.read())
            
    with open(tsv_path, mode='r', encoding=result['encoding']) as tsv_file:
        #tsv_reader = csv.reader(tsv_file, delimiter='\t')
        tsv_reader = csv.DictReader(tsv_file, delimiter='\t')
        for row in tsv_reader:
            if row and row['H-Code'].startswith('H') and len(row) > 1:  # Ensure the row is not empty
                h_codes_dict[row['H-Code']] = {
                    'pictogram': row['CISPro pictogram'], 
                    'classification': row['CISPro classification'],
                    'signalWord_id': row['CISPro signal word Id'],
                    'GHS07_not_skin_eye' : row['CISPRO_exclamation_not_skin_eye_irritation'],
                    'P-Code': row.get('P-Code', '')
                }
    return h_codes_dict

# Function to re-write correctly the H code. For example, H302+312+332 => H302+H312+H332
def expand_hcodes(hcode_str):
    parts = hcode_str.split('+')
    return '+'.join([p if p.startswith('H') else 'H' + p for p in parts])

# Function to search for H-codes in a PDF file
def search_h_codes_in_pdf(text, h_codes_dict, nodeid, euh_codes_dict=None):
    """
    Match GHS H-codes (and, depending on the active EUH mode, EU CLP EUH statements)
    and build the main CISPro JSON.

    euh_codes_dict : optional EUH reference. When None the module-level cache of
                     euh_codes.py is used, so existing call sites need no change.

    EUH statements are supplemental information under CLP: no pictogram, no
    signal word. They only ever appear in `labelCodes`, and only when the
    active EUH mode is "on".
    """
    found_h_codes = {
        "labelCodes": "", "pictograms": "", "classifications" : "", "signalWord_id" : "", "jurisdiction_id": "", "material_id": "", "nodetypename": ""
    }
    h_codes_set = set()
    pictograms_set = set()
    classifications_set = set()
    signalwords_set = set()
    has_exclamation_not_skin_eye_irritation = False
    p_codes_set = set()

    # Minimal normalization
    normalized_text = re.sub(r'\s*\+\s*', '+', text)
    # Exemple de texte
    #normalized_text = """Le produit est classé : H302+312+332, H360F, H361fd, H319, H999, H302+999"""
    
    # All H-codes in the text
    #hcode_matches = re.findall(r'\bH\d{3}(?:\+(?:H)?\d{3})*\b', normalized_text)
    hcode_matches = re.findall(r'\bH\d{3}[a-zA-Z]{0,2}(?:\+(?:H)?\d{3}[a-zA-Z]{0,2})*\b', normalized_text)
    
    hcode_matches_canonized = [expand_hcodes(c) for c in hcode_matches]

    # EUH statements (EU CLP) — deliberately kept in their own set.
    # They carry no pictogram and no signal word, so they must never reach
    # pictograms_set / signalwords_set nor the GHS07 suppression rules below.
    # Matching runs on `text` and not on `normalized_text`, because the '+'
    # normalisation is meaningless for EUH codes (they never combine).
    euh_codes_set = set()
    if get_euh_mode() == "on":
        euh_codes_set, _ = find_euh_codes(text, euh_codes_dict)

    # To avoid duplicates
    found_hcode_set = set()

    for match in hcode_matches_canonized:
        if match in h_codes_dict and match not in found_hcode_set:
            found_hcode_set.add(match)
            h_codes_set.add(match)
            pictograms_set.add(h_codes_dict[match]['pictogram'])
            classifications_set.add(h_codes_dict[match]['classification'])
            signalwords_set.add(h_codes_dict[match]['signalWord_id'])
            if h_codes_dict[match].get('GHS07_not_skin_eye') == 'TRUE':
                has_exclamation_not_skin_eye_irritation = True
            pcode_raw = h_codes_dict[match].get('P-Code', '')
            if pcode_raw:
                for p in pcode_raw.split(','):
                    cleaned = p.strip()
                    if cleaned:
                        p_codes_set.add(cleaned)


    # No valid H-code found : return an 'empty' JSON to create a Jurisdiction without any H-code
    # A product can legitimately carry EUH statements and no H-code at all
    # (e.g. a solvent labelled only EUH019 / EUH066). In that case the signal
    # word stays 'none' — EUH statements never carry one — but the codes are
    # still reported. Note this branch must not fall through to the signal-word
    # priority loop below, which would end on int(None).
    if not h_codes_set:
        empty_result = {
            "signalWord_id": 332028,
            "jurisdiction_id": 31745,
            "material_id": int(nodeid),
            "nodetypename": "GHS"
         }
        if euh_codes_set:
            empty_result["labelCodes"] = ",".join(sorted(euh_codes_set))
        return empty_result

    # Rule for Signal Word
    # Define priority order for signalWord_id
    priority_order = ['41941', '41942', '327286', '332028']

    # Find the highest priority signalWord_id
    selected_signal_word_id = None
    for priority_id in priority_order:
        if priority_id in signalwords_set:
            selected_signal_word_id = priority_id
            break

    # Rules for pictograms
    # Rule 1 : if the skull and crossbones applies, the exclamation mark should not appear
    if 'Acute Toxicity (severe)' in pictograms_set and 'Irritant' in pictograms_set:
        pictograms_set.discard('Irritant')
        logging.info("Rule 1 applied: GHS07 removed due to GHS06 (skull and crossbones)")

    # Rule 2 : if the corrosive symbol applies, the exclamation mark should not appear where it is used for skin or eye irritation
    if 'Corrosive' in pictograms_set and 'Irritant' in pictograms_set and has_exclamation_not_skin_eye_irritation is not True:
        pictograms_set.discard('Irritant')
        logging.info("Rule 2 applied: GHS07 - only skin or eye irritation - removed due to GHS05")       

    # Rule 3 : if the health hazard symbol appears for respiratory sensitization, 
    # the exclamation mark should not appear where it is used for skin or for skin or eye irritation
    if 'Target Organ Toxicity' in pictograms_set and 'Irritant' in pictograms_set and has_exclamation_not_skin_eye_irritation is not True:
        pictograms_set.discard('Irritant')
        logging.info("Rule 3 applied: GHS07 - only skin or eye irritation - removed due to GHS08")       

    # Convert sets to comma-separated strings and construct JSON
    # EUH codes join labelCodes only; pictograms, classifications and the signal
    # word are built exclusively from the GHS H-codes above.
    found_h_codes = {
        "labelCodes": ",".join(sorted(h_codes_set | p_codes_set | euh_codes_set)),
        "pictograms": ",".join(filter(None, pictograms_set)),  # Filter out empty strings
        "classifications": ",".join(filter(None, classifications_set)),
        "signalWord_id": int(selected_signal_word_id),
        "jurisdiction_id": 31745,
        "material_id": int(nodeid),
        "nodetypename": "GHS"
     }

    return found_h_codes


def assess_text_quality(doc: fitz.Document) -> str:
    """ Assess the quality of a text
    Useful to select between reading the PDF of using the OCR
    Return "good", "poor" or "none"
    """
    total_chars = 0
    meaningful_chars = 0
    pages_with_text = 0

    for page in doc:
        text = page.get_text()
        if not text.strip():
            continue

        pages_with_text += 1
        total_chars += len(text)

        # Count "normal" characters (letters, numbers, punctuation)
        meaningful = sum(1 for c in text if c.isalnum() or c in " ,.;:-+/()[]%")
        meaningful_chars += meaningful

        # if too many weird characters
        weird_ratio = (len(text) - meaningful) / len(text) if len(text) > 0 else 1
        if weird_ratio > 0.25:
            return "poor"

    if pages_with_text == 0:
        return "none"

    if meaningful_chars / total_chars < 0.75:
        return "poor"
    
    return "good"


# Main function of analyze
def analyser_material_id(material_id, h_codes_dict):
    if not re.match(r'^Z\d{7}$', material_id):
        return "❌ Invalid format of Material ID (Z & 7 numbers)"

    try:
        conn = get_oracle_connection()
        nodeid = get_nodeid_from_material_id(conn, material_id)
        filename, pdf_bytes = get_pdf_blob_from_db(conn, nodeid)

        doc = fitz.open(stream=BytesIO(pdf_bytes), filetype="pdf")
        text_quality = assess_text_quality(doc)
        if text_quality == "good":
            full_text = "\n".join(page.get_text() for page in doc)
            logging.info("✅ Text detected as good quality")
        else:
            logging.info(f"⚠️ Text detected as poor quality ({text_quality}) -> forwarded to OCR")
            full_text = perform_ocr_on_pdf(pdf_bytes)
        doc.close()

        mupdf_warns = fitz.TOOLS.mupdf_warnings()
        if mupdf_warns:
            logging.warning(f"⚠️  {material_id}: MuPDF — {mupdf_warns}")

        # ── Deterministic isolation of section 2 ────────────────────────────
        section2_text, confidence = isolate_section_2(full_text)

        # Scan "shadow" of full document (always done for auditing)
        result_fulldoc = search_h_codes_in_pdf(full_text, h_codes_dict, nodeid)

        # Scan of section 2 (if isolation has generated a text)
        result_section2 = (
            search_h_codes_in_pdf(section2_text, h_codes_dict, nodeid)
            if section2_text is not None else None
        )

        # Livrable : section 2 if confidence is high, if not fallback on full doc
        use_section2 = (section2_text is not None and confidence >= CONFIDENCE_THRESHOLD)
        result = result_section2 if use_section2 else result_fulldoc

        if use_section2:
            logging.info(f"H-codes from SECTION 2 (confidence={confidence})")
        else:
            logging.info(f"⚠️ Isolation section 2 insufficient (confidence={confidence}) "
                         f"-> fallback on full document")

        # Audit : store difference between section 2 vs full doc
        log_shadow_diff(material_id, result_section2, result_fulldoc,
                        confidence, use_section2)

        # Audit EUH : run on the scope that actually produced the deliverable,
        # so the audit file reflects what would be (or was) sent to CISPro.
        # Runs in "audit" and "on" modes alike; "audit" changes nothing in the
        # JSON above and exists purely to quantify occurrences before switching.
        euh_mode = get_euh_mode()
        if euh_mode in ("audit", "on"):
            euh_scope_text = section2_text if use_section2 else full_text
            euh_known, euh_unknown = find_euh_codes(euh_scope_text)
            log_euh_findings(
                material_id, euh_known, euh_unknown, euh_mode,
                scope="section2" if use_section2 else "fulldoc",
            )

        global LAST_RESULT_JSON
        LAST_RESULT_JSON = result

        return json.dumps(result, indent=4), full_text

    except Exception as e:
        return None, f"Error : {e}"


# Function Send to API
def envoyer_json(username, password, json_data):
    global CISPRO_TOKEN

    if json_data is None:
        return "Nothing to send. Please first analyze a file."
 
    # Parse the JSON string into a dict for the POST body
    try:
        payload = json.loads(json_data) if isinstance(json_data, str) else json_data
    except json.JSONDecodeError as exc:
        return f"Invalid JSON payload: {exc}"
 
    if not CISPRO_TOKEN:
        CISPRO_TOKEN = connect_to_CISPro_api(username, password)
    if not CISPRO_TOKEN:
        raise Exception("Login failed.")

    headers = {
        'Authorization': f'Bearer {CISPRO_TOKEN}',
        'Content-Type': 'application/json'
    }
    
    try:
        # Post
        resp = requests.post(UPLOAD_URL, json=payload, headers=headers, verify=False)
        if resp.status_code in [200, 201]:
            return "✅ Success. SDS data is saved in CISPro."
        return f"❌ Error : {resp.status_code} - {resp.text}"
    except Exception as exc:
        return f"Exception during POST : {exc}"

def build_additional_json(result_json, full_text=None, model_name=None, use_llm=False):
    """
    Create the secondary JSON for Additional information.
    LLM enrichment (via Ollama) runs only when use_llm is True and a model is given.
    """
    if result_json is None:
        logging.error("⚠️ result_json is None")
        return None

    pictograms = result_json.get("pictograms", "")
    nodeid = result_json.get("material_id")
    labelCodes = result_json.get("labelCodes", "")

    # Hazardous is True if labelCodes is not null
    hazardous = bool(labelCodes.strip())

    # PPE
    if "Corrosive" in pictograms.split(","):
        ppe = "Face Shield,Goggles,Gloves,Fume Hood,Lab Coat"
    else:
        ppe = "Goggles,Gloves,Fume Hood,Lab Coat"

    # Default values
    physical_state = boiling_point = flash_point = storage_and_handling = None

    # Sans LLM : JSON court (comportement identique à l'original)
    if not (full_text and model_name and use_llm):
        return {"nodeid": nodeid, "hazardous": hazardous, "ppe": ppe}

    # Un seul appel LLM (Ollama), résultat validé. base_url/num_ctx viennent de config.
    data = extract_sds_data_via_llm(full_text, model_name)
    logging.info(f"✅ LLM extraction: {data.model_dump()}")

    return {
        "nodeid":             nodeid,
        "hazardous":          hazardous,
        "ppe":                ppe,
        "physicalState":      data.physical_state,
        "boilingPoint":       data.boiling_point,
        "flashPoint":         data.flash_point,
        "storageAndHandling": data.storage_and_handling,
    }


def send_additional_json(username, password, additional_json):
    if additional_json is None:
        return "Nothing to send. Please analyze a file first."

    try:
        # Parse la chaîne JSON en dictionnaire Python
        additional_json_dict = json.loads(additional_json)
    except json.JSONDecodeError:
        return "Invalid JSON format in additional data."

    global CISPRO_TOKEN
    
    # When using the Gradio UI, the login is not yet done at this step
    if not CISPRO_TOKEN:
        CISPRO_TOKEN = connect_to_CISPro_api(username, password)
    
    # If not yet logged in, there is an issue
    if not CISPRO_TOKEN:
        raise Exception("Login failed.")

    upload_url = f"{UPLOAD_ADDITIONAL_URL}/{additional_json_dict['nodeid']}"
    headers = {
        'Authorization': f'Bearer {CISPRO_TOKEN}',
        'Content-Type': 'application/json'
    }

    try:
        upload_response = requests.put(upload_url, json=additional_json_dict, headers=headers, verify=False)
        logging.info(f"LOG --- upload_response: {upload_response}")
        
        if upload_response.status_code in [200, 201, 202]:
            return f"✅ Additional data successfully sent for node {additional_json_dict['nodeid']}."
        else:
            return f"❌ Error sending PPE data: {upload_response.status_code} - {upload_response.text}"

    except Exception as e:
        return f"Exception during PUT: {e}"
