import pandas as pd
import folium
from branca.colormap import LinearColormap
from streamlit_folium import st_folium
import streamlit as st
import altair as alt
import os
from datetime import timedelta

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
        df['Date_Sample_Collected'] = pd.to_datetime(df['Date_Sample_Collected'])

    if not os.path.exists(coords_csv):
        st.error(f"⚠️ Coordinates file '{coords_csv}' not found. Please generate site_coordinates.csv first.")
        st.stop()

    coords_df = pd.read_csv(coords_csv)
    return df.merge(coords_df, on="Site_Description", how="left")

@st.cache_data
def load_community(file_path="community_algae.csv", coords_csv="site_coordinates.csv"):
    if not os.path.exists(file_path):
        st.warning(f"⚠️ Community data file '{file_path}' not found. Using empty dataset.")
        return pd.DataFrame()
    
    df = pd.read_csv(file_path)
    
    # Identify species columns: everything after 'Salinity (ppt)' up to before 'Notes'
    salinity_idx = df.columns.get_loc('Salinity (ppt)')
    notes_idx = df.columns.get_loc('Notes')
    species_cols = df.columns[salinity_idx + 1 : notes_idx].tolist()
    
    # Melt to long format: one row per species per sample
    melted_df = pd.melt(df, 
                        id_vars=['Location', 'Date', 'Time', 'Temp', 'Salinity (ppt)', 'Notes'], 
                        value_vars=species_cols, 
                        var_name='Result_Name', 
                        value_name='Result_Value_Numeric')
    
    # Rename columns to match main data structure
    melted_df['Site_Description'] = melted_df['Location']
    melted_df['Date_Sample_Collected'] = pd.to_datetime(melted_df['Date'], dayfirst=True)
    
    # Drop original Location and Date
    melted_df = melted_df.drop(['Location', 'Date'], axis=1)
    
    # Add units if not present
    melted_df['Units'] = 'cells/L'
    
    # Optional: Filter to non-zero values to reduce noise (uncomment if desired)
    # melted_df = melted_df[melted_df['Result_Value_Numeric'] > 0]
    
    # Species name mapping: Map community-specific species names to main dataset names
    # Add mappings here as needed, e.g., {'Karenia sp.': 'Karenia mikimotoi'}
    species_mapping = {
        # 'Karenia sp.': 'Karenia mikimotoi',
        # 'Gymnoids': 'Gymnodinium sp.',
        # Add your mappings...
    }
    melted_df['Result_Name'] = melted_df['Result_Name'].map(species_mapping).fillna(melted_df['Result_Name'])
    
    # Merge with coordinates (assumes Site_Description matches or can be cleaned to match coords)
    # If site names don't match exactly, add a site_mapping dict below and apply similarly
    if os.path.exists(coords_csv):
        coords_df = pd.read_csv(coords_csv)
        melted_df = melted_df.merge(coords_df, on="Site_Description", how="left")
    else:
        st.warning(f"⚠️ Coordinates file '{coords_csv}' not found. Community sites will not have lat/long.")
    
    # Optional: Site name standardization/cleaning
    # site_mapping = {'Wright Is/ Yilki Bay/reef': 'Wright Island Yilki Bay', ...}
    # melted_df['Site_Description'] = melted_df['Site_Description'].map(site_mapping).fillna(melted_df['Site_Description'])
    
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
    # Custom CSS
    # ---------------------------
    st.markdown("""
    <style>
    .block-container {padding-top: 1rem; padding-bottom: 0.25rem;}
    footer {visibility: hidden;}
    
    /* Sidebar styling */
    section[data-testid="stSidebar"] {
        font-size: 11px;
        padding: 0.4rem 0.5rem 0.5rem 0.5rem;
        max-width: 350px;
    }
    
    section[data-testid="stSidebar"] .stMarkdown p {margin-bottom: 0.25rem;}
    .sidebar-card {
        border: none; /* Remove border for subtlety */
        border-radius: 0; /* Flatten it */
        padding: 4px 0; /* Reduce vertical padding */
        background: transparent; /* No background to blend in */
        margin-bottom: 0.25rem; /* Tighten spacing */
        font-size: 14px; /* Slightly smaller */
        font-weight: normal; /* Not bold */
        color: #666; /* Medium grey for low emphasis */
        font-style: italic; /* Optional: subtle italic tilt */
        text-decoration: underline; /* Adds underline */
    }
    
    /* Sidebar section filter header labels */
    section[data-testid="stSidebar"] label {
        font-weight: bold !important;
        color: #000 !important;
    }
    
    /* Smaller font for selected chips in sidebar multiselect */
    section[data-testid="stSidebar"] span[data-baseweb="tag"] {
        font-size: 14px !important; /* Adjust as needed for subtlety */
    }
    
    /* Smaller font and padding for sidebar multiselect (species) */
    section[data-testid="stSidebar"] [data-testid="stMultiSelect"] {
        font-size: 12px !important;
        padding: 0.2rem 0.3rem !important; /* Reduces internal padding; adjust values as needed */
        margin: 0rem 0 0 !important; /* Optional: tightens outer margins for less vertical space */
    }
    
    /* Smaller font and padding for sidebar date input (range) */
    section[data-testid="stSidebar"] [data-testid="stDateInput"] {
        font-size: 12px !important;
        padding: 0.2rem 0.3rem !important; /* Reduces internal padding; tweak values for fit */
        margin: 0rem 0 0 !important; /* Optional: tightens outer margins for less vertical space */
    }
    
    /* Record counter*/
    .records-count {
        font-size: 14px !important;
        color: #666; /* Optional: medium grey for subtlety, or keep #000 for black */
        margin: 0.1rem 0 0;
        padding-left: 4px; /* Indents text to the right */
    }
    
    /* Horizontal colorbar */
    .colorbar-wrapper {
        display: flex;
        align-items: center;
        justify-content: center;
        margin-bottom: 2px;
    }
    .colorbar-container {
        background: linear-gradient(to right, #641478 0%, #3b528b 25%, #21908c 50%, #5dc863 75%, #fde725 100%);
        height: 30px;
        border: 1px solid #ccc;
        border-radius: 4px;
        padding: 0 5px;
        font-size: 12px;
        color: #333333; /* dark grey text */
        font-weight: normal;
        max-width: 95%;
        width: 100%;
    }
    .colorbar-labels {
        display: flex;
        justify-content: space-between;
        width: 100%;
        font-size: 11px;
        margin-top: 2px;
        color: #333333; /* dark grey instead of white */
    }
    .colorbar-labels span {
        flex: 1;
        text-align: center;
        color: #000; /* Keep black base */
        font-weight: bold;
        text-shadow: 0 0 2px rgba(255, 255, 255, 1); /* Thicker, fully opaque white halo */
    }
    .colorbar-units {
        font-size: 12px;
        color: #000;
        font-weight: bold;
        margin-top: 4px;
        margin-bottom: 4px; /* Added to match doubled top */
        text-align: center;
        white-space: nowrap;
    }
    </style>
    """, unsafe_allow_html=True)

    # ---------------------------
    # File paths and data
    # ---------------------------
    file_path = "HarmfulAlgalBloom_MonitoringSites_8382667239581124066.csv"
    coords_csv = "site_coordinates.csv"
    df = load_data(file_path, coords_csv)
    community_df = load_community()

    # ---------------------------
    # Sidebar: Title, colorbar, filters
    # ---------------------------
    with st.sidebar:
        # Title
        st.markdown(
        '<div style="font-size:18px; font-weight:bold; text-align:center; margin: 0 0 0.5rem 0;">'  # Zeros top margin
        'Harmful Algal Bloom Dashboard  South Australia</div>',  
        unsafe_allow_html=True
        )

        # Colorbar
        st.markdown(
        """
        <div class="colorbar-wrapper">
            <div class="colorbar-container">
                <div class="colorbar-labels">
                    <span>0</span><span>100,000</span><span>200,000</span>
                    <span>300,000</span><span>400,000</span><span>>500,000</span>
                </div>
            </div>
        </div>
        <div class="colorbar-units">Cell count per L</div>
        """,
        unsafe_allow_html=True
        )
        
        # Filters card
        st.markdown('<div class="sidebar-card">Filters</div>', unsafe_allow_html=True)

        # Species filter (now includes species from both datasets after mapping)
        combined_df = pd.concat([df, community_df], ignore_index=True)
        all_species = sorted(combined_df['Result_Name'].dropna().unique())
        default_species = [s for s in all_species if "Karenia" in s] or all_species[:1]
        species_selected = st.multiselect("Select species  (via dropdown or start typing)", options=all_species, default=default_species)
        if not species_selected:
            species_selected = all_species[:1]

        # Date range filter
        min_date, max_date = combined_df['Date_Sample_Collected'].min(), combined_df['Date_Sample_Collected'].max()
        last_week_start = max_date - timedelta(days=7)
        date_range = st.date_input("Date range   (year/month/day format)", [last_week_start, max_date],
                                   min_value=min_date, max_value=max_date)
        if len(date_range) == 2:
            start_date, end_date = pd.to_datetime(date_range[0]), pd.to_datetime(date_range[1])
        else:
            start_date, end_date = min_date, max_date

        # Checkbox for including community data
        include_community = st.checkbox('Include community data')

    # ---------------------------
    # Filter dataset
    # ---------------------------
    mask_main = (
        df['Result_Name'].isin(species_selected) &
        df['Date_Sample_Collected'].between(start_date, end_date) &
        df['Result_Value_Numeric'].notna()
    )
    sub_df = df[mask_main]

    comm_sub_df = pd.DataFrame()
    if include_community:
        mask_comm = (
            community_df['Result_Name'].isin(species_selected) &
            community_df['Date_Sample_Collected'].between(start_date, end_date) &
            community_df['Result_Value_Numeric'].notna()
        )
        comm_sub_df = community_df[mask_comm]

    st.sidebar.markdown(f'<div class="records-count">{len(sub_df) + len(comm_sub_df)} of {len(df) + len(community_df)} records for selected species located in this date range</div>', unsafe_allow_html=True)

    # Disclaimer at sidebar bottom
    st.sidebar.markdown(
        """
        <div style="font-size:11px; color:#666; margin-top:10px; margin-bottom:20px; padding:4px; border-top:1px solid #ddd;">
        Disclaimer: This application is a research product that utilises publicly available 
        <a href="https://experience.arcgis.com/experience/5f0d6b22301a47bf91d198cabb030670" target="_blank">
        data</a> from the South Australian Government No liability is accepted 
        by the creator (A/Prof. Luke Mosley) or Adelaide University for the use 
        of this system or the data it contains, which may be incomplete, inaccurate, 
        or out of date. Users should consult the official South Australian Government 
        advice (see <a href="https://www.algalbloom.sa.gov.au/" target="_blank">
        https://www.algalbloom.sa.gov.au/</a>) and/or obtain independent advice before 
        relying on information in this application.
        </div>
        """,
        unsafe_allow_html=True
    )

    # ---------------------------
    # Map
    # ---------------------------
    m = folium.Map(
        location=[-34.9, 138.6], 
        zoom_start=6, 
        control_scale=True,
        zoom_control='bottomleft'  # Native positioning for zoom buttons
    )

    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr='Esri', name='Satellite', overlay=False, control=True
    ).add_to(m)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
        attr='Esri', name='Labels', overlay=True, control=True
    ).add_to(m)
    folium.LayerControl(position='bottomright').add_to(m)  # Native positioning for layers

    # Color scale (Viridis-inspired: purple → green → yellow)
    viridis_colors = ['#641478', '#3b528b', '#21908c', '#5dc863', '#fde725']
    colormap = LinearColormap(colors=viridis_colors, vmin=0, vmax=500000)
    ## colormap = LinearColormap(colors=['green', 'yellow', 'red'], vmin=0, vmax=500000) ##old traffic light colormap

    # Add markers for main data
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

    # Add markers for community data (if included)
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

    # Fit bounds to all plotted data
    bounds_df = pd.concat([sub_df, comm_sub_df], ignore_index=True)
    if not bounds_df.empty:
        m.fit_bounds([[bounds_df['Latitude'].min(), bounds_df['Longitude'].min()],
                      [bounds_df['Latitude'].max(), bounds_df['Longitude'].max()]])

    # ---------------------------
    # Map display (undocked)
    # ---------------------------
    st_folium(m, width='100%', height=550)

    # ---------------------------
    # Trends Section
    # ---------------------------
    if not df.empty:  # Check full df for options, even if sub_df is filtered
        st.subheader("Trends Over Time")
        
        # Get all unique species from full dataset
        all_species = sorted(df['Result_Name'].dropna().unique())
        
        # Default to Karenia species (matching sidebar logic)
        default_trend_species = [s for s in all_species if "Karenia" in s] or all_species[:3]  # Fallback to first 3 if no Karenia
        
        # Multi-select for species (defaults to Karenia)
        selected_trend_species = st.multiselect(
            "Select species for trend chart",
            options=all_species,
            default=default_trend_species
        )
        
        # Site filter: All or specific
        all_sites = sorted(df['Site_Description'].dropna().unique())
        selected_site = st.selectbox(
            "Filter by site",
            options=["All Sites"] + all_sites,
            index=0
        )
        
        # Filter data for plot
        plot_df = df[
            (df['Result_Name'].isin(selected_trend_species)) &
            (df['Result_Value_Numeric'].notna())
        ].copy()
        
        if selected_site != "All Sites":
            plot_df = plot_df[plot_df['Site_Description'] == selected_site]
        
        # Sort by date (keep as datetime)
        plot_df = plot_df.sort_values('Date_Sample_Collected')
        
        if not plot_df.empty:
            # Pivot for multi-line chart
            trend_df = plot_df.pivot_table(
                index='Date_Sample_Collected',
                columns='Result_Name',
                values='Result_Value_Numeric',
                aggfunc='mean'  # Average if multiple samples per day/species
            ).reset_index()
            
            # Melt without date conversion
            trend_melted = trend_df.melt(
                id_vars='Date_Sample_Collected',
                var_name='Species',
                value_name='Cell_Count',
                ignore_index=False
            )
            # No .dt.date here—keep as datetime for Altair
            
            # Altair chart (linear scale only)
            base = alt.Chart(trend_melted).mark_line().encode(
                x=alt.X('Date_Sample_Collected:T', title='Date', axis=alt.Axis(labelAngle=0)),
                y=alt.Y('Cell_Count:Q', title='Cell Count per L'),
                color=alt.Color('Species:N', title='Species'),
                tooltip=['Date_Sample_Collected', 'Species', 'Cell_Count']
            ).properties(
                width=800,
                height=400,
                title=alt.TitleParams(
                    text="Trends for Selected Species (note: average values will be displayed if 'All Sites' selected)",
                    fontSize=14,
                    fontWeight='normal',
                    color='#4c4c4c'  # dark grey
                )
            ).interactive()  # Enables zoom/pan

            st.altair_chart(base, use_container_width=True)
            
            # Show filtered row count for transparency
            st.caption(f"Showing {len(plot_df)} data points across {len(selected_trend_species)} species and {'all sites' if selected_site == 'All Sites' else selected_site}.")
        else:
            st.info("No data available for the selected species and site. Adjust options above.")
    else:
        st.info("No data loaded. Check file paths in the code.")

if __name__ == "__main__":
    main()
