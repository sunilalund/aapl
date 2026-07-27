import hashlib
import hmac
import random
import smtplib
import time
from email.mime.text import MIMEText
import gspread
import pandas as pd
import streamlit as st

# -------------------------------------------------------------
# CONFIGURATION
# -------------------------------------------------------------
st.set_page_config(page_title="Business Portal", layout="wide")

SPREADSHEET_NAME = "AAPL-Jockey-Reporter"
WORKSHEET_OUTSTANDING = "OUTSTANDING"
USERS_WORKSHEET = "USERS"
JC_WORKSHEET = "JC_CONFIG"

SENDER_EMAIL = st.secrets.get("SENDER_EMAIL", "your-email@gmail.com")
SENDER_APP_PASSWORD = st.secrets.get(
    "SENDER_APP_PASSWORD", "xxxx xxxx xxxx xxxx"
)


# -------------------------------------------------------------
# AUTH & ROLE HELPERS
# -------------------------------------------------------------
def generate_auth_token(email, expiry_timestamp):
  secret = st.secrets.get("SENDER_APP_PASSWORD", "secret_salt_key")
  payload = f"{str(email).lower().strip()}:{expiry_timestamp}"
  return hmac.new(
      secret.encode(), payload.encode(), hashlib.sha256
  ).hexdigest()[:16]


def get_gspread_client():
  creds_dict = dict(st.secrets["gcp_service_account"])
  return gspread.service_account_from_dict(creds_dict)


def get_user_info(user_email):
  """Returns (is_authorized, role) for the user."""
  if not user_email or not str(user_email).strip():
    return False, "User"
  try:
    gc = get_gspread_client()
    sheet = gc.open(SPREADSHEET_NAME).worksheet(USERS_WORKSHEET)
    users_df = pd.DataFrame(sheet.get_all_records())

    users_df.columns = users_df.columns.str.strip().str.capitalize()
    if "Email" not in users_df.columns or "Status" not in users_df.columns:
      return False, "User"

    users_df["Email"] = users_df["Email"].astype(str).str.strip().str.lower()
    users_df["Status"] = users_df["Status"].astype(str).str.strip().str.capitalize()
    if "Role" not in users_df.columns:
      users_df["Role"] = "User"

    match = users_df[
        (users_df["Email"] == user_email.lower().strip())
        & (users_df["Status"] == "Active")
    ]
    if not match.empty:
      role = match.iloc[0]["Role"]
      return True, role
    return False, "User"
  except Exception as e:
    st.error(f"User verification error: {e}")
    return False, "User"


def send_otp_email(recipient_email, otp_code):
  subject = "Your Login OTP - Business Portal"
  body = (
      f"Your one-time authentication code is: {otp_code}\n\nValid for 5"
      " minutes."
  )
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
    st.error(f"SMTP Error: {e}")
    return False


# -------------------------------------------------------------
# MOCK DATA SUPPLIERS (Replace with real extractions tomorrow)
# -------------------------------------------------------------
def get_mock_sales_data():
  """Simulates Shoper sales report per division."""
  return {
      "SPM": {"achieved": 420000, "value": 480000, "stock_val": 1250000},
      "SPW": {"achieved": 380000, "value": 410000, "stock_val": 980000},
      "THM": {"achieved": 310000, "value": 350000, "stock_val": 750000},
      "KTH": {"achieved": 290000, "value": 310000, "stock_val": 620000},
  }


def get_mock_tally_outstanding():
  """Simulates Tally ERP 9 outstanding totals by bill prefix."""
  return {
      "SPM": 850000,  # SCRS
      "SPW": 620000,  # SWCRS
      "THM": 490000,  # THCRS
      "KTH": 410000,  # KTHCRS
  }


# -------------------------------------------------------------
# SESSION STATE & AUTO-LOGIN
# -------------------------------------------------------------
if "authenticated" not in st.session_state:
  st.session_state.authenticated = False
if "user_role" not in st.session_state:
  st.session_state.user_role = "User"
if "otp_sent" not in st.session_state:
  st.session_state.otp_sent = False
if "generated_otp" not in st.session_state:
  st.session_state.generated_otp = None
if "target_email" not in st.session_state:
  st.session_state.target_email = ""

# Check URL Auto-Login
if not st.session_state.authenticated:
  url_email = st.query_params.get("user")
  url_token = st.query_params.get("token")
  url_exp = st.query_params.get("exp")

  if url_email and url_token and url_exp:
    try:
      exp_ts = int(url_exp)
      if int(time.time()) < exp_ts:
        if url_token == generate_auth_token(url_email, exp_ts):
          is_auth, role = get_user_info(url_email)
          if is_auth:
            st.session_state.authenticated = True
            st.session_state.target_email = url_email
            st.session_state.user_role = role
          else:
            st.query_params.clear()
        else:
          st.query_params.clear()
      else:
        st.query_params.clear()
    except ValueError:
      st.query_params.clear()

# -------------------------------------------------------------
# LOGIN GATE
# -------------------------------------------------------------
if not st.session_state.authenticated:
  st.title("🔐 Portal Authentication")

  if not st.session_state.otp_sent:
    email_input = st.text_input("Enter Authorized Email Address:")
    if st.button("Send OTP"):
      if email_input:
        is_auth, role = get_user_info(email_input)
        if is_auth:
          otp = str(random.randint(100000, 999999))
          if send_otp_email(email_input, otp):
            st.session_state.generated_otp = otp
            st.session_state.target_email = email_input
            st.session_state.user_role = role
            st.session_state.otp_sent = True
            st.success("OTP sent!")
            st.rerun()
        else:
          st.error("Email not authorized.")
  else:
    st.info(f"OTP sent to **{st.session_state.target_email}**")
    entered_otp = st.text_input("Enter 6-digit OTP:", max_chars=6)

    col1, col2 = st.columns([1, 4])
    with col1:
      if st.button("Verify OTP"):
        if entered_otp == st.session_state.generated_otp:
          st.session_state.authenticated = True
          exp_ts = int(time.time()) + 86400  # 24 hrs
          st.query_params["user"] = st.session_state.target_email
          st.query_params["token"] = generate_auth_token(
              st.session_state.target_email, exp_ts
          )
          st.query_params["exp"] = str(exp_ts)
          st.rerun()
        else:
          st.error("Invalid OTP.")
    with col2:
      if st.button("Cancel"):
        st.session_state.otp_sent = False
        st.rerun()

# -------------------------------------------------------------
# MAIN APP (AUTHENTICATED)
# -------------------------------------------------------------
else:
  # Navigation & Logout Header
  top1, top2, top3 = st.columns([4, 2, 1])
  with top1:
    st.caption(
        f"Logged in as: **{st.session_state.target_email}** | Role:"
        f" **{st.session_state.user_role}**"
    )
  with top3:
    if st.button("Logout"):
      st.query_params.clear()
      st.session_state.authenticated = False
      st.rerun()

  # Menu Navigation
  menu_choice = st.radio(
      "Navigation",
      ["Dashboard", "Outstanding", "Stock"],
      horizontal=True,
      label_visibility="collapsed",
  )
  st.divider()

  # =========================================================
  # MENU ITEM 1: DASHBOARD
  # =========================================================
  if menu_choice == "Dashboard":
    st.title("📊 Business Dashboard")

    # Top Controls: JC Month Selection
    jc_options = ["M4", "M3", "M2", "M1"]
    selected_jc = st.selectbox("Select Journey Cycle Month:", jc_options)

    # 2-Column Split Layout
    left_col, right_col = st.columns([7, 3])

    # --- LEFT COLUMN: Active JC & Investment Tables ---
    with left_col:
      st.subheader(f"Sales Performance ({selected_jc})")

      # Mock targets based on JC selection
      shoper_data = get_mock_sales_data()
      divisions = ["SPM", "SPW", "THM", "KTH"]

      sales_rows = []
      for div in divisions:
        target = 500000
        achieved = shoper_data[div]["achieved"]
        pct = (achieved / target) * 100 if target > 0 else 0
        balance = target - achieved
        val = shoper_data[div]["value"]

        row = {
            "Division": div,
            "Target": f"₹ {target:,.0f}",
            "Achieved": f"₹ {achieved:,.0f}",
            "Achieved %": f"{pct:.1f}%",
            "Balance": f"₹ {balance:,.0f}",
        }

        # Value column visible ONLY to Admin & Manager
        if st.session_state.user_role in ["Admin", "Manager"]:
          row["Value"] = f"₹ {val:,.0f}"

        sales_rows.append(row)

      st.dataframe(pd.DataFrame(sales_rows), use_container_width=True, hide_index=True)

      # Additional Investment Table (Manager/Admin Only)
      if st.session_state.user_role in ["Admin", "Manager"]:
        st.divider()
        st.subheader("💼 Investment Breakdown")

        tally_data = get_mock_tally_outstanding()
        invest_rows = []

        for div in divisions:
          stk_val = shoper_data[div]["stock_val"]
          out_val = tally_data[div]

          # Manual Input widget for Balance Cheques on Hand
          chq_input = st.number_input(
              f"Balance Cheques on Hand ({div}):",
              min_value=0,
              value=50000,
              step=5000,
              key=f"chq_{div}",
          )

          invest_rows.append({
              "Division": div,
              "Stock Value": f"₹ {stk_val:,.0f}",
              "Outstanding": f"₹ {out_val:,.0f}",
              "Balance Cheques on Hand": f"₹ {chq_input:,.0f}",
          })

        st.dataframe(
            pd.DataFrame(invest_rows), use_container_width=True, hide_index=True
        )

    # --- RIGHT COLUMN: Historical JCs Summary ---
    with right_col:
      st.subheader("🕒 Previous JCs Summary")

      # Simulated historical totals for past JCs
      past_jc_data = [
          {
              "Month": "M1",
              "Target": "₹ 18,00,000",
              "Achieved": "₹ 17,50,000",
              "% Ach": "97.2%",
          },
          {
              "Month": "M2",
              "Target": "₹ 19,00,000",
              "Achieved": "₹ 18,20,000",
              "% Ach": "95.7%",
          },
          {
              "Month": "M3",
              "Target": "₹ 20,00,000",
              "Achieved": "₹ 19,80,000",
              "% Ach": "99.0%",
          },
      ]

      # Filter out current and future months
      current_idx = jc_options.index(selected_jc)
      visible_past = past_jc_data[len(jc_options) - 1 - current_idx :]

      if visible_past:
        st.dataframe(
            pd.DataFrame(visible_past), use_container_width=True, hide_index=True
        )
      else:
        st.info("No past JCs prior to M1.")

  # =========================================================
  # MENU ITEM 2: OUTSTANDING
  # =========================================================
  elif menu_choice == "Outstanding":
    st.title("💳 Outstanding Debtors")
    st.info("Your existing Outstanding Debtors table renders here.")

  # =========================================================
  # MENU ITEM 3: STOCK
  # =========================================================
  elif menu_choice == "Stock":
    st.title("📦 Stock Report")
    st.info("Stock reports from Shoper 9 will be connected here.")