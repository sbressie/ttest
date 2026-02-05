import streamlit as st
import ee
import datetime
import folium
from streamlit_folium import st_folium

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

# --- 2. THE MAP HELPER ---
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

# --- 3. SESSION STATE INITIALIZATION ---
# This is the secret to stopping the flicker!
if 'map_done' not in st.session_state:
    st.session_state.map_done = False
if 'folium_map' not in st.session_state:
    st.session_state.folium_map = None

# --- 4. UI ---
st.title("🛰️ SAR Damage Assessment")

with st.sidebar:
    st.header("Parameters")
    t_thresh = st.slider("T-Score Threshold", 2.0, 10.0, 3.5, 0.5)
    aoi_input = st.text_input("AOI (minLon, minLat, maxLon, maxLat)", "37.45, 47.05, 37.65, 47.15")
    run_button = st.button("🚀 Run Analysis")

# --- 5. ANALYSIS LOGIC ---
if run_button:
    try:
        coords = [float(x.strip()) for x in aoi_input.split(',')]
        roi = ee.Geometry.Rectangle(coords)
        
        # (Your perform_damage_test_welch logic here...)
        # damage_raw = perform_damage_test_welch(...)

        # Create the map and store it in session_state
        m = folium.Map(location=[coords[1], coords[0]], zoom_start=14)
        
        # Example layer (replace with your damage_raw)
        # m.add_ee_layer(damage_raw, {...}, 'Damage')
        
        st.session_state.folium_map = m
        st.session_state.map_done = True
        
    except Exception as e:
        st.error(f"Analysis Error: {e}")

# --- 6. PERSISTENT DISPLAY ---
# This stays outside the 'if run_button' block
if st.session_state.map_done:
    st_folium(
        st.session_state.folium_map, 
        width=1200, 
        height=600, 
        key="main_map"  # Unique key prevents flickering/resetting
    )
