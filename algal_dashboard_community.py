import pandas as pd
import folium
from branca.colormap import LinearColormap
from streamlit_folium import st_folium
import streamlit as st
import altair as alt
import os
from datetime import timedelta
import requests

# ---------------------------
# Fetch data from ArcGIS Feature Services
# ---------------------------
@st.cache_data(ttl=3600)
def fetch_from_arcgis(base_url, layer_id, is_point=False):
    query_url = f"{base_url}/{layer_id}/query"
    params = {
        'where': '1=1',
        'outFields': '*',
        'returnGeometry': 'true' if is_point else 'false',
        'outSR': '4326',
        'f': 'json'
    }
    rows = []
    offset = 0
    while True:
        params['resultOffset'] = offset
        try:
            r = requests.get(query_url, params=params, timeout=20)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            st.error(f"ArcGIS request failed (layer {layer_id}): {e}")
            return pd.DataFrame()
        features = data.get("features", [])
        if not features:
            break
        for f in features:
            attrs = f.get("attributes", {}).copy()
            if is_point:
                geom = f.get("geometry", {})
                attrs["Longitude"] = geom.get("x")
                attrs["Latitude"] = geom.get("y")
            rows.append(attrs)
        if not data.get("exceededTransferLimit", False):
            break
        offset += len(features)
    if not rows:
        st.warning(f"No features returned from layer {layer_id}")
        return pd.DataFrame()
    return pd.DataFrame(rows)
    
@st.cache_data(ttl=7200)
def load_gov_data():

    BASE_SERVICE_URL = "https://services6.arcgis.com/WS2XycMNFieWAsfS/arcgis/rest/services/HarmfulAlgalBloom_MonitoringSites/FeatureServer"

    sample_df = fetch_from_arcgis(BASE_SERVICE_URL, 1, is_point=False)
    sites_df = fetch_from_arcgis(BASE_SERVICE_URL, 0, is_point=True)

    if sample_df.empty:
        st.error("No government sample data retrieved.")
        return pd.DataFrame()

    if sites_df.empty:
        st.error("No monitoring site geometry retrieved.")
        return pd.DataFrame()

    # -------------------------
    # Clean column names
    # -------------------------
    sample_df.columns = sample_df.columns.str.strip()
    sites_df.columns = sites_df.columns.str.strip()

    # -------------------------
    # Explicit Join
    # -------------------------
    SAMPLE_KEY = "Site_Number"
    SITE_KEY = "SiteNumber"

    if SAMPLE_KEY not in sample_df.columns:
        st.error(f"{SAMPLE_KEY} not found in sample data.")
        return pd.DataFrame()

    if SITE_KEY not in sites_df.columns:
        st.error(f"{SITE_KEY} not found in site geometry.")
        return pd.DataFrame()

    sample_df[SAMPLE_KEY] = sample_df[SAMPLE_KEY].astype(str)
    sites_df[SITE_KEY] = sites_df[SITE_KEY].astype(str)

    sites_df = sites_df.rename(columns={SITE_KEY: SAMPLE_KEY})

    merged_df = sample_df.merge(
        sites_df[["Site_Number", "Latitude", "Longitude", "SiteName", "Region"]],
        on="Site_Number",
        how="left"
    )

    # -------------------------
    # Ensure numeric coordinates
    # -------------------------
    merged_df["Latitude"] = pd.to_numeric(merged_df["Latitude"], errors="coerce")
    merged_df["Longitude"] = pd.to_numeric(merged_df["Longitude"], errors="coerce")

    # -------------------------
    # Date handling
    # -------------------------
    if "Date_Sample_Collected" in merged_df.columns:

        if pd.api.types.is_numeric_dtype(merged_df["Date_Sample_Collected"]):
            merged_df["Date_Sample_Collected"] = pd.to_datetime(
                merged_df["Date_Sample_Collected"], unit="ms", errors="coerce"
            )
        else:
            merged_df["Date_Sample_Collected"] = pd.to_datetime(
                merged_df["Date_Sample_Collected"], errors="coerce"
            )

    else:
        st.warning("Date_Sample_Collected column not found.")

    # -------------------------
    # Clean species names
    # -------------------------
    if "Result_Name" in merged_df.columns:
        merged_df["Result_Name"] = (
            merged_df["Result_Name"]
            .astype(str)
            .str.strip()
            .str.replace(r"\s+", " ", regex=True)
        )

    # -------------------------
    # Ensure numeric values
    # -------------------------
    if "Result_Value_Numeric" in merged_df.columns:
        merged_df["Result_Value_Numeric"] = pd.to_numeric(
            merged_df["Result_Value_Numeric"],
            errors="coerce"
        )

    # Debug (safe)
    st.sidebar.caption(f"Gov rows: {len(merged_df)}")
    st.sidebar.caption(
        f"Gov rows with coords: {merged_df[['Latitude','Longitude']].dropna().shape[0]}"
    )

    return merged_df

@st.cache_data
def load_community(file_path="MASTER spreadsheet of community summaries.xlsx"):

    if not os.path.exists(file_path):
        st.warning("Community Excel file not found.")
        return pd.DataFrame()

    df = pd.read_excel(file_path)
    df.columns = df.columns.str.strip()

    if "Lat" in df.columns:
        df = df.rename(columns={"Lat": "Latitude"})
    if "Long" in df.columns:
        df = df.rename(columns={"Long": "Longitude"})

    # Ensure Date
    if "Date" in df.columns:
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")

    # Identify species columns automatically
    fixed_cols = ["Location", "Latitude", "Longitude", "Date"]
    species_cols = [c for c in df.columns if c not in fixed_cols]

    melted_df = pd.melt(
        df,
        id_vars=fixed_cols,
        value_vars=species_cols,
        var_name="Result_Name",
        value_name="Result_Value_Numeric"
    )

    # Clean values
    melted_df["Result_Value_Numeric"] = pd.to_numeric(
        melted_df["Result_Value_Numeric"],
        errors="coerce"
    )

    melted_df["Result_Value_Numeric"] *= 1000  # convert to cells/L

    melted_df["Result_Name"] = (
        melted_df["Result_Name"]
        .astype(str)
        .str.strip()
    ) + " *"

    melted_df["Date_Sample_Collected"] = melted_df["Date"]
    melted_df["Site_Description"] = melted_df["Location"]

    melted_df["Latitude"] = pd.to_numeric(melted_df["Latitude"], errors="coerce")
    melted_df["Longitude"] = pd.to_numeric(melted_df["Longitude"], errors="coerce")

    st.sidebar.caption(f"Community rows: {len(melted_df)}")

    return melted_df

# ---------------------------
# Streamlit App
# ---------------------------
def main():
    st.set_page_config(
        page_title="Harmful Algal Bloom Monitoring - South Australia",
        layout="wide",
        initial_sidebar_state="expanded"
    )
    
    st.markdown("""
    <style>
    .block-container {padding-top: 1rem; padding-bottom: 0.25rem;}
    footer {visibility: hidden;}
    section[data-testid="stSidebar"] {
        font-size: 11px;
        padding: 0.4rem 0.5rem 0.5rem 0.5rem;
        max-width: 350px;
    }
    section[data-testid="stSidebar"] .stMarkdown p {margin-bottom: 0.25rem;}
    .sidebar-card {
        border: none; border-radius: 0; padding: 4px 0; background: transparent;
        margin-bottom: 0.25rem; font-size: 14px; font-weight: normal; color: #666;
        font-style: italic; text-decoration: underline;
    }
    section[data-testid="stSidebar"] [data-testid="stCheckbox"] {margin-top: 10px !important;}
    section[data-testid="stSidebar"] [data-testid="stMultiSelect"] {margin-top: -8px !important;}
    section[data-testid="stSidebar"] label {font-weight: bold !important; color: #000 !important;}
    section[data-testid="stSidebar"] span[data-baseweb="tag"] {font-size: 14px !important;}
    section[data-testid="stSidebar"] [data-testid="stMultiSelect"] {
        font-size: 12px !important; padding: 0.2rem 0.3rem !important; margin: 0rem 0 0 !important;
    }
    section[data-testid="stSidebar"] [data-testid="stDateInput"] {
        font-size: 12px !important; padding: 0.2rem 0.3rem !important; margin: 0rem 0 0 !important;
    }
    .records-count {font-size: 14px !important; color: #666; margin: 0.1rem 0 0; padding-left: 4px;}
    .colorbar-wrapper {display: flex; flex-direction: column; align-items: center; justify-content: center; margin-bottom: 2px;}
    .colorbar-container {
        background: linear-gradient(to right, #641478 0%, #89CFF0 20%, #21908c 40%, #5dc863 60%, #fde725 100%);
        height: 20px; border: 1px solid #ccc; border-radius: 4px; padding: 0; max-width: 95%; width: 100%;
    }
    .colorbar-labels {display: flex; justify-content: space-between; width: 100%; font-size: 11px; margin-top: 4px; color: #666;}
    .colorbar-labels span {flex: 1; text-align: center; color: #666; font-weight: bold;}
    .colorbar-units {font-size: 12px; color: #666; font-weight: bold; margin: 4px 0; text-align: center; white-space: nowrap;}
    </style>
    """, unsafe_allow_html=True)
    
    df = load_gov_data()
    community_df = load_community()
    
    # Temporary debug captions - remove after testing
    st.sidebar.caption(f"Government data loaded: {len(df)} rows")
    st.sidebar.caption(f"Community data loaded: {len(community_df)} rows")
    
    if 'species_multiselect' not in st.session_state:
        st.session_state.species_multiselect = []
    if 'date_range' not in st.session_state:
        st.session_state.date_range = []
    
    with st.sidebar:
        st.markdown(
            '<div style="font-size:18px; font-weight:bold; text-align:center; margin: 0 0 0.5rem 0;">'
            'Harmful Algal Bloom Dashboard South Australia</div>',
            unsafe_allow_html=True
        )
        
        st.markdown("""
        <div class="colorbar-wrapper">
            <div class="colorbar-container"></div>
            <div class="colorbar-labels">
                <span>0</span><span>100,000</span><span>200,000</span><span>300,000</span><span>400,000</span><span>>500,000</span>
            </div>
        </div>
        <div class="colorbar-units">Cell count per L</div>
        """, unsafe_allow_html=True)
        
        include_community = st.checkbox('Include community data', value=True)
        
        if 'prev_include_community' not in st.session_state:
            st.session_state.prev_include_community = True
        if include_community != st.session_state.prev_include_community:
            st.session_state.date_range = []  
            st.session_state.prev_include_community = include_community
        
        if st.button("↻ Refresh Government Data"):
            load_gov_data.clear()
            st.success("Government data refreshed from ArcGIS!")
            st.rerun()
        
        st.markdown('<div class="sidebar-card">Filters</div>', unsafe_allow_html=True)
        
        if include_community:
            combined_df = pd.concat([df, community_df], ignore_index=True)
        else:
            combined_df = df.copy()
        
        if combined_df.empty or 'Date_Sample_Collected' not in combined_df.columns:
            min_date = pd.to_datetime('2020-01-01')
            max_date = pd.to_datetime('2030-12-31')
        else:
            min_date = combined_df['Date_Sample_Collected'].min()
            max_date = combined_df['Date_Sample_Collected'].max()
            if pd.isna(min_date):
                min_date = pd.to_datetime('2020-01-01')
            if pd.isna(max_date):
                max_date = pd.to_datetime('2030-12-31')
        
        all_species = sorted(combined_df['Result_Name'].dropna().unique()) if 'Result_Name' in combined_df else []
        
        previous_selected = st.session_state.get("species_multiselect", [])
        valid_previous = [s for s in previous_selected if s in all_species]
        
        if not valid_previous:
            karenia_defaults = [s for s in all_species if "Karenia" in s]
            default_species = karenia_defaults if karenia_defaults else (all_species[:3] if all_species else [])
        else:
            default_species = valid_previous
        
        species_selected = st.multiselect(
            "Select species (via dropdown or start typing, *denotes community data)",
            options=all_species,
            default=default_species,
            key="species_multiselect"
        )
        
        last_two_weeks_start = max_date - timedelta(days=14)
        default_date_range = [last_two_weeks_start.date(), max_date.date()]
        
        date_range = st.date_input(
            "Date range (year/month/day format)",
            value=st.session_state.date_range if st.session_state.date_range else default_date_range,
            min_value=min_date.date(),
            max_value=max_date.date(),
            key="date_input"
        )
        st.session_state.date_range = date_range
        
        if len(date_range) == 2:
            start_date, end_date = pd.to_datetime(date_range[0]), pd.to_datetime(date_range[1])
        else:
            start_date, end_date = min_date, max_date
        
        if include_community:
            mask = (
                combined_df['Result_Name'].isin(species_selected) &
                combined_df['Date_Sample_Collected'].between(start_date, end_date) &
                combined_df['Result_Value_Numeric'].notna()
            )
            filtered_records = len(combined_df[mask])
        else:
            mask = (
                df['Result_Name'].isin(species_selected) &
                df['Date_Sample_Collected'].between(start_date, end_date) &
                df['Result_Value_Numeric'].notna()
            )
            filtered_records = len(df[mask])
        
        st.markdown(f'<div class="records-count">Showing {filtered_records} records matching selected species and date range</div>', unsafe_allow_html=True)
        
        st.markdown(
            """
            <div style="font-size:11px; color:#666; margin-top:10px; margin-bottom:20px; padding:4px; border-top:1px solid #ddd;">
            <p style="margin-bottom: 10px;">An instructional video on use of this dashboard can be found <a href="https://vimeo.com/manage/videos/1126101537" target="_blank">here</a>.</p>
            <p>Disclaimer: This application is a research product that utilises publicly available
            <a href="https://experience.arcgis.com/experience/5f0d6b22301a47bf91d198cabb030670" target="_blank">
            data</a> from the South Australian Government. No liability is accepted
            by the creator (A/Prof. Luke Mosley) or Adelaide University for the use
            of this system or the data it contains, which may be incomplete, inaccurate,
            or out of date. Users should consult the official South Australian Government
            advice (see <a href="https://www.algalbloom.sa.gov.au/" target="_blank">
            https://www.algalbloom.sa.gov.au/</a>) and/or obtain independent advice before
            relying on information in this application.</p>
            <p style="margin-top: 10px;">The <a href="https://www.facebook.com/groups/3434137440095343" target="_blank">
            Phytoplankton of South Australia group</a> and many community volunteers who contributed data and feedback for this dashboard are kindly thanked, in particular: Peri Coleman, Samantha Sea, Carey Hannaford, Kathryn Lewis, Troy Johnson, Lyndon Zimmermann, Faith Coleman, Anthony Rowland, Phil Bamford, Jane Power, Lochie Cameron, Wendy Lambert, Letitia Dahl-Helm, Caro Hannan, Greg Hyde and Karin Hatch.</p>
            </div>
            """,
            unsafe_allow_html=True
        )
    
    mask_main = (
        df['Result_Name'].isin(species_selected) &
        df['Date_Sample_Collected'].between(start_date, end_date) &
        df['Result_Value_Numeric'].notna()
    )
    sub_df = df[mask_main].copy()
    
    comm_sub_df = pd.DataFrame()
    if include_community:
        mask_comm = (
            community_df['Result_Name'].isin(species_selected) &
            community_df['Date_Sample_Collected'].between(start_date, end_date) &
            community_df['Result_Value_Numeric'].notna()
        )
        comm_sub_df = community_df[mask_comm].copy()
    
    m = folium.Map(location=[-34.9, 138.6], zoom_start=6, control_scale=True)
    
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr='Esri', name='Satellite', overlay=False
    ).add_to(m)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
        attr='Esri', name='Labels', overlay=True
    ).add_to(m)
    
    colormap = LinearColormap(
        colors=['#641478', '#89CFF0', '#21908c', '#5dc863', '#fde725'],
        index=[0, 100000, 200000, 300000, 500000],
        vmin=0, vmax=500000
    )
    
    for _, row in sub_df.iterrows():
        if pd.notna(row.get('Latitude')) and pd.notna(row.get('Longitude')):
            value = row['Result_Value_Numeric']
            color = colormap(value)
            popup = f"<b>{row.get('Site_Description', 'Unknown')}</b><br>{row['Date_Sample_Collected'].date()}<br>{row['Result_Name']}<br>{value:,.0f} {row.get('Units', 'cells/L')}"
            folium.CircleMarker(
                location=[row['Latitude'], row['Longitude']],
                radius=6, color=color, fill=True, fill_color=color, fill_opacity=0.8,
                popup=popup
            ).add_to(m)
    
    for _, row in comm_sub_df.iterrows():
        if pd.notna(row.get('Latitude')) and pd.notna(row.get('Longitude')):
            value = row['Result_Value_Numeric']
            color = colormap(value)
            popup = f"<b>{row.get('Site_Description', 'Unknown')}</b><br>{row['Date_Sample_Collected'].date()}<br>{row['Result_Name']}<br>{value:,.0f} {row.get('Units', 'cells/L')}"
            folium.CircleMarker(
                location=[row['Latitude'], row['Longitude']],
                radius=6, color=color, fill=True, fill_color=color, fill_opacity=0.8,
                popup=popup
            ).add_to(m)
    
    if not sub_df.empty or not comm_sub_df.empty:
        combined_sub = pd.concat([sub_df, comm_sub_df])
        lat_min, lat_max = combined_sub['Latitude'].min(), combined_sub['Latitude'].max()
        lon_min, lon_max = combined_sub['Longitude'].min(), combined_sub['Longitude'].max()
        if all(pd.notna(x) for x in [lat_min, lat_max, lon_min, lon_max]):
            m.fit_bounds([[lat_min, lon_min], [lat_max, lon_max]])
    
    st_folium(m, width='100%', height=550)
    
    st.subheader("Trends Over Time")
    include_comm_in_trends = st.checkbox("Include community data in trends", value=include_community)
    
    base_trends_df = pd.concat([df, community_df], ignore_index=True) if include_comm_in_trends and include_community else df.copy()
    
    all_species_trends = sorted(base_trends_df['Result_Name'].dropna().unique()) if 'Result_Name' in base_trends_df else []
    default_trend_species = [s for s in all_species_trends if "Karenia" in s] or all_species_trends[:3]
    
    selected_trend_species = st.multiselect(
        "Select species for trend chart",
        options=all_species_trends,
        default=default_trend_species
    )
    
    all_sites = ["All Sites"] + sorted(base_trends_df['Site_Description'].dropna().unique().tolist())
    selected_site = st.selectbox("Filter by site", options=all_sites, index=0)
    
    plot_df = base_trends_df[
        base_trends_df['Result_Name'].isin(selected_trend_species) &
        base_trends_df['Result_Value_Numeric'].notna()
    ].copy()
    
    if selected_site != "All Sites":
        plot_df = plot_df[plot_df['Site_Description'] == selected_site]
    
    plot_df = plot_df.sort_values('Date_Sample_Collected')
    
    if not plot_df.empty:
        trend_df = plot_df.pivot_table(
            index='Date_Sample_Collected',
            columns='Result_Name',
            values='Result_Value_Numeric',
            aggfunc='mean'
        ).reset_index()
        
        trend_melted = trend_df.melt(
            id_vars='Date_Sample_Collected',
            var_name='Species',
            value_name='Cell_Count'
        )
        
        base_chart = alt.Chart(trend_melted).mark_line(point=True).encode(
            x=alt.X('Date_Sample_Collected:T', title='Date', axis=alt.Axis(format='%d %b %Y', labelAngle=0)),
            y=alt.Y('Cell_Count:Q', title='Cell Count per L'),
            color=alt.Color('Species:N'),
            tooltip=['Date_Sample_Collected', 'Species', 'Cell_Count']
        ).properties(
            width=800, height=400,
            title="Trends for selected species (average if 'All Sites', * = community)"
        ).interactive()
        
        st.altair_chart(base_chart, use_container_width=True)
        st.caption(f"Showing {len(plot_df)} data points across {len(selected_trend_species)} species and {selected_site.lower()}.")
    else:
        st.info("No data available for selected species and site.")
    
    st.subheader("NASA PACE Satellite Remote Sensing Reflectance Image")
    st.caption("This map is derived from NASA PACE satellite imagery processed on date(s) (UTC) indicated on the plot. I have found, via specific calibration to Karenia sp. cell counts, that using a specific wavelength (approx. 470 nm) gives more accurate results than use of chlorophyll imagery (that uses other non-specific wavelengths). This is because many algal species contribute to chlorophyll, so it can be inaccurate in regard to detecting Karenia species. Blue indicates lower levels; lighter blue to green moderate levels; orange to red high levels. Note this is a beta version and subject to change. Frequency of updates is reliant on relatively cloud-free conditions!")
    if os.path.exists("pace_rrs_at_470.0_nm.png"):
        st.image("pace_rrs_at_470.0_nm.png", use_container_width=True)
    
    image_path = "pace_rrs_at_470.0_nm_composite.png"
    if os.path.exists(image_path):
        with open(image_path, "rb") as file:
            st.download_button(
                label="Download Composite Image",
                data=file,
                file_name="pace_rrs_at_470.0_nm_composite.png",
                mime="image/png"
            )
    else:
        st.warning("Composite image file not found.")

if __name__ == "__main__":
    main()
