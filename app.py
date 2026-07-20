import streamlit as st
import pandas as pd
import gspread
import json

# -------------------------------------------------------------
# CONFIGURATION
# -------------------------------------------------------------
st.set_page_config(page_title="Debtors Portal", layout="wide")
SPREADSHEET_NAME = "AAPL-Jockey-Reporter"
WORKSHEET_NAME = "OUTSTANDING"

# -------------------------------------------------------------
# FETCH & CACHE DATA
# -------------------------------------------------------------
# @st.cache_data prevents the app from downloading the sheet every time you click a button
@st.cache_data(ttl=600) 
def load_data():
    # Streamlit automatically parses the TOML block into a Python dictionary
    creds_dict = dict(st.secrets["gcp_service_account"])
    
    # Connect to Google Sheets using the dictionary directly
    gc = gspread.service_account_from_dict(creds_dict)
    sheet = gc.open(SPREADSHEET_NAME).worksheet(WORKSHEET_NAME)
    
    # Get all data as a list of dictionaries
    data = sheet.get_all_records()
    df = pd.DataFrame(data)
    
    # Clean up dates and numbers for sorting and math
    if "Invoice Date" in df.columns:
        df["Invoice Date"] = pd.to_datetime(df["Invoice Date"], dayfirst=True, errors="coerce")
        
    if "Pending Amount" in df.columns:
        df["Pending Amount"] = df["Pending Amount"].astype(str).str.replace(',', '')
        df["Pending Amount"] = pd.to_numeric(df["Pending Amount"], errors="coerce").fillna(0)
        
    return df

# -------------------------------------------------------------
# MAIN UI
# -------------------------------------------------------------
st.title("AAPL Jockey Outstanding Debtors")

try:
    df = load_data()
    
    # 1. Searchable Dropdown for Party Name
    # Extract unique party names, drop empty ones, sort alphabetically
    unique_parties = sorted([p for p in df["Party Name"].unique() if str(p).strip() != ""])
    party_list = ["All Parties"] + unique_parties
    
    selected_party = st.selectbox("Search and Select Party Name:", party_list)
    
    # 2. Filter Data based on selection
    if selected_party != "All Parties":
        filtered_df = df[df["Party Name"] == selected_party]
    else:
        filtered_df = df
        
    # 3. Sort Chronologically (Oldest bills first)
    if "Invoice Date" in filtered_df.columns:
        filtered_df = filtered_df.sort_values(by="Invoice Date", ascending=True)
        # Format the date back to a clean string for display (DD-MM-YYYY)
        filtered_df["Invoice Date"] = filtered_df["Invoice Date"].dt.strftime('%d-%m-%Y')
        
    # 4. Display Quick Summary Metrics
    total_pending = filtered_df["Pending Amount"].sum()
    bill_count = len(filtered_df)
    
    col1, col2 = st.columns(2)
    col1.metric("Total Outstanding", f"₹ {total_pending:,.2f}")
    col2.metric("Total Pending Bills", bill_count)
    
    # 5. Display the Interactive Table
    st.dataframe(
        filtered_df, 
        use_container_width=True, 
        hide_index=True
    )
    
except Exception as e:
    st.error(f"Error loading data from Google Sheets: {e}")