import streamlit as st
import ee
import plotly.graph_objects as go
import json
import datetime
import os
from google.oauth2 import service_account

# --- 1. CONFIGURATION & AUTH ---
st.set_page_config(page_title="VIDA Damage Assessment", layout="wide")

def authenticate_gee():
    """Initializes GEE using Service Account with v1 API compatibility."""
    if 'ee_initialized' not in st.session_state:
        try:
            cred_info = st.secrets["EARTHENGINE_SERVICE_ACCOUNT"]
            project_id = cred_info.get('project_id')
            credentials = service_account.Credentials.from_service_account_info(
                cred_info, scopes=['https://www.googleapis.com/auth/earthengine']
            )
            # Explicitly pass project to satisfy Community Tier requirements
            ee.Initialize(credentials, project=project_id)
            st.session_state['ee_initialized'] = True
            st.session_state['project_id'] = project_id
            st.sidebar.success(f"✅ GEE Connected: {project_id}")
        except Exception as e:
            st.sidebar.error(f"❌ Auth Error: {e}")
            st.session_state['ee_initialized'] = False

authenticate_gee()

# --- 2. DATA LOADING ---
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

# --- 3. ANALYSIS LOGIC ---
def perform_damage_test(aoi, mask, p_start, p_end, a_start, a_end):
    """Welch's t-test logic for SAR change detection."""
    s1 = ee.ImageCollection('COPERNICUS/S1_GRD').filterBounds(aoi)\
           .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VV')).select('VV')
    
    pre = s1.filterDate(str(p_start), str(p_end))
    post = s1.filterDate(str(a_start), str(a_end))
    
    def stats(col): 
        return {'m': col.mean(), 's': col.reduce(ee.Reducer.stdDev()), 'n': col.count()}
    
    s_pre, s_post = stats(pre), stats(post)
    
    # Welch's T-Test
    t_score = s_pre['m'].subtract(s_post['m']).abs().divide(
        (s_pre['s'].pow(2).divide(s_pre['n'])).add(s_post['s'].pow(2).divide(s_post['n'])).sqrt()
    )
    return t_score.updateMask(mask).updateMask(t_score.gt(3.5))

def calculate_pop(damage_layer, aoi):
    pop = ee.ImageCollection("WorldPop/GP/100m/pop").filterBounds(aoi).sort('year', False).first()
    stats = pop.updateMask(damage_layer.gt(0)).reduceRegion(
        reducer=ee.Reducer.sum(), geometry=aoi, scale=100, maxPixels=1e9
    )
    return stats.get('population')

# --- 4. UI LAYOUT ---
st.title("🛰️ VIDA Building Damage & Population Analysis")

# Header Metrics
m_col1, m_col2 = st.columns(2)
pop_placeholder = m_col1.empty()
structure_placeholder = m_col2.empty()

with st.sidebar:
    st.header("Settings")
    selected_country = st.selectbox("Select Country", country_names, index=country_names.index("Ukraine") if "Ukraine" in country_names else 0)
    current_iso = iso_map.get(selected_country, "UKR")
    
    st.markdown("---")
    st.subheader("Baseline (Pre)")
    pre_s = st.date_input("Start", datetime.date(2021, 1, 1), key="p1")
    pre_e = st.date_input("End", datetime.date(2021, 12, 31), key="p2")
    
    st.subheader("Assessment (Post)")
    post_s = st.date_input("Start", datetime.date(2024, 6, 1), key="a1")
    post_e = st.date_input("End", datetime.date.today(), key="a2")

aoi_input = st.text_input("CSV Bounding Box (minLon, minLat, maxLon, maxLat)", "37.45, 47.05, 37.65, 47.15")

# --- 5. EXECUTION & MAP ---
if st.button("🚀 Run Analysis"):
    if not st.session_state.get('ee_initialized'):
        st.error("Earth Engine not initialized. Check sidebar for errors.")
    else:
        try:
            coords = [float(x.strip()) for x in aoi_input.split(',')]
            roi = ee.Geometry.Rectangle(coords)

            with st.status("Crunching Satellite Data...") as status:
                # 1. Fetch Buildings
                buildings = ee.FeatureCollection(f"projects/sat-io/open-datasets/VIDA_COMBINED/{current_iso}").filterBounds(roi)
                count = buildings.size().getInfo()
                structure_placeholder.metric("Total Structures", f"{count:,}")

                if count > 0:
                    # 2. Damage Analysis (Welch's t-test)
                    b_mask = ee.Image.constant(1).clip(buildings).mask()
                    damage = perform_damage_test(roi, b_mask, pre_s, pre_e, post_s, post_e)
                    
                    # 3. Population Impact
                    pop_val = calculate_pop(damage, roi).getInfo()
                    pop_placeholder.metric("Estimated People Affected", f"{int(pop_val or 0):,}")

                    # 4. Generate Map IDs
                    # We use .getMapId() directly to bypass geemap's internal checks
                    build_mapid = ee.Image().byte().paint(buildings, 1, 2).getMapId({'palette': '00FFFF'})
                    damage_mapid = damage.getMapId({'min': 3.5, 'max': 10, 'palette': ['#ffffb2', '#fd8d3c', '#e31a1c']})

                
                    # Get your GEE tile URL as we did before
map_id = ee.Image(my_image).getMapId({'palette': 'cyan'})
tile_url = map_id['tile_fetcher'].url_format

fig = go.Figure(go.Scattermapbox())
fig.update_layout(
    mapbox=dict(
        style="carto-positron", # No Mapbox token needed for this style
        layers=[{
            "below": 'traces',
            "sourcetype": "raster",
            "source": [tile_url]
        }],
        center={"lat": 35.72, "lon": 51.40},
        zoom=12
    )
)
st.plotly_chart(fig, use_container_width=True)
                    
                    status.update(label="Analysis Complete!", state="complete")
                else:
                    st.warning("No buildings found. Try a different bounding box.")
        except Exception as e:
            st.error(f"Render Error: {e}")
