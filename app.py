import os, json, random, logging, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import streamlit as st
from streamlit.runtime.scriptrunner import RerunException
from openai import OpenAI, OpenAIError, RateLimitError
from dotenv import load_dotenv
from supabase import create_client
from postgrest.exceptions import APIError
import sendgrid
from sendgrid.helpers.mail import Mail

st.session_state.show_admin = True

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

# ────────── CUSTOM EMAIL CONFIRMATION HANDLER ─────────────
params = st.query_params

# Handle our custom email confirmation (direct SendGrid)
if "confirm_email" in params:
    user_id = params.get("confirm_email", [""])[0] if "confirm_email" in params else ""
    email = params.get("email", [""])[0] if "email" in params else ""
    
    if user_id and email:
        try:
            # Confirm the user in Supabase Auth
            SRS.auth.admin.update_user_by_id(user_id, {"email_confirm": True})
            
            # Check if user row exists, create if needed
            existing_user = get_user_row(user_id)
            if not existing_user:
                pending = get_pending_signup(email)
                if pending:
                    username = pending["username"]
                    logger.info(f"Creating user row for confirmed user: {email} with username: {username}")
                    
                    create_user_row(user_id, username)
                    SRS.table("invitees").update({"claimed": True}).eq("email", email).execute()
                    cleanup_pending_signup(email)
                    
                    st.success("✅ Your email has been confirmed! You can now sign in below.")
                else:
                    st.error("❌ Confirmation link expired or invalid.")
            else:
                st.info("✅ Your email is already confirmed. You can sign in below.")
            
            if st.button("Continue to Sign In"):
                st.query_params.clear()
                st.rerun()
                
        except Exception as e:
            logger.error(f"Custom email confirmation error: {str(e)}")
            st.error(f"❌ Email confirmation error: {str(e)}")
    else:
        st.error("❌ Invalid confirmation link.")

# Keep original Supabase confirmation as fallback
elif "type" in params and params["type"][0] == "signup":
    confirmation_type = params.get("type", [""])[0] if "type" in params else ""
    error_code = params.get("error_code", [""])[0] if "error_code" in params else ""
    error_description = params.get("error_description", [""])[0] if "error_description" in params else ""

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
def send_confirmation_email_direct(email: str, username: str, user_id: str) -> bool:
    """Send confirmation email directly via SendGrid API (bypass Supabase)"""
    try:
        logger.info(f"Starting email send: email={email}, username={username}, user_id={user_id}")
        
        api_key = os.environ.get('SENDGRID_API_KEY')
        if not api_key:
            logger.error("No SENDGRID_API_KEY found in environment")
            return False
        
        logger.info(f"API key found: {api_key[:15]}...")
        
        sg = sendgrid.SendGridAPIClient(api_key=api_key)
        logger.info("SendGrid client created successfully")
        
        # Create confirmation URL - user clicks this to confirm
        confirmation_url = f"https://ai-matchmaker-demo.streamlit.app/?confirm_email={user_id}&email={email}"
        logger.info(f"Confirmation URL created: {confirmation_url}")
        
        # More professional, less spammy email template
        html_content = f"""
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
            <div style="background-color: #ffffff; padding: 40px; border: 1px solid #e1e5e9; border-radius: 8px;">
                <h1 style="color: #1a1a1a; font-size: 24px; margin-bottom: 16px; text-align: center;">Confirm Your Email Address</h1>
                
                <p style="color: #333333; font-size: 16px; line-height: 1.5; margin-bottom: 24px;">
                    Hello {username},
                </p>
                
                <p style="color: #333333; font-size: 16px; line-height: 1.5; margin-bottom: 32px;">
                    Thank you for creating your BONDIGO account. To complete your registration, please confirm your email address by clicking the button below:
                </p>
                
                <div style="text-align: center; margin: 32px 0;">
                    <a href="{confirmation_url}" 
                       style="background-color: #0066cc; color: #ffffff; padding: 12px 24px; 
                              text-decoration: none; border-radius: 6px; font-weight: 500; 
                              display: inline-block; font-size: 16px;">
                        Confirm Email Address
                    </a>
                </div>
                
                <p style="color: #666666; font-size: 14px; line-height: 1.4; margin-top: 32px;">
                    If you're unable to click the button above, please copy and paste the following link into your browser:
                </p>
                
                <p style="color: #0066cc; font-size: 14px; word-break: break-all; background-color: #f8f9fa; padding: 12px; border-radius: 4px; margin: 16px 0;">
                    {confirmation_url}
                </p>
                
                <hr style="margin: 32px 0; border: none; border-top: 1px solid #e1e5e9;">
                
                <p style="color: #999999; font-size: 12px; text-align: center; margin: 0;">
                    If you didn't create this account, please ignore this email.
                </p>
            </div>
        </div>
        """
        
        logger.info("HTML content created")
        
        message = Mail(
            from_email=('web34llc@gmail.com', 'BONDIGO Team'),  # Add sender name
            to_emails=email,
            subject='Confirm your BONDIGO account',  # Less promotional subject
            html_content=html_content
        )
        
        # Add headers to improve deliverability
        message.header = {
            'List-Unsubscribe': '<mailto:unsubscribe@your-domain.com>',
            'X-Entity-Ref-ID': f'account-confirmation-{user_id}'
        }
        
        logger.info("Mail object created successfully")
        
        response = sg.send(message)
        logger.info(f"SendGrid response: status={response.status_code}")
        
        success = response.status_code == 202
        logger.info(f"Email send result: {success}")
        
        return success
        
    except Exception as e:
        logger.error(f"Direct SendGrid email failed: {str(e)}")
        logger.error(f"Error type: {type(e).__name__}")
        return False

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
        
        # Use direct SendGrid for resend
        if pending.get("auth_uid"):
            return send_confirmation_email_direct(email, pending["username"], pending["auth_uid"])
        else:
            logger.error(f"No auth_uid found for pending signup: {email}")
            return False
            
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
# Replace your existing admin panel code with this version that works in main area
# Put this RIGHT AFTER your streamlit config and CSS, before the login section

# ─────────────────── ADMIN PANEL (BEFORE LOGIN) ─────────────
# Place this RIGHT AFTER your CSS styling and BEFORE the login section
# This allows testing email functionality without being logged in

if st.session_state.get('show_admin', True):  # Set to True for development
    with st.expander("🔧 Admin Panel - Email Testing (Development)", expanded=False):
        # Create proper column layout
        col1, col2 = st.columns(2)
        
        with col1:
            st.subheader("📊 User Status Check")
            
            check_email = st.text_input("Check user status:", key="check_user_email")
            if st.button("🔍 Check Status", key="check_status") and check_email:
                try:
                    status = check_user_status(check_email)
                    st.json(status)
                    
                    # Show pending signup info
                    pending = get_pending_signup(check_email)
                    if pending:
                        st.info("📋 Pending signup found:")
                        st.json({
                            "username": pending["username"],
                            "created_at": pending["created_at"],
                            "expires_at": pending["expires_at"],
                            "auth_uid": pending.get("auth_uid", "Not set")
                        })
                        
                except Exception as e:
                    st.error(f"❌ Error checking status: {str(e)}")
            
            # Cleanup expired signups
            if st.button("🧹 Cleanup Expired Signups", key="cleanup_expired"):
                try:
                    cleanup_expired_signups()
                    st.success("✅ Cleanup completed")
                except Exception as e:
                    st.error(f"❌ Cleanup error: {str(e)}")

        with col2:
            st.subheader("📧 Direct SendGrid Tests")
            
            # Test 1: Direct SendGrid API test
            if st.button("🚀 Test Direct SendGrid API", key="test_direct_sendgrid"):
                    try:
                        import sendgrid
                        from sendgrid.helpers.mail import Mail
                        
                        api_key = os.environ.get('SENDGRID_API_KEY')
                        st.info(f"Using API key: {api_key[:15]}..." if api_key else "No API key found")
                        
                        sg = sendgrid.SendGridAPIClient(api_key=api_key)
                        
                        # Simple test message
                        message = Mail(
                            from_email='web34llc@gmail.com',
                            to_emails='web34llc@gmail.com',
                            subject='SendGrid Test from BONDIGO',
                            html_content='<p>This is a test email from your BONDIGO app!</p>'
                        )
                        
                        response = sg.send(message)
                        
                        st.success(f"✅ SendGrid API Response: {response.status_code}")
                        st.info("📧 Check your email and SendGrid activity feed")
                        
                        # Show response details
                        with st.expander("Response Details"):
                            st.json({
                                "status_code": response.status_code,
                                "headers": dict(response.headers) if hasattr(response, 'headers') else "No headers",
                                "body": response.body.decode() if hasattr(response, 'body') and response.body else "No body"
                            })
                        
                    except Exception as e:
                        st.error(f"❌ SendGrid error: {str(e)}")
                        
                        # More detailed error information
                        error_type = type(e).__name__
                        st.code(f"Error type: {error_type}")
                        
                        if "401" in str(e) or "unauthorized" in str(e).lower():
                            st.warning("🔑 API key authentication failed. Check your SendGrid API key permissions.")
                            st.info("Make sure your API key has 'Mail Send' permissions in SendGrid dashboard.")
                        elif "403" in str(e) or "forbidden" in str(e).lower():
                            st.warning("🚫 Account verification may be required. Check your SendGrid account status.")
                        elif "429" in str(e) or "rate" in str(e).lower():
                            st.warning("⏰ Rate limit exceeded. Wait a few minutes and try again.")
                        else:
                            st.info("Debug info:")
                            st.code(str(e))
            
            # Test 2: Check API key status
            if st.button("🔑 Check SendGrid API Key", key="check_api_key"):
                api_key = os.environ.get('SENDGRID_API_KEY')
                if api_key:
                    if api_key.startswith('SG.'):
                        st.success(f"✅ API key found: {api_key[:10]}...")
                    else:
                        st.error("❌ API key format looks wrong (should start with 'SG.')")
                else:
                    st.error("❌ No SENDGRID_API_KEY found in environment")
                    st.code("Add to Streamlit Secrets:\nSENDGRID_API_KEY = 'SG.your_key_here'")
        
        # Additional row for more tests
        col3, col4 = st.columns(2)
        
        with col3:
            st.markdown("**🧹 Complete User Cleanup:**")
            cleanup_test_email = st.text_input("Email to completely clean:", key="cleanup_test_email", value="wakeyourmindup21@gmail.com")
            if st.button("🔥 Nuclear Cleanup User", key="nuclear_cleanup") and cleanup_test_email:
                try:
                    # Step 1: Delete from auth - FIXED VERSION
                    deleted_auth = False
                    users_response = SRS.auth.admin.list_users()
                    
                    # Handle the list format correctly
                    users = users_response if isinstance(users_response, list) else []
                    
                    for user in users:
                        if user.email == cleanup_test_email:
                            SRS.auth.admin.delete_user(user.id)
                            deleted_auth = True
                            st.info(f"🗑️ Deleted auth user: {user.id}")
                    
                    # Step 2: Clean up all related data
                    cleanup_pending_signup(cleanup_test_email)
                    
                    # Step 3: Reset invite status
                    SRS.table("invitees").update({"claimed": False}).eq("email", cleanup_test_email).execute()
                    
                    if deleted_auth:
                        st.success(f"✅ Completely cleaned up: {cleanup_test_email}")
                    else:
                        st.info(f"ℹ️ No auth user found for: {cleanup_test_email}")
                        
                    st.info("🔄 Now try signing up again!")
                    
                except Exception as e:
                    st.error(f"❌ Cleanup error: {str(e)}")
                    st.info("Debug info:")
                    st.code(f"Error type: {type(e)}\nError details: {str(e)}")
        
        with col4:
            # Test 4: List all auth users
            if st.button("👥 List All Auth Users", key="list_auth_users"):
                try:
                    users_response = SRS.auth.admin.list_users()
                    st.info(f"Raw response type: {type(users_response)}")
                    st.json({"raw_response": str(users_response)})
        
                    # Try different ways to access users
                    if hasattr(users_response, 'users'):
                        users = users_response.users
                    elif isinstance(users_response, list):
                        users = users_response
                    elif hasattr(users_response, 'data'):
                        users = users_response.data
                    else:
                        st.error("Unknown response format")
                        st.json(dir(users_response))
                        users = []
                    
                    if users:
                        st.json([{
                            "email": getattr(user, 'email', 'No email'),
                            "id": getattr(user, 'id', 'No id'), 
                            "confirmed": bool(getattr(user, 'email_confirmed_at', None) or getattr(user, 'confirmed_at', None))
                        } for user in users])
                    else:
                        st.info("No auth users found or unable to parse response")
                        
                except Exception as e:
                    st.error(f"❌ Error listing users: {str(e)}")
                    st.info("This might be a Supabase API version difference")

            
            # Resend confirmation email test
            st.markdown("**📨 Resend Confirmation:**")
            resend_email = st.text_input("Email to resend:", key="resend_email")
            if st.button("📤 Resend", key="resend_confirm") and resend_email:
                try:
                    if resend_confirmation_email(resend_email):
                        st.success("✅ Confirmation email resent!")
                    else:
                        st.error("❌ Failed to resend confirmation email")
                except Exception as e:
                    st.error(f"❌ Resend error: {str(e)}")

    st.markdown("---")  # Add a separator before login section
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

        # ─── SIGN UP WITH DIRECT SENDGRID EMAIL ─────────────────────────────
        
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

            # Create user in Supabase Auth WITHOUT email confirmation
            try:
                res = SB.auth.sign_up({
                    "email": email, 
                    "password": pwd,
                    "options": {
                        "data": {"username": uname}
                        # NO emailRedirectTo - we handle emails ourselves
                    }
                })
                
                if res.user:
                    logger.info(f"Created auth user: {res.user.id} for email: {email}")
                    
                    # Store the auth_uid for later confirmation
                    try:
                        SRS.table("pending_signups")\
                        .update({"auth_uid": res.user.id})\
                        .eq("email", email)\
                        .execute()
                        logger.info(f"Updated pending signup with auth_uid: {res.user.id}")
                    except Exception as e:
                        logger.error(f"Failed to update pending signup: {str(e)}")
                    
                    # Send confirmation email directly via SendGrid
                    try:
                        email_sent = send_confirmation_email_direct(email, uname, res.user.id)
                        logger.info(f"Email send attempt result: {email_sent}")
                        
                        if email_sent:
                            logger.info(f"Direct SendGrid signup email sent to: {email}")
                            
                            st.success("✅ Account created! Check your email for confirmation link.")
                            st.info("📧 **Email sent directly via SendGrid** (bypassing Supabase)")
                            
                            st.markdown("""
                            **Where to look for your email:**
                            - 📥 **Primary inbox** (Gmail main tab)
                            - 🎯 **Promotions tab** (most likely location)  
                            - 🚫 **Spam folder** (check here too)
                            - 🔍 **Search** for "BONDIGO" if you can't find it
                            """)
                            
                            with st.expander("🔧 Technical Details", expanded=False):
                                st.json({
                                    "email": email,
                                    "auth_uid": res.user.id,
                                    "signup_time": datetime.now().isoformat(),
                                    "email_method": "direct_sendgrid_api",
                                    "status": "email_sent_successfully"
                                })
                            
                        else:
                            # More detailed error for email failure
                            st.error("❌ Account created but email failed to send.")
                            
                            # Show what we tried
                            with st.expander("🔧 Debug Info", expanded=True):
                                st.json({
                                    "email": email,
                                    "username": uname,
                                    "auth_uid": res.user.id,
                                    "sendgrid_api_key_present": bool(os.environ.get('SENDGRID_API_KEY')),
                                    "error": "send_confirmation_email_direct returned False"
                                })
                            
                            st.info("**Options:**")
                            st.markdown("- Try the 📤 Resend button in the admin panel above")
                            st.markdown("- Contact support for manual confirmation")
                            st.markdown("- Try signing up again")
                        
                    except Exception as e:
                        logger.error(f"Email sending exception: {str(e)}")
                        st.error(f"❌ Account created but email error: {str(e)}")
                        
                        with st.expander("🔧 Debug Info", expanded=True):
                            st.code(f"Error type: {type(e).__name__}\nError details: {str(e)}")
                    
                    st.stop()
                else:
                    cleanup_pending_signup(email)
                    st.error("❌ Failed to create account. Please try again.")
                    st.stop()
                    
            except Exception as e:
                cleanup_pending_signup(email)
                error_msg = str(e)
                logger.error(f"Supabase signup error for {email}: {error_msg}")
                
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