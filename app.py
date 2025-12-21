import streamlit as st
import ee
import geemap.foliumap as geemap
import json

# --- 1. AUTHENTICATION ---
def authenticate_gee():
    try:
        # Pulling from Streamlit Cloud Secrets
        ee_creds = st.secrets["EARTHENGINE_SERVICE_ACCOUNT"]
        credentials = ee.ServiceAccountCredentials(
            ee_creds['client_email'],
            key_data=json.dumps(ee_creds)
        )
        ee.Initialize(credentials)
    except Exception as e:
        st.error(f"GEE Auth Failed: {e}")

authenticate_gee()



# --- 2. CONFIGURATION ---
st.sidebar.header("Step 1: Data Sources")
footprint_source = st.sidebar.selectbox(
    "Building Footprint Set",
    ["Google Open Buildings (V3)", "OpenStreetMap (OSM)", "Global Building Atlas (GBA)"]
)

st.sidebar.header("Step 2: Analysis Dates")
col1, col2 = st.sidebar.columns(2)
with col1:
    pre_start = st.date_input("Pre-War Start", datetime.date(2021, 1, 1))
    post_start = st.date_input("Assessment Start", datetime.date(2024, 6, 1))
with col2:
    pre_end = st.date_input("Pre-War End", datetime.date(2021, 12, 31))
    post_end = st.date_input("Assessment End", datetime.date.today())

# --- 3. LOGIC ---
def get_building_fc(aoi, source):
    if source == "Google Open Buildings (V3)":
        return ee.FeatureCollection("GOOGLE/Research/open-buildings/v3/polygons").filterBounds(aoi)
    elif source == "OpenStreetMap (OSM)":
        # Community-maintained OSM layer
        return ee.FeatureCollection("projects/sat-io/open-datasets/OSM/buildings").filterBounds(aoi)
    else:
        return ee.FeatureCollection("projects/sat-io/open-datasets/GBA/polygons").filterBounds(aoi)

def perform_damage_test(aoi, mask):
    s1 = ee.ImageCollection('COPERNICUS/S1_GRD').filterBounds(aoi).select('VV')
    pre = s1.filterDate(str(pre_start), str(pre_end))
    post = s1.filterDate(str(post_start), str(post_end))

    def stats(col): return {'m': col.mean(), 's': col.reduce(ee.Reducer.stdDev()), 'n': col.count()}
    s_pre, s_post = stats(pre), stats(post)

    # Welch's T-Test Calculation
    t_score = s_pre['m'].subtract(s_post['m']).abs().divide(
        (s_pre['s'].pow(2).divide(s_pre['n'])).add(s_post['s'].pow(2).divide(s_post['n'])).sqrt()
    )
    return t_score.updateMask(mask).updateMask(t_score.gt(3.5))

# --- 4. MAP INTERFACE ---
m = geemap.Map(center=[48.379, 38.016], zoom=12) # Centered on Donbas region
aoi_input = st.text_input("Coordinates (MinLon, MinLat, MaxLon, MaxLat)", "37.45, 47.05, 37.65, 47.15")

if st.button("Generate Damage Map"):
    coords = [float(x.strip()) for x in aoi_input.split(',')]
    roi = ee.Geometry.Rectangle(coords)
    def calculate_population_impact(damage_layer, aoi):
        """Calculates the estimated population within damaged pixels"""

    # 1. Load WorldPop Global Project Population (100m resolution)
    # This dataset contains the number of people per pixel
    worldpop = ee.ImageCollection("WorldPop/GP/100m/pop") \
        .filterBounds(aoi) \
        .filter(ee.Filter.date('2020-01-01', '2020-12-31')) \
        .first() # Use the most recent baseline

    # 2. Mask WorldPop by your Damage Layer
    # This ensures we only count people in areas flagged as damaged
    impacted_pop_image = worldpop.updateMask(damage_layer.gt(0))

    # 3. Sum the population within the AOI
    stats = impacted_pop_image.reduceRegion(
        reducer=ee.Reducer.sum(),
        geometry=aoi,
        scale=100, # Matches WorldPop resolution
        maxPixels=1e9
    )

        return stats.get('population')

# Integration into your existing Streamlit 'Run' button:
# if st.button("Generate Damage Map"):
#     ... (existing damage analysis code) ...
#     total_affected = calculate_population_impact(damage_layer, roi).getInfo()
#     st.metric("Estimated People Affected", f"{int(total_affected):,}")

    # Pre-flight Check for Footprint Coverage
    buildings = get_building_fc(roi, footprint_source)
    count = buildings.size().getInfo()

    if count == 0:
        st.warning(f"⚠️ No footprints found in this AOI using **{footprint_source}**.")
        if footprint_source != "OpenStreetMap (OSM)":
            st.info("💡 **Tip:** Try switching to **OpenStreetMap (OSM)**. It has broader coverage for Eastern Europe and Russia.")
    else:
        with st.spinner(f"Analyzing {count} building footprints..."):
            b_mask = ee.Image.constant(0).paint(buildings, 1)
            damage_layer = perform_damage_test(roi, b_mask)

            # Add Legend to the Map
            legend_dict = {
                'Likely Damage (T > 3.5)': '#ffffb2',
                'Significant Damage (T > 5)': '#fd8d3c',
                'Severe Destruction (T > 8)': '#e31a1c'
            }
            m.add_legend(title="Damage Confidence", legend_dict=legend_dict)

            # Add Layers
            m.addLayer(b_mask.updateMask(b_mask), {'palette': 'gray'}, 'Building Outlines')
            m.addLayer(damage_layer, {'min': 3.5, 'max': 10, 'palette': ['#ffffb2', '#fd8d3c', '#e31a1c']}, 'Damage Map')
            m.centerObject(roi, 14)
            st.success("Analysis complete.")

m.to_streamlit(height=600)
