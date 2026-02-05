import streamlit as st
import ee
import geemap as geemap
import folium as foliumap
import json
import datetime
import pandas as pd
from google.oauth2 import service_account


# --- 1. CONFIG & AUTH ---
st.set_page_config(page_title="SAR Damage Assessment", layout="wide")

def authenticate_gee():
    if 'ee_initialized' not in st.session_state:
        try:
            cred_info = st.secrets["EARTHENGINE_SERVICE_ACCOUNT"]
            if hasattr(cred_info, "to_dict"):
                cred_info = cred_info.to_dict()

            credentials = service_account.Credentials.from_service_account_info(
                cred_info, scopes=['https://www.googleapis.com/auth/earthengine']
            )
            ee.Initialize(credentials, project=cred_info.get('project_id'))
            st.session_state['ee_initialized'] = True
        except Exception as e:
            st.sidebar.error(f"❌ Auth Error: {e}")

authenticate_gee()

# --- 2. DATA HELPERS ---
@st.cache_data
def load_iso_data():
    try:
        with open('iso.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return [{"name": "Ukraine", "code": "UKR"}]

def perform_damage_test_welch(aoi, buildings, p_start, p_end, a_start, a_end, threshold):
    """
    Implements Welch's t-test:
    t = |mean1 - mean2| / sqrt( (var1/n1) + (var2/n2) )
    """
    s1 = ee.ImageCollection('COPERNICUS/S1_GRD').filterBounds(aoi).select('VV')

    pre = s1.filterDate(str(p_start), str(p_end))
    post = s1.filterDate(str(a_start), str(a_end))

    def get_stats(col):
        return {
            'mean': col.mean(),
            'var': col.reduce(ee.Reducer.variance()),
            'n': col.count()
        }

    s_pre = get_stats(pre)
    s_post = get_stats(post)

    # Welch's Formula
    numerator = s_pre['mean'].subtract(s_post['mean']).abs()
    # Calculate pooled variance components
    var_term = (s_pre['var'].divide(s_pre['n'])).add(s_post['var'].divide(s_post['n']))
    t_score = numerator.divide(var_term.sqrt())

    # Create a mask from buildings
    building_mask = ee.Image.constant(0).paint(buildings, 1)

    # Mask by threshold and strictly by building footprint
    return t_score.updateMask(building_mask).updateMask(t_score.gt(threshold))

def calculate_population_impact(damage_layer, aoi):
    pop_image = ee.ImageCollection("projects/sat-io/open-datasets/ORNL/LANDSCAN_GLOBAL") \
                  .filterDate('2022-01-01', '2022-12-31').first().select('b1')

    impacted_pop_image = pop_image.updateMask(damage_layer.gt(0))
    stats = impacted_pop_image.reduceRegion(
        reducer=ee.Reducer.sum(),
        geometry=aoi,
        scale=1000,
        maxPixels=1e9
    )
    return stats.get('b1')

# --- 3. UI LAYOUT ---
countries = load_iso_data()
country_options = {c['name']: c['code'] for c in countries}

report_container = st.container()

with st.sidebar:
    st.header("Analysis Parameters")

    selected_country_name = st.selectbox("Select Country", options=list(country_options.keys()), index=0)
    selected_iso = country_options[selected_country_name]

    footprint_source = st.selectbox("Building Footprint Set", ["MS Global Buildings"])
    #other building fp sets ("Building Footprint Set", ["Google Open Buildings (V3)", "MS Global Buildings"])

    st.subheader("Dates")
    pre_s = st.date_input("Baseline Start", datetime.date(2021, 1, 1))
    pre_e = st.date_input("Baseline End", datetime.date(2021, 12, 31))
    post_s = st.date_input("Assessment Start", datetime.date(2024, 6, 1))
    post_e = st.date_input("Assessment End", datetime.date.today())

    st.subheader("Sensitivity")
    # T-score threshold slider
    t_thresh = st.slider("T-Score Threshold (Confidence)", 2.0, 10.0, 3.5, 0.5,
                         help="Higher values = Higher confidence, but identifies fewer damaged buildings.")

    st.subheader("Map Layers")
    show_footprints = st.checkbox("Show Building Outlines", value=True)

# --- 4. MAIN UI ---

st.markdown("### 🗺️ Define Area of Interest")
st.caption("Use [Klokantech Bounding Box Tool](https://boundingbox.klokantech.com/) (Format: CSV) and paste above.")
aoi_input = st.text_input("AOI Bounding Box (minLon, minLat, maxLon, maxLat)", "37.45, 47.05, 37.65, 47.15")
    #aoi_input = st.text_input("CSV Bounding Box (minLon, minLat, maxLon, maxLat)", "37.45, 47.05, 37.65, 47.15")

if 'map_obj' not in st.session_state:
    st.session_state.map_obj = None
if 'report_data' not in st.session_state:
    st.session_state.report_data = None



if st.button("🚀 Run Welch's T-Test Analysis"):
    if st.session_state.get('ee_initialized'):
        try:
            coords = [float(x.strip()) for x in aoi_input.split(',')]
            roi = ee.Geometry.Rectangle(coords)

            with st.status("Computing Welch's T-Test...") as status:
                # 1. Fetch Buildings
                if footprint_source == "Google Open Buildings (V3)":
                    buildings = ee.FeatureCollection("GOOGLE/Research/open-buildings/v3/polygons").filterBounds(roi)
                else:
                    buildings = ee.FeatureCollection(f"projects/sat-io/open-datasets/VIDA_COMBINED/{selected_iso}").filterBounds(roi)

                # 2. Perform Welch's T-Test with the user-defined threshold
                damage_raw = perform_damage_test_welch(roi, buildings, pre_s, pre_e, post_s, post_e, t_thresh)

                # 3. GEOMETRIC CLIP: This clips the raster data to the vector edges
                damage_clipped = damage_raw.clip(buildings)

                # 4. Map Setup
                m = geemap.Map()
                m.add_basemap("OpenStreetMap")
                m.to_streamlit(height=700, responsive=True)
                #m.centerObject(roi, 16)
                #m.to_streamlit(height=600)
                

                if show_footprints:
                    outline = ee.Image().paint(buildings, 0, 1.5) # Thicker outline for visibility
                    m.addLayer(outline, {'palette': '28659c'}, 'Building Outlines')

                # Heatmap: Green (low sig) to Red (high sig)
                m.addLayer(damage_clipped, {
                    'min': t_thresh,
                    'max': t_thresh + 6,
                    'palette': ['#ffffb2', '#fecc5c', '#fd8d3c', '#f03b20', '#bd0026']
                }, 'Welch T-Test (Clipped)')

                st.session_state.map_obj = m

                # 5. Stats calculation
                pop_val = calculate_population_impact(damage_raw, roi).getInfo() or 0
                st.session_state.report_data = {
                    "country": selected_country_name,
                    "count": buildings.size().getInfo(),
                    "pop": int(pop_val),
                    "thresh": t_thresh
                }
                status.update(label="Analysis Complete!", state="complete")

        except Exception as e:
            st.error(f"Analysis Error: {e}")

# --- 5. PERSISTENT DISPLAY ---
if st.session_state.report_data:
    with report_container:
        d = st.session_state.report_data
        st.info(f"Analysis Result: {d['country']} at $t > {d['thresh']}$")
        c1, c2 = st.columns(2)
        c1.metric("Buildings Analyzed", f"{d['count']:,}")
        c2.metric("Est. Pop. in Damage Zone", f"{d['pop']:,}")
df = pd.DataFrame([st.session_state.report_data])
csv = df.to_csv(index=False).encode('utf-8')

st.download_button(
    label="📥 Download Assessment Report (CSV)",
    data=csv,
    file_name=f"damage_report_{selected_iso}.csv",
    mime="text/csv",
)
if st.session_state.map_obj:
    st.session_state.map_obj.to_streamlit(height=700)
