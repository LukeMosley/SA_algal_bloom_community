import pandas as pd
import folium
from branca.colormap import LinearColormap
from streamlit_folium import st_folium
import streamlit as st
import altair as alt
import os
from datetime import timedelta
from PIL import Image


# ---------------------------
# Load data + coordinates
# ---------------------------
@st.cache_data
def load_data(file_path, coords_csv="site_coordinates.csv"):
    if not os.path.exists(file_path):
        st.warning(f"⚠️ Main data file '{file_path}' not found. Using empty dataset.")
        df = pd.DataFrame()
    else:
        if file_path.endswith(('.xlsx', '.xls')):
            df = pd.read_excel(file_path, sheet_name=0)
        else:
            df = pd.read_csv(file_path)
        df['Date_Sample_Collected'] = pd.to_datetime(df['Date_Sample_Collected'], errors='coerce')
        if 'Result_Name' in df.columns:
            df['Result_Name'] = (
                df['Result_Name']
                .astype(str)
                .str.strip()
                .str.replace(r'\s+', ' ', regex=True)
                .str.replace('\xa0', ' ', regex=False)
            )
  
    if not os.path.exists(coords_csv):
        st.error(f"⚠️ Coordinates file '{coords_csv}' not found. Please generate site_coordinates.csv first.")
        st.stop()
    coords_df = pd.read_csv(coords_csv)
    df = df.merge(coords_df, on="Site_Description", how="left")
    df['Latitude'] = pd.to_numeric(df['Latitude'], errors='coerce')
    df['Longitude'] = pd.to_numeric(df['Longitude'], errors='coerce')
    return df

@st.cache_data
def load_community(file_path="MASTER spreadsheet of community summaries.xlsx"):
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
    melted_df['Result_Name'] = melted_df['Result_Name'].astype(str) + ' *'
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
    # Custom CSS
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
        border: none;
        border-radius: 0;
        padding: 4px 0;
        background: transparent;
        margin-bottom: 0.25rem;
        font-size: 14px;
        font-weight: normal;
        color: #666;
        font-style: italic;
        text-decoration: underline;
    }
    section[data-testid="stSidebar"] [data-testid="stCheckbox"] {
        margin-top: 10px !important;
    }
    section[data-testid="stSidebar"] [data-testid="stMultiSelect"] {
        margin-top: -8px !important;
        font-size: 12px !important;
        padding: 0.2rem 0.3rem !important;
        margin: 0rem 0 0 !important;
    }
    section[data-testid="stSidebar"] span[data-baseweb="tag"] {
        font-size: 14px !important;
    }
    section[data-testid="stSidebar"] [data-testid="stDateInput"] {
        font-size: 12px !important;
        padding: 0.2rem 0.3rem !important;
        margin: 0rem 0 0 !important;
    }
    section[data-testid="stSidebar"] label {
        font-weight: bold !important;
        color: #000 !important;
    }
    .records-count {
        font-size: 14px !important;
        color: #666;
        margin: 0.1rem 0 0;
        padding-left: 4px;
    }
    .colorbar-wrapper {
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        margin-bottom: 2px;
    }
    .colorbar-container {
        background: linear-gradient(to right, #641478 0%, #3b528b 25%, #21908c 50%, #5dc863 75%, #fde725 100%);
        height: 20px;
        border: 1px solid #ccc;
        border-radius: 4px;
        padding: 0;
        max-width: 95%;
        width: 100%;
    }
    .colorbar-labels {
        display: flex;
        justify-content: space-between;
        width: 100%;
        font-size: 11px;
        margin-top: 4px;
        color: #666;
    }
    .colorbar-labels span {
        flex: 1;
        text-align: center;
        color: #666;
        font-weight: bold;
    }
    .colorbar-units {
        font-size: 12px;
        color: #666;
        font-weight: bold;
        margin-top: 4px;
        margin-bottom: 4px;
        text-align: center;
        white-space: nowrap;
    }
    </style>
    """, unsafe_allow_html=True)
    # File paths and data
    file_path = "HarmfulAlgalBloom_MonitoringSites_8382667239581124066.csv"
    coords_csv = "site_coordinates.csv"
    raster_file = "pace_rrs_at_470.0_nm.png"
    df = load_data(file_path, coords_csv)
    community_df = load_community()
    # Persistent state for filters
    if 'species_selected' not in st.session_state:
        st.session_state.species_selected = []
    if 'date_range' not in st.session_state:
        st.session_state.date_range = []
    # Sidebar: Title, colorbar, filters
    with st.sidebar:
        st.markdown(
            '<div style="font-size:18px; font-weight:bold; text-align:center; margin: 0 0 0.5rem 0;">'
            'Harmful Algal Bloom Dashboard South Australia</div>',
            unsafe_allow_html=True
        )
        st.markdown(
            """
            <div class="colorbar-wrapper">
                <div class="colorbar-container"></div>
                <div class="colorbar-labels">
                    <span>0</span><span>100,000</span><span>200,000</span><span>300,000</span><span>400,000</span><span>>500,000</span>
                </div>
            </div>
            <div class="colorbar-units">Cell count per L</div>
            """,
            unsafe_allow_html=True
        )
        include_community = st.checkbox('Include community data')
        include_raster = st.checkbox('Show satellite remote reflectance sensing data at 470nm')
        st.markdown('<div class="sidebar-card">Filters</div>', unsafe_allow_html=True)
        combined_df = pd.concat([df, community_df], ignore_index=True) if include_community else df.copy()
        if not combined_df.empty:
            min_date, max_date = combined_df['Date_Sample_Collected'].min(), combined_df['Date_Sample_Collected'].max()
        else:
            min_date, max_date = pd.to_datetime('2020-01-01'), pd.to_datetime('2025-12-31')
        all_species = sorted(combined_df['Result_Name'].dropna().unique())
        previous_selected = st.session_state.species_selected
        filtered_previous = [s for s in previous_selected if s in all_species]
        karenia_defaults = [s for s in all_species if "Karenia" in s]
        if include_community and "Karenia spp subcount *" in all_species:
            if "Karenia spp subcount *" not in filtered_previous:
                if filtered_previous:
                    filtered_previous.append("Karenia spp subcount *")
                else:
                    filtered_previous = ["Karenia spp subcount *"]
        default_species = filtered_previous if filtered_previous else karenia_defaults
        species_selected = st.multiselect(
            "Select species (via dropdown or start typing, *denotes community data)",
            options=all_species,
            default=default_species,
            key='species_multiselect'
        )
        st.session_state.species_selected = species_selected
        previous_date_range = st.session_state.date_range
        last_week_start = max_date - timedelta(days=14)
        if previous_date_range and len(previous_date_range) == 2:
            clamped_start = max(min_date.date(), min(previous_date_range[0], max_date.date()))
            clamped_end = max(clamped_start, min(max_date.date(), previous_date_range[1]))
            date_range = st.date_input(
                "Date range (year/month/day format)",
                [clamped_start, clamped_end],
                min_value=min_date.date(),
                max_value=max_date.date(),
                key='date_input'
            )
        else:
            date_range = st.date_input(
                "Date range (year/month/day format)",
                [last_week_start.date(), max_date.date()],
                min_value=min_date.date(),
                max_value=max_date.date(),
                key='date_input'
            )
        st.session_state.date_range = date_range
        if len(date_range) == 2:
            start_date, end_date = pd.to_datetime(date_range[0]), pd.to_datetime(date_range[1])
        else:
            start_date, end_date = min_date, max_date
    # Filter dataset
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
    filtered_records = len(sub_df) + len(comm_sub_df)
    st.sidebar.markdown(f'<div class="records-count">Showing {filtered_records} records matching selected species and date range</div>', unsafe_allow_html=True)
    st.sidebar.markdown(
        """
        <div style="font-size:11px; color:#666; margin-top:10px; margin-bottom:20px; padding:4px; border-top:1px solid #ddd;">
        <p style="margin-bottom: 10px;">An instructional video on use of this dashboard can be found <a href="https://vimeo.com/manage/videos/1126101537" target="_blank">here</a>.</p>
        <p>Disclaimer: This application is a research product that utilises publicly available
        <a href="https://experience.arcgis.com/experience/5f0d6b22301a47bf91d198cabb030670" target="_blank">
        data</a> from the South Australian Government No liability is accepted
        by the creator (A/Prof. Luke Mosley) or Adelaide University for the use
        of this system or the data it contains, which may be incomplete, inaccurate,
        or out of date. Users should consult the official South Australian Government
        advice (see <a href="https://www.algalbloom.sa.gov.au/" target="_blank">
        https://www.algalbloom.sa.gov.au/</a>) and/or obtain independent advice before
        relying on information in this application.</p>
        <p style="margin-top: 10px;">The many community volunteers who contributed samples and undertook analyses for this application are kindly thanked, in particular: Peri Coleman, Samantha Sea, Faith Coleman.</p>
        </div>
        """,
        unsafe_allow_html=True
    )
    # Map
    m = folium.Map(
        location=[-34.9, 138.6],
        zoom_start=6,
        control_scale=True,
        zoom_control='bottomleft'
    )
    folium.TileLayer(
        'openstreetmap', name='OpenStreetMap', overlay=False, control=True
    ).add_to(m)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr='Esri', name='Satellite', overlay=False, control=True
    ).add_to(m)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
        attr='Esri', name='Labels', overlay=True, control=True
    ).add_to(m)
    folium.LayerControl(position='bottomright').add_to(m)
    if include_raster and os.path.exists(raster_file):
        try:
            # Load image and add transparency where black
            img = Image.open(raster_file).convert("RGBA")
            datas = img.getdata()
            new_data = []
            for item in datas:
                if item[0] < 10 and item[1] < 10 and item[2] < 10:  # black → transparent
                    new_data.append((0, 0, 0, 0))
                else:
                    new_data.append(item)
            img.putdata(new_data)
            img.save("overlay_temp.png", format="PNG")

            # Add to Folium
            folium.raster_layers.ImageOverlay(
                name="Rrs 470 nm (PACE)",
                image="overlay_temp.png",
                bounds=[[-36, 134], [-32, 140]],  # ✅ matches 3000×2000 ratio
                opacity=0.75,
                interactive=True,
                cross_origin=False
            ).add_to(m)

            # Optional debug outline
            folium.Rectangle(
                bounds=[[-36, 134], [-32, 140]],
                color="red", fill=False, weight=2
            ).add_to(m)

        except Exception as e:
            st.error(f"Error overlaying raster: {e}")

    viridis_colors = ['#641478', '#3b528b', '#21908c', '#5dc863', '#fde725']
    colormap = LinearColormap(colors=viridis_colors, vmin=0, vmax=500000)
    for _, row in sub_df.iterrows():
        if pd.notna(row.get('Latitude')) and pd.notna(row.get('Longitude')):
            value = row['Result_Value_Numeric']
            color = colormap(value if pd.notna(value) else 1)
            units = row.get('Units', 'cells/L')
            folium.CircleMarker(
                location=[row['Latitude'], row['Longitude']],
                radius=6, color=color, fill=True, fill_color=color, fill_opacity=0.8,
                popup=(f"<b>{row['Site_Description']}</b><br>"
                       f"{row['Date_Sample_Collected'].date()}<br>"
                       f"{row['Result_Name']}<br>"
                       f"{value:,.0f} {units}")
            ).add_to(m)
    for _, row in comm_sub_df.iterrows():
        if pd.notna(row.get('Latitude')) and pd.notna(row.get('Longitude')):
            value = row['Result_Value_Numeric']
            color = colormap(value if pd.notna(value) else 1)
            units = row.get('Units', 'cells/L')
            folium.CircleMarker(
                location=[row['Latitude'], row['Longitude']],
                radius=6, color=color, fill=True, fill_color=color, fill_opacity=0.8,
                popup=(f"<b>{row['Site_Description']}</b><br>"
                       f"{row['Date_Sample_Collected'].date()}<br>"
                       f"{row['Result_Name']}<br>"
                       f"{value:,.0f} {units}")
            ).add_to(m)
    combined_sub = pd.concat([sub_df, comm_sub_df], ignore_index=True)
    # Calculate bounds
    lat_min, lon_min, lat_max, lon_max = None, None, None, None
    if include_raster:
        lat_min = -36
        lon_min = 134
        lat_max = -32
        lon_max = 140
    if not combined_sub.empty:
        p_lat_min = combined_sub['Latitude'].min()
        p_lon_min = combined_sub['Longitude'].min()
        p_lat_max = combined_sub['Latitude'].max()
        p_lon_max = combined_sub['Longitude'].max()
        if pd.notna(p_lat_min):
            if lat_min is None:
                lat_min = p_lat_min
                lon_min = p_lon_min
                lat_max = p_lat_max
                lon_max = p_lon_max
            else:
                lat_min = min(lat_min, p_lat_min)
                lon_min = min(lon_min, p_lon_min)
                lat_max = max(lat_max, p_lat_max)
                lon_max = max(lon_max, p_lon_max)
    if lat_min is not None and pd.notna(lat_min) and pd.notna(lon_min) and pd.notna(lat_max) and pd.notna(lon_max):
        m.fit_bounds([[lat_min, lon_min], [lat_max, lon_max]])
    st_folium(m, width='100%', height=550, key=f"map_{include_raster}")
    # Trends Section
    if not df.empty:
        st.subheader("Trends Over Time")
        include_comm_in_trends = st.checkbox("Include community data in trends", value=include_community)
        base_trends_df = pd.concat([df, community_df], ignore_index=True) if include_comm_in_trends and include_community and not community_df.empty else df.copy()
        all_species_trends = sorted(base_trends_df['Result_Name'].dropna().unique())
        default_trend_species = [s for s in all_species_trends if "Karenia" in s] or all_species_trends[:3]
        selected_trend_species = st.multiselect(
            "Select species for trend chart",
            options=all_species_trends,
            default=default_trend_species
        )
        all_sites = sorted(base_trends_df['Site_Description'].dropna().unique())
        selected_site = st.selectbox(
            "Filter by site",
            options=["All Sites"] + all_sites,
            index=0
        )
        plot_df = base_trends_df[
            (base_trends_df['Result_Name'].isin(selected_trend_species)) &
            (base_trends_df['Result_Value_Numeric'].notna())
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
                value_name='Cell_Count',
                ignore_index=False
            )
            base = alt.Chart(trend_melted).mark_line(point=True).encode(
                x=alt.X('Date_Sample_Collected:T', title='Date', axis=alt.Axis(labelAngle=0)),
                y=alt.Y('Cell_Count:Q', title='Cell Count per L'),
                color=alt.Color('Species:N', title='Species'),
                tooltip=['Date_Sample_Collected', 'Species', 'Cell_Count']
            ).properties(
                width=800,
                height=400,
                title=alt.TitleParams(
                    text="Trends for selected species (note: average values will be displayed if 'All Sites' selected, *denotes community data)",
                    fontSize=14,
                    fontWeight='normal',
                    color='#4c4c4c'
                )
            ).interactive()
            st.altair_chart(base, use_container_width=True)
            st.caption(f"Showing {len(plot_df)} data points across {len(selected_trend_species)} species and {'all sites' if selected_site == 'All Sites' else selected_site}.")
        else:
            st.info("No data available for the selected species and site. Adjust options above.")
    else:
        st.info("No data loaded. Check file paths in the code.")

if __name__ == "__main__":
    main()
