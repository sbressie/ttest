import streamlit as st
import ee
import json
import datetime
import pandas as pd
import folium
from streamlit_folium import st_folium
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

def add_ee_layer(self, ee_image_object, vis_params, name):
    map_id_dict = ee.Image(ee_image_object).getMapId(vis_params)
    folium.raster_layers.TileLayer(
        tiles=map_id_dict['tile_fetcher'].url_format,
        attr='Google Earth Engine',
        name=name,
        overlay=True,
        control=True
    ).add_to(self)

folium.Map.add_ee_layer = add_ee_layer

def perform_damage_test_welch(aoi, buildings, p_start, p_end, a_start, a_end, threshold, orbit_pass):
    """
    Optimized SAR collection: filters by orbit direction (ASCENDING/DESCENDING) 
    to ensure geometric consistency.
    """
    s1 = ee.ImageCollection('COPERNICUS/S1_GRD') \
        .filterBounds(aoi) \
        .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VV')) \
        .filter(ee.Filter.eq('instrumentMode', 'IW')) \
        .filter(ee.Filter.eq('orbitProperties_pass', orbit_pass)) \
        .select('VV')

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

    # Welch's T-Test calculation
    numerator = s_pre['mean'].subtract(s_post['mean']).abs()
    var_term = (s_pre['var'].divide(s_pre['n'])).add(s_post['var'].divide(s_post['n']))
    t_score = numerator.divide(var_term.sqrt())

    building_mask = ee.Image.constant(0).paint(buildings, 1)
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

# --- 3. SESSION STATE ---
if 'map_obj' not in st.session_state:
    st.session_state.map_obj = None
if 'report_data' not in st.session_state:
    st.session_state.report_data = None

# --- 4. UI LAYOUT ---
countries = load_iso_data()
country_options = {c['name']: c['code'] for c in countries}

st.title("🛰️ SAR Damage Assessment")

with st.sidebar:
    st.header("Analysis Parameters")
    selected_country_name = st.selectbox("Select Country", options=list(country_options.keys()), index=0)
    selected_iso = country_options[selected_country_name]

    st.subheader("Satellite Optimization")
    orbit_direction = st.radio(
        "Orbit Direction", 
        ["ASCENDING", "DESCENDING"], 
        index=0,
        help="Ascending (South to North) and Descending (North to South) have different radar look-angles. Stick to one for accuracy."
    )

    st.subheader("Dates")
    pre_s = st.date_input("Baseline Start", datetime.date(2021, 1, 1))
    pre_e = st.date_input("Baseline End", datetime.date(2021, 12, 31))
    post_s = st.date_input("Assessment Start", datetime.date(2024, 6, 1))
    post_e = st.date_input("Assessment End", datetime.date.today())

    st.subheader("Sensitivity")
    t_thresh = st.slider("T-Score Threshold", 2.0, 10.0, 3.5, 0.5)
    show_footprints = st.checkbox("Show Building Outlines", value=True)

    # --- New: Availability Check ---
    if st.button("🔍 Check Image Availability"):
        if st.session_state.get('ee_initialized'):
            try:
                coords = [float(x.strip()) for x in aoi_input.split(',')]
                tmp_roi = ee.Geometry.Rectangle(coords)
                
                def count_orbit(direction):
                    return ee.ImageCollection('COPERNICUS/S1_GRD') \
                        .filterBounds(tmp_roi) \
                        .filter(ee.Filter.eq('orbitProperties_pass', direction)) \
                        .filterDate(str(pre_s), str(post_e)) \
                        .size().getInfo()

                asc_count = count_orbit('ASCENDING')
                desc_count = count_orbit('DESCENDING')
                
                st.sidebar.write(f"📈 **Images found (Baseline to Present):**")
                st.sidebar.write(f"- Ascending: {asc_count}")
                st.sidebar.write(f"- Descending: {desc_count}")
                
                if asc_count == 0 and desc_count == 0:
                    st.sidebar.warning("No images found for these dates/AOI.")
            except Exception as e:
                st.sidebar.error(f"Check failed: {e}")
    run_button = st.button("🚀 Run Welch's T-Test Analysis")

st.markdown("### 🗺️ Define Area of Interest")
aoi_input = st.text_input("AOI Bounding Box (minLon, minLat, maxLon, maxLat)", "37.45, 47.05, 37.65, 47.15")

# --- 5. ANALYSIS EXECUTION ---
if run_button:
    if st.session_state.get('ee_initialized'):
        try:
            coords = [float(x.strip()) for x in aoi_input.split(',')]
            roi = ee.Geometry.Rectangle(coords)

            with st.status("Performing Track Optimization & Analysis...") as status:
                # 1. Fetch Buildings
                buildings = ee.FeatureCollection(f"projects/sat-io/open-datasets/VIDA_COMBINED/{selected_iso}").filterBounds(roi)

                # 2. Perform Analysis with optimized orbit pass
                damage_raw = perform_damage_test_welch(roi, buildings, pre_s, pre_e, post_s, post_e, t_thresh, orbit_direction)
                damage_clipped = damage_raw.clip(buildings)

                # 3. Create Folium Map
                m = folium.Map(location=[coords[1], coords[0]], zoom_start=14)
                folium.TileLayer(
                    tiles='https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}',
                    attr='Google', name='Google Satellite', overlay=False, control=True
                ).add_to(m)
                
                if show_footprints:
                    outline = ee.Image().paint(buildings, 0, 1)
                    m.add_ee_layer(outline, {'palette': '28659c'}, 'Building Outlines')

                m.add_ee_layer(damage_clipped, {
                    'min': t_thresh, 'max': t_thresh + 6,
                    'palette': ['#ffffb2', '#fecc5c', '#fd8d3c', '#f03b20', '#bd0026']
                }, 'Welch T-Test (Clipped)')
                
                folium.LayerControl().add_to(m)
                st.session_state.map_obj = m

                # 4. Stats
                pop_val = calculate_population_impact(damage_raw, roi).getInfo() or 0
                st.session_state.report_data = {
                    "country": selected_country_name,
                    "count": buildings.size().getInfo(),
                    "pop": int(pop_val),
                    "thresh": t_thresh,
                    "orbit": orbit_direction
                }
                status.update(label="Analysis Complete!", state="complete")

        except Exception as e:
            st.error(f"Analysis Error: {e}")

# --- 6. PERSISTENT DISPLAY ---
if st.session_state.report_data:
    d = st.session_state.report_data
    st.info(f"Analysis Result: {d['country']} | Orbit: {d['orbit']} | $t > {d['thresh']}$")
    c1, c2, c3 = st.columns([1, 1, 1])
    c1.metric("Buildings Analyzed", f"{d['count']:,}")
    c2.metric("Est. Pop. Impacted", f"{d['pop']:,}")
    
    df = pd.DataFrame([d])
    csv = df.to_csv(index=False).encode('utf-8')
    c3.download_button("📥 Download CSV", csv, f"damage_{selected_iso}.csv", "text/csv")

if st.session_state.map_obj:
    st_folium(st.session_state.map_obj, width=1200, height=600, key="damage_map")
if st.session_state.report_data:
    d = st.session_state.report_data
    
    # Use .get() to provide fallbacks and prevent KeyErrors
    country = d.get('country', 'Unknown')
    orbit = d.get('orbit', 'Not Specified')
    thresh = d.get('thresh', 3.5)
    
    #st.info(f"Analysis Result: {country} | Orbit: {orbit} | $t > {thresh}$")
    
    #c1, c2, c3 = st.columns([1, 1, 1])
    #c1.metric("Buildings Analyzed", f"{d.get('count', 0):,}")
    #c2.metric("Est. Pop. Impacted", f"{d.get('pop', 0):,}")
    
    # Download logic
    #df = pd.DataFrame([d])
    #csv = df.to_csv(index=False).encode('utf-8')
    #c3.download_button("📥 Download CSV", csv, f"damage_report.csv", "text/csv")
