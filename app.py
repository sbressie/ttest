import streamlit as st
import ee
import geemap.foliumap as geemap
import json
import datetime
import os
from google.oauth2 import service_account

# --- 1. SILENT AUTHENTICATION ---
def authenticate_gee():
    if 'ee_initialized' not in st.session_state:
        try:
            if "EARTHENGINE_SERVICE_ACCOUNT" not in st.secrets:
                st.error("Secret 'EARTHENGINE_SERVICE_ACCOUNT' not found.")
                st.stop()

            cred_info = st.secrets["EARTHENGINE_SERVICE_ACCOUNT"].to_dict()
            scopes = [
                'https://www.googleapis.com/auth/earthengine',
                'https://www.googleapis.com/auth/cloud-platform'
            ]

            credentials = service_account.Credentials.from_service_account_info(
                cred_info, scopes=scopes
            )

            ee.Initialize(credentials, project=cred_info.get('project_id'))
            st.session_state['ee_initialized'] = True
        except Exception as e:
            st.session_state['ee_initialized'] = False
            st.error(f"🛰️ GEE Auth Failed: {e}")

authenticate_gee()

# --- 2. LOAD ISO DATA ---
@st.cache_data
def load_iso_data(file_path='iso.json'):
    """Loads country names and codes from iso.json."""
    try:
        if os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return []
    except Exception as e:
        st.error(f"Failed to load ISO data: {e}")
        return []

iso_list = load_iso_data()
country_names = [c['name'] for c in iso_list]
iso_map = {c['name']: c['code'] for c in iso_list}

# --- 3. HELPER FUNCTIONS ---
def get_building_fc(aoi, iso_code):
    """
    Dynamically constructs the asset path using the ISO code 
    from the dropdown menu.
    """
    asset_path = f"projects/sat-io/open-datasets/VIDA_COMBINED/{iso_code}"
    try:
        return ee.FeatureCollection(asset_path).filterBounds(aoi)
    except Exception as e:
        st.error(f"Could not find building asset for {iso_code}: {e}")
        return None

def perform_damage_test(aoi, mask, p_start, p_end, a_start, a_end):
    s1 = ee.ImageCollection('COPERNICUS/S1_GRD') \
           .filterBounds(aoi) \
           .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VV')) \
           .select('VV')
    
    pre = s1.filterDate(str(p_start), str(p_end))
    post = s1.filterDate(str(a_start), str(a_end))

    def stats(col):
        return {'m': col.mean(), 's': col.reduce(ee.Reducer.stdDev()), 'n': col.count()}

    s_pre, s_post = stats(pre), stats(post)

    t_score = s_pre['m'].subtract(s_post['m']).abs().divide(
        (s_pre['s'].pow(2).divide(s_pre['n'])).add(s_post['s'].pow(2).divide(s_post['n'])).sqrt()
    )
    return t_score.updateMask(mask).updateMask(t_score.gt(3.5))

def calculate_population_impact(damage_layer, aoi):
    pop_col = ee.ImageCollection("WorldPop/GP/100m/pop").filterBounds(aoi).sort('year', False) 
    pop_image = pop_col.first()
    impacted_pop_image = pop_image.updateMask(damage_layer.gt(0))
    stats = impacted_pop_image.reduceRegion(reducer=ee.Reducer.sum(), geometry=aoi, scale=100, maxPixels=1e9)
    return stats.get('population')

# --- 4. UI LAYOUT ---
st.set_page_config(page_title="Vida Global Damage Assessment", layout="wide")
st.title("🛰️ Vida Global Building & Population Analysis")

# Sidebar Configuration
st.sidebar.header("1. Settings & Data")
selected_country = st.sidebar.selectbox("Select Country", country_names, index=country_names.index("Ukraine") if "Ukraine" in country_names else 0)
current_iso = iso_map[selected_country]
st.sidebar.info(f"Target ISO: **{current_iso}**")

basemap_choice = st.sidebar.selectbox("Choose Basemap", ["OpenStreetMap", "Google Satellite"])

st.sidebar.header("2. Analysis Dates")
col1, col2 = st.sidebar.columns(2)
with col1:
    pre_s = st.date_input("Pre-Event Start", datetime.date(2021, 1, 1))
    post_s = st.date_input("Assessment Start", datetime.date(2024, 6, 1))
with col2:
    pre_e = st.date_input("Pre-Event End", datetime.date(2021, 12, 31))
    post_e = st.date_input("Assessment End", datetime.date.today())

# Bounding Box Instructions
st.markdown("""
### 🗺️ Area of Interest
Use the [Klokantech Bounding Box Tool](https://boundingbox.klokantech.com/) to select your area. 
Set the format to **CSV** at the bottom, copy the coordinates, and paste them below.
""")
aoi_input = st.text_input("CSV Bounding Box (minLon, minLat, maxLon, maxLat)", "37.45, 47.05, 37.65, 47.15")

# --- 5. MAP & EXECUTION ---
m = geemap.Map()
if basemap_choice == "Google Satellite":
    m.add_basemap("SATELLITE")
else:
    m.add_basemap("ROADMAP")

if st.button("🚀 Run Analysis"):
    try:
        coords = [float(x.strip()) for x in aoi_input.split(',')]
        roi = ee.Geometry.Rectangle(coords)

        with st.status("Fetching Data...", expanded=True) as status:
            st.write(f"🔍 Loading VIDA footprints for {current_iso}...")
            buildings = get_building_fc(roi, current_iso)
            
            if buildings:
                count = buildings.size().getInfo()
                st.write(f"🛰️ Processing SAR for {count} structures...")
                b_mask = ee.Image.constant(0).paint(buildings, 1)
                damage = perform_damage_test(roi, b_mask, pre_s, pre_e, post_s, post_e)

                st.write("👥 Calculating population impact...")
                pop_val = calculate_population_impact(damage, roi).getInfo()

                if pop_val:
                    st.metric("Estimated People Affected", f"{int(pop_val):,}")

                m.addLayer(b_mask.updateMask(b_mask), {'palette': 'blue'}, 'Buildings')
                m.addLayer(damage, {'min': 3.5, 'max': 10, 'palette': ['#ffffb2', '#fd8d3c', '#e31a1c']}, 'Damage')
                m.centerObject(roi, 14)
                status.update(label="Complete!", state="complete")
    except Exception as e:
        st.error(f"Error: {e}")

m.to_streamlit(height=600)
