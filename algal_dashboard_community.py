import pandas as pd
import folium
from branca.colormap import LinearColormap
from streamlit_folium import st_folium
import streamlit as st
import altair as alt
import os
import requests
from datetime import timedelta


# ---------------------------------------------------------------------------
# Helper: paginated ArcGIS FeatureServer / Table query → DataFrame
# ---------------------------------------------------------------------------
def fetch_arcgis_layer(layer_id: int, out_fields: str = "*", page_size: int = 2000) -> pd.DataFrame:
    """
    Download an entire ArcGIS Feature Layer or Table with pagination.
    layer_id 0 = Monitoring Sites, 1 = Sample Data.
    """
    base_url = (
        "https://services6.arcgis.com/WS2XycMNFieWAsfS/arcgis/rest/services/"
        "HarmfulAlgalBloom_MonitoringSites/FeatureServer"
    )
    url = f"{base_url}/{layer_id}/query"

    all_rows = []
    offset = 0

    while True:
        params = {
            "where": "1=1",
            "outFields": out_fields,
            "returnGeometry": "false",
            "f": "json",
            "resultOffset": offset,
            "resultRecordCount": page_size,
            "orderByFields": "OBJECTID ASC",
        }
        r = requests.get(url, params=params, timeout=90)
        r.raise_for_status()
        data = r.json()

        features = data.get("features", [])
        if not features:
            break

        all_rows.extend(f["attributes"] for f in features)

        if len(features) < page_size:
            break
        offset += page_size

    return pd.DataFrame(all_rows)


# ---------------------------------------------------------------------------
# Load government data from ArcGIS (auto-refreshes every 6 hours)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=6 * 3600)
def load_data():
    # Sample results (table 1)
    df = fetch_arcgis_layer(layer_id=1)

    # Site coordinates (layer 0)
    sites = fetch_arcgis_layer(layer_id=0)
    sites = sites.rename(columns={"SiteName": "Site_Description"})

    # ------------------------------------------------------------------
    # Cleaning (same logic as original)
    # ------------------------------------------------------------------
    df.columns = df.columns.str.strip()
    df["Date_Sample_Collected"] = pd.to_datetime(df["Date_Sample_Collected"], errors="coerce")

    # Clean Result_Name
    df["Result_Name"] = (
        df["Result_Name"]
        .astype(str)
        .str.strip()
        .str.replace(r"\s+", " ", regex=True)
        .str.replace("\xa0", " ")
    )

    karenia_standardization = {
        "Karenia sp": "Karenia sp.",
        "Karenia spp": "Karenia sp.",
        "Karenia cf. longicanalis": "Karenia longicanalis",
        "Karenia spp.": "Karenia sp.",
        "Karenia spp": "Karenia sp.",
    }
    df["Result_Name"] = df["Result_Name"].replace(karenia_standardization)

    # Convert "Not detected" → 0 cells/L
    not_detected_mask = (
        df["Result_Value_String"]
        .astype(str)
        .str.strip()
        .str.lower()
        .str.contains("not detected", na=False)
    )
    df.loc[not_detected_mask, "Result_Value_Numeric"] = 0.0
    df.loc[not_detected_mask, "Result_Value_String"] = "0"

    # Site cleaning + merge
    def clean_site(series):
        return (
            series.astype(str)
            .str.strip()
            .str.replace("\xa0", " ", regex=False)
            .str.replace("’", "'", regex=False)
            .str.replace("‘", "'", regex=False)
            .str.replace(r"\s+", " ", regex=True)
            .str.lower()
        )

    df["site_key"] = clean_site(df["Site_Description"])
    sites["site_key"] = clean_site(sites["Site_Description"])

    sites = (
        sites[["site_key", "Latitude", "Longitude"]]
        .dropna(subset=["Latitude", "Longitude"])
        .drop_duplicates(subset=["site_key"])
    )

    df = df.merge(sites, on="site_key", how="left")

    # Small lat/lon offset for Bottom samples (≈220 m)
    bottom_mask = df["Site_Description"].fillna("").str.contains("bottom", case=False)
    OFFSET_LAT = 0.002
    OFFSET_LON = 0.0008
    df.loc[bottom_mask, "Latitude"] += OFFSET_LAT
    df.loc[bottom_mask, "Longitude"] += OFFSET_LON

    return df


@st.cache_data
def load_community(file_path="MASTER spreadsheet of community summaries.xlsx"):
    if not os.path.exists(file_path):
        st.warning(f"⚠️ Community data file '{file_path}' not found. Using empty dataset.")
        return pd.DataFrame()

    df = pd.read_excel(file_path, sheet_name=0)
    df.columns = df.columns.str.strip()

    if "Lat" in df.columns:
        df = df.rename(columns={"Lat": "Latitude"})
    if "Long" in df.columns:
        df = df.rename(columns={"Long": "Longitude"})

    if not pd.api.types.is_datetime64_any_dtype(df["Date"]):
        df["Date"] = pd.to_datetime(df["Date"], origin="1899-12-30", errors="coerce")

    if "Time" in df.columns:
        start_idx = df.columns.get_loc("Time") + 1
    else:
        start_idx = df.columns.get_loc("Date") + 1
    total_idx = df.columns.get_loc("Total plankton")
    species_cols = df.columns[start_idx : total_idx + 1].tolist()

    id_vars = ["Location", "Latitude", "Longitude", "Date"]
    if "Time" in df.columns:
        id_vars.append("Time")

    melted_df = pd.melt(
        df,
        id_vars=id_vars,
        value_vars=species_cols,
        var_name="Result_Name",
        value_name="Result_Value_Numeric",
    )

    melted_df["Site_Description"] = melted_df["Location"]
    melted_df["Date_Sample_Collected"] = melted_df["Date"]
    melted_df = melted_df.drop(["Location", "Date"], axis=1)

    # cells/mL → cells/L
    melted_df["Result_Value_Numeric"] *= 1000
    melted_df["Units"] = "cells/L"

    # Cleanup & standardisation
    melted_df["Site_Description"] = (
        melted_df["Site_Description"]
        .astype(str)
        .str.strip()
        .str.replace(r"\s+", " ", regex=True)
    )

    name_corrections = {
        "Louth Bay jetty": "Louth Bay Jetty",
        "Louth Bay Jetty": "Louth Bay Jetty",
    }
    melted_df["Site_Description"] = melted_df["Site_Description"].replace(name_corrections)

    # Important: suffix so community data does not override recent government data
    melted_df["Site_Description"] = melted_df["Site_Description"] + " - community data"

    melted_df["Result_Name"] = (
        melted_df["Result_Name"]
        .astype(str)
        .str.strip()
        .str.replace(r"\s+", " ", regex=True)
        .str.replace("\xa0", " ", regex=False)
    )
    melted_df["Result_Name"] += " *"

    melted_df["Latitude"] = pd.to_numeric(melted_df["Latitude"], errors="coerce")
    melted_df["Longitude"] = pd.to_numeric(melted_df["Longitude"], errors="coerce")

    return melted_df


# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------
def main():
    st.set_page_config(
        page_title="Harmful Algal Bloom Monitoring - South Australia",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    # Custom CSS
    st.markdown(
        """
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
    }

    section[data-testid="stSidebar"] label {
        font-weight: bold !important;
        color: #000 !important;
    }

    section[data-testid="stSidebar"] span[data-baseweb="tag"] {
        font-size: 14px !important;
    }

    section[data-testid="stSidebar"] [data-testid="stMultiSelect"] {
        font-size: 12px !important;
        padding: 0.2rem 0.3rem !important;
        margin: 0rem 0 0 !important;
    }

    section[data-testid="stSidebar"] [data-testid="stDateInput"] {
        font-size: 12px !important;
        padding: 0.2rem 0.3rem !important;
        margin: 0rem 0 0 !important;
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
        background: linear-gradient(to right,
            #641478 0%,
            #89CFF0 20%,
            #21908c 40%,
            #5dc863 60%,
            #fde725 100%
        );
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
    """,
        unsafe_allow_html=True,
    )

    # ------------------------------------------------------------------
    # Load data
    # ------------------------------------------------------------------
    with st.spinner("Downloading latest government data from ArcGIS… (first load or cache refresh only)"):
        df = load_data()

    community_df = load_community()

    # Persistent state for filters
    if "species_selected" not in st.session_state:
        st.session_state.species_selected = []
    if "date_range" not in st.session_state:
        st.session_state.date_range = []

    # ------------------------------------------------------------------
    # Sidebar
    # ------------------------------------------------------------------
    with st.sidebar:
        st.markdown(
            '<div style="font-size:18px; font-weight:bold; text-align:center; margin: 0 0 0.5rem 0;">'
            "Harmful Algal Bloom Dashboard South Australia</div>",
            unsafe_allow_html=True,
        )

        # ---------- Colour bar + adjustable scale ----------
        def fmt(n):
            if n >= 1_000_000:
                return f"{n/1_000_000:.1f}M"
            if n >= 1_000:
                return f"{n/1_000:.0f}k"
            return f"{int(n):,}"

        # Slider first so we can use the value for labels
        vmax = st.slider(
            "Colour scale maximum (cells/L)",
            min_value=10_000,
            max_value=1_000_000,
            value=500_000,
            step=10_000,
            help="Adjust the upper limit of the colour scale. All values ≥ this number will appear the same colour (yellow).",
        )

        # Dynamic labels that match the current vmax
        labels_html = (
            f"<span>{fmt(0)}</span>"
            f"<span>{fmt(0.2 * vmax)}</span>"
            f"<span>{fmt(0.4 * vmax)}</span>"
            f"<span>{fmt(0.6 * vmax)}</span>"
            f"<span>{fmt(vmax)}</span>"
            f"<span>&gt;{fmt(vmax)}</span>"
        )

        st.markdown(
            f"""
            <div class="colorbar-wrapper">
                <div class="colorbar-container"></div>
                <div class="colorbar-labels">
                    {labels_html}
                </div>
            </div>
            <div class="colorbar-units">Cell count per L</div>
            """,
            unsafe_allow_html=True,
        )

        # Force refresh button
        if st.button("🔄 Force refresh government data", help="Clear cache and pull the latest data from ArcGIS"):
            load_data.clear()
            st.rerun()

        # Include community data
        include_community = st.checkbox("Include community data", value=True)

        if "prev_include_community" not in st.session_state:
            st.session_state.prev_include_community = True
        if include_community != st.session_state.prev_include_community:
            st.session_state.date_range = []
            st.session_state.prev_include_community = include_community

        st.markdown('<div class="sidebar-card">Filters</div>', unsafe_allow_html=True)

        # Combined data for filters
        if include_community:
            combined_df = pd.concat([df, community_df], ignore_index=True)
            if not combined_df.empty:
                min_date, max_date = (
                    combined_df["Date_Sample_Collected"].min(),
                    combined_df["Date_Sample_Collected"].max(),
                )
            else:
                min_date, max_date = pd.to_datetime("2020-01-01"), pd.to_datetime("2030-12-31")
        else:
            combined_df = df.copy()
            if not df.empty:
                min_date, max_date = (
                    df["Date_Sample_Collected"].min(),
                    df["Date_Sample_Collected"].max(),
                )
            else:
                min_date, max_date = pd.to_datetime("2020-01-01"), pd.to_datetime("2030-12-31")

        all_species = sorted(combined_df["Result_Name"].dropna().unique())

        if "species_multiselect" not in st.session_state:
            st.session_state["species_multiselect"] = [s for s in all_species if "Karenia" in s]

        current_selections = st.session_state.get("species_multiselect", [])
        valid_selections = [s for s in current_selections if s in all_species]

        if include_community and "Karenia spp subcount *" in all_species:
            if "Karenia spp subcount *" not in valid_selections:
                valid_selections.append("Karenia spp subcount *")

        if not valid_selections:
            valid_selections = [s for s in all_species if "Karenia" in s]

        st.session_state["species_multiselect"] = valid_selections

        # Custom ordering
        karenia_sp = [s for s in all_species if "Karenia sp." in s and "subcount" not in s]
        subcount = [s for s in all_species if s == "Karenia spp subcount *"]
        other_karenia = [s for s in all_species if "Karenia" in s and s not in karenia_sp + subcount]
        remaining = [s for s in all_species if "Karenia" not in s]
        custom_options = karenia_sp + subcount + other_karenia + sorted(remaining)

        if (
            "species_multiselect" not in st.session_state
            or st.session_state.get("last_include_community", None) != include_community
        ):
            preferred_order = []
            for group in [karenia_sp, subcount, other_karenia]:
                for item in group:
                    if item in valid_selections:
                        preferred_order.append(item)
            for item in valid_selections:
                if item not in preferred_order:
                    preferred_order.append(item)
            st.session_state["species_multiselect"] = preferred_order
            st.session_state["last_include_community"] = include_community

        species_selected = st.multiselect(
            "Select species (via dropdown or start typing, *denotes community data)",
            options=custom_options,
            default=st.session_state["species_multiselect"],
            key=f"species_multiselect_{include_community}_{len(all_species)}",
        )
        st.session_state.species_selected = species_selected

        # Date range
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
                key="date_input",
            )
        else:
            date_range = st.date_input(
                "Date range (year/month/day format)",
                [last_week_start.date(), max_date.date()],
                min_value=min_date.date(),
                max_value=max_date.date(),
                key="date_input",
            )
        st.session_state.date_range = date_range

        if len(date_range) == 2:
            start_date, end_date = pd.to_datetime(date_range[0]), pd.to_datetime(date_range[1])
        else:
            start_date, end_date = min_date, max_date

    # ------------------------------------------------------------------
    # Filter data
    # ------------------------------------------------------------------
    mask_main = (
        df["Result_Name"].isin(species_selected)
        & df["Date_Sample_Collected"].between(start_date, end_date)
    )
    sub_df = df[mask_main].copy()

    comm_sub_df = pd.DataFrame()
    if include_community:
        mask_comm = (
            community_df["Result_Name"].isin(species_selected)
            & community_df["Date_Sample_Collected"].between(start_date, end_date)
        )
        comm_sub_df = community_df[mask_comm].copy()

    filtered_records = len(sub_df) + len(comm_sub_df)
    st.sidebar.markdown(
        f'<div class="records-count">Showing {filtered_records} records matching selected species and date range</div>',
        unsafe_allow_html=True,
    )

    # Disclaimer
    st.sidebar.markdown(
        """
        <div style="font-size:11px; color:#666; margin-top:10px; margin-bottom:20px; padding:4px; border-top:1px solid #ddd;">
        <p style="margin-bottom: 10px;">An instructional video on use of this dashboard can be found
        <a href="https://vimeo.com/manage/videos/1126101537" target="_blank">here</a>.</p>
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
        Phytoplankton of South Australia group</a> and many community volunteers who contributed data and feedback for this dashboard are kindly thanked, in particular: Peri Coleman, Samantha Sea, Carey Hannaford, Kathryn Lewis, Troy Johnson, Lyndon Zimmermann, Faith Coleman, Anthony Rowland, Phil Bamford, Jane Power, Lochie Cameron, Wendy Lambert, Letitia Dahl-Helm, Caro Hannan, Greg Hyde, Karin Hatch, Colleen Burke, Lyndall Booth and Johanna Williams.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ------------------------------------------------------------------
    # Map
    # ------------------------------------------------------------------
    m = folium.Map(
        location=[-34.9, 138.6],
        zoom_start=6,
        control_scale=True,
        zoom_control="bottomleft",
    )

    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri",
        name="Satellite",
        overlay=False,
        control=True,
    ).add_to(m)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
        attr="Esri",
        name="Labels",
        overlay=True,
        control=True,
    ).add_to(m)
    folium.LayerControl(position="bottomright").add_to(m)

    # Colour scale now uses the user-selected vmax
    viridis_colors = ["#641478", "#89CFF0", "#21908c", "#5dc863", "#fde725"]
    colormap = LinearColormap(
        colors=viridis_colors,
        index=[0, 0.2 * vmax, 0.4 * vmax, 0.6 * vmax, vmax],
        vmin=0,
        vmax=vmax,
    )

    # Community markers
    for _, row in comm_sub_df.iterrows():
        if pd.notna(row.get("Latitude")) and pd.notna(row.get("Longitude")):
            value = row["Result_Value_Numeric"]
            value_display = "0" if pd.isna(value) else f"{value:,.0f}"
            color = colormap(value if pd.notna(value) else 0)
            units = row.get("Units", "cells/L")

            time_str = ""
            if "Time" in row and pd.notna(row.get("Time")):
                try:
                    fractional_day = float(row["Time"])
                    total_minutes = int(fractional_day * 1440)
                    hours = total_minutes // 60
                    minutes = total_minutes % 60
                    time_str = f"Time: {hours:02d}:{minutes:02d}<br>"
                except Exception:
                    time_str = f"Time: {row['Time']}<br>"

            folium.CircleMarker(
                location=[row["Latitude"], row["Longitude"]],
                radius=6,
                color=color,
                fill=True,
                fill_color=color,
                fill_opacity=0.8,
                popup=(
                    f"<b>{row['Site_Description']}</b><br>"
                    f"{row['Date_Sample_Collected'].date()}<br>"
                    f"{time_str}"
                    f"{row['Result_Name']}<br>"
                    f"{value_display} {units}"
                ),
            ).add_to(m)

    # Government markers
    for _, row in sub_df.iterrows():
        if pd.notna(row.get("Latitude")) and pd.notna(row.get("Longitude")):
            value = row["Result_Value_Numeric"]
            value_display = "0" if pd.isna(value) else f"{value:,.0f}"
            color = colormap(value if pd.notna(value) else 0)
            units = row.get("Units", "cells/L")

            time_str = ""
            if "Time" in row and pd.notna(row.get("Time")):
                try:
                    fractional_day = float(row["Time"])
                    total_minutes = int(fractional_day * 1440)
                    hours = total_minutes // 60
                    minutes = total_minutes % 60
                    time_str = f"Time: {hours:02d}:{minutes:02d}<br>"
                except Exception:
                    time_str = f"Time: {row['Time']}<br>"

            folium.CircleMarker(
                location=[row["Latitude"], row["Longitude"]],
                radius=6,
                color=color,
                fill=True,
                fill_color=color,
                fill_opacity=0.8,
                popup=(
                    f"<b>{row['Site_Description']}</b><br>"
                    f"{row['Date_Sample_Collected'].date()}<br>"
                    f"{time_str}"
                    f"{row['Result_Name']}<br>"
                    f"{value_display} {units}"
                ),
            ).add_to(m)

    # Fit bounds
    combined_sub = pd.concat([sub_df, comm_sub_df], ignore_index=True)
    if not combined_sub.empty:
        lat_min = combined_sub["Latitude"].min()
        lon_min = combined_sub["Longitude"].min()
        lat_max = combined_sub["Latitude"].max()
        lon_max = combined_sub["Longitude"].max()
        if pd.notna(lat_min) and pd.notna(lon_min) and pd.notna(lat_max) and pd.notna(lon_max):
            m.fit_bounds([[lat_min, lon_min], [lat_max, lon_max]])

    st_folium(m, width="100%", height=550)

    # ------------------------------------------------------------------
    # Trends section
    # ------------------------------------------------------------------
    if not df.empty:
        st.subheader("Trends Over Time")

        include_comm_in_trends = st.checkbox(
            "Include community data in trends", value=include_community
        )

        if include_comm_in_trends and include_community and not community_df.empty:
            base_trends_df = pd.concat([df, community_df], ignore_index=True)
        else:
            base_trends_df = df.copy()

        all_species_trends = sorted(base_trends_df["Result_Name"].dropna().unique())

        subcount = [s for s in all_species_trends if "Karenia spp subcount *" in s]
        karenia_sp = [s for s in all_species_trends if "Karenia sp." in s and "subcount" not in s]
        other_karenia = [
            s for s in all_species_trends if "Karenia" in s and s not in subcount + karenia_sp
        ]
        remaining = [s for s in all_species_trends if "Karenia" not in s]
        custom_trend_options = subcount + karenia_sp + other_karenia + sorted(remaining)

        default_trend_species = (
            [s for s in custom_trend_options if "Karenia" in s] or custom_trend_options[:3]
        )

        selected_trend_species = st.multiselect(
            "Select species for trend chart",
            options=custom_trend_options,
            default=default_trend_species,
        )

        all_sites = sorted(base_trends_df["Site_Description"].dropna().unique())
        selected_site = st.selectbox(
            "Filter by site", options=["All Sites"] + all_sites, index=0
        )

        plot_df = base_trends_df[
            (base_trends_df["Result_Name"].isin(selected_trend_species))
            & (base_trends_df["Result_Value_Numeric"].notna())
        ].copy()

        if selected_site != "All Sites":
            plot_df = plot_df[plot_df["Site_Description"] == selected_site]

        plot_df = plot_df.sort_values("Date_Sample_Collected")

        if not plot_df.empty:
            trend_df = plot_df.pivot_table(
                index="Date_Sample_Collected",
                columns="Result_Name",
                values="Result_Value_Numeric",
                aggfunc="mean",
            ).reset_index()

            trend_melted = trend_df.melt(
                id_vars="Date_Sample_Collected",
                var_name="Species",
                value_name="Cell_Count",
                ignore_index=False,
            )

            base = (
                alt.Chart(trend_melted)
                .mark_line(point=True)
                .encode(
                    x=alt.X(
                        "Date_Sample_Collected:T",
                        title="Date",
                        axis=alt.Axis(labelAngle=0, format="%d %b %Y"),
                    ),
                    y=alt.Y("Cell_Count:Q", title="Cell Count per L"),
                    color=alt.Color("Species:N", title="Species"),
                    tooltip=["Date_Sample_Collected", "Species", "Cell_Count"],
                )
                .properties(
                    width=800,
                    height=400,
                    title=alt.TitleParams(
                        text="Trends for selected species (note: average values will be displayed if 'All Sites' selected, *denotes community data)",
                        fontSize=14,
                        fontWeight="normal",
                        color="#4c4c4c",
                    ),
                )
                .interactive()
            )
            st.altair_chart(base, use_container_width=True)

            st.caption(
                f"Showing {len(plot_df)} data points across {len(selected_trend_species)} species "
                f"and {'all sites' if selected_site == 'All Sites' else selected_site}."
            )
        else:
            st.info("No data available for the selected species and site. Adjust options above.")
    else:
        st.info("No data loaded. Check the ArcGIS connection.")


if __name__ == "__main__":
    main()
