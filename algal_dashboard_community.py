import pandas as pd
import folium
from branca.colormap import LinearColormap
from streamlit_folium import st_folium
import streamlit as st
import altair as alt
import os
from datetime import timedelta
import requests  # Added for fetching data from ArcGIS

# ---------------------------
# Fetch data from ArcGIS feature services
# ---------------------------
@st.cache_data
def fetch_from_arcgis(base_url, is_point=False):
    query_url = f"{base_url}/query"
    
    # Get total count
    params = {
        'where': '1=1',
        'returnCountOnly': 'true',
        'f': 'json'
    }
    response = requests.get(query_url, params=params)
    response.raise_for_status()
    count = response.json()['count']
    
    # Fetch all features in batches
    features = []
    batch_size = 1000  # Adjust if the service has a different maxRecordCount
    for offset in range(0, count, batch_size):
        params = {
            'where': '1=1',
            'outFields': '*',
            'resultOffset': offset,
            'resultRecordCount': batch_size,
            'f': 'json'
        }
        if is_point:
            params['returnGeometry'] = 'true'
            params['outSR'] = '4326'  # WGS84 for lat/long
        r = requests.get(query_url, params=params)
        r.raise_for_status()
        data = r.json()
        features.extend(data['features'])
    
    # Convert to DataFrame
    rows = []
    for f in features:
        row = f['attributes']
        if is_point and 'geometry' in f:
            geom = f['geometry']
            row['Longitude'] = geom.get('x')
            row['Latitude'] = geom.get('y')
        rows.append(row)
    df = pd.DataFrame(rows)
    return df

@st.cache_data
def load_data(sample_url, sites_url):
    # Fetch sample data (assuming it's a table without geometry)
    df = fetch_from_arcgis(sample_url, is_point=False)
    
    if df.empty:
        st.warning("⚠️ No sample data fetched. Check the URL.")
    
    # Process date and normalize Result_Name
    if not df.empty:
        df['Date_Sample_Collected'] = pd.to_datetime(df['Date_Sample_Collected'], errors='coerce', unit='ms') if df['Date_Sample_Collected'].dtype == 'int64' else pd.to_datetime(df['Date_Sample_Collected'], errors='coerce')  # Handle Unix timestamp if present
        if 'Result_Name' in df.columns:
            df['Result_Name'] = (
                df['Result_Name']
                .astype(str)
                .str.strip()
                .str.replace(r'\s+', ' ', regex=True)
                .str.replace('\xa0', ' ', regex=False)
            )
    
    # Fetch monitoring sites (assuming points with geometry)
    coords_df = fetch_from_arcgis(sites_url, is_point=True)
    
    if coords_df.empty:
        st.error("⚠️ No coordinates data fetched. Check the URL.")
        st.stop()
    
    # Assume 'Site_Description' is the join field; adjust if different
    df = df.merge(coords_df[['Site_Description', 'Latitude', 'Longitude']], on="Site_Description", how="left")
    df['Latitude'] = pd.to_numeric(df['Latitude'], errors='coerce')
    df['Longitude'] = pd.to_numeric(df['Longitude'], errors='coerce')
    
    return df

@st.cache_data
def load_community(file_path="MASTER spreadsheet of community summaries.xlsx"):
    # This remains local, as per original
    if not os.path.exists(file_path):
        st.warning(f"⚠️ Community data file '{file_path}' not found. Using empty dataset.")
        return pd.DataFrame()
  
    df = pd.read_excel(file_path, sheet_name=0)
    df.columns = df.columns.str.strip()
    if 'Lat' in df.columns:
        df = df.rename(columns={'Lat': 'Latitude'})
    if 'Long' in df.columns:
        df = df.rename(columns={'Long': 'Longitude'})
    if not pd.api.types.is_datetime64_any_dtype(df['Date']):
        df['Date'] = pd.to_datetime(df['Date'], origin='1899-12-30', errors='coerce')
    date_idx = df.columns.get_loc('Date')
    total_idx = df.columns.get_loc('Total plankton')
    species_cols = df.columns[date_idx + 1 : total_idx + 1].tolist()
    melted_df = pd.melt(df,
                        id_vars=['Location', 'Latitude', 'Longitude', 'Date'],
                        value_vars=species_cols,
                        var_name='Result_Name',
                        value_name='Result_Value_Numeric')
    melted_df['Site_Description'] = melted_df['Location']
    melted_df['Date_Sample_Collected'] = melted_df['Date']
    melted_df = melted_df.drop(['Location', 'Date'], axis=1)
    melted_df['Result_Value_Numeric'] *= 1000
    melted_df['Units'] = 'cells/L'
    melted_df['Result_Name'] = (
        melted_df['Result_Name']
            .astype(str)
            .str.strip()
            .str.replace(r'\s+', ' ', regex=True)
            .str.replace('\xa0', ' ', regex=False)
    )
    melted_df['Result_Name'] += ' *'
    melted_df['Latitude'] = pd.to_numeric(melted_df['Latitude'], errors='coerce')
    melted_df['Longitude'] = pd.to_numeric(melted_df['Longitude'], errors='coerce')
    return melted_df

# ---------------------------
# Build Streamlit app
# ---------------------------
def main():
    st.set_page_config(
        page_title="Harmful Algal Bloom Monitoring - South Australia",
        layout="wide",
        initial_sidebar_state="expanded"
    )
    # ---------------------------
    # Custom CSS (unchanged)
    # ---------------------------
    st.markdown("""
    <style>
    # ... (keep the original CSS)
    </style>
    """, unsafe_allow_html=True)

    ## Fetch data from government website
    # Base service URL (both layers/tables live here)
    BASE_SERVICE_URL = "https://services6.arcgis.com/WS2XycMNFieWAsfS/arcgis/rest/services/HarmfulAlgalBloom_MonitoringSites/FeatureServer"

    # For sample measurements (non-spatial table)
    sample_data_url = f"{BASE_SERVICE_URL}/1"          # ← your URL, this is correct for samples

    # For monitoring sites (spatial points)
    monitoring_sites_url = f"{BASE_SERVICE_URL}/0"     # ← add this one
    
    df = load_data(sample_data_url, monitoring_sites_url)
    community_df = load_community()
    
    
if __name__ == "__main__":
    main()
