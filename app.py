import streamlit as st
import gspread
import pandas as pd
import smtplib
from email.mime.text import MIMEText
import random
import time

# -------------------------------------------------------------
# CONFIGURATION
# -------------------------------------------------------------
SPREADSHEET_NAME = "AAPL-Jockey-Reporter"
CREDENTIALS_FILE = "credentials.json"

# SMTP Settings for sending OTP
SENDER_EMAIL = "aapljockey@gmail.com"          # Your Gmail address
SENDER_APP_PASSWORD = "vkcx puyj meux cpvy"    # 16-character App Password

# Page setup
st.set_page_config(page_title="Outstanding Reports", page_icon="📊", layout="wide")

# -------------------------------------------------------------
# HELPER FUNCTIONS
# -------------------------------------------------------------
def get_google_sheet_client():
    return gspread.service_account(filename=CREDENTIALS_FILE)

def is_email_authorized(user_email):
    """Check if email exists in USERS tab with Active status"""
    try:
        gc = get_google_sheet_client()
        sheet = gc.open(SPREADSHEET_NAME).worksheet("USERS")
        users_df = pd.DataFrame(sheet.get_all_records())
        
        # Clean column names and emails
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
    """Send 6-digit OTP via Gmail SMTP"""
    subject = "Your Login OTP - Outstanding Reports"
    body = f"Your one-time authentication code is: {otp_code}\n\nThis code is valid for 5 minutes."
    
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = SENDER_EMAIL
    msg['To'] = recipient_email
    
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(SENDER_EMAIL, SENDER_APP_PASSWORD)
            server.sendmail(SENDER_EMAIL, recipient_email, msg.as_string())
        return True
    except Exception as e:
        st.error(f"Failed to send email: {e}")
        return False

def load_outstanding_data():
    """Fetch report data from Google Sheets"""
    gc = get_google_sheet_client()
    sheet = gc.open(SPREADSHEET_NAME).worksheet("OUTSTANDING")
    data = sheet.get_all_records()
    return pd.DataFrame(data)

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
# INTERFACE LOGIC
# -------------------------------------------------------------
st.title("📊 Outstanding Bills Dashboard")

if not st.session_state.authenticated:
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
                st.warning("Please enter a valid email.")
    else:
        st.info(f"An OTP has been sent to **{st.session_state.target_email}**")
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

else:
    # -------------------------------------------------------------
    # VIEWER DASHBOARD (AUTHENTICATED)
    # -------------------------------------------------------------
    col_user, col_logout = st.columns([4, 1])
    with col_user:
        st.caption(f"Logged in as: **{st.session_state.target_email}**")
    with col_logout:
        if st.button("Logout"):
            st.session_state.authenticated = False
            st.session_state.otp_sent = False
            st.rerun()
            
    st.markdown("---")
    
    with st.spinner("Loading latest outstanding data..."):
        df = load_outstanding_data()
        
    if not df.empty:
        # Metrics summary
        total_pending = df["Pending Amount"].sum() if "Pending Amount" in df.columns else 0
        total_parties = len(df["Party Name"].unique()) if "Party Name" in df.columns else len(df)
        
        m1, m2 = st.columns(2)
        m1.metric("Total Outstanding Amount", f"₹ {total_pending:,.2f}")
        m2.metric("Total Pending Parties", total_parties)
        
        st.markdown("### Outstanding Ledger")
        
        # Search filter
        search_query = st.text_input("🔍 Search by Party Name or Reference:")
        if search_query:
            filtered_df = df[
                df["Party Name"].astype(str).str.contains(search_query, case=False, na=False) |
                df["Reference"].astype(str).str.contains(search_query, case=False, na=False)
            ]
        else:
            filtered_df = df
            
        st.dataframe(filtered_df, use_container_width=True, hide_index=True)
    else:
        st.warning("No records found in the Outstanding report.")