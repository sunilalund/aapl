import datetime
import hashlib
import hmac
import os
import random
import smtplib
import time
from email.mime.text import MIMEText
import gspread
import pandas as pd
import plotly.express as px
import streamlit as st
from google.oauth2.service_account import Credentials

# -------------------------------------------------------------
# 1. PAGE & BRANDING CONFIGURATION
# -------------------------------------------------------------
st.set_page_config(
    page_title="AAPL Sales & Operations Portal",
    page_icon="ðŸ“Š",
    layout="wide",
    initial_sidebar_state="expanded",
)

SPREADSHEET_NAME = "AAPL-Jockey-Reporter"
WORKSHEET_OUTSTANDING = "OUTSTANDING"
WORKSHEET_USERS = "USERS"
WORKSHEET_JC = "JC_Master"
WORKSHEET_INVESTMENT = "Investment_Master"
WORKSHEET_STOCK = "STOCK"  # Sheet containing full granular stock item records

# Gmail SMTP Credentials (from Secrets or fallbacks)
SENDER_EMAIL = st.secrets.get("SENDER_EMAIL", "your-email@gmail.com")
SENDER_APP_PASSWORD = st.secrets.get("SENDER_APP_PASSWORD", "xxxx xxxx xxxx xxxx")


# -------------------------------------------------------------
# 2. GOOGLE SHEETS & AUTH HELPERS
# -------------------------------------------------------------
def generate_auth_token(email, expiry_timestamp):
    """Generates a secure 16-character token from the user email."""
    secret = st.secrets.get("SENDER_APP_PASSWORD", "secret_salt_key")
    payload = f"{email.lower().strip()}:{expiry_timestamp}"
    return hmac.new(
        secret.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()[:16]


def get_gspread_client():
    """Connects to Google Sheets using Streamlit Secrets TOML or local credentials file."""
    if "gcp_service_account" in st.secrets:
        creds_dict = dict(st.secrets["gcp_service_account"])
        return gspread.service_account_from_dict(creds_dict)
    elif os.path.exists("credentials.json"):
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_file(
            "credentials.json", scopes=scopes
        )
        return gspread.authorize(creds)
    else:
        st.error("No valid GCP credentials found in Secrets or credentials.json!")
        st.stop()


def get_user_auth_info(user_email):
    """Checks user email against USERS tab. Returns tuple: (is_active, role)."""
    try:
        gc = get_gspread_client()
        sheet = gc.open(SPREADSHEET_NAME).worksheet(WORKSHEET_USERS)
        users_df = pd.DataFrame(sheet.get_all_records())

        users_df.columns = users_df.columns.str.strip().str.capitalize()
        if "Email" not in users_df.columns or "Status" not in users_df.columns:
            return False, "Guest"

        users_df["Email"] = users_df["Email"].astype(str).str.strip().str.lower()
        users_df["Status"] = users_df["Status"].astype(str).str.strip().str.capitalize()

        if "Role" not in users_df.columns:
            users_df["Role"] = "Sales"
        else:
            users_df["Role"] = users_df["Role"].astype(str).str.strip().str.title()

        match = users_df[
            (users_df["Email"] == user_email.lower().strip())
            & (users_df["Status"] == "Active")
        ]

        if not match.empty:
            role = match.iloc[0]["Role"]
            return True, role
        return False, "Guest"
    except Exception as e:
        st.error(f"User verification error: {e}")
        return False, "Guest"


def send_otp_email(recipient_email, otp_code):
    """Sends 6-digit OTP using Gmail SMTP & App Password."""
    subject = "Your Login OTP - AAPL Sales & Operations Portal"
    body = f"Your one-time authentication code is: {otp_code}\n\nThis code is valid for 5 minutes."

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = SENDER_EMAIL
    msg["To"] = recipient_email

    try:
        clean_password = SENDER_APP_PASSWORD.replace(" ", "")
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(SENDER_EMAIL, clean_password)
            server.sendmail(SENDER_EMAIL, recipient_email, msg.as_string())
        return True
    except Exception as e:
        st.error(f"Failed to send email via SMTP: {e}")
        return False


def format_inr(val):
    """Formats numeric values into Indian Rupee format."""
    try:
        val = float(val)
        is_neg = val < 0
        val = abs(val)
        s, *d = f"{val:.2f}".split(".")
        r = s[-3:]
        s = s[:-3]
        groups = []
        while s:
            groups.append(s[-2:])
            s = s[:-2]
        groups.reverse()
        formatted_int = ",".join(groups + [r]) if groups else r
        res = f"â‚¹{formatted_int}.{d[0]}"
        return f"-{res}" if is_neg else res
    except Exception:
        return f"â‚¹{val}"


# -------------------------------------------------------------
# 3. CACHED DATA LOADING ENGINE
# -------------------------------------------------------------
@st.cache_data(ttl=300)
def load_all_portal_data():
    """Loads all worksheets from Google Sheets into DataFrames."""
    gc = get_gspread_client()
    sh = gc.open(SPREADSHEET_NAME)

    # 1. Outstanding Data
    try:
        ws_out = sh.worksheet(WORKSHEET_OUTSTANDING).get_all_records()
        df_out = pd.DataFrame(ws_out)
        if "Invoice Date" in df_out.columns:
            df_out["Invoice Date"] = pd.to_datetime(
                df_out["Invoice Date"], dayfirst=True, errors="coerce"
            )
        if "Pending Amount" in df_out.columns:
            df_out["Pending Amount"] = (
                df_out["Pending Amount"].astype(str).str.replace(",", "")
            )
            df_out["Pending Amount"] = (
                pd.to_numeric(df_out["Pending Amount"], errors="coerce").fillna(0)
            )
    except Exception:
        df_out = pd.DataFrame()

    # 2. JC Master Performance Data
    try:
        ws_jc = sh.worksheet(WORKSHEET_JC).get_all_records()
        df_jc = pd.DataFrame(ws_jc)
    except Exception:
        df_jc = pd.DataFrame()

    # 3. Investment Master Data
    try:
        ws_inv = sh.worksheet(WORKSHEET_INVESTMENT).get_all_records()
        df_inv = pd.DataFrame(ws_inv)
    except Exception:
        df_inv = pd.DataFrame()

    # 4. Full Granular Stock Data
    try:
        ws_stock = sh.worksheet(WORKSHEET_STOCK).get_all_records()
        df_stock = pd.DataFrame(ws_stock)
    except Exception:
        df_stock = pd.DataFrame()

    return df_out, df_jc, df_inv, df_stock


# -------------------------------------------------------------
# 4. SESSION STATE MANAGEMENT & AUTO-LOGIN GATE
# -------------------------------------------------------------
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
if "otp_sent" not in st.session_state:
    st.session_state.otp_sent = False
if "generated_otp" not in st.session_state:
    st.session_state.generated_otp = None
if "target_email" not in st.session_state:
    st.session_state.target_email = ""
if "user_role" not in st.session_state:
    st.session_state.user_role = "Sales"

# Auto-login via Query Params
if not st.session_state.authenticated:
    url_email = st.query_params.get("user")
    url_token = st.query_params.get("token")
    url_exp = st.query_params.get("exp")

    if url_email and url_token and url_exp:
        try:
            exp_ts = int(url_exp)
            current_ts = int(time.time())

            if current_ts < exp_ts:
                expected_token = generate_auth_token(url_email, exp_ts)
                is_active, role = get_user_auth_info(url_email)
                if url_token == expected_token and is_active:
                    st.session_state.authenticated = True
                    st.session_state.target_email = url_email
                    st.session_state.user_role = role
                else:
                    st.query_params.clear()
            else:
                st.query_params.clear()
        except ValueError:
            st.query_params.clear()

# -------------------------------------------------------------
# 5. AUTHENTICATION UI GATE (LOGIN PAGE)
# -------------------------------------------------------------
if not st.session_state.authenticated:
    st.title("ðŸ” AAPL Sales & Operations Portal")
    st.subheader("Login Authentication")

    if not st.session_state.otp_sent:
        email_input = st.text_input("Enter your authorized Email Address:")

        if st.button("Send OTP"):
            if email_input:
                with st.spinner("Verifying credentials..."):
                    is_active, role = get_user_auth_info(email_input)
                    if is_active:
                        otp = str(random.randint(100000, 999999))
                        if send_otp_email(email_input, otp):
                            st.session_state.generated_otp = otp
                            st.session_state.target_email = email_input
                            st.session_state.user_role = role
                            st.session_state.otp_sent = True
                            st.success(f"OTP sent successfully to {email_input}!")
                            st.rerun()
                    else:
                        st.error(
                            "Access Denied: Email not authorized or inactive."
                        )
            else:
                st.warning("Please enter a valid email address.")
    else:
        st.info(f"OTP sent to **{st.session_state.target_email}**")
        entered_otp = st.text_input("Enter 6-digit OTP:", max_chars=6)

        col1, col2 = st.columns([1, 4])
        with col1:
            if st.button("Verify OTP"):
                if entered_otp == st.session_state.generated_otp:
                    st.session_state.authenticated = True
                    exp_ts = int(time.time()) + 43200  # 12 Hours persistent URL token
                    token = generate_auth_token(
                        st.session_state.target_email, exp_ts
                    )

                    st.query_params["user"] = st.session_state.target_email
                    st.query_params["token"] = token
                    st.query_params["exp"] = str(exp_ts)

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
# 6. MAIN PORTAL (POST-AUTHENTICATION)
# -------------------------------------------------------------
else:
    df_out, df_jc, df_inv, df_stock = load_all_portal_data()
    role = st.session_state.user_role
    is_admin_or_mgr = role in ["Admin", "Manager"]

    # Top Navigation Bar & User Information
    top_col1, top_col2 = st.columns([5, 1])
    with top_col1:
        st.caption(
            f"Logged in as: **{st.session_state.target_email}** | Role: **{role}**"
        )
    with top_col2:
        if st.button("Logout"):
            st.query_params.clear()
            st.session_state.authenticated = False
            st.session_state.otp_sent = False
            st.session_state.generated_otp = None
            st.rerun()

    # Sidebar Navigation Menu
    st.sidebar.title("AAPL Portal")
    st.sidebar.caption(f"Role View: {role}")
    st.sidebar.divider()

    # Define Navigation Options based on Role
    if is_admin_or_mgr:
        menu_options = [
            "Executive Dashboard",
            "Outstanding Debtors",
            "Stock Details",
            "Investment Breakdown",
        ]
    else:
        # Sales & Operations Menu Options
        menu_options = [
            "Achievement & Targets (Units)",
            "Outstanding Debtors",
            "Stock Details",
        ]

    menu = st.sidebar.radio("Navigation Menu", options=menu_options)

    st.sidebar.divider()
    if st.sidebar.button("ðŸ”„ Refresh Data"):
        st.cache_data.clear()
        st.rerun()

    # =========================================================
    # MENU VIEW 2: SALES & OPERATIONS UNITS DASHBOARD
    # =========================================================
    elif menu == "Achievement & Targets (Units)":
        st.title("ðŸŽ¯ Sales Performance & Target Tracker")
        st.caption("Unit-level achievement metrics per division.")

        available_jcs = (
            df_jc["JC_Month"].unique().tolist()
            if not df_jc.empty and "JC_Month" in df_jc.columns
            else ["M1"]
        )
        selected_jc = st.selectbox("Select Active JC Month", options=available_jcs)

        df_jc_filtered = df_jc[df_jc["JC_Month"] == selected_jc].copy()

        tot_target_pcs = (
            pd.to_numeric(df_jc_filtered["Target_Pcs"], errors="coerce")
            .fillna(0)
            .sum()
        )
        tot_achv_pcs = (
            pd.to_numeric(df_jc_filtered["Achv_Pcs"], errors="coerce")
            .fillna(0)
            .sum()
        )
        tot_bal_pcs = (
            pd.to_numeric(df_jc_filtered["Balance_Pcs"], errors="coerce")
            .fillna(0)
            .sum()
        )
        overall_pct = (
            (tot_achv_pcs / tot_target_pcs) * 100 if tot_target_pcs > 0 else 0.0
        )
        tot_achv_val = (
            pd.to_numeric(df_jc_filtered["Achv_Value"], errors="coerce")
            .fillna(0)
            .sum()
	)

	c1, c2, c3, c4, c5 = st.columns(5)

	if is_admin_or_mgr:

	    c1.metric("Target (Pcs)", f"{tot_target_pcs:,.0f}")
            c2.metric("Achieved (Pcs)", f"{tot_achv_pcs:,.0f}")
            c3.metric("Achievement %", f"{overall_pct:.1f}%")
            c4.metric("Balance Target (Pcs)", f"{tot_bal_pcs:,.0f}")
	    c5.metric("Achieved (Value)", format_inr(tot_achv_val))
	else:
	    c1.metric("Target (Pcs)", f"{tot_target_pcs:,.0f}")
            c2.metric("Achieved (Pcs)", f"{tot_achv_pcs:,.0f}")
            c3.metric("Achievement %", f"{overall_pct:.1f}%")
            c4.metric("Balance Target (Pcs)", f"{tot_bal_pcs:,.0f}")

        st.divider()

        st.subheader(f"Division Target vs Achievement in Units ({selected_jc})")
        fig_units = px.bar(
            df_jc_filtered,
            x="Division",
            y=["Target_Pcs", "Achv_Pcs", "Balance_Pcs", "Achv_Value"],
            barmode="group",
            labels={"value": "Quantity (Pcs)", "variable": "Status"},
            color_discrete_sequence=["#0d6efd", "#198754", "#dc3545"],
        )
        st.plotly_chart(fig_units, use_container_width=True)

        st.subheader("Division Performance Breakdown")
        st.dataframe(
            df_jc_filtered[
                ["Division", "Target_Pcs", "Achv_Pcs", "Achv_Pct", "Balance_Pcs", "Achv_Value"]
            ],
            use_container_width=True,
            hide_index=True,
        )

    # =========================================================
    # MENU VIEW 1: ADMIN/MANAGER EXECUTIVE DASHBOARD
    # =========================================================
    if menu == "Executive Dashboard":
        st.title("ðŸ“Œ Executive Overview Dashboard")

        available_jcs = (
            df_jc["JC_Month"].unique().tolist()
            if not df_jc.empty and "JC_Month" in df_jc.columns
            else ["M1"]
        )
        selected_jc = st.selectbox("Select Active JC Month", options=available_jcs)

        df_jc_filtered = df_jc[df_jc["JC_Month"] == selected_jc].copy()

        tot_target_pcs = (
            pd.to_numeric(df_jc_filtered["Target_Pcs"], errors="coerce")
            .fillna(0)
            .sum()
        )
        tot_achv_pcs = (
            pd.to_numeric(df_jc_filtered["Achv_Pcs"], errors="coerce")
            .fillna(0)
            .sum()
        )
        tot_achv_val = (
            pd.to_numeric(df_jc_filtered["Achv_Value"], errors="coerce")
            .fillna(0)
            .sum()
        )
        tot_invested = (
            pd.to_numeric(df_inv["Total_Invested"], errors="coerce")
            .fillna(0)
            .sum()
            if not df_inv.empty
            else 0.0
        )

        overall_pct = (
            (tot_achv_pcs / tot_target_pcs) * 100 if tot_target_pcs > 0 else 0.0
        )

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Target (Pcs)", f"{tot_target_pcs:,.0f}")
        c2.metric(
            "Achieved (Pcs)", f"{tot_achv_pcs:,.0f}", delta=f"{overall_pct:.1f}%"
        )
        c3.metric("Sales Achieved (Value)", format_inr(tot_achv_val))
        c4.metric("Total Capital Invested", format_inr(tot_invested))

        st.divider()

        col_left, col_right = st.columns([6, 4])
        with col_left:
            st.subheader(f"JC Target vs Achievement ({selected_jc})")
            fig_perf = px.bar(
                df_jc_filtered,
                x="Division",
                y=["Target_Pcs", "Achv_Pcs"],
                barmode="group",
                labels={"value": "Pieces", "variable": "Metric"},
                color_discrete_sequence=["#6c757d", "#0d6efd"],
            )
            st.plotly_chart(fig_perf, use_container_width=True)

        with col_right:
            st.subheader("Capital Investment Share")
            if not df_inv.empty:
                fig_inv = px.pie(
                    df_inv,
                    names="Division",
                    values="Total_Invested",
                    hole=0.4,
                    color_discrete_sequence=px.colors.qualitative.Pastel,
                )
                st.plotly_chart(fig_inv, use_container_width=True)


    # Place this at the very bottom of the Dashboard menu block
    if is_admin_or_mgr:
         st.divider()
         st.subheader("💼 Investment Breakdown")
         st.dataframe(df_inv, use_container_width=True, hide_index=True)

    # =========================================================
    # MENU VIEW 3: OUTSTANDING DEBTORS (ACCESSIBLE TO ALL)
    # =========================================================
    elif menu == "Outstanding Debtors":
        st.title("ðŸ’¸ Outstanding Debtors Portal")

        if not df_out.empty and "Party Name" in df_out.columns:
            unique_parties = sorted(
                [p for p in df_out["Party Name"].unique() if str(p).strip() != ""]
            )
            party_list = ["All Parties"] + unique_parties
            selected_party = st.selectbox("Search and Select Party Name:", party_list)

            if selected_party != "All Parties":
                filtered_df = df_out[df_out["Party Name"] == selected_party].copy()
            else:
                filtered_df = df_out.copy()

            if "Invoice Date" in filtered_df.columns:
                filtered_df = filtered_df.sort_values(
                    by="Invoice Date", ascending=True
                )
                filtered_df["Invoice Date"] = filtered_df["Invoice Date"].dt.strftime(
                    "%d-%m-%Y"
                )

            total_pending = filtered_df["Pending Amount"].sum()
            bill_count = len(filtered_df)

            col1, col2 = st.columns(2)
            col1.metric("Total Outstanding", format_inr(total_pending))
            col2.metric("Total Pending Bills", bill_count)

            st.divider()

            # Format Pending Amount column for display
            display_df = filtered_df.copy()
            display_df["Pending Amount"] = display_df["Pending Amount"].apply(
                format_inr
            )
            st.dataframe(display_df, use_container_width=True, hide_index=True)
        else:
            st.warning("No data found in OUTSTANDING sheet.")

    # =========================================================
    # MENU VIEW 4: GRANULAR STOCK DETAILS (ACCESSIBLE TO ALL)
    # =========================================================
    elif menu == "Stock Details":
        st.title("ðŸ“¦ Granular Inventory & Stock Details")

        if not df_stock.empty:
            # Multi-column Search/Filter
            col_search, col_div = st.columns([2, 1])

            with col_search:
                search_query = st.text_input(
                    "ðŸ” Search Stock by Item Name / Code:"
                )

            with col_div:
                div_options = (
                    ["All Divisions"] + sorted(df_stock["Division"].unique().tolist())
                    if "Division" in df_stock.columns
                    else ["All Divisions"]
                )
                selected_stock_div = st.selectbox(
                    "Filter Division:", div_options
                )

            filtered_stock = df_stock.copy()

            if selected_stock_div != "All Divisions":
                filtered_stock = filtered_stock[
                    filtered_stock["Division"] == selected_stock_div
                ]

            if search_query:
                # Fuzzy string match across string columns
                mask = filtered_stock.astype(str).apply(
                    lambda row: row.str.contains(search_query, case=False).any(),
                    axis=1,
                )
                filtered_stock = filtered_stock[mask]

            # High-level Summary Metrics for Stock
            st.caption(f"Displaying {len(filtered_stock)} stock items")
            st.dataframe(filtered_stock, use_container_width=True, hide_index=True)
        else:
            st.warning(
                "Granular stock sheet (`STOCK`) is empty or not created yet. Please add item rows to `STOCK` worksheet."
            )

    # =========================================================
    # MENU VIEW 5: INVESTMENT BREAKDOWN (ADMIN/MANAGER ONLY)
    # =========================================================
    elif menu == "Investment Breakdown":
        st.title("ðŸ’¼ Capital & Investment Master Breakdown")

        if not df_inv.empty:
            tot_out = (
                pd.to_numeric(df_inv["Outstanding"], errors="coerce")
                .fillna(0)
                .sum()
            )
            tot_chq = (
                pd.to_numeric(df_inv["Chqs_Hand"], errors="coerce").fillna(0).sum()
            )
            tot_stk = (
                pd.to_numeric(df_inv["Stock_Value"], errors="coerce")
                .fillna(0)
                .sum()
            )
            tot_inv = (
                pd.to_numeric(df_inv["Total_Invested"], errors="coerce")
                .fillna(0)
                .sum()
            )

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Stock Valuation", format_inr(tot_stk))
            c2.metric("Total Debtors Outstanding", format_inr(tot_out))
            c3.metric("Cheques / PDCs in Hand", format_inr(tot_chq))
            c4.metric("Total Capital Invested", format_inr(tot_inv))

            st.divider()

            display_inv = df_inv.copy()
            for col in ["Stock_Value", "Outstanding", "Chqs_Hand", "Total_Invested"]:
                if col in display_inv.columns:
                    display_inv[col] = display_inv[col].apply(format_inr)

            st.dataframe(display_inv, use_container_width=True, hide_index=True)
        else:
            st.warning("No data found in Investment_Master sheet.")