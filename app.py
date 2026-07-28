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
    page_icon="\U0001F4CA",
    layout="wide",
    initial_sidebar_state="expanded",
)

SPREADSHEET_NAME = "AAPL-Jockey-Reporter"
WORKSHEET_OUTSTANDING = "OUTSTANDING"
WORKSHEET_USERS = "USERS"
WORKSHEET_JC = "JC_Master"
WORKSHEET_INVESTMENT = "Investment_Master"
WORKSHEET_STOCK = "STOCK"  # Sheet containing full granular stock item records

# Gmail SMTP Credentials
SENDER_EMAIL = st.secrets.get("SENDER_EMAIL", "your-email@gmail.com")
SENDER_APP_PASSWORD = st.secrets.get("SENDER_APP_PASSWORD", "xxxx xxxx xxxx xxxx")


# -------------------------------------------------------------
# 2. HELPER FUNCTIONS & AUTHENTICATION
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
    """Formats numeric values into Indian Rupee format using universal unicode escape."""
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
        res = f"\u20b9{formatted_int}.{d[0]}"
        return f"-{res}" if is_neg else res
    except Exception:
        return f"\u20b9{val}"


def sort_jc_months(months_list):
    """Sorts JC Month strings chronologically from M1 to M13."""
    def parse_m(m):
        m_str = str(m).strip().upper()
        if m_str.startswith("M") and m_str[1:].isdigit():
            return int(m_str[1:])
        return 999

    return sorted([m for m in set(months_list) if str(m).strip() != ""], key=parse_m)


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
            df_out["Pending Amount"] = pd.to_numeric(
                df_out["Pending Amount"], errors="coerce"
            ).fillna(0)
    except Exception:
        df_out = pd.DataFrame()

    # 2. JC Master Performance Data
    try:
        ws_jc = sh.worksheet(WORKSHEET_JC).get_all_records()
        df_jc = pd.DataFrame(ws_jc)
        
        if not df_jc.empty:
            df_jc.columns = df_jc.columns.astype(str).str.strip()
            col_map = {
                "Achv Value": "Achv_Value",
                "Achv_value": "Achv_Value",
                "Achieved Value": "Achv_Value",
                "Achieved_Value": "Achv_Value",
                "Sales Value": "Achv_Value",
            }
            df_jc.rename(columns=col_map, inplace=True)
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
    st.title("\U0001F511 AAPL Sales & Operations Portal")
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
                        st.error("Access Denied: Email not authorized or inactive.")
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
                    exp_ts = int(time.time()) + 43200  # 12 Hours persistent token
                    token = generate_auth_token(st.session_state.target_email, exp_ts)

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
    role = str(st.session_state.get("user_role", "Sales")).strip().title()
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

    if is_admin_or_mgr:
        menu_options = [
            "Executive Dashboard",
            "Outstanding Debtors",
            "Stock Details",
            "Investment Breakdown",
        ]
    else:
        menu_options = [
            "Achievement & Targets (Units)",
            "Outstanding Debtors",
            "Stock Details",
        ]

    menu = st.sidebar.radio("Navigation Menu", options=menu_options)

    st.sidebar.divider()
    if st.sidebar.button("\U0001F504 Refresh Data"):
        st.cache_data.clear()
        st.rerun()

    # =========================================================
    # MENU VIEW 1 & 2: UNIFIED DASHBOARD
    # =========================================================
    if menu in ["Executive Dashboard", "Achievement & Targets (Units)"]:
        st.title("\U0001F4CA Sales Performance & Dashboard")

        all_jcs = (
            sort_jc_months(df_jc["JC_Month"].tolist())
            if not df_jc.empty and "JC_Month" in df_jc.columns
            else ["M1"]
        )
        selected_jc = st.selectbox("Select JC Month:", options=all_jcs)

        current_m_num = (
            int(selected_jc.replace("M", ""))
            if selected_jc.startswith("M") and selected_jc[1:].isdigit()
            else 1
        )

        df_jc_curr = df_jc[df_jc["JC_Month"] == selected_jc].copy()
        for col in ["Target_Pcs", "Achv_Pcs", "Balance_Pcs"]:
            if col in df_jc_curr.columns:
                df_jc_curr[col] = pd.to_numeric(df_jc_curr[col], errors="coerce").fillna(0)

        if is_admin_or_mgr and "Achv_Value" in df_jc_curr.columns:
            df_jc_curr["Achv_Value"] = pd.to_numeric(
                df_jc_curr["Achv_Value"], errors="coerce"
            ).fillna(0)

        tot_target_pcs = df_jc_curr["Target_Pcs"].sum()
        tot_achv_pcs = df_jc_curr["Achv_Pcs"].sum()
        tot_bal_pcs = df_jc_curr["Balance_Pcs"].sum()
        overall_pct = (tot_achv_pcs / tot_target_pcs * 100) if tot_target_pcs > 0 else 0.0

        if is_admin_or_mgr:
            tot_achv_val = (
                df_jc_curr["Achv_Value"].sum()
                if "Achv_Value" in df_jc_curr.columns
                else 0
            )
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("Target (Pcs)", f"{tot_target_pcs:,.0f}")
            c2.metric("Achieved (Pcs)", f"{tot_achv_pcs:,.0f}")
            c3.metric("Achievement %", f"{overall_pct:.1f}%")
            c4.metric("Balance Target (Pcs)", f"{tot_bal_pcs:,.0f}")
            c5.metric("Sales Achieved (Value)", format_inr(tot_achv_val))
        else:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Target (Pcs)", f"{tot_target_pcs:,.0f}")
            c2.metric("Achieved (Pcs)", f"{tot_achv_pcs:,.0f}")
            c3.metric("Achievement %", f"{overall_pct:.1f}%")
            c4.metric("Balance Target (Pcs)", f"{tot_bal_pcs:,.0f}")

        st.divider()

        # TABLES AT TOP
        st.subheader(f"\U0001F4CB Sales Performance Tables ({selected_jc})")

        col_left_tbl, col_right_tbl = st.columns([6, 4])

        with col_left_tbl:
            st.markdown(f"**Current Month ({selected_jc}) Division Breakdown**")

            display_cols = ["Division", "Target_Pcs", "Achv_Pcs", "Balance_Pcs"]
            if is_admin_or_mgr and "Achv_Value" in df_jc_curr.columns:
                display_cols.append("Achv_Value")

            df_curr_table = df_jc_curr[
                [c for c in display_cols if c in df_jc_curr.columns]
            ].copy()

            df_curr_table["Achv_%"] = df_curr_table.apply(
                lambda r: f"{(r['Achv_Pcs']/r['Target_Pcs']*100):.1f}%"
                if r["Target_Pcs"] > 0
                else "0.0%",
                axis=1,
            )

            if is_admin_or_mgr and "Achv_Value" in df_curr_table.columns:
                df_curr_table["Achv_Value"] = df_curr_table["Achv_Value"].apply(
                    format_inr
                )

            st.dataframe(df_curr_table, use_container_width=True, hide_index=True)

        with col_right_tbl:
            st.markdown("**Prior Months Performance Summary (M1 to Prior)**")

            prior_jcs = [
                m
                for m in all_jcs
                if (
                    int(m.replace("M", ""))
                    if m.startswith("M") and m[1:].isdigit()
                    else 999
                )
                < current_m_num
            ]

            if prior_jcs:
                hist_records = []
                for m_code in prior_jcs:
                    df_m = df_jc[df_jc["JC_Month"] == m_code]
                    m_tgt_pcs = pd.to_numeric(
                        df_m["Target_Pcs"], errors="coerce"
                    ).sum()
                    m_achv_pcs = pd.to_numeric(
                        df_m["Achv_Pcs"], errors="coerce"
                    ).sum()
                    m_pct = (m_achv_pcs / m_tgt_pcs * 100) if m_tgt_pcs > 0 else 0.0

                    row_dict = {
                        "JC Month": m_code,
                        "Target (Pcs)": f"{m_tgt_pcs:,.0f}",
                        "Achv (Pcs)": f"{m_achv_pcs:,.0f}",
                        "Achv %": f"{m_pct:.1f}%",
                    }

                    if is_admin_or_mgr and "Achv_Value" in df_m.columns:
                        m_val = pd.to_numeric(
                            df_m["Achv_Value"], errors="coerce"
                        ).sum()
                        row_dict["Achv (Value)"] = format_inr(m_val)

                    hist_records.append(row_dict)

                df_hist = pd.DataFrame(hist_records)
                st.dataframe(df_hist, use_container_width=True, hide_index=True)
            else:
                st.info(
                    "Currently viewing M1. Prior month history will appear here from M2 onwards."
                )

        st.divider()

        # GRAPH DOWN
        st.subheader(f"\U0001F4C8 Performance Chart ({selected_jc})")

        fig_perf = px.bar(
            df_jc_curr,
            x="Division",
            y=["Target_Pcs", "Achv_Pcs", "Balance_Pcs"],
            barmode="group",
            labels={"value": "Units (Pcs)", "variable": "Status"},
            color_discrete_sequence=["#0d6efd", "#198754", "#dc3545"],
        )
        st.plotly_chart(fig_perf, use_container_width=True)

        if is_admin_or_mgr:
            st.divider()
            st.subheader("\U0001F4BC Capital & Investment Master Breakdown")

            if not df_inv.empty:
                inv_cols = [
                    "Division",
                    "Stock_Value",
                    "Outstanding",
                    "Chqs_Hand",
                    "Total_Invested",
                ]
                existing_cols = [c for c in inv_cols if c in df_inv.columns]
                display_inv = df_inv[existing_cols].copy()

                column_renames = {
                    "Stock_Value": "Stock Value",
                    "Outstanding": "O/S Value",
                    "Chqs_Hand": "Chqs on Hand Value",
                    "Total_Invested": "Total Invested",
                }
                display_inv = display_inv.rename(columns=column_renames)

                for col in [
                    "Stock Value",
                    "O/S Value",
                    "Chqs on Hand Value",
                    "Total Invested",
                ]:
                    if col in display_inv.columns:
                        display_inv[col] = pd.to_numeric(
                            display_inv[col], errors="coerce"
                        ).fillna(0)
                        display_inv[col] = display_inv[col].apply(format_inr)

                st.dataframe(display_inv, use_container_width=True, hide_index=True)
            else:
                st.warning("No data found in Investment_Master sheet.")

    # =========================================================
    # MENU VIEW 3: OUTSTANDING DEBTORS
    # =========================================================
    elif menu == "Outstanding Debtors":
        st.title("\U0001F4B8 Outstanding Debtors Portal")

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

            # Calculate Age-wise Buckets based on Invoice Date
            if "Invoice Date" in filtered_df.columns:
                filtered_df = filtered_df.sort_values(
                    by="Invoice Date", ascending=True
                )
                today = pd.Timestamp.now()
                days_diff = (today - filtered_df["Invoice Date"]).dt.days.fillna(0)

                def assign_bucket(d):
                    if d > 90:
                        return "90+ Days"
                    elif d > 60:
                        return "61-90 Days"
                    elif d > 30:
                        return "31-60 Days"
                    else:
                        return "0-30 Days"

                filtered_df["_Age_Bucket"] = days_diff.apply(assign_bucket)
            else:
                filtered_df["_Age_Bucket"] = "0-30 Days"

            total_pending = filtered_df["Pending Amount"].sum()
            bill_count = len(filtered_df)

            # Top Section: Metrics (Left) & Agewise Chart (Right)
            top_m_col, top_c_col = st.columns([1, 2])

            with top_m_col:
                st.metric("Total Outstanding", format_inr(total_pending))
                st.metric("Total Pending Bills", bill_count)

            with top_c_col:
                bucket_order = ["0-30 Days", "31-60 Days", "61-90 Days", "90+ Days"]
                age_summary = (
                    filtered_df.groupby("_Age_Bucket")["Pending Amount"]
                    .sum()
                    .reindex(bucket_order, fill_value=0)
                    .reset_index()
                )
                age_summary["Full_Text"] = age_summary["Pending Amount"].apply(lambda x: f"{x:,.0f}" if x > 0 else "0")
                fig_age = px.bar(
                    age_summary,
                    x="_Age_Bucket",
                    y="Pending Amount",
                    text_auto="Full_Text",
                    labels={"_Age_Bucket": "Age Bucket", "Pending Amount": "Amount"},
                    color="_Age_Bucket",
                    color_discrete_map={
                        "0-30 Days": "#198754",   # Green
                        "31-60 Days": "#0dcaf0",  # Cyan
                        "61-90 Days": "#ffc107",  # Warning Yellow
                        "90+ Days": "#dc3545",    # Alert Red
                    },
                )
                fig_age.update_traces(textposition="auto")
                fig_age.update_layout(
                    height=180,
                    margin=dict(l=10, r=10, t=10, b=10),
                    showlegend=False,
                    xaxis_title=None,
                    yaxis_title=None,
                )
                st.plotly_chart(fig_age, use_container_width=True)

            st.divider()

            # Format table for display
            display_df = filtered_df.copy()
            if "Invoice Date" in display_df.columns:
                display_df["Invoice Date"] = display_df["Invoice Date"].dt.strftime(
                    "%d-%m-%Y"
                )
            display_df["Pending Amount"] = display_df["Pending Amount"].apply(
                format_inr
            )
            display_df = display_df.drop(columns=["_Age_Bucket"], errors="ignore")

            st.dataframe(display_df, use_container_width=True, hide_index=True)
        else:
            st.warning("No data found in OUTSTANDING sheet.")
    # =========================================================
    # MENU VIEW 4: GRANULAR STOCK DETAILS
    # =========================================================
    elif menu == "Stock Details":
        st.title("\U0001F4E6 Granular Inventory & Stock Details")

        if not df_stock.empty and len(df_stock.columns) >= 2:
            df_stk = df_stock.copy()

            df_stk.columns = [str(col).strip() for col in df_stk.columns]

            # Extract Division Tag from Column 2 (Index 1)
            col_2_name = df_stk.columns[1]
            df_stk["_Division_Tag"] = (
                df_stk[col_2_name].astype(str).str.strip().str[-3:]
            )

            # Rename Column 10 (Index 9) to 'MRP'
            if len(df_stk.columns) >= 10:
                col_10_name = df_stk.columns[9]
                df_stk = df_stk.rename(columns={col_10_name: "MRP"})

            col_search, col_div = st.columns([2, 1])

            with col_div:
                div_list = sorted(
                    [d for d in df_stk["_Division_Tag"].unique() if str(d).strip() != ""]
                )
                div_options = ["All Divisions"] + div_list
                selected_stock_div = st.selectbox("Filter Division:", div_options)

            with col_search:
                search_query = st.text_input("\U0001F50D Search Stock by Item Name / Code:")

            filtered_stock = df_stk.copy()
            if selected_stock_div != "All Divisions":
                filtered_stock = filtered_stock[
                    filtered_stock["_Division_Tag"] == selected_stock_div
                ]

            if search_query:
                mask = filtered_stock.astype(str).apply(
                    lambda row: row.str.contains(search_query, case=False).any(),
                    axis=1,
                )
                filtered_stock = filtered_stock[mask]

            # Exclude Columns 1, 2, 3, 11, 13, 14 (indices 0, 1, 2, 10, 12, 13)
            exclude_indices = [0, 1, 2, 10, 12, 13]
            cols_to_drop = [
                df_stock.columns[i]
                for i in exclude_indices
                if i < len(df_stock.columns)
            ]

            display_stock = filtered_stock.drop(
                columns=cols_to_drop + ["_Division_Tag"], errors="ignore"
            )
            st.caption(f"Displaying {len(display_stock)} stock items")
            st.dataframe(display_stock, use_container_width=True, hide_index=True)
        else:
            st.warning(
                "Granular stock sheet (`STOCK`) is empty or does not contain enough columns."
            )

    # =========================================================
    # MENU VIEW 5: INVESTMENT BREAKDOWN
    # =========================================================
    elif menu == "Investment Breakdown":
        st.title("\U0001F4BC Capital & Investment Master Breakdown")

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
