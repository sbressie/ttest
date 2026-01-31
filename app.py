import streamlit as st
import ee
import geemap.foliumap as geemap 
from streamlit_folium import st_folium
import json
import datetime
import os
from google.oauth2 import service_account


# --- 1. CONFIGURATION ---
st.set_page_config(page_title="VIDA Damage Assessment", layout="wide")

# --- 2. AUTHENTICATION ---
def authenticate_gee():
    if 'ee_initialized' not in st.session_state:
        try:
            cred_info = st.secrets["EARTHENGINE_SERVICE_ACCOUNT"]
            project_id = cred_info.get('project_id')
            credentials = service_account.Credentials.from_service_account_info(
                cred_info, scopes=['https://www.googleapis.com/auth/earthengine']
            )
            ee.Initialize(credentials, project=project_id)
            st.session_state['ee_initialized'] = True
            st.session_state['project_id'] = project_id
        except Exception as e:
            st.sidebar.error(f"❌ Auth Error: {e}")
            st.session_state['ee_initialized'] = False

authenticate_gee()

# --- 3. DATA HELPERS ---
@st.cache_data
def load_iso_data(file_path='iso.json'):
    if os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return [{"name": "Ukraine", "code": "UKR"}]

def perform_damage_test(aoi, mask, p_start, p_end, a_start, a_end):
    s1 = ee.ImageCollection('COPERNICUS/S1_GRD').filterBounds(aoi)\
           .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VV')).select('VV')
    pre = s1.filterDate(str(p_start), str(p_end))
    post = s1.filterDate(str(a_start), str(a_end))
    def stats(col): 
        return {'m': col.mean(), 's': col.reduce(ee.Reducer.stdDev()), 'n': col.count()}
    s_pre, s_post = stats(pre), stats(post)
    t_score = s_pre['m'].subtract(s_post['m']).abs().divide(
        (s_pre['s'].pow(2).divide(s_pre['n'])).add(s_post['s'].pow(2).divide(s_post['n'])).sqrt()
    )
    return t_score.updateMask(mask).updateMask(t_score.gt(3.5))

# --- 4. SIDEBAR (Defined before map logic to avoid NameErrors) ---
iso_list = load_iso_data()
country_names = [c['name'] for c in iso_list]
iso_map = {c['name']: c['code'] for c in iso_list}

with st.sidebar:
    st.header("Map Layers")
    show_buildings = st.checkbox("Show Building Footprints", value=True)
    show_damage = st.checkbox("Show Damage Heatmap", value=True)
    
    st.markdown("---")
    st.header("Settings")
    selected_country = st.selectbox("Select Country", country_names, index=0)
    current_iso = iso_map.get(selected_country, "UKR")
    
    st.subheader("Analysis Dates")
    pre_s = st.date_input("Baseline Start", datetime.date(2021, 1, 1))
    pre_e = st.date_input("Baseline End", datetime.date(2021, 12, 31))
    post_s = st.date_input("Assessment Start", datetime.date(2024, 6, 1))
    post_e = st.date_input("Assessment End", datetime.date.today())

# --- 5. MAIN UI ---
st.title("🛰️ VIDA Building Damage & Population Analysis")
aoi_input = st.text_input("CSV Bounding Box (minLon, minLat, maxLon, maxLat)", "37.45, 47.05, 37.65, 47.15")

if st.button("🚀 Run Analysis"):
    if not st.session_state.get('ee_initialized'):
        st.error("Earth Engine not initialized.")
    else:
        try:
            coords = [float(x.strip()) for x in aoi_input.split(',')]
            roi = ee.Geometry.Rectangle(coords)

            with st.status("Analyzing Satellite Data...") as status:
                # Setup Map
                m = geemap.Map(ee_initialize=False)
                m.centerObject(roi, 13)
                m.add_basemap("SATELLITE")
                st_folium(m, width=1100, height=600)

                # Fetch Buildings
                buildings = ee.FeatureCollection(f"projects/sat-io/open-datasets/VIDA_COMBINED/{current_iso}").filterBounds(roi)
                
                if show_buildings:
                    m.addLayer(buildings.style(color='00FFFF', fillColor='00000000'), {}, 'Buildings')

                if show_damage:
                    b_mask = ee.Image.constant(1).clip(buildings).mask()
                    damage = perform_damage_test(roi, b_mask, pre_s, pre_e, post_s, post_e)
                    m.addLayer(damage, {'min': 3.5, 'max': 10, 'palette': ['#ffffb2', '#fd8d3c', '#e31a1c']}, 'Damage')

                # Render
                st_folium(m, width=1100, height=600)
                status.update(label="Complete!", state="complete")
        except Exception as e:
            st.error(f"Error: {e}")
