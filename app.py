import streamlit as st
import ee
import json
import datetime
import pandas as pd
from google.oauth2 import service_account
import folium
from streamlit_folium import st_folium

# --- 1. CONFIG & AUTH ---
st.set_page_config(page_title="SAR Damage Assessment", layout="wide")

def authenticate_gee():
    if 'ee_initialized' not in st.session_state:
        try:
            # Assumes you have your service account JSON in Streamlit Secrets
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
    """Bypasses geemap to add GEE layers to folium directly."""
    map_id_dict = ee.Image(ee_image_object).getMapId(vis_params)
    folium.raster_layers.TileLayer(
        tiles=map_id_dict['tile_fetcher'].url_format,
        attr='Google Earth Engine',
        name=name,
        overlay=True,
        control=True
    ).add_to(self)

folium.Map.add_ee_layer = add_ee_layer

# --- 3. SAR ANALYSIS LOGIC ---
def perform_damage_test_welch(aoi, buildings, p_start, p_end, a_start, a_end, threshold):
    # Sentinel-1 GRD Data
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

    # Welch's T-Test calculation
    numerator = s_pre['mean'].subtract(s_post['mean']).abs()
    var_term = (s_pre['var'].divide(s_pre['n'])).add(s_post['var'].divide(s_post['n']))
    t_score = numerator.divide(var_term.sqrt())

    building_mask = ee.Image.constant(0).paint(buildings, 1)
    return t_score.updateMask(building_mask).updateMask(t_score.gt(threshold))

# --- 4. MAIN UI ---
st.title("🛰️ SAR Damage Assessment")

with st.sidebar:
    st.header("Parameters")
    t_thresh = st.slider("T-Score Threshold", 2.0, 10.0, 3.5, 0.5)
    pre_s = st.date_input("Baseline Start", datetime.date(2021, 1, 1))
    pre_e = st.date_input("Baseline End", datetime.date(2021, 12, 31))
    post_s = st.date_input("Assessment Start", datetime.date(2024, 6, 1))
    post_e = st.date_input("Assessment End", datetime.date.today())

aoi_input = st.text_input("AOI (minLon, minLat, maxLon, maxLat)", "37.45, 47.05, 37.65, 47.15")

if st.button("🚀 Run Analysis"):
    if st.session_state.get('ee_initialized'):
        try:
            coords = [float(x.strip()) for x in aoi_input.split(',')]
            roi = ee.Geometry.Rectangle(coords)
            
            # Fetch Building Footprints (MS Global / Vida)
            # Adjust the project path if your VIDA dataset is stored elsewhere
            buildings = ee.FeatureCollection("projects/sat-io/open-datasets/VIDA_COMBINED/UKR").filterBounds(roi)
            
            damage_raw = perform_damage_test_welch(roi, buildings, pre_s, pre_e, post_s, post_e, t_thresh)

            # Initialize Map
            m = folium.Map(location=[coords[1], coords[0]], zoom_start=14)
            folium.TileLayer('Stamen Terrain', attr="Stamen").add_to(m)

            # Add the SAR Damage Result
            m.add_ee_layer(damage_raw, {
                'min': t_thresh,
                'max': t_thresh + 6,
                'palette': ['#ffffb2', '#fecc5c', '#fd8d3c', '#f03b20', '#bd0026']
            }, 'SAR Damage (T-Test)')

            # Render the Map
            st_folium(m, width=1200, height=600)
            
        except Exception as e:
            st.error(f"Analysis Error: {e}")
