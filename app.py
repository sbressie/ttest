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
            # Check for Streamlit Secrets (Required for Cloud Deployment)
            if "EARTHENGINE_SERVICE_ACCOUNT" not in st.secrets:
                st.error("Secret 'EARTHENGINE_SERVICE_ACCOUNT' not found in Streamlit Secrets.")
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

# --- 2. LOAD ISO DATA FROM REPOSITORY ---
@st.cache_data
def load_iso_data(file_path='iso.json'):
    """Loads the ISO country codes from a local JSON file."""
    try:
        if os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        else:
            st.error(f"Error: {file_path} not found in repository.")
            return []
    except Exception as e:
        st.error(f"Failed to load ISO data: {e}")
        return []

# Process ISO data for the UI
iso_list = load_iso_data()
if not iso_list:
    st.stop() # Halt if data is missing

country_names = [c['name'] for c in iso_list]
iso_map = {c['name']: c['code'] for c in iso_list}

# --- 3. HELPER FUNCTIONS ---
def get_building_fc(aoi, source, iso_code):
    """Uses official GEE assets for global coverage."""
    if source == "Google Open Buildings (V3)":
        return ee.FeatureCollection("GOOGLE/Research/open-buildings/v3/polygons").filterBounds(aoi)
    elif source == "Microsoft Global Buildings":
        # Official Microsoft Global ML Building Footprints asset
        return ee.FeatureCollection("USDOS/LSAD/MS_Buildings/v1") \
                 .filter(ee.Filter.eq('countrycode', iso_code)) \
                 .filterBounds(aoi)
    return None

def perform_damage_test(aoi, mask, p_start, p_end, a_start, a_end):
    # Filter by AOI and VV polarization
    s1 = ee.ImageCollection('COPERNICUS/S1_GRD') \
           .filterBounds(aoi) \
           .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VV')) \
           .select('VV')
    
    pre = s1.filterDate(str(p_start), str(p_end))
    post = s1.filterDate(str(a_start), str(a_end))

    def stats(col):
        return {'m': col.mean(), 's': col.reduce(ee.Reducer.stdDev()), 'n': col.count()}

    s_pre, s_post = stats(pre), stats(post)

    # Statistical T-Test for change detection
    t_score = s_pre['m'].subtract(s_post['m']).abs().divide(
        (s_pre['s'].pow(2).divide(s_pre['n'])).add(s_post['s'].pow(2).divide(s_post['n'])).sqrt()
    )
    return t_score.updateMask(mask).updateMask(t_score.gt(3.5))

def calculate_population_impact(damage_layer, aoi):
    """Calculates population impact using WorldPop 100m resolution."""
    pop_col = ee.ImageCollection("WorldPop/GP/100m/pop") \
                .filterBounds(aoi) \
                .sort('year', False) 
    
    pop_image = pop_col.first()
    impacted_pop_image = pop_image.updateMask(damage_layer.gt(0))

    stats = impacted_pop_image.reduceRegion(
        reducer=ee.Reducer.sum(),
        geometry=aoi,
        scale=100,
        maxPixels=1e9
    )
    return stats.get('population')

# --- 4. UI LAYOUT ---
st.set_page_config(page_title="Global Damage Assessment", layout="wide")
st.title("🛰️ SAR Damage & Population Impact")

st.sidebar.header("1. Region Selection")
selected_country = st.sidebar.selectbox("Select Country", country_names, index=country_names.index("Ukraine") if "Ukraine" in country_names else 0)
current_iso = iso_map[selected_country]
st.sidebar.info(f"Using ISO Code: **{current_iso}**")

footprint_source = st.sidebar.selectbox(
    "Building Footprint Set",
    ["Microsoft Global Buildings", "Google Open Buildings (V3)"],
    index=0
)

st.sidebar.header("2. Analysis Timeline")
col1, col2 = st.sidebar.columns(2)
with col1:
    pre_s = st.date_input("Baseline Start", datetime.date(2021, 1, 1))
    post_s = st.date_input("Analysis Start", datetime.date(2024, 6, 1))
with col2:
    pre_e = st.date_input("Baseline End", datetime.date(2021, 12, 31))
    post_e = st.date_input("Analysis End", datetime.date.today())

# --- 5. EXECUTION ---
m = geemap.Map(center=[48.379, 38.016], zoom=12)
aoi_input = st.text_input("AOI Bounding Box (MinLon, MinLat, MaxLon, MaxLat)", "37.45, 47.05, 37.65, 47.15")

if st.button("🚀 Run Analysis"):
    try:
        coords = [float(x.strip()) for x in aoi_input.split(',')]
        if len(coords) != 4:
            raise ValueError("Please provide exactly 4 coordinates.")
        roi = ee.Geometry.Rectangle(coords)

        with st.status("Fetching Satellite Data...", expanded=True) as status:
            st.write(f"🔍 Loading {footprint_source}...")
            buildings = get_building_fc(roi, footprint_source, current_iso)
            count = buildings.size().getInfo()

            if count == 0:
                st.warning(f"No structures found for {selected_country} in this bounding box.")
                status.update(label="No Data Found", state="error")
            else:
                st.write(f"🛰️ Analyzing SAR change for {count} footprints...")
                b_mask = ee.Image.constant(0).paint(buildings, 1)
                damage = perform_damage_test(roi, b_mask, pre_s, pre_e, post_s, post_e)

                st.write("👥 Calculating population density impact...")
                pop_val = calculate_population_impact(damage, roi).getInfo()

                if pop_val:
                    st.metric("Estimated People in Affected Areas", f"{int(pop_val):,}")
                else:
                    st.metric("Estimated People in Affected Areas", "0")

                st.write("🗺️ Rendering Map...")
                m.addLayer(b_mask.updateMask(b_mask), {'palette': '0000FF'}, 'Buildings')
                m.addLayer(damage, {'min': 3.5, 'max': 10, 'palette': ['#ffffb2', '#fd8d3c', '#e31a1c']}, 'Damage Intensity')
                m.centerObject(roi, 14)
                
                status.update(label="Analysis Complete!", state="complete", expanded=False)
                st.success("Scroll down to view results.")
    except Exception as e:
        st.error(f"Analysis Error: {e}")

m.to_streamlit(height=600)
