import streamlit as st
import ee
import geemap.foliumap as geemap
import json
import datetime
from google.oauth2 import service_account

# --- 1. AUTHENTICATION ---
def authenticate_gee():
    try:
        ee_creds = st.secrets["EARTHENGINE_SERVICE_ACCOUNT"]
        cred_dict = ee_creds.to_dict()
        credentials = service_account.Credentials.from_service_account_info(cred_dict)
        ee.Initialize(credentials, project=cred_dict['sarttest'])

    except Exception as e:
        st.error(f"GEE Auth Failed: {e}")
        st.stop() # Prevents the rest of the app from running without auth
# --- 2. LOGIC & HELPER FUNCTIONS ---

def get_building_fc(aoi, source):
    """Fetches building footprints based on user selection"""
    if source == "Google Open Buildings (V3)":
        return ee.FeatureCollection("GOOGLE/Research/open-buildings/v3/polygons").filterBounds(aoi)
    elif source == "OpenStreetMap (OSM)":
        return ee.FeatureCollection("projects/sat-io/open-datasets/OSM/buildings").filterBounds(aoi)
    else:
        return ee.FeatureCollection("projects/sat-io/open-datasets/GBA/polygons").filterBounds(aoi)

def perform_damage_test(aoi, mask, p_start, p_end, a_start, a_end):
    """Performs Welch's T-Test on Sentinel-1 SAR stacks"""
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
    """Calculates the estimated population within damaged pixels using WorldPop"""
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

# --- 3. CONFIGURATION (SIDEBAR) ---
st.sidebar.header("Step 1: Data Sources")
footprint_source = st.sidebar.selectbox(
    "Building Footprint Set",
    ["Google Open Buildings (V3)", "OpenStreetMap (OSM)", "Global Building Atlas (GBA)"]
)

st.sidebar.header("Step 2: Analysis Dates")
col1, col2 = st.sidebar.columns(2)
with col1:
    pre_start_date = st.date_input("Pre-War Start", datetime.date(2021, 1, 1))
    post_start_date = st.date_input("Assessment Start", datetime.date(2024, 6, 1))
with col2:
    pre_end_date = st.date_input("Pre-War End", datetime.date(2021, 12, 31))
    post_end_date = st.date_input("Assessment End", datetime.date.today())

# --- 4. MAP INTERFACE & EXECUTION ---
st.info("Enter coordinates or use the map to define your Area of Interest (AOI).")
m = geemap.Map(center=[48.379, 38.016], zoom=12)
aoi_input = st.text_input("Coordinates (MinLon, MinLat, MaxLon, MaxLat)", "37.45, 47.05, 37.65, 47.15")

if st.button("🚀 Generate Damage Map"):
    try:
        coords = [float(x.strip()) for x in aoi_input.split(',')]
        roi = ee.Geometry.Rectangle(coords)

        # Pre-flight Check for Footprint Coverage
        buildings = get_building_fc(roi, footprint_source)
        count = buildings.size().getInfo()

        if count == 0:
            st.warning(f"⚠️ No footprints found using **{footprint_source}**.")
            if footprint_source != "OpenStreetMap (OSM)":
                st.info("💡 **Tip:** Switch to **OpenStreetMap (OSM)** for Eastern Europe/Russia.")
        else:
            with st.spinner(f"Analyzing {count} building footprints..."):
                b_mask = ee.Image.constant(0).paint(buildings, 1)

                # Perform analysis
                damage_layer = perform_damage_test(
                    roi, b_mask, pre_start_date, pre_end_date, post_start_date, post_end_date
                )

                # Calculate population impact
                total_affected = calculate_population_impact(damage_layer, roi).getInfo()

                # Visuals
                if total_affected is not None:
                    st.metric("Estimated People Affected", f"{int(total_affected):,}")

                legend_dict = {
                    'Likely Damage (T > 3.5)': '#ffffb2',
                    'Significant Damage (T > 5)': '#fd8d3c',
                    'Severe Destruction (T > 8)': '#e31a1c'
                }
                m.add_legend(title="Damage Confidence", legend_dict=legend_dict)
                m.addLayer(b_mask.updateMask(b_mask), {'palette': 'gray'}, 'Building Outlines')
                m.addLayer(damage_layer, {'min': 3.5, 'max': 10, 'palette': ['#ffffb2', '#fd8d3c', '#e31a1c']}, 'Damage Map')
                m.centerObject(roi, 14)
                st.success("Analysis complete.")
    except Exception as e:
        st.error(f"Error during analysis: {e}")

m.to_streamlit(height=600)
