import streamlit as st
import pandas as pd
import gspread
import smtplib
from email.mime.text import MIMEText
import random

# -------------------------------------------------------------
# CONFIGURATION
# -------------------------------------------------------------
st.set_page_config(page_title="Debtors Portal", layout="wide")

SPREADSHEET_NAME = "AAPL-Jockey-Reporter"
WORKSHEET_NAME = "OUTSTANDING"
USERS_WORKSHEET = "USERS"

# Gmail SMTP Credentials (pulls from Streamlit Secrets or uses constants below)
SENDER_EMAIL = st.secrets.get("SENDER_EMAIL", "your-email@gmail.com")
SENDER_APP_PASSWORD = st.secrets.get("SENDER_APP_PASSWORD", "xxxx xxxx xxxx xxxx")

# -------------------------------------------------------------
# GOOGLE SHEETS & AUTH HELPERS
# -------------------------------------------------------------
def get_gspread_client():
    """Connects to Google Sheets using st.secrets TOML block."""
    creds_dict = dict(st.secrets["gcp_service_account"])
    return gspread.service_account_from_dict(creds_dict)

def is_email_authorized(user_email):
    """Check if user email exists in USERS tab with Active status."""
    try:
        gc = get_gspread_client()
        sheet = gc.open(SPREADSHEET_NAME).worksheet(USERS_WORKSHEET)
        users_df = pd.DataFrame(sheet.get_all_records())
        
        users_df.columns = users_df.columns.str.strip().str.capitalize()
        if "Email" not in users_df.columns or "Status" not in users_df.columns:
            return False
            
        users_df["Email"] = users_df["Email"].astype(str).str.strip().str.lower()
        users_df["Status"] = users_df["Status"].astype(str).str.strip().str.capitalize()
        
        match = users_df[(users_df["Email"] == user_email.lower().strip()) & (users_df["Status"] == "Active")]
        return not match.empty
    except Exception as e:
        st.error(f"User verification error: {e}")
        return False

def send_otp_email(recipient_email, otp_code):
    """Sends 6-digit OTP using Gmail SMTP & 16-digit App Password."""
    subject = "Your Login OTP - Debtors Portal"
    body = f"Your one-time authentication code is: {otp_code}\n\nThis code is valid for 5 minutes."
    
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = SENDER_EMAIL
    msg['To'] = recipient_email
    
    try:
        # Connect via SSL port 465
        clean_password = SENDER_APP_PASSWORD.replace(" ", "")
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(SENDER_EMAIL, clean_password)
            server.sendmail(SENDER_EMAIL, recipient_email, msg.as_string())
        return True
    except Exception as e:
        st.error(f"Failed to send email via SMTP: {e}")
        return False

# -------------------------------------------------------------
# FETCH & CACHE DATA (Original Production Logic)
# -------------------------------------------------------------
@st.cache_data(ttl=600) 
def load_data():
    gc = get_gspread_client()
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
# SESSION STATE MANAGEMENT
# -------------------------------------------------------------
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
if "otp_sent" not in st.session_state:
    st.session_state.otp_sent = False
if "generated_otp" not in st.session_state:
    st.session_state.generated_otp = None
if "target_email" not in st.session_state:
    st.session_state.target_email = ""

# -------------------------------------------------------------
# AUTHENTICATION UI GATE
# -------------------------------------------------------------
if not st.session_state.authenticated:
    st.title("🔐 Debtors Portal Access")
    st.subheader("Login Authentication")
    
    if not st.session_state.otp_sent:
        email_input = st.text_input("Enter your authorized Email Address:")
        
        if st.button("Send OTP"):
            if email_input:
                with st.spinner("Checking permissions..."):
                    if is_email_authorized(email_input):
                        otp = str(random.randint(100000, 999999))
                        if send_otp_email(email_input, otp):
                            st.session_state.generated_otp = otp
                            st.session_state.target_email = email_input
                            st.session_state.otp_sent = True
                            st.success(f"OTP sent successfully to {email_input}!")
                            st.rerun()
                    else:
                        st.error("Access Denied: Email address not found in authorized users list.")
            else:
                st.warning("Please enter a valid email address.")
    else:
        st.info(f"An OTP code has been sent to **{st.session_state.target_email}**")
        entered_otp = st.text_input("Enter 6-digit OTP:", max_chars=6)
        
        col1, col2 = st.columns([1, 4])
        with col1:
            if st.button("Verify OTP"):
                if entered_otp == st.session_state.generated_otp:
                    st.session_state.authenticated = True
                    st.success("Authenticated successfully!")
                    st.rerun()
                else:
                    st.error("Invalid OTP code. Please try again.")
        with col2:
            if st.button("Cancel / Change Email"):
                st.session_state.otp_sent = False
                st.session_state.generated_otp = None
                st.rerun()

# -------------------------------------------------------------
# MAIN PORTAL UI (Original Production Interface)
# -------------------------------------------------------------
else:
    # Header & Logout
    top_col1, top_col2 = st.columns([5, 1])
    with top_col1:
        st.caption(f"Logged in as: **{st.session_state.target_email}**")
    with top_col2:
        if st.button("Logout"):
            st.session_state.authenticated = False
            st.session_state.otp_sent = False
            st.session_state.generated_otp = None
            st.rerun()

    st.title("AAPL Jockey Outstanding Debtors")

    try:
        df = load_data()
        
        # 1. Searchable Dropdown for Party Name
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