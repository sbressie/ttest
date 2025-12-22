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
    """Uses only public, high-reliability assets"""
    if source == "Google Open Buildings (V3)":
        # Note: Google Open Buildings currently covers Africa, Latin America,
        return ee.FeatureCollection("GOOGLE/Research/open-buildings/v3/polygons").filterBounds(aoi)
    elif source == "MS Global Buildings":

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
def summarize_sar_change(roi, b_mask, pre_s, pre_e, post_s, post_e):
    """Calculates quantitative change in SAR backscatter (VV band)"""
    
    def get_collection_mean(start, end):
        return ee.ImageCollection('COPERNICUS/S1_GRD') \
            .filterBounds(roi) \
            .filterDate(start, end) \
            .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VV')) \
            .select('VV') \
            .mean() \
            .updateMask(b_mask)

    pre_mean = get_collection_mean(pre_s, pre_e)
    post_mean = get_collection_mean(post_s, post_e)

    # Calculate mean values over the ROI
    stats_pre = pre_mean.reduceRegion(reducer=ee.Reducer.mean(), geometry=roi, scale=10).get('VV')
    stats_post = post_mean.reduceRegion(reducer=ee.Reducer.mean(), geometry=roi, scale=10).get('VV')
    
    return stats_pre, stats_post
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
st.title("🛰️ SAR T-Test")

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
    pre_s = st.sidebar.date_input("Pre-Event Start", datetime.date(2021, 12, 1)).strftime('%Y-%m-%d')
    pre_e = st.sidebar.date_input("Pre-Event End", datetime.date(2021, 12, 31)).strftime('%Y-%m-%d')
with col2:
    post_s = st.sidebar.date_input("Post-Event Start", datetime.date(2024, 6, 1)).strftime('%Y-%m-%d')
    post_e = st.sidebar.date_input("Post-Event End", datetime.date(2025, 12, 22)).strftime('%Y-%m-%d')

# --- 4. EXECUTION ---
m = geemap.Map(center=[48.379, 38.016], zoom=12)
aoi_input = st.text_input("AOI (MinLon, MinLat, MaxLon, MaxLat)", "37.45, 47.05, 37.65, 47.15")

if st.button("🚀 Run Analysis"):
    try:
        coords = [float(x.strip()) for x in aoi_input.split(',')]
        roi = ee.Geometry.Rectangle(coords)

        with st.status("Analyzing Satellite Data...", expanded=True) as status:
            st.write("🔍 Loading building footprints...")
            buildings = get_building_fc(roi, footprint_source)
            count = buildings.size().getInfo()

            if count == 0:
                st.warning("No structures found in this area.")
                status.update(label="No Data Found", state="error")
            else:
                st.write(f"🛰️ Processing SAR change detection for {count} structures...")
                b_mask = ee.Image.constant(0).paint(buildings, 1)
                damage = perform_damage_test(roi, b_mask, pre_s, pre_e, post_s, post_e)
             
                st.write("📊 Summarizing SAR intensity changes...")
                pre_val, post_val = summarize_sar_change(roi, b_mask, pre_s, pre_e, post_s, post_e)
                
                # Convert from Earth Engine objects to Python floats
                pre_db = pre_val.getInfo() if pre_val else 0
                post_db = post_val.getInfo() if post_val else 0
                diff = post_db - pre_db
                
                # Display as Streamlit Columns
                col1, col2, col3 = st.columns(3)
                col1.metric("Pre-Event VV (dB)", f"{pre_db:.2f}")
                col2.metric("Post-Event VV (dB)", f"{post_db:.2f}")
                col3.metric("Net Change", f"{diff:.2f} dB", delta=diff, delta_color="inverse")
                
                if diff < -1.5:
                    st.info("💡 Large negative change detected: Typical of structural collapse or flooding.")

                st.write("👥 Calculating population impact...")
                pop_val = calculate_population_impact(damage, roi).getInfo()

                if pop_val is not None:
                    st.metric("Estimated People Affected", f"{int(pop_val):,}")

                st.write("🗺️ Finalizing Map...")
                m.addLayer(b_mask.updateMask(b_mask), {'palette': 'blue'}, 'Buildings')
                m.addLayer(damage, {'min': 3.5, 'max': 10, 'palette': ['#ffffb2', '#fd8d3c', '#e31a1c']}, 'Damage Map')
                m.centerObject(roi, 14)
                
                status.update(label="Analysis Complete!", state="complete", expanded=False)
                st.success("Results ready below.")
    except Exception as e:
        st.error(f"Analysis Error: {e}")

m.to_streamlit(height=600)
