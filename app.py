import os, json, random, logging, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import streamlit as st
from streamlit.runtime.scriptrunner import RerunException
from openai import OpenAI, OpenAIError, RateLimitError
from dotenv import load_dotenv
from supabase import create_client
from postgrest.exceptions import APIError

# ─────────────────── LOGGING SETUP ─────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bondigo_auth.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ─────────────────── ENVIRONMENT & CLIENTS ─────────────────────────
load_dotenv()
SB  = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
SRS = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
OA  = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
if "user_jwt" in st.session_state:
    SB.postgrest.headers["Authorization"] = f"Bearer {st.session_state.user_jwt}"

# ────────── EMAIL CONFIRMATION & ERROR HANDLING ─────────────
params = st.query_params
confirmation_type = params.get("type", [""])[0] if "type" in params else ""
error_code = params.get("error_code", [""])[0] if "error_code" in params else ""
error_description = params.get("error_description", [""])[0] if "error_description" in params else ""

# Handle email confirmation
if confirmation_type == "signup":
    access_token = params.get("access_token", [""])[0] if "access_token" in params else ""
    refresh_token = params.get("refresh_token", [""])[0] if "refresh_token" in params else ""
    
    if error_code:
        logger.error(f"Email confirmation error: {error_code} - {error_description}")
        if error_code == "signup_disabled":
            st.error("🚫 Account signup is currently disabled.")
        elif "expired" in error_description.lower():
            st.error("⏰ Your confirmation link has expired. Please request a new one below.")
            st.session_state.show_resend = True
        else:
            st.error(f"❌ Email confirmation failed: {error_description}")
            st.session_state.show_resend = True
    elif access_token and refresh_token:
        try:
            session_response = SB.auth.set_session(access_token, refresh_token)
            
            if session_response and session_response.user:
                user_meta = session_response.user
                logger.info(f"Email confirmed for user: {user_meta.email}")
                
                existing_user = get_user_row(user_meta.id)
                if not existing_user:
                    pending = get_pending_signup(user_meta.email)
                    if pending:
                        username = pending["username"]
                        logger.info(f"Creating user row for confirmed user: {user_meta.email} with username: {username}")
                        
                        create_user_row(user_meta.id, username)
                        SRS.table("invitees").update({"claimed": True}).eq("email", user_meta.email).execute()
                        cleanup_pending_signup(user_meta.email)
                        
                        st.success("✅ Your email has been confirmed! You can now sign in below.")
                    else:
                        username = user_meta.user_metadata.get("username", f"user_{user_meta.id[:8]}")
                        create_user_row(user_meta.id, username)
                        SRS.table("invitees").update({"claimed": True}).eq("email", user_meta.email).execute()
                        st.success("✅ Your email has been confirmed! You can now sign in below.")
                else:
                    st.info("✅ Your email is already confirmed. You can sign in below.")
                
                if st.button("Continue to Sign In"):
                    st.query_params.clear()
                    st.rerun()
                
            else:
                logger.error("Failed to get user info during email confirmation")
                st.error("❌ Failed to confirm email. Please try again.")
        except Exception as e:
            logger.error(f"Email confirmation error: {str(e)}")
            st.error(f"❌ Email confirmation error: {str(e)}")
    else:
        st.error("❌ Invalid confirmation link.")

# Also check for fragment parameters (alternative method)
elif st.query_params.get("access_token"):
    access_token = params.get("access_token", [""])[0] if "access_token" in params else ""
    refresh_token = params.get("refresh_token", [""])[0] if "refresh_token" in params else ""
    
    if access_token and refresh_token:
        try:
            session_response = SB.auth.set_session(access_token, refresh_token)
            if session_response and session_response.user:
                user_meta = session_response.user
                existing_user = get_user_row(user_meta.id)
                if not existing_user:
                    pending = get_pending_signup(user_meta.email)
                    if pending:
                        username = pending["username"]
                        create_user_row(user_meta.id, username)
                        SRS.table("invitees").update({"claimed": True}).eq("email", user_meta.email).execute()
                        cleanup_pending_signup(user_meta.email)
                st.success("✅ Your email has been confirmed! You can now sign in below.")
                if st.button("Continue to Sign In"):
                    st.query_params.clear()
                    st.rerun()
        except Exception as e:
            st.error(f"❌ Email confirmation error: {str(e)}")

# ─────────────────── STREAMLIT CONFIG ──────────────────────────────
st.set_page_config(
    page_title="BONDIGO",
    page_icon="🩷",
    layout="centered",
    menu_items={"Get Help": None, "Report a bug": None, "About": None},
)
st.markdown("""<style>
  #MainMenu, header, footer, [data-testid="stSidebar"] {
    visibility: hidden; height: 0;
  }
</style>""", unsafe_allow_html=True)

# ─────────────────── CONSTANTS & DATA ─────────────────────────────
MAX_TOKENS    = 10_000
DAILY_AIRDROP = 150
COST          = {"Common":50,"Rare":200,"Legendary":700}
CONFIRMATION_EXPIRY_HOURS = 24

PLACEHOLDER = "assets/placeholder.png"
LOGO        = "assets/bondigo_banner.png"
TAGLINE     = "Talk the Lingo · Master the Bond · Dominate the Game"
CLR         = {"Common":"#bbb","Rare":"#57C7FF","Legendary":"#FFAA33"}

# ─── load your companions.json from the same folder ───────────────
BASE = Path(__file__).parent
COMPANIONS = json.load(open(BASE / "companions.json", encoding="utf-8-sig"))
CID2COMP   = {c["id"]: c for c in COMPANIONS}

# ─────────────────── HELPERS ───────────────────────────────────────
def apply_daily_airdrop(user: dict) -> dict:
    last = user["last_airdrop"] or user["created_at"]
    last_dt = datetime.fromisoformat(last.replace("Z","+00:00"))
    if datetime.now(timezone.utc) - last_dt >= timedelta(hours=24):
        return SRS.table("users").update({
            "tokens": user["tokens"] + DAILY_AIRDROP,
            "last_airdrop": datetime.now(timezone.utc).isoformat()
        }).eq("auth_uid", user["auth_uid"]).execute().data[0]
    return user

def create_user_row(auth_uid: str, username: str) -> dict:
    try:
        result = SRS.table("users").insert({
            "id": auth_uid,
            "auth_uid": auth_uid,
            "username": username,
            "tokens": 1000,
            "last_airdrop": None
        }).execute()
        logger.info(f"Created user row for: {auth_uid} with username: {username}")
        return result.data[0]
    except Exception as e:
        logger.error(f"Failed to create user row: {str(e)}")
        raise

def get_user_row(auth_uid: str) -> dict | None:
    try:
        rows = SRS.table("users").select("*")\
                  .eq("auth_uid", auth_uid).execute().data
        return rows[0] if rows else None
    except Exception as e:
        logger.error(f"Failed to get user row: {str(e)}")
        return None

def create_pending_signup(email: str, username: str, auth_uid: str = None) -> bool:
    try:
        cleanup_pending_signup(email)
        
        SRS.table("pending_signups").insert({
            "email": email,
            "username": username,
            "auth_uid": auth_uid,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=CONFIRMATION_EXPIRY_HOURS)).isoformat()
        }).execute()
        logger.info(f"Created pending signup for: {email} with username: {username}")
        return True
    except Exception as e:
        logger.error(f"Failed to create pending signup: {str(e)}")
        return False

def get_pending_signup(email: str) -> dict | None:
    try:
        rows = SRS.table("pending_signups").select("*")\
                  .eq("email", email)\
                  .gte("expires_at", datetime.now(timezone.utc).isoformat())\
                  .order("created_at", desc=True)\
                  .limit(1)\
                  .execute().data
        return rows[0] if rows else None
    except Exception as e:
        logger.error(f"Failed to get pending signup: {str(e)}")
        return None

def cleanup_pending_signup(email: str):
    try:
        SRS.table("pending_signups").delete().eq("email", email).execute()
        logger.info(f"Cleaned up pending signup for: {email}")
    except Exception as e:
        logger.error(f"Failed to cleanup pending signup: {str(e)}")

def cleanup_expired_signups():
    try:
        expired_count = SRS.table("pending_signups")\
                           .delete()\
                           .lt("expires_at", datetime.now(timezone.utc).isoformat())\
                           .execute()
        if expired_count.data:
            logger.info(f"Cleaned up {len(expired_count.data)} expired pending signups")
    except Exception as e:
        logger.error(f"Failed to cleanup expired signups: {str(e)}")

def cleanup_unconfirmed_user(email: str) -> bool:
    try:
        users_response = SRS.auth.admin.list_users()
        if users_response and users_response.users:
            for user in users_response.users:
                if (user.email == email and 
                    not getattr(user, 'email_confirmed_at', None) and 
                    not getattr(user, 'confirmed_at', None)):
                    
                    SRS.auth.admin.delete_user(user.id)
                    logger.info(f"Cleaned up unconfirmed user: {email}")
                    cleanup_pending_signup(email)
                    return True
        return False
    except Exception as e:
        logger.error(f"Failed to cleanup unconfirmed user: {str(e)}")
        return False

def check_user_status(email: str) -> dict:
    status = {
        "auth_user_exists": False,
        "auth_user_confirmed": False,
        "user_row_exists": False,
        "pending_signup_exists": False,
        "invite_claimed": False
    }
    
    try:
        users_response = SRS.auth.admin.list_users()
        if users_response and users_response.users:
            for user in users_response.users:
                if user.email == email:
                    status["auth_user_exists"] = True
                    status["auth_user_confirmed"] = bool(
                        getattr(user, 'email_confirmed_at', None) or 
                        getattr(user, 'confirmed_at', None)
                    )
                    user_row = get_user_row(user.id)
                    status["user_row_exists"] = bool(user_row)
                    break
        
        pending = get_pending_signup(email)
        status["pending_signup_exists"] = bool(pending)
        
        invite = SRS.table("invitees").select("claimed").eq("email", email).execute().data
        if invite:
            status["invite_claimed"] = invite[0]["claimed"]
        
        return status
    except Exception as e:
        logger.error(f"Failed to check user status: {str(e)}")
        return status

def resend_confirmation_email(email: str) -> bool:
    try:
        pending = get_pending_signup(email)
        if not pending:
            logger.warning(f"No pending signup found for resend: {email}")
            return False
        
        SB.auth.resend(type="signup", email=email)
        
        SRS.table("pending_signups")\
           .update({"expires_at": (datetime.now(timezone.utc) + timedelta(hours=CONFIRMATION_EXPIRY_HOURS)).isoformat()})\
           .eq("email", email)\
           .execute()
        
        logger.info(f"Resent confirmation email to: {email}")
        return True
    except Exception as e:
        logger.error(f"Failed to resend confirmation email: {str(e)}")
        return False

def collection_set(user_id: str) -> set[str]:
    rows = SRS.table("collection").select("companion_id")\
              .eq("user_id", user_id).execute().data
    return {r["companion_id"] for r in rows}

def buy(user: dict, comp: dict):
    price = COST[comp.get("rarity","Common")]
    if price > user["tokens"]:
        return False, "Not enough 💎"
    owned = SRS.table("collection").select("companion_id")\
               .eq("user_id", user["id"])\
               .eq("companion_id", comp["id"])\
               .execute().data
    if owned:
        return False, "Already owned"
    
    SRS.table("users").update({"tokens": user["tokens"] - price})\
       .eq("id", user["id"]).execute()
    SRS.table("collection").insert({
        "user_id": user["id"],
        "companion_id": comp["id"]
    }).execute()
    fresh = get_user_row(user["auth_uid"])
    return True, apply_daily_airdrop(fresh)

# ─────────────────── CALLBACKS ────────────────────────────────────
def bond_and_chat(cid: str, comp: dict):
    ok, new_user = buy(st.session_state.user, comp)
    if ok:
        st.session_state.user     = new_user
        st.session_state.page     = "Chat"
        st.session_state.chat_cid = cid
        st.session_state.flash    = f"Bonded with {comp['name']}!"
    else:
        st.warning(new_user)

def goto_chat(cid: str):
    st.session_state.page     = "Chat"
    st.session_state.chat_cid = cid

# ─────────────────── ADMIN PANEL (DEVELOPMENT ONLY) ─────────────
if st.sidebar.button("🔧 Admin Panel (Dev Only)"):
    st.session_state.show_admin = not st.session_state.get("show_admin", False)

if st.session_state.get("show_admin", False):
    st.sidebar.markdown("---")
    st.sidebar.subheader("🔧 Admin Tools")
    
    check_email = st.sidebar.text_input("Check user status:", key="admin_check_email")
    if st.sidebar.button("Check Status") and check_email:
        status = check_user_status(check_email)
        st.sidebar.json(status)
    
    cleanup_email = st.sidebar.text_input("Cleanup unconfirmed user:", key="admin_cleanup_email")
    if st.sidebar.button("⚠️ Cleanup User") and cleanup_email:
        if cleanup_unconfirmed_user(cleanup_email):
            st.sidebar.success("✅ User cleaned up")
        else:
            st.sidebar.error("❌ Cleanup failed")
    
    if st.sidebar.button("View Pending Signups"):
        pending = SRS.table("pending_signups").select("*").execute().data
        st.sidebar.json(pending)

cleanup_expired_signups()

if st.sidebar.button("🧪 Test Email"):
    try:
        SB.auth.reset_password_email("test@example.com")
        st.sidebar.success("Email system working!")
    except Exception as e:
        st.sidebar.error(f"Email system broken: {str(e)}")

# ─────────────────── EMAIL RESEND INTERFACE ────────────────────────
if st.session_state.get("show_resend", False):
    st.warning("⏰ Your confirmation link has expired or failed.")
    
    with st.expander("🔄 Resend Confirmation Email", expanded=True):
        resend_email = st.text_input("Enter your email to resend confirmation:", key="resend_email")
        col1, col2 = st.columns(2)
        
        with col1:
            if st.button("📧 Resend Email", key="resend_btn"):
                if resend_email:
                    if resend_confirmation_email(resend_email):
                        st.success("✅ Confirmation email resent! Check your inbox.")
                        st.session_state.show_resend = False
                        st.rerun()
                    else:
                        st.error("❌ Failed to resend email. Please check the email address or contact support.")
                else:
                    st.warning("Please enter your email address.")
        
        with col2:
            if st.button("❌ Cancel", key="cancel_resend"):
                st.session_state.show_resend = False
                st.rerun()

# ─────────────────── LOGIN / SIGN‑UP ───────────────────────────────
if "user" not in st.session_state:
    if Path(LOGO).is_file():
        st.image(LOGO, width=380)
        st.markdown(
            f"<p style='text-align:center;margin-top:-2px;font-size:1.05rem;"
            f"color:#FFC8D8'>{TAGLINE}</p>",
            unsafe_allow_html=True,
        )

    st.title("🔐 Sign in / Sign up to **BETA TEST**")

    email = st.text_input("Email", key="login_email")
    mode  = st.radio("Choose", ["Sign in","Sign up"], horizontal=True, key="login_mode")
    if mode == "Sign up":
        uname = st.text_input("Choose a username", max_chars=20, key="login_uname")
        
        if uname:
            existing_user = SRS.table("users").select("username").eq("username", uname).execute().data
            pending_user = SRS.table("pending_signups").select("username").eq("username", uname).execute().data
            
            if existing_user or pending_user:
                st.error(f"❌ Username '{uname}' is already taken. Please choose another.")
            else:
                st.success(f"✅ Username '{uname}' is available!")
    
    pwd = st.text_input("Password", type="password", key="login_pwd")

    if st.button("Go ➜", key="login_go"):
        if not email or not pwd or (mode=="Sign up" and not uname):
            st.warning("Fill all required fields.")
            st.stop()

        try:
            invite = SRS.table("invitees")\
                        .select("claimed")\
                        .eq("email", email)\
                        .execute().data
            if not invite:
                logger.warning(f"Unauthorized signup attempt: {email}")
                st.error("🚧 You're not on the invite list.")
                st.stop()
        except Exception as e:
            logger.error(f"Failed to check invite list: {str(e)}")
            st.error("❌ System error. Please try again.")
            st.stop()

        # ─── SIGN UP WITH WORKING EMAIL CONFIRMATION ─────────────────────────────
        if mode == "Sign up":
            if invite[0]["claimed"]:
                st.error("🚫 This email has already been used.")
                st.stop()

            user_status = check_user_status(email)
            logger.info(f"User status for {email}: {user_status}")
            
            if user_status["auth_user_exists"] and not user_status["auth_user_confirmed"]:
                st.warning("🔄 Found previous unconfirmed signup. Cleaning up...")
                if cleanup_unconfirmed_user(email):
                    st.success("✅ Cleaned up previous signup. Proceeding with new signup.")
                    time.sleep(1)
                else:
                    st.error("❌ Failed to cleanup previous signup. Please contact support.")
                    st.stop()
            elif user_status["auth_user_exists"] and user_status["auth_user_confirmed"]:
                st.error("🚫 This email is already registered and confirmed. Please sign in instead.")
                st.stop()

            existing_user = SRS.table("users").select("username").eq("username", uname).execute().data
            pending_user = SRS.table("pending_signups").select("username").eq("username", uname).execute().data
            
            if existing_user or pending_user:
                st.error(f"❌ Username '{uname}' is already taken. Please choose another.")
                st.stop()

            if not create_pending_signup(email, uname):
                st.error("❌ Failed to process signup. Please try again.")
                st.stop()

            # Use the WORKING link-based confirmation with SendGrid
            try:
                res = SB.auth.sign_up({
                    "email": email, 
                    "password": pwd,
                    "options": {
                        "data": {"username": uname},
                        "emailRedirectTo": "https://ai-matchmaker-demo.streamlit.app/"
                    }
                })
                
                if res.user:
                    SRS.table("pending_signups")\
                       .update({"auth_uid": res.user.id})\
                       .eq("email", email)\
                       .execute()
                    
                    logger.info(f"Link-based signup initiated for: {email} with username: {uname}")
                    
                    # Enhanced success message with better guidance
                    st.success("✅ Check your email for the confirmation link!")
                    
                    st.info("📧 **Where to look for your email:**")
                    st.markdown("""
                    - 📥 **Primary inbox** (Gmail main tab)
                    - 🎯 **Promotions tab** (most likely location)  
                    - 🚫 **Spam folder** (check here too)
                    - 📱 **Mobile app notifications**
                    """)
                    
                    st.warning("🔍 **Search your email** for 'BONDIGO' or 'Welcome to BONDIGO' if you can't find it!")
                    
                    st.info("💡 After clicking the confirmation link, come back here and sign in with your credentials.")
                    
                    # Show helpful debug info
                    with st.expander("🔧 Troubleshooting Info", expanded=False):
                        st.json({
                            "email": email,
                            "auth_uid": res.user.id,
                            "signup_time": datetime.now().isoformat(),
                            "status": "confirmation_email_sent_via_sendgrid",
                            "note": "Email should appear in SendGrid activity feed"
                        })
                        st.markdown("**If no email arrives in 5 minutes:**")
                        st.markdown("1. Check SendGrid activity feed")
                        st.markdown("2. Try the 'Resend Email' option below")
                        st.markdown("3. Contact support if still having issues")
                    
                    st.stop()
                else:
                    cleanup_pending_signup(email)
                    st.error("❌ Failed to create account. Please try again.")
                    st.stop()
                    
            except Exception as e:
                cleanup_pending_signup(email)
                error_msg = str(e)
                logger.error(f"Link-based signup error for {email}: {error_msg}")
                
                if "already registered" in error_msg.lower():
                    st.error("🚫 This email is already registered. Please sign in instead.")
                elif "weak password" in error_msg.lower():
                    st.error("🔒 Password too weak. Please use a stronger password.")
                else:
                    st.error(f"❌ Sign-up error: {error_msg}")
                st.stop()

        # ─── SIGN IN ─────────────────────────────
        try:
            logger.info(f"Sign-in attempt for: {email}")
            resp = SB.auth.sign_in_with_password({"email": email, "password": pwd})
        except Exception as e:
            error_msg = str(e)
            logger.warning(f"Sign-in failed for {email}: {error_msg}")
            
            if "invalid_credentials" in error_msg.lower():
                st.error("🚫 Invalid email or password.")
            elif "email_not_confirmed" in error_msg.lower():
                st.error("📬 Please confirm your email before signing in. Check your inbox for the confirmation link.")
                st.session_state.show_resend = True
                st.rerun()
            elif "too_many_requests" in error_msg.lower():
                st.error("⏰ Too many login attempts. Please wait a few minutes and try again.")
            else:
                st.error(f"❌ Sign‑in error: {error_msg}")
            st.stop()
            
        sess      = resp.session
        user_meta = resp.user

        if not getattr(user_meta, "email_confirmed_at", None) and not getattr(user_meta, "confirmed_at", None):
            logger.warning(f"Unconfirmed email sign-in attempt: {email}")
            st.error("📬 Please confirm your email before continuing. Check your inbox for the confirmation link.")
            st.session_state.show_resend = True
            st.rerun()

        user = get_user_row(user_meta.id)
        if not user:
            logger.error(f"No user row found for confirmed user: {email}")
            st.error("❌ Account setup incomplete. Please contact support.")
            st.stop()
            
        user = apply_daily_airdrop(user)
        logger.info(f"Successful sign-in for: {email}")

        st.session_state.user_jwt = sess.access_token
        SB.postgrest.headers["Authorization"] = f"Bearer {sess.access_token}"
        st.session_state.user     = user
        st.session_state.spent    = 0
        st.session_state.matches  = []
        st.session_state.hist     = {}
        st.session_state.page     = "Find matches"
        st.session_state.chat_cid = None
        st.session_state.flash    = None
        st.session_state.show_resend = False

        raise RerunException()

    st.stop()

# ─────────────────── ENSURE STATE KEYS ────────────────────────────
for k,v in {
    "spent":0, "matches":[], "hist":{},
    "page":"Find matches", "chat_cid":None, "flash":None, 
    "show_resend":False
}.items():
    st.session_state.setdefault(k, v)

user   = st.session_state.user
colset = collection_set(user["id"])

# ─────────────────── APP HEADER & NAVIGATION ────────────────────
if Path(LOGO).is_file():
    st.image(LOGO, width=380)
    st.markdown(
        f"<p style='text-align:center;margin-top:-2px;font-size:1.05rem;"
        f"color:#FFC8D8'>{TAGLINE}</p>",
        unsafe_allow_html=True,
    )
st.markdown(
    f"<span style='background:#f93656;padding:6px 12px;border-radius:8px;display:inline-block;"
    f"font-size:1.25rem;color:#000;font-weight:600;margin-right:8px;'>"
    f"{user['username']}'s Wallet</span>"
    f"<span style='background:#000;color:#57C784;padding:6px 12px;border-radius:8px;"
    f"display:inline-block;font-size:1.25rem;'>{user['tokens']} 💎</span>",
    unsafe_allow_html=True,
)

page = st.radio(
    "", ["Find matches","Chat","My Collection"],
    index=["Find matches","Chat","My Collection"].index(st.session_state.page),
    key="page", horizontal=True
)
st.session_state.page = page

# ─────────────────── FIND MATCHES ────────────────────────────────
if page == "Find matches":
    if st.session_state.flash:
        st.success(st.session_state.flash)
        st.session_state.flash = None

    st.image("assets/bondcosts.png", width=380)
    hobby = st.selectbox("Pick a hobby",   ["space","foodie","gaming","music","art",
                   "sports","reading","travel","gardening","coding"])
    trait = st.selectbox("Pick a trait",   ["curious","adventurous","night‑owl","chill",
                   "analytical","energetic","humorous","kind","bold","creative"])
    vibe  = st.selectbox("Pick a vibe",    ["witty","caring","mysterious","romantic",
                   "sarcastic","intellectual","playful","stoic","optimistic","pragmatic"])
    scene = st.selectbox("Pick a scene",   ["beach","forest","cafe","space‑station",
                   "cyberpunk‑city","medieval‑castle","mountain","underwater",
                   "neon‑disco","cozy‑library"])
    if st.button("Show matches"):
        st.session_state.matches = (
           [c for c in COMPANIONS if all(t in c["tags"] for t in (hobby,trait,vibe,scene))]
           or random.sample(COMPANIONS, 5)
        )

    for c in st.session_state.matches:
        rarity, clr = c.get("rarity","Common"), CLR[c.get("rarity","Common")]
        c1,c2,c3    = st.columns([1,5,2])
        c1.image(c.get("photo",PLACEHOLDER), width=90)
        c2.markdown(
          f"<span style='background:{clr};color:black;padding:2px 6px;"
          f"border-radius:4px;font-size:0.75rem'>{rarity}</span> "
          f"**{c['name']}** • {COST[rarity]} 💎  \n"
          f"<span class='match-bio'>{c['bio']}</span>",
          unsafe_allow_html=True,
        )
        if c["id"] in colset:
            c3.button("💬 Chat", key=f"chat-{c['id']}",
                      on_click=goto_chat, args=(c["id"],))
        else:
            c3.button("💖 Bond", key=f"bond-{c['id']}",
                      on_click=bond_and_chat, args=(c["id"],c))

# ─────────────────── CHAT ────────────────────────────────────────
elif page == "Chat":
    if st.session_state.flash:
        st.success(st.session_state.flash)
        st.session_state.flash = None
    if not colset:
        st.info("Bond first!"); st.stop()

    options = [CID2COMP[i]["name"] for i in colset]
    default = CID2COMP.get(st.session_state.chat_cid, {}).get("name")
    sel     = st.selectbox("Choose companion", options,
                index=options.index(default) if default else 0)
    cid = next(k for k,v in CID2COMP.items() if v["name"]==sel)
    st.session_state.chat_cid = cid

    hist = st.session_state.hist.get(cid)
    if hist is None:
        rows = (SRS.table("messages")
                  .select("role,content,created_at")
                  .eq("user_id", user["id"])
                  .eq("companion_id", cid)
                  .order("created_at")
                  .execute().data)
        base = [{"role":"system","content":
                 f"You are {CID2COMP[cid]['name']}. {CID2COMP[cid]['bio']} Speak PG‑13."}]
        hist = base + [{"role":r["role"],"content":r["content"]} for r in rows]
        st.session_state.hist[cid] = hist

    for msg in hist[1:]:
        st.chat_message("assistant" if msg["role"]=="assistant" else "user")\
          .write(msg["content"])
    if st.session_state.spent >= MAX_TOKENS:
        st.warning("Daily token budget hit."); st.stop()

    user_input = st.chat_input("Say something…")
    if user_input:
        hist.append({"role":"user","content":user_input})
        try:
            resp  = OA.chat.completions.create(
                model="gpt-4o-mini", messages=hist, max_tokens=120
            )
            reply = resp.choices[0].message.content
            st.session_state.spent += resp.usage.prompt_tokens + resp.usage.completion_tokens
            hist.append({"role":"assistant","content":reply})
            st.chat_message("assistant").write(reply)
            SRS.table("messages").insert({
                "user_id":      user["id"],
                "companion_id": cid,
                "role":         "user",
                "content":      user_input
            }).execute()
            SRS.table("messages").insert({
                "user_id":      user["id"],
                "companion_id": cid,
                "role":         "assistant",
                "content":      reply
            }).execute()
        except RateLimitError:
            st.warning("OpenAI rate‑limit.")
        except OpenAIError as e:
            st.error(str(e))

# ─────────────────── MY COLLECTION ───────────────────────────────
elif page == "My Collection":
    st.header("My BONDIGO Collection")
    colset = collection_set(user["id"])
    if not colset:
        st.info("No Bonds yet.")
    for cid in sorted(colset):
        c   = CID2COMP[cid]
        rar = c.get("rarity","Common"); clr = CLR[rar]
        col1, col2 = st.columns([1,5])
        col1.image(c.get("photo",PLACEHOLDER), width=80)
        col2.markdown(
          f"<span style='background:{clr};color:black;padding:2px 6px;"
          f"border-radius:4px;font-size:0.75rem'>{rar}</span> "
          f"**{c['name']}**  \n"
          f"<span style='font-size:0.85rem'>{c['bio']}</span>",
          unsafe_allow_html=True,
        )