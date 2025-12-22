import streamlit as st
import ee
import geemap.foliumap as geemap
import json
import datetime
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

# --- 2. HELPER FUNCTIONS ---
def get_building_fc(aoi, source):
    """Dynamically fetches building footprints based on AOI location"""
    if source == "Google Open Buildings (V3)":
        # Google V3 is one global collection (filtered by AOI)
        return ee.FeatureCollection("GOOGLE/Research/open-buildings/v3/polygons").filterBounds(aoi)
    
    elif source == "MS Global Buildings":
        try:
            # 1. Load a dataset of world boundaries to find the ISO code
            countries = ee.FeatureCollection("USDOS/LSIB_SIMPLE/2017")
            
            # 2. Find which country contains the center of the user's AOI
            target_country = countries.filterBounds(aoi.centroid()).first()
            iso_code = target_country.get('country_na').getInfo() # e.g., 'Ukraine' or 'United States'
            
            # 3. Construct the path dynamically. Note: MS Assets in GEE 
            # are often named by the country name in the community catalog.
            # We capitalize to match catalog naming conventions.
            asset_path = f"projects/sat-io/open-datasets/MSBuildings/{iso_code}"
            
            return ee.FeatureCollection(asset_path).filterBounds(aoi)
        except Exception:
            # Fallback if the dynamic path fails (e.g., country name mismatch)
            return ee.FeatureCollection("projects/sat-io/open-datasets/MSBuildings/Ukraine").filterBounds(aoi)
    else:
        # Fallback to MSFP if selection varies
        return ee.FeatureCollection("projects/google/ms_buildings").filterBounds(aoi)

def perform_damage_test(aoi, mask, p_start, p_end, a_start, a_end):
    s1 = ee.ImageCollection('COPERNICUS/S1_GRD').filterBounds(aoi).select('VV')
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
    """
    Selects LandScan HD for Ukraine or Global LandScan for other regions.
    """
    # 1. Define Ukraine's rough geographic bounds to toggle datasets
    # [minLon, minLat, maxLon, maxLat]
    ukraine_bounds = ee.Geometry.Rectangle([22.1, 44.4, 40.2, 52.4])
    
    # 2. Determine which asset to use based on intersection with Ukraine
    is_ukraine = ukraine_bounds.intersects(aoi).getInfo()
    
    if is_ukraine:
        # High-definition LandScan for Ukraine (approx. 100m)
        pop_image = ee.Image('DOE/ORNL/LandScan_HD/Ukraine_202201').select('population')
        scale_val = 100
    else:
        # Global LandScan (approx. 1km resolution)
        # We take the most recent year (2022) from the collection
        pop_image = ee.ImageCollection("projects/sat-io/open-datasets/landscan-global") \
                      .filterDate('2022-01-01', '2022-12-31') \
                      .first() \
                      .select('b1') # Global LandScan typically uses 'b1' for population count
        scale_val = 1000

    # 3. Mask population by damage and reduce
    impacted_pop_image = pop_image.updateMask(damage_layer.gt(0))
    
    stats = impacted_pop_image.reduceRegion(
        reducer=ee.Reducer.sum(),
        geometry=aoi,
        scale=scale_val,
        maxPixels=1e9
    )
    
    return stats.get(pop_image.bandNames().get(0))

# --- 3. UI LAYOUT ---
st.title("🛰️ SAR Conflict T-Test")

# Connection Indicator
if st.session_state.get('ee_initialized'):
    st.sidebar.success("✅ GEE Connected")
else:
    st.sidebar.error("❌ GEE Disconnected")

st.sidebar.header("1. Data Sources")
footprint_source = st.sidebar.selectbox(
    "Building Footprint Set",
    ["Google Open Buildings (V3)", "MS Global Buildings"],
    index=0 # Defaults to Google V3
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
        coords = [float(x.strip()) for x in aoi_input.split(',')]
        roi = ee.Geometry.Rectangle(coords)
        

        with st.status("Analyzing Satellite Data...", expanded=True) as status:
            st.write("🔍 Loading building footprints...")
            # Inside the "Run Analysis" button block
            buildings = get_building_fc(roi, footprint_source)
            m.centerObject(roi, 14) # Automatically zooms to the new area
            m.addLayer(buildings, {'color': 'red'}, 'Dynamic Buildings')
            

            if count == 0:
                st.warning("No structures found in this area.")
                status.update(label="No Data Found", state="error")
            else:
                st.write(f"🛰️ Processing SAR change detection for {count} structures...")
                b_mask = ee.Image.constant(0).paint(buildings, 1)
                damage = perform_damage_test(roi, b_mask, pre_s, pre_e, post_s, post_e)
                
                st.write("👥 Calculating population impact...")
                pop_val = calculate_population_impact(damage, roi).getInfo()
                
                if pop_val is not None:
                    st.metric("Estimated People Affected", f"{int(pop_val):,}")

                st.write("🗺️ Finalizing Map...")
                m.addLayer(b_mask.updateMask(b_mask), {'palette': 'red'}, 'Buildings')
                m.addLayer(damage, {'min': 3.5, 'max': 10, 'palette': ['#ffffb2', '#fd8d3c', '#e31a1c']}, 'Damage Map')
                m.centerObject(roi, 14)
                
                status.update(label="Analysis Complete!", state="complete", expanded=False)
                st.success("Results ready below.")
    except Exception as e:
        st.error(f"Analysis Error: {e}")

m.to_streamlit(height=600)
