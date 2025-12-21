import streamlit as st
import ee
import geemap.foliumap as geemap
import json
import datetime
from google.oauth2 import service_account

# --- 1. SILENT AUTHENTICATION ---
def authenticate_gee():
    # Use st.cache_resource so this only runs once and stays active
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
            
            # Explicitly initialize
            ee.Initialize(credentials, project=cred_info.get('project_id'))
            st.session_state['ee_initialized'] = True
            
        except Exception as e:
            st.error(f"🛰️ GEE Auth Failed: {e}")
            st.stop()

# Run the auth function
authenticate_gee()
# --- 2. HELPER FUNCTIONS ---

ddef get_building_fc(aoi, source):
    """Fetches building footprints using reliable public GEE assets"""
    if source == "Google Open Buildings (V3)":
        return ee.FeatureCollection("GOOGLE/Research/open-buildings/v3/polygons").filterBounds(aoi)
    elif source == "OpenStreetMap (OSM)":
        return ee.FeatureCollection("projects/google/ms_buildings").filterBounds(aoi)
    
    else:
        return ee.FeatureCollection("projects/sat-io/open-datasets/MSFP/buildings").filterBounds(aoi)

def perform_damage_test(aoi, mask, p_start, p_end, a_start, a_end):
    s1 = ee.ImageCollection('COPERNICUS/S1_GRD').filterBounds(aoi).select('VV')
    pre = s1.filterDate(str(p_start), str(p_end))
    post = s1.filterDate(str(a_start), str(a_end))

    def stats(col): 
        return {'m': col.mean(), 's': col.reduce(ee.Reducer.stdDev()), 'n': col.count()}
    
    s_pre, s_post = stats(pre), stats(post)

    # Welch's T-Test Calculation
    t_score = s_pre['m'].subtract(s_post['m']).abs().divide(
        (s_pre['s'].pow(2).divide(s_pre['n'])).add(s_post['s'].pow(2).divide(s_post['n'])).sqrt()
    )
    return t_score.updateMask(mask).updateMask(t_score.gt(3.5))

def calculate_population_impact(damage_layer, aoi):
    worldpop = ee.ImageCollection("WorldPop/GP/100m/pop") \
        .filterBounds(aoi) \
        .filter(ee.Filter.date('2020-01-01', '2020-12-31')) \
        .first() 
    impacted_pop_image = worldpop.updateMask(damage_layer.gt(0))
    stats = impacted_pop_image.reduceRegion(
        reducer=ee.Reducer.sum(),
        geometry=aoi,
        scale=100, 
        maxPixels=1e9
    )
    return stats.get('population')

# --- 3. UI LAYOUT ---
st.title("🛰️ SAR Conflict Damage Detector")

st.sidebar.header("1. Data Sources")
footprint_source = st.sidebar.selectbox(
    "Building Footprint Set",
    ["Google Open Buildings (V3)", "OpenStreetMap (OSM)", "Global Building Atlas (GBA)"]
)

st.sidebar.header("2. Analysis Dates")
col1, col2 = st.sidebar.columns(2)
with col1:
    pre_s = st.date_input("Pre-War Start", datetime.date(2021, 1, 1))
    post_s = st.date_input("Assessment Start", datetime.date(2024, 6, 1))
with col2:
    pre_e = st.date_input("Pre-War End", datetime.date(2021, 12, 31))
    post_e = st.date_input("Assessment End", datetime.date.today())

# --- 4. EXECUTION ---
m = geemap.Map(center=[48.379, 38.016], zoom=12)
aoi_input = st.text_input("AOI (MinLon, MinLat, MaxLon, MaxLat)", "37.45, 47.05, 37.65, 47.15")

if st.button("🚀 Run Analysis"):
    try:
        # Parse coordinates
        coords = [float(x.strip()) for x in aoi_input.split(',')]
        roi = ee.Geometry.Rectangle(coords)

        # Create a status container
        with st.status("Analyzing Satellite Data...", expanded=True) as status:
            
            st.write("🔍 Searching for building footprints...")
            buildings = get_building_fc(roi, footprint_source)
            # This is the first GEE network call
            count = buildings.size().getInfo()

            if count == 0:
                st.warning(f"No footprints found using {footprint_source}.")
                status.update(label="Analysis Failed", state="error")
            else:
                st.write(f"🛰️ Fetching Sentinel-1 SAR stacks for {count} buildings...")
                b_mask = ee.Image.constant(0).paint(buildings, 1)
                
                st.write("📉 Performing Welch's T-Test (Change Detection)...")
                damage = perform_damage_test(roi, b_mask, pre_s, pre_e, post_s, post_e)
                
                st.write("👥 Estimating population impact via WorldPop...")
                # Second GEE network call
                pop_val = calculate_population_impact(damage, roi).getInfo()
                
                if pop_val is not None:
                    st.metric("Estimated People Affected", f"{int(pop_val):,}")

                st.write("🗺️ Rendering final map layers...")
                m.add_legend(title="Damage Confidence", legend_dict={
                    'Likely Damage': '#ffffb2', 'Significant': '#fd8d3c', 'Severe': '#e31a1c'
                })
                m.addLayer(b_mask.updateMask(b_mask), {'palette': 'gray'}, 'Buildings')
                m.addLayer(damage, {'min': 3.5, 'max': 10, 'palette': ['#ffffb2', '#fd8d3c', '#e31a1c']}, 'Damage Map')
                m.centerObject(roi, 14)
                
                status.update(label="Analysis Complete!", state="complete", expanded=False)
                st.success(f"Successfully analyzed {count} structures.")

    except Exception as e:
        st.error(f"Analysis Error: {e}")

m.to_streamlit(height=600)
