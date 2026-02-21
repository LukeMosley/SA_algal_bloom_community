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
# Fetch data from ArcGIS Feature Services (with pagination)
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

    sample_df.columns = sample_df.columns.str.strip()
    sites_df.columns = sites_df.columns.str.strip()

    SAMPLE_KEY = "Site_Number"
    SITE_KEY = "SiteNumber"
    if SAMPLE_KEY not in sample_df.columns or SITE_KEY not in sites_df.columns:
        st.error("Key column missing in ArcGIS data.")
        return pd.DataFrame()

    sample_df[SAMPLE_KEY] = sample_df[SAMPLE_KEY].astype(str)
    sites_df[SITE_KEY] = sites_df[SITE_KEY].astype(str)
    sites_df = sites_df.rename(columns={SITE_KEY: SAMPLE_KEY})

    merged_df = sample_df.merge(
        sites_df[["Site_Number", "Latitude", "Longitude", "SiteName", "Region"]],
        on="Site_Number",
        how="left"
    )

    merged_df["Latitude"] = pd.to_numeric(merged_df["Latitude"], errors="coerce")
    merged_df["Longitude"] = pd.to_numeric(merged_df["Longitude"], errors="coerce")

    # Improved date handling for DateOnly → ms epoch
    if "Date_Sample_Collected" in merged_df.columns:
        col = merged_df["Date_Sample_Collected"]
        if pd.api.types.is_numeric_dtype(col):
            merged_df["Date_Sample_Collected"] = pd.to_datetime(
                col, unit="ms", errors="coerce", utc=True
            ).dt.tz_localize(None)
        else:
            # Fallback: try string parse if not numeric
            merged_df["Date_Sample_Collected"] = pd.to_datetime(
                col.astype(str), errors="coerce"
            )
    else:
        st.warning("Date_Sample_Collected column not found.")

    if "Result_Name" in merged_df.columns:
        merged_df["Result_Name"] = (
            merged_df["Result_Name"]
            .astype(str)
            .str.strip()
            .str.replace(r"\s+", " ", regex=True)
            .replace("nan", pd.NA)
        )

    if "Result_Value_Numeric" in merged_df.columns:
        merged_df["Result_Value_Numeric"] = pd.to_numeric(
            merged_df["Result_Value_Numeric"], errors="coerce"
        )

    # Debug inside function (will show in sidebar)
    st.sidebar.caption(f"Gov rows: {len(merged_df)}")
    st.sidebar.caption(f"Gov rows with coords: {merged_df[['Latitude','Longitude']].dropna().shape[0]}")

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
    if "Date" in df.columns:
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    fixed_cols = ["Location", "Latitude", "Longitude", "Date"]
    species_cols = [c for c in df.columns if c not in fixed_cols]
    melted_df = pd.melt(
        df,
        id_vars=fixed_cols,
        value_vars=species_cols,
        var_name="Result_Name",
        value_name="Result_Value_Numeric"
    )
    melted_df["Result_Value_Numeric"] = pd.to_numeric(
        melted_df["Result_Value_Numeric"], errors="coerce"
    ) * 1000
    melted_df["Result_Name"] = melted_df["Result_Name"].astype(str).str.strip() + " *"
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

    # Your CSS styles here (unchanged) ...
    st.markdown("""<style> ... your full CSS ... </style>""", unsafe_allow_html=True)

    df = load_gov_data()
    community_df = load_community()

    # Debug: show immediately after loading
    st.sidebar.subheader("DEBUG: Government Data")
    st.sidebar.write("Sample dates head:", df["Date_Sample_Collected"].head(10))
    st.sidebar.write("Date dtype:", df["Date_Sample_Collected"].dtype)
    st.sidebar.write("Valid dates count:", df["Date_Sample_Collected"].notna().sum())
    st.sidebar.write("Min/max date:", 
                     df["Date_Sample_Collected"].min(), 
                     df["Date_Sample_Collected"].max())
    st.sidebar.write("Sample Result_Name:", df["Result_Name"].dropna().unique()[:15])

    st.sidebar.caption(f"Government data loaded: {len(df)} rows")
    st.sidebar.caption(f"Community data loaded: {len(community_df)} rows")

    if 'species_multiselect' not in st.session_state:
        st.session_state.species_multiselect = []
    if 'date_range' not in st.session_state:
        st.session_state.date_range = []

    with st.sidebar:
        # Your sidebar title, colorbar, checkbox, refresh button ... (unchanged)

        include_community = st.checkbox('Include community data', value=True)
        # ... your prev_include_community logic ...

        if st.button("↻ Refresh Government Data"):
            load_gov_data.clear()
            st.success("Government data refreshed!")
            st.rerun()

        # Filters ...
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

        all_species = sorted(combined_df['Result_Name'].dropna().unique())

        # Your species multiselect logic ...

        # ... after calculating combined_df, min_date, max_date ...

        all_species = sorted(combined_df['Result_Name'].dropna().unique().tolist()) if 'Result_Name' in combined_df else []

        # Debug: show what species are actually available
        st.sidebar.caption(f"Available species count: {len(all_species)}")
        if all_species:
            st.sidebar.caption("First 10 species: " + ", ".join(all_species[:10]))
        else:
            st.sidebar.warning("No species names found in data.")

        # Safe default: only Karenia that actually exist in all_species
        karenia_defaults = [s for s in all_species if "karenia" in s.lower()]
        if karenia_defaults:
            default_species = karenia_defaults[:3]  # limit to avoid overload
        else:
            default_species = all_species[:3] if all_species else []

        # Extra safety: filter defaults to only those in options
        default_species = [s for s in default_species if s in all_species]

        previous_selected = st.session_state.get("species_multiselect", [])
        valid_previous = [s for s in previous_selected if s in all_species]

        if valid_previous:
            default_species = valid_previous

        species_selected = st.multiselect(
            "Select species (via dropdown or start typing, *denotes community data)",
            options=all_species,
            default=default_species,
            key="species_multiselect",
            placeholder="Select one or more species..."
        )

        # Your date_input ...

        # Calculate filtered_records (unchanged)

        # Your disclaimer markdown ...

    # Main filtering
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

    # Additional debug: filtered counts
    st.sidebar.subheader("DEBUG: After Filters")
    st.sidebar.caption(f"Government filtered: {len(sub_df)} rows")
    st.sidebar.caption(f"Community filtered: {len(comm_sub_df)} rows")
    st.sidebar.caption(f"Total points to plot: {len(sub_df) + len(comm_sub_df)}")

    # Map creation (unchanged, but add safe .date() handling)
    m = folium.Map(location=[-34.9, 138.6], zoom_start=6, control_scale=True)
    # ... tile layers ...

    colormap = LinearColormap(...)  # unchanged

    for _, row in sub_df.iterrows():
        if pd.notna(row.get('Latitude')) and pd.notna(row.get('Longitude')):
            value = row['Result_Value_Numeric']
            color = colormap(value)
            date_str = row['Date_Sample_Collected'].strftime('%Y-%m-%d') if pd.notna(row['Date_Sample_Collected']) else "Unknown date"
            popup = f"<b>{row.get('Site_Description', row.get('SiteName', 'Unknown'))}</b><br>{date_str}<br>{row['Result_Name']}<br>{value:,.0f} {row.get('Units', 'cells/L')}"
            folium.CircleMarker(
                location=[row['Latitude'], row['Longitude']],
                radius=6, color=color, fill=True, fill_color=color, fill_opacity=0.8,
                popup=popup
            ).add_to(m)

    # Same for comm_sub_df (use same popup logic)

    # fit_bounds if data ...

    st_folium(m, width='100%', height=550)

    # Rest of your app: Trends, NASA image ... (unchanged)

if __name__ == "__main__":
    main()
