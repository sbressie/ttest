import streamlit as st
import ee
import geemap
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
            scopes = ['https://www.googleapis.com/auth/earthengine', 'https://www.googleapis.com/auth/cloud-platform']
            credentials = service_account.Credentials.from_service_account_info(cred_info, scopes=scopes)
            ee.Initialize(credentials, project=cred_info.get('project_id'))
            st.session_state['ee_initialized'] = True
        except Exception as e:
            st.session_state['ee_initialized'] = False
            st.error(f"🛰️ GEE Auth Failed: {e}")

authenticate_gee()

# --- 2. LOAD ISO DATA ---
@st.cache_data
def load_iso_data(file_path='iso.json'):
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
    """Dynamic pathing for VIDA Global Buildings using ISO code."""
    asset_path = f"projects/sat-io/open-datasets/VIDA_COMBINED/{iso_code}"
    return ee.FeatureCollection(asset_path).filterBounds(aoi)

def perform_damage_test(aoi, mask, p_start, p_end, a_start, a_end):
    """Performs Welch's t-test between baseline (pre) and assessment (post) SAR imagery."""
    s1 = ee.ImageCollection('COPERNICUS/S1_GRD').filterBounds(aoi)\
           .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VV')).select('VV')
    
    # Baseline Imagery
    pre = s1.filterDate(str(p_start), str(p_end))
    # Assessment Imagery
    post = s1.filterDate(str(a_start), str(a_end))
    
    def stats(col): 
        return {'m': col.mean(), 's': col.reduce(ee.Reducer.stdDev()), 'n': col.count()}
    
    s_pre, s_post = stats(pre), stats(post)
    
    # Welch's T-Test formula
    t_score = s_pre['m'].subtract(s_post['m']).abs().divide(
        (s_pre['s'].pow(2).divide(s_pre['n'])).add(s_post['s'].pow(2).divide(s_post['n'])).sqrt()
    )
    # Return damage score clipped to building mask and thresholded
    return t_score.updateMask(mask).updateMask(t_score.gt(3.5))

def calculate_population_impact(damage_layer, aoi):
    pop_col = ee.ImageCollection("WorldPop/GP/100m/pop").filterBounds(aoi).sort('year', False) 
    pop_image = pop_col.first()
    impacted_pop_image = pop_image.updateMask(damage_layer.gt(0))
    stats = impacted_pop_image.reduceRegion(reducer=ee.Reducer.sum(), geometry=aoi, scale=100, maxPixels=1e9)
    return stats.get('population')

# --- 4. UI LAYOUT ---
st.set_page_config(page_title="VIDA Damage Assessment", layout="wide")
st.title("🛰️ VIDA Building Damage & Population Analysis")

# Metric containers at the top
metric_col1, metric_col2 = st.columns(2)
pop_placeholder = metric_col1.empty()
structure_placeholder = metric_col2.empty()

with st.sidebar:
    st.header("1. Region & Basemap")
    selected_country = st.selectbox("Select Country", country_names, index=country_names.index("Ukraine") if "Ukraine" in country_names else 0)
    current_iso = iso_map[selected_country]
    basemap_choice = st.selectbox("Choose Basemap", ["OpenStreetMap", "Google Satellite"])
    
    st.markdown("---")
    st.header("2. Analysis Dates")
    st.subheader("Baseline (Pre-Event)")
    pre_s = st.date_input("Baseline Start", datetime.date(2021, 1, 1))
    pre_e = st.date_input("Baseline End", datetime.date(2021, 12, 31))
    
    st.subheader("Assessment (Post-Event)")
    post_s = st.date_input("Assessment Start", datetime.date(2024, 6, 1))
    post_e = st.date_input("Assessment End", datetime.date.today())

st.markdown("### 🗺️ Define Area of Interest")
st.caption("Use [Klokantech Bounding Box](https://boundingbox.klokantech.com/) (Format: CSV) and paste below.")
aoi_input = st.text_input("CSV Bounding Box (minLon, minLat, maxLon, maxLat)", "37.45, 47.05, 37.65, 47.15")

# --- 5. MAP & EXECUTION ---
m = geemap.Map()
m.add_basemap("SATELLITE" if basemap_choice == "Google Satellite" else "ROADMAP")

if st.button("🚀 Run Analysis"):
    try:
        coords = [float(x.strip()) for x in aoi_input.split(',')]
        roi = ee.Geometry.Rectangle(coords)

        with st.status("Analyzing Satellite Data...", expanded=True) as status:
            st.write(f"🔍 Fetching {selected_country} ({current_iso}) footprints...")
            buildings = get_building_fc(roi, current_iso)
            count = buildings.size().getInfo()
            structure_placeholder.metric("Total Structures Found", f"{count:,}")

            if count > 0:
                # Create a binary mask of buildings to clip the SAR analysis
                b_mask = ee.Image.constant(1).clip(buildings).mask()
                
                st.write(f"🛰️ Processing Welch's t-test for {count} structures...")
                damage = perform_damage_test(roi, b_mask, pre_s, pre_e, post_s, post_e)

                st.write("👥 Calculating population impact...")
                pop_val = calculate_population_impact(damage, roi).getInfo()
                pop_placeholder.metric("Estimated People Affected", f"{int(pop_val or 0):,}")

                # Render Layers: Blue outlines for buildings, Heatmap for damage
                m.addLayer(buildings.style(fillColor='00000000', color='0000FF', width=1), {}, 'Building Outlines')
                m.addLayer(damage, {'min': 3.5, 'max': 10, 'palette': ['#ffffb2', '#fd8d3c', '#e31a1c']}, 'Clipped Building Damage')
                m.centerObject(roi, 14)
                
                status.update(label="Analysis Complete!", state="complete", expanded=False)
            else:
                st.warning("No buildings found in this AOI.")
                status.update(label="No Data Found", state="error")
    except Exception as e:
        st.error(f"Analysis Error: {e}")

m.to_streamlit(height=600)
