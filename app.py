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

# ─────────────────── DEVELOPMENT MODE TOGGLE ─────────────────────
# Set to False for production, True for development
DEV_MODE = os.environ.get('DEV_MODE', 'False').lower() == 'true'



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

st.markdown("""
<style>
@keyframes shimmer {
    0% { transform: translateX(-100%); }
    100% { transform: translateX(100%); }
}
</style>
""", unsafe_allow_html=True)

# Auto-scroll JavaScript for popups
st.markdown("""
<script>
function scrollToTop() {
    setTimeout(function() {
        window.scrollTo({top: 0, behavior: 'smooth'});
    }, 100);
}

// Monitor for popup changes
window.addEventListener('load', function() {
    const observer = new MutationObserver(function(mutations) {
        mutations.forEach(function(mutation) {
            if (mutation.addedNodes.length > 0) {
                const addedNode = mutation.addedNodes[0];
                if (addedNode.nodeType === 1 && addedNode.textContent.includes('Full Details')) {
                    scrollToTop();
                }
            }
        });
    });
    observer.observe(document.body, { childList: true, subtree: true });
});
</script>
""", unsafe_allow_html=True)



# ─────────────────── CONSTANTS & DATA ─────────────────────────────
MAX_TOKENS    = 10_000
DAILY_AIRDROP = 150
COST = {
    "Common": 50,
    "Rare": 150,
    "Legendary": 400
}

def calculate_true_rarity(companion_stats):
    total = sum(companion_stats.values())
    if total >= 400: return "Legendary"
    elif total >= 300: return "Rare"
    else: return "Common"

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
            # Only show user-friendly error, not technical details
            if DEV_MODE:
                st.error("🔑 No SendGrid API key found")
            return False
        
        # Log technical details but don't show to users
        logger.info(f"API key found: {api_key[:15]}...")
        
        try:
            sg = sendgrid.SendGridAPIClient(api_key=api_key)
            logger.info("SendGrid client created successfully")
        except Exception as e:
            logger.error(f"Failed to create SendGrid client: {str(e)}")
            if DEV_MODE:
                st.error(f"❌ SendGrid client error: {str(e)}")
            return False
        
        # Create confirmation URL - user clicks this to confirm
        confirmation_url = f"https://ai-matchmaker-demo.streamlit.app/?confirm_email={user_id}&email={email}"
        logger.info(f"Confirmation URL created: {confirmation_url}")
        
        # Professional email template (same as before)
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
        
        try:
            message = Mail(
                from_email=('web34llc@gmail.com', 'BONDIGO Team'),
                to_emails=email,
                subject='Confirm your BONDIGO account',
                html_content=html_content
            )
            
            logger.info("Mail object created successfully")
        except Exception as e:
            logger.error(f"Failed to create Mail object: {str(e)}")
            if DEV_MODE:
                st.error(f"❌ Mail object error: {str(e)}")
            return False
        
        try:
            response = sg.send(message)
            logger.info(f"SendGrid response: status={response.status_code}")
            
            if hasattr(response, 'body') and response.body:
                logger.info(f"Response body: {response.body}")
            
            success = response.status_code == 202
            logger.info(f"Email send result: {success}")
            
            # Don't show technical details to regular users
            return success
            
        except Exception as e:
            logger.error(f"SendGrid send failed: {str(e)}")
            
            # Only show technical errors in dev mode
            if DEV_MODE:
                st.error(f"❌ SendGrid send error: {str(e)}")
                error_str = str(e)
                if "401" in error_str:
                    st.error("🔑 API key authentication failed")
                elif "403" in error_str:
                    st.error("🚫 Forbidden - check account verification")
                elif "429" in error_str:
                    st.error("⏰ Rate limit exceeded")
            
            return False
        
    except Exception as e:
        logger.error(f"Direct SendGrid email failed: {str(e)}")
        logger.error(f"Error type: {type(e).__name__}")
        
        # Only show technical errors in dev mode
        if DEV_MODE:
            st.error(f"❌ Unexpected error: {str(e)}")
        
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



# ─────────────────── MYSTERY BOX SYSTEM (ADD AFTER YOUR EXISTING HELPERS) ─────────────────────
import random



def get_mystery_tier_from_companion(companion):
    """Determine which mystery tier a companion should be sold as"""
    total_stats = companion.get("total_stats", 0)
    
    # Use actual stats to determine tier, not the JSON rarity field
    if total_stats >= 400:
        return "Elite Bond"
    elif total_stats >= 300:
        return "Premium Bond"  
    else:
        return "Basic Bond"
    
# ─────────────────── CLEAN RARITY SYSTEM ─────────────────────
def get_actual_rarity(companion):
    """Clean, logical rarity thresholds"""
    total_stats = companion.get("total_stats", sum(companion.get("stats", {}).values()))
    
    # Clean round numbers that make sense
    if total_stats >= 400:      # 400+ = Legendary (top tier)
        return "Legendary"
    elif total_stats >= 350:    # 350-399 = Rare (solid tier)
        return "Rare" 
    else:                       # Under 350 = Common
        return "Common"

def get_stat_display_config():
    """Configuration for displaying stats with colors and emojis"""
    return {
        "wit": {"emoji": "🧠", "color": "#9333EA"},          # Purple
        "empathy": {"emoji": "❤️", "color": "#DC2626"},      # Red
        "creativity": {"emoji": "🎨", "color": "#EA580C"},   # Orange
        "knowledge": {"emoji": "📚", "color": "#2563EB"},    # Blue
        "boldness": {"emoji": "⚡", "color": "#059669"}      # Green
    }

def format_stats_display(stats):
    """Format companion stats for display with emojis and colors"""
    config = get_stat_display_config()
    formatted_stats = []
    
    for stat_name, value in stats.items():
        if stat_name in config:
            emoji = config[stat_name]["emoji"]
            color = config[stat_name]["color"]
            formatted_stats.append(
                f"<span style='color:{color};font-weight:600;'>"
                f"{emoji} {stat_name.title()}: {value}</span>"
            )
    
    return " • ".join(formatted_stats)

def format_stats_display_clean(stats):
    """IMPROVED: Cleaner stat display with better contrast"""
    config = {
        "wit": {"emoji": "🧠", "color": "#A78BFA"},          # Lighter purple
        "empathy": {"emoji": "❤️", "color": "#FB7185"},      # Lighter red
        "creativity": {"emoji": "🎨", "color": "#FBBF24"},   # Lighter orange
        "knowledge": {"emoji": "📚", "color": "#60A5FA"},    # Lighter blue
        "boldness": {"emoji": "⚡", "color": "#34D399"}      # Lighter green
    }
    
    formatted_stats = []
    for stat_name, value in stats.items():
        if stat_name in config:
            emoji = config[stat_name]["emoji"]
            color = config[stat_name]["color"]
            formatted_stats.append(
                f"<div style='display: inline-block; margin: 8px 12px; text-align: center;'>"
                f"<div style='color: {color}; font-size: 1.5rem; margin-bottom: 4px;'>{emoji}</div>"
                f"<div style='color: white; font-weight: 600; font-size: 1.1rem;'>{value}</div>"
                f"<div style='color: #D1D5DB; font-size: 0.8rem; text-transform: uppercase; letter-spacing: 1px;'>{stat_name}</div>"
                f"</div>"
            )
    
    return "<div style='text-align: center;'>" + "".join(formatted_stats) + "</div>"

def format_stats_display_badges(stats):
    """Badge-style stats display for Option C"""
    config = {
        "wit": {"emoji": "🧠", "color": "#FFFFFF", "bg": "#8B5CF6"},
        "empathy": {"emoji": "❤️", "color": "#FFFFFF", "bg": "#EF4444"},
        "creativity": {"emoji": "🎨", "color": "#FFFFFF", "bg": "#F97316"},
        "knowledge": {"emoji": "📚", "color": "#FFFFFF", "bg": "#3B82F6"},
        "boldness": {"emoji": "⚡", "color": "#FFFFFF", "bg": "#10B981"}
    }
    
    formatted_stats = []
    for stat_name, value in stats.items():
        if stat_name in config:
            emoji = config[stat_name]["emoji"]
            color = config[stat_name]["color"]
            bg_color = config[stat_name]["bg"]
            formatted_stats.append(
                f"<span style='background:{bg_color};color:{color};padding:6px 10px;border-radius:20px;"
                f"font-weight:600;margin:3px;display:inline-block;font-size:0.85rem;"
                f"box-shadow:0 2px 4px rgba(0,0,0,0.1);'>"
                f"{emoji} {value}</span>"
            )
    
    return "<div style='margin:8px 0;'>" + " ".join(formatted_stats) + "</div>"



# COMPLETE HYBRID FUNCTION (D3 interior + D2 border):
def format_companion_card_enhanced_hybrid(companion, show_stats=True):
    """FIXED: D3 Premium interior with D2 animated border - PROPERLY CLOSED HTML"""
    rarity = get_actual_rarity(companion)
    
    # Keep all the D3 styling (badges, traits, etc.)
    rarity_styles = {
        "Common": {
            "bg_gradient": "linear-gradient(135deg, rgba(156, 163, 175, 0.1), rgba(156, 163, 175, 0.05))",
            "border_color": "#9CA3AF",  # Simplified from border-gradient
            "badge_gradient": "linear-gradient(45deg, #6B7280, #9CA3AF, #D1D5DB)",
            "glow": "rgba(156, 163, 175, 0.3)",
            "badge_icon": "⚪"
        },
        "Rare": {
            "bg_gradient": "linear-gradient(135deg, rgba(59, 130, 246, 0.1), rgba(59, 130, 246, 0.05))",
            "border_color": "#3B82F6",  # Simplified from border-gradient
            "badge_gradient": "linear-gradient(45deg, #1E40AF, #3B82F6, #60A5FA)",
            "glow": "rgba(59, 130, 246, 0.4)",
            "badge_icon": "💎"
        },
        "Legendary": {
            "bg_gradient": "linear-gradient(135deg, rgba(245, 158, 11, 0.15), rgba(245, 158, 11, 0.05))",
            "border_color": "#F59E0B",  # Simplified from border-gradient
            "badge_gradient": "linear-gradient(45deg, #92400E, #F59E0B, #FBBF24)",
            "glow": "rgba(245, 158, 11, 0.5)",
            "badge_icon": "🏆"
        }
    }
    
    style = rarity_styles.get(rarity, rarity_styles["Common"])
    total_stats = companion.get("total_stats", sum(companion.get("stats", {}).values()))
    companion_name = companion["name"]
    companion_bio = companion["bio"]
    
    # Keep all the D3 trait styling (same as before)
    stats_section = ""
    if show_stats:
        trait_configs = {
            "wit": {
                "emoji": "🧠", 
                "text_color": "#E0E7FF",
                "bg_gradient": "linear-gradient(45deg, rgba(139, 92, 246, 0.3), rgba(167, 139, 250, 0.2))",
                "border_color": "rgba(167, 139, 250, 0.5)",
                "glow": "rgba(139, 92, 246, 0.25)"
            },
            "empathy": {
                "emoji": "❤️", 
                "text_color": "#FEE2E2",
                "bg_gradient": "linear-gradient(45deg, rgba(239, 68, 68, 0.3), rgba(248, 113, 113, 0.2))",
                "border_color": "rgba(248, 113, 113, 0.5)",
                "glow": "rgba(239, 68, 68, 0.25)"
            },
            "creativity": {
                "emoji": "🎨", 
                "text_color": "#FED7AA",
                "bg_gradient": "linear-gradient(45deg, rgba(249, 115, 22, 0.3), rgba(251, 146, 60, 0.2))",
                "border_color": "rgba(251, 146, 60, 0.5)",
                "glow": "rgba(249, 115, 22, 0.25)"
            },
            "knowledge": {
                "emoji": "📚", 
                "text_color": "#DBEAFE",
                "bg_gradient": "linear-gradient(45deg, rgba(59, 130, 246, 0.3), rgba(96, 165, 250, 0.2))",
                "border_color": "rgba(96, 165, 250, 0.5)",
                "glow": "rgba(59, 130, 246, 0.25)"
            },
            "boldness": {
                "emoji": "⚡", 
                "text_color": "#D1FAE5",
                "bg_gradient": "linear-gradient(45deg, rgba(16, 185, 129, 0.3), rgba(52, 211, 153, 0.2))",
                "border_color": "rgba(52, 211, 153, 0.5)",
                "glow": "rgba(16, 185, 129, 0.25)"
            }
        }
        
        trait_html = []
        for stat_name, value in companion["stats"].items():
            if stat_name in trait_configs:
                config = trait_configs[stat_name]
                trait_html.append(
                    f"<span style='color: {config['text_color']}; font-weight: 700; "
                    f"background: {config['bg_gradient']}; "
                    f"border: 1px solid {config['border_color']}; "
                    f"padding: 6px 10px; border-radius: 10px; margin: 2px; "
                    f"box-shadow: 0 2px 8px {config['glow']}; display: inline-block;'>"
                    f"{config['emoji']} {value}</span>"
                )
        
        stats_section = f"<div style='margin: 12px 0; font-size: 0.85rem;'>{''.join(trait_html)}</div>"
    
    # FIXED: Simplified border style + proper closing tags
    card_html = f"""
    <div style='background: {style["bg_gradient"]}; 
                border-left: 6px solid {style["border_color"]};
                padding: 16px; margin: 8px 0; border-radius: 8px;
                box-shadow: 0 0 20px {style["glow"]}, inset 0 1px 0 rgba(255,255,255,0.2);'>
        <div style='margin-bottom: 12px;'>
            <span style='background: {style["badge_gradient"]};
                       color: white; padding: 6px 14px; border-radius: 20px;
                       font-size: 0.8rem; font-weight: 700;
                       box-shadow: 0 4px 15px {style["glow"]};
                       text-shadow: 0 1px 2px rgba(0,0,0,0.5);
                       border: 1px solid rgba(255,255,255,0.2);'>{style["badge_icon"]} {rarity} {style["badge_icon"]}</span>
            <span style='color: white; font-weight: 600; font-size: 1.1rem; margin-left: 12px; 
                       text-shadow: 0 1px 3px rgba(0,0,0,0.5);'>{companion_name} • {total_stats} ⭐</span>
        </div>
        {stats_section}
        <div style='color: #F1F5F9; font-style: italic; font-size: 0.9rem; margin-top: 12px; opacity: 0.9;'>{companion_bio}</div>
    </div>
    """
    
    return card_html

def is_companion_revealed(user_id: str, companion_id: str) -> bool:
    """Check if companion stats have been revealed"""
    try:
        result = SRS.table("collection")\
                   .select("revealed")\
                   .eq("user_id", user_id)\
                   .eq("companion_id", companion_id)\
                   .execute().data
        return result[0]["revealed"] if result else True  # Default to revealed if not found
    except:
        return True  # Default to revealed on error

def get_companion_mystery_tier(user_id: str, companion_id: str) -> str:
    """Get the mystery tier the user purchased"""
    try:
        result = SRS.table("collection")\
                   .select("mystery_tier")\
                   .eq("user_id", user_id)\
                   .eq("companion_id", companion_id)\
                   .execute().data
        return result[0]["mystery_tier"] if result else "Basic Bond"  # Changed default
    except:
        return "Basic Bond"  # Changed default
    
# ─────────────────── ADD THESE ADDITIONAL MYSTERY BOX FUNCTIONS ─────────────────────
# Add these right after your existing mystery box functions

def calculate_mystery_reveal_tier(companion, purchased_tier):
    """
    FIXED: Compare actual companion rarity vs expected rarity for purchased tier
    """
    # Get the companion's actual rarity based on stats
    actual_rarity = get_actual_rarity(companion)  # "Common", "Rare", or "Legendary"
    
    # Define what rarity you'd EXPECT for each mystery tier (most likely outcome)
    expected_rarity_for_tier = {
        "Basic Bond": "Common",      # 80% chance Common
        "Premium Bond": "Rare",      # 50% chance Rare  
        "Elite Bond": "Legendary"    # 60% chance Legendary
    }
    
    expected_rarity = expected_rarity_for_tier[purchased_tier]
    
    # Compare actual vs expected rarity
    rarity_hierarchy = {"Common": 1, "Rare": 2, "Legendary": 3}
    actual_level = rarity_hierarchy[actual_rarity]
    expected_level = rarity_hierarchy[expected_rarity]
    
    if actual_level > expected_level:
        surprise = "upgrade"        # Got better than expected!
    elif actual_level == expected_level:
        surprise = "expected"       # Got what you'd expect
    else:
        surprise = "downgrade"      # Got worse than expected
    
    return {
        "actual_tier": get_mystery_tier_from_companion(companion),  # Keep for other uses
        "actual_rarity": actual_rarity,                            # The key fix!
        "surprise_factor": surprise,
        "stat_total": companion.get("total_stats", sum(companion.get("stats", {}).values()))
    }

def buy_mystery_box(user: dict, comp: dict, mystery_tier: str):
    """Updated buy function for mystery box system"""
    price = MYSTERY_COST[mystery_tier]
    
    if price > user["tokens"]:
        return False, "Not enough 💎"
    
    # Check if already owned
    owned = SRS.table("collection").select("companion_id")\
               .eq("user_id", user["id"])\
               .eq("companion_id", comp["id"])\
               .execute().data
    if owned:
        return False, "Already owned"
    
    # Deduct tokens
    SRS.table("users").update({"tokens": user["tokens"] - price})\
       .eq("id", user["id"]).execute()
    
    # Add to collection with mystery box info
    SRS.table("collection").insert({
        "user_id": user["id"],
        "companion_id": comp["id"],
        "revealed": False,  # Hidden until first chat
        "mystery_tier": mystery_tier,
        "bonded_at": datetime.now(timezone.utc).isoformat()
    }).execute()
    
    fresh = get_user_row(user["auth_uid"])
    return True, apply_daily_airdrop(fresh)

def reveal_companion_stats(user_id: str, companion_id: str):
    """Reveal companion stats when first entering chat"""
    try:
        # Mark as revealed
        SRS.table("collection").update({"revealed": True})\
           .eq("user_id", user_id)\
           .eq("companion_id", companion_id)\
           .execute()
        
        # Get collection info for analytics
        collection_info = SRS.table("collection")\
                            .select("mystery_tier")\
                            .eq("user_id", user_id)\
                            .eq("companion_id", companion_id)\
                            .execute().data[0]
        
        companion = CID2COMP[companion_id]
        reveal_info = calculate_mystery_reveal_tier(companion, collection_info["mystery_tier"])
        
        # Track the reveal for analytics
        SRS.table("companion_stats_revealed").insert({
            "user_id": user_id,
            "companion_id": companion_id,
            "original_tier": collection_info["mystery_tier"],
            "actual_rarity": companion.get("rarity", "Common"),
            "stat_total": reveal_info["stat_total"],
            "surprise_factor": reveal_info["surprise_factor"]
        }).execute()
        
        return reveal_info
        
    except Exception as e:
        logger.error(f"Failed to reveal companion stats: {str(e)}")
        return None
    
  # ─────────────────── UPDATED HYBRID MYSTERY SYSTEM ─────────────────────

# Update the Mystery Cost names and add odds
MYSTERY_COST = {
    "Basic Bond": 50,      # Changed from "Mystery Bond" 
    "Premium Bond": 150,   # Guaranteed better odds
    "Elite Bond": 400      # Best odds for legendaries
}

# Define the odds for each tier
MYSTERY_ODDS = {
    "Basic Bond": {"Common": 80, "Rare": 18, "Legendary": 2},
    "Premium Bond": {"Common": 30, "Rare": 50, "Legendary": 20}, 
    "Elite Bond": {"Common": 10, "Rare": 30, "Legendary": 60}
}

def should_show_companion_identity(companion):
    """
    Decide if this companion should show their true identity or be a mystery box
    - Show identity for ~30% of companions (the premium/elite ones mostly)
    - Keep ~70% as mystery boxes
    """
    import random
    
    # Always show some legendary companions (collectors will want specific ones)
    if companion.get("rarity") == "Legendary" and random.random() < 0.4:  # 40% of legendaries show
        return True
    
    # Show some rare companions 
    if companion.get("rarity") == "Rare" and random.random() < 0.2:  # 20% of rares show
        return True
        
    # Rarely show common companions
    if companion.get("rarity") == "Common" and random.random() < 0.1:  # 10% of commons show
        return True
    
    return False  # Most companions stay as mystery boxes

def roll_mystery_companion(mystery_tier, available_companions):
    """
    Roll a random companion based on the mystery tier odds - FIXED VERSION
    """
    import random
    
    odds = MYSTERY_ODDS[mystery_tier]
    
    # Create weighted list based on odds
    weighted_companions = []
    for companion in available_companions:
        rarity = get_actual_rarity(companion)  # ✅ FIXED: Use stats-based rarity
        weight = odds.get(rarity, 0)
        
        # Add this companion multiple times based on its weight
        for _ in range(weight):
            weighted_companions.append(companion)
    
    if not weighted_companions:
        # Fallback to random selection if no weighted companions
        return random.choice(available_companions)
    
    return random.choice(weighted_companions)

def buy_mystery_box_hybrid(user: dict, mystery_tier: str, specific_companion=None):
    """
    Updated buy function for hybrid mystery box system
    - If specific_companion is provided, buy that exact one
    - If not, roll a random companion based on mystery_tier odds
    """
    price = MYSTERY_COST[mystery_tier]
    
    if price > user["tokens"]:
        return False, "Not enough 💎", None
    
    # If buying a specific companion, check if already owned
    if specific_companion:
        owned = SRS.table("collection").select("companion_id")\
                   .eq("user_id", user["id"])\
                   .eq("companion_id", specific_companion["id"])\
                   .execute().data
        if owned:
            return False, "Already owned", None
            
        chosen_companion = specific_companion
    else:
        # Roll a mystery companion!
        # Get all companions not already owned
        owned_ids = collection_set(user["id"])
        available_companions = [c for c in COMPANIONS if c["id"] not in owned_ids]
        
        if not available_companions:
            return False, "No more companions available!", None
            
        chosen_companion = roll_mystery_companion(mystery_tier, available_companions)
        
        # Double-check we don't already own this one (safety check)
        if chosen_companion["id"] in owned_ids:
            # Try again with a different companion
            available_companions = [c for c in available_companions if c["id"] != chosen_companion["id"]]
            if available_companions:
                chosen_companion = roll_mystery_companion(mystery_tier, available_companions)
            else:
                return False, "No more companions available!", None
    
    # Deduct tokens
    SRS.table("users").update({"tokens": user["tokens"] - price})\
       .eq("id", user["id"]).execute()
    
    # Add to collection with mystery box info
    SRS.table("collection").insert({
        "user_id": user["id"],
        "companion_id": chosen_companion["id"],
        "revealed": False if not specific_companion else True,  # Specific purchases are immediately revealed
        "mystery_tier": mystery_tier,
        "bonded_at": datetime.now(timezone.utc).isoformat()
    }).execute()

    # Update collection score after purchase
    update_user_collection_score(user["auth_uid"])
    
    fresh = get_user_row(user["auth_uid"])
    return True, apply_daily_airdrop(fresh), chosen_companion

# Update the display function
def display_mystery_tier_info():
    """Display the updated mystery box pricing explanation - COMPACT VERSION"""
    st.markdown("""
    <div style='background: linear-gradient(45deg, #FF6B9D, #C44569); 
                padding: 15px; border-radius: 8px; margin: 15px 0;'>
        <h4 style='color: white; text-align: center; margin-bottom: 10px; font-size: 1.1rem;'>
            🎁 Mystery Box System
        </h4>
        <div style='display: flex; justify-content: space-around; flex-wrap: wrap;'>
            <div style='background: rgba(255,255,255,0.1); padding: 8px; border-radius: 6px; margin: 3px; min-width: 120px;'>
                <div style='text-align: center; color: white;'>
                    <div style='font-size: 1.2rem;'>🎁</div>
                    <div style='font-size: 0.9rem;'><strong>Basic Bond</strong></div>
                    <div style='font-size: 1rem; color: #FFD700;'>50 💎</div>
                    <div style='font-size: 0.7rem;'>80% Common, 18% Rare, 2% Legendary</div>
                </div>
            </div>
            <div style='background: rgba(255,255,255,0.1); padding: 8px; border-radius: 6px; margin: 3px; min-width: 120px;'>
                <div style='text-align: center; color: white;'>
                    <div style='font-size: 1.2rem;'>✨</div>
                    <div style='font-size: 0.9rem;'><strong>Premium Bond</strong></div>
                    <div style='font-size: 1rem; color: #FFD700;'>150 💎</div>
                    <div style='font-size: 0.7rem;'>30% Common, 50% Rare, 20% Legendary</div>
                </div>
            </div>
            <div style='background: rgba(255,255,255,0.1); padding: 8px; border-radius: 6px; margin: 3px; min-width: 120px;'>
                <div style='text-align: center; color: white;'>
                    <div style='font-size: 1.2rem;'>🏆</div>
                    <div style='font-size: 0.9rem;'><strong>Elite Bond</strong></div>
                    <div style='font-size: 1rem; color: #FFD700;'>400 💎</div>
                    <div style='font-size: 0.7rem;'>10% Common, 30% Rare, 60% Legendary</div>
                </div>
            </div>
        </div>
        <p style='color: white; text-align: center; margin-top: 10px; font-size: 0.8rem;'>
            💡 <strong>Strategy:</strong> Some companions show their identity - buy them directly or try your luck with mystery boxes!
        </p>
    </div>
    """, unsafe_allow_html=True) 
    

# ─────────────────── UI DISPLAY FUNCTIONS FOR MYSTERY BOX ─────────────────────


def show_stats_reveal_animation(companion, reveal_info):
    """Reveal animation that matches the companion's actual rarity colors"""
    
    # Show the upgrade message first
    if reveal_info["surprise_factor"] == "upgrade":
        st.balloons()
        st.success(f"🎉 SURPRISE UPGRADE! You got a {reveal_info['actual_rarity']} companion!")
    elif reveal_info["surprise_factor"] == "expected":
        st.success(f"✨ Perfect match! You got the expected {reveal_info['actual_rarity']} companion!")
    else:
        st.info(f"You got a {reveal_info['actual_rarity']} companion.")
    
    # Get the info we need
    total_stats = reveal_info["stat_total"]
    actual_rarity = reveal_info["actual_rarity"]
    
    # FIXED: Use the same rarity-based colors as the collection cards
    rarity_styles = {
        "Common": {
            "border_color": "#9CA3AF",
            "badge_color": "#9CA3AF", 
            "glow": "rgba(156, 163, 175, 0.3)"
        },
        "Rare": {
            "border_color": "#3B82F6",
            "badge_color": "#3B82F6",
            "glow": "rgba(59, 130, 246, 0.3)"
        },
        "Legendary": {
            "border_color": "#F59E0B",
            "badge_color": "#F59E0B", 
            "glow": "rgba(245, 158, 11, 0.4)"
        }
    }
    
    style = rarity_styles.get(actual_rarity, rarity_styles["Common"])
    
    # Create reveal card with MATCHING colors
    st.markdown(f"""
    <div style='background: linear-gradient(135deg, #1F2937 0%, #374151 100%); 
                border-left: 6px solid {style["border_color"]};
                padding: 20px; border-radius: 12px; margin: 15px 0;
                box-shadow: 0 4px 20px {style["glow"]};'>
        <h3 style='color: white; text-align: center; margin: 0 0 15px 0;'>
            🎊 {companion['name']} Revealed! 🎊
        </h3>
        <div style='text-align: center; margin-bottom: 15px;'>
            <span style='background: {style["badge_color"]}; color: white; padding: 8px 16px; 
                        border-radius: 8px; font-weight: 600;'>
                💎 {actual_rarity} • {total_stats} ⭐ Total
            </span>
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    # Use Streamlit's built-in metrics for stats
    st.markdown("**📊 Individual Stats:**")
    stat_cols = st.columns(len(companion["stats"]))
    
    stat_emojis = {
        "wit": "🧠",
        "empathy": "❤️", 
        "creativity": "🎨",
        "knowledge": "📚",
        "boldness": "⚡"
    }
    
    for i, (stat, value) in enumerate(companion["stats"].items()):
        emoji = stat_emojis.get(stat, "⭐")
        with stat_cols[i]:
            st.metric(f"{emoji} {stat.title()}", value)
    
    # Show the bio
    st.markdown(f"*\"{companion['bio']}\"*")
    st.markdown("---")



def show_companion_details_popup(companion):
    """Show full companion details in a prominent popup-style display with enhanced auto-scroll"""
    
    # Enhanced scroll solution - multiple attempts for reliability
    current_page = st.session_state.get('page', 'Find matches')
    
    if current_page == "My Collection":
        # More aggressive scroll for collection page
        st.markdown("""
        <script>
        // Immediate scroll
        window.scrollTo(0, 0);
        
        // Follow-up scrolls with delays
        setTimeout(() => window.scrollTo({top: 0, behavior: 'instant'}), 10);
        setTimeout(() => window.scrollTo({top: 0, behavior: 'smooth'}), 100);
        setTimeout(() => window.scrollTo({top: 0, behavior: 'smooth'}), 200);
        </script>
        """, unsafe_allow_html=True)
    else:
        # Standard scroll for other pages
        st.markdown("""
        <script>
        window.scrollTo({top: 0, behavior: 'smooth'});
        setTimeout(() => window.scrollTo({top: 0, behavior: 'smooth'}), 50);
        </script>
        """, unsafe_allow_html=True)
    
    # Create a prominent container that takes up most of the screen
    st.markdown("""
    <div style='background: linear-gradient(135deg, #1F2937, #374151); 
                border: 3px solid #3B82F6; border-radius: 20px; 
                padding: 30px; margin: 20px 0;
                box-shadow: 0 20px 60px rgba(0,0,0,0.5);
                position: relative; z-index: 1000;'>
    """, unsafe_allow_html=True)
    
    # Header with close button
    col_header1, col_header2, col_header3 = st.columns([1, 3, 1])
    with col_header2:
        st.markdown(f"### 📸 {companion['name']} - Full Details")
    with col_header3:
        if st.button("❌ Close", key=f"close_details_header_{companion['id']}", use_container_width=True):
            st.session_state.show_companion_details = None
            st.rerun()
    
    # Two-column layout: Image + Stats
    col1, col2 = st.columns([1, 1])
    
    with col1:
        # Large portrait - clean version
        st.image(companion.get("photo", PLACEHOLDER), width=400)
        
    with col2:
        # Companion details using your existing card styling
        rarity = get_actual_rarity(companion)
        total_stats = companion.get("total_stats", sum(companion.get("stats", {}).values()))
        
        st.markdown(f"**Rarity:** {rarity}")
        st.markdown(f"**Total Stats:** {total_stats} ⭐")
        st.markdown(f"**Bio:** *{companion['bio']}*")
        
        # Individual stats with emojis
        st.markdown("**Stats:**")
        stat_emojis = {
            "wit": "🧠",
            "empathy": "❤️", 
            "creativity": "🎨",
            "knowledge": "📚",
            "boldness": "⚡"
        }
        
        for stat, value in companion["stats"].items():
            emoji = stat_emojis.get(stat, "⭐")
            st.markdown(f"• {emoji} **{stat.title()}:** {value}")
    
    # Bottom close button
    col1, col2, col3 = st.columns([2, 1, 2])
    with col2:
        if st.button("❌ Close", key=f"close_details_bottom_{companion['id']}", use_container_width=True):
            st.session_state.show_companion_details = None
            st.rerun()
    
    st.markdown("</div>", unsafe_allow_html=True)

def display_mystery_companion_card(companion, user_id, owned=False, in_collection=False):
    """Display companion card with mystery box or revealed stats"""
    rarity = companion.get("rarity", "Common")
    clr = CLR[rarity]
    
    # Check if this companion is revealed for this user
    revealed = True  # Default for non-owned
    mystery_tier = "Basic Bond"
    
    if owned:
        revealed = is_companion_revealed(user_id, companion["id"])
        mystery_tier = get_companion_mystery_tier(user_id, companion["id"])
    
    # Create columns
    c1, c2, c3 = st.columns([1, 5, 2])
    
    # Image
    if revealed or not owned:
        c1.image(companion.get("photo", PLACEHOLDER), width=90)
    else:
        # Mystery box - show a question mark or mystery image
        mystery_placeholder = "❓"  # We'll use emoji for now
        c1.markdown(f"<div style='font-size: 60px; text-align: center; margin: 10px 0;'>{mystery_placeholder}</div>", unsafe_allow_html=True)
    
    # Info column
    with c2:
        if revealed or not owned:
            # Show full info
            if in_collection:
                # In collection, show stats
                stats_html = format_stats_display(companion["stats"])
                total_stats = companion.get("total_stats", sum(companion["stats"].values()))
                
                st.markdown(
                    f"<span style='background:{clr};color:black;padding:2px 6px;"
                    f"border-radius:4px;font-size:0.75rem'>{rarity}</span> "
                    f"**{companion['name']}** • Total: {total_stats} ⭐<br>"
                    f"<div style='font-size:0.8rem;margin:4px 0;'>{stats_html}</div>"
                    f"<span style='font-size:0.85rem;font-style:italic;'>{companion['bio']}</span>",
                    unsafe_allow_html=True,
                )
            else:
                # In find matches, show mystery tier
                if owned:
                    display_tier = mystery_tier
                    price = MYSTERY_COST[mystery_tier]
                else:
                    display_tier = get_mystery_tier_from_companion(companion)
                    price = MYSTERY_COST[display_tier]
                
                tier_colors = {
                    "Mystery Bond": "#8B5CF6",     # Purple
                    "Premium Bond": "#3B82F6",     # Blue  
                    "Elite Bond": "#F59E0B"        # Gold
                }
                tier_color = tier_colors.get(display_tier, "#8B5CF6")
                
                st.markdown(
                    f"<span style='background:{tier_color};color:white;padding:2px 6px;"
                    f"border-radius:4px;font-size:0.75rem'>{display_tier}</span> "
                    f"**{companion['name']}** • {price} 💎<br>"
                    f"<span style='font-size:0.85rem;font-style:italic;'>{companion['bio']}</span>",
                    unsafe_allow_html=True,
                )
        else:
            # Mystery box - hide details
            tier_colors = {
                "Mystery Bond": "#8B5CF6",
                "Premium Bond": "#3B82F6", 
                "Elite Bond": "#F59E0B"
            }
            tier_color = tier_colors.get(mystery_tier, "#8B5CF6")
            
            st.markdown(
                f"<span style='background:{tier_color};color:white;padding:2px 6px;"
                f"border-radius:4px;font-size:0.75rem'>{mystery_tier}</span> "
                f"**Mystery Companion** 🎁<br>"
                f"<span style='font-size:0.85rem;font-style:italic;'>Stats will be revealed when you start chatting!</span>",
                unsafe_allow_html=True,
            )
    
    return c3  # Return the action column for buttons

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
        # Normalize email to lowercase
        email = email.lower().strip()
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
        # Normalize email to lowercase
        email = email.lower().strip()
        rows = SRS.table("pending_signups").select("*")\
                  .ilike("email", email)\
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
        # Normalize email to lowercase  
        email = email.lower().strip()
        SRS.table("pending_signups").delete().ilike("email", email).execute()
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
    # Normalize email to lowercase
    email = email.lower().strip()
    
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
                # Compare lowercase emails
                if user.email.lower() == email:
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
        
        invite = SRS.table("invitees").select("claimed").ilike("email", email).execute().data
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

def get_bond_level_info(bond_xp: int) -> tuple[int, str, int, int]:
    """
    Calculate bond level and title from XP
    Returns: (level, title, xp_for_current_level, xp_for_next_level)
    """
    if bond_xp < 500:
        return 1, "Bond Newbie", 0, 500
    elif bond_xp < 1500:
        return 2, "Heart Hacker", 500, 1500
    elif bond_xp < 3000:
        return 3, "Soul Syncer", 1500, 3000
    elif bond_xp < 5000:
        return 4, "Bond Virtuoso", 3000, 5000
    else:
        return 5, "Love Legend", 5000, 10000

def update_user_bond_xp(user_id: str, xp_to_add: int) -> dict:
    """Add XP to user and update their level/title"""
    try:
        # Get current user data
        user = get_user_row(user_id)
        if not user:
            logger.error(f"User not found for XP update: {user_id}")
            return None
            
        # Calculate new XP and level
        old_xp = user.get('bond_xp', 0)
        new_xp = old_xp + xp_to_add
        level, title, _, _ = get_bond_level_info(new_xp)
        
        # Update database
        updated_user = SRS.table("users").update({
            "bond_xp": new_xp,
            "bond_level": level,
            "bond_title": title
        }).eq("auth_uid", user_id).execute().data[0]
        
        logger.info(f"Updated user {user_id}: +{xp_to_add} XP (total: {new_xp})")
        return updated_user
        
    except Exception as e:
        logger.error(f"Failed to update bond XP: {str(e)}")
        return None

def award_chat_xp(user_id: str, companion_id: str, message_length: int) -> int:
    """Award XP for sending a chat message"""
    try:
        # Base XP (we'll make this dynamic later with companion stats)
        base_xp = 1
        
        # Quality bonus for longer messages
        quality_bonus = 4 if message_length > 20 else 0
        
        # TODO: Add streak multiplier in Phase 2
        total_xp = base_xp + quality_bonus
        
        # Update user's total Bond XP
        update_user_bond_xp(user_id, total_xp)
        
        # Track individual companion bond (create if doesn't exist)
        try:
            # Try to update existing bond
            result = SRS.table("companion_bonds").update({
                "messages_sent": SRS.table("companion_bonds").select("messages_sent").eq("user_id", user_id).eq("companion_id", companion_id).execute().data[0]["messages_sent"] + 1,
                "total_xp_earned": SRS.table("companion_bonds").select("total_xp_earned").eq("user_id", user_id).eq("companion_id", companion_id).execute().data[0]["total_xp_earned"] + total_xp,
                "last_interaction_at": datetime.now(timezone.utc).isoformat()
            }).eq("user_id", user_id).eq("companion_id", companion_id).execute()
            
        except:
            # Create new bond record if it doesn't exist
            SRS.table("companion_bonds").insert({
                "user_id": user_id,
                "companion_id": companion_id,
                "messages_sent": 1,
                "total_xp_earned": total_xp,
                "bond_strength": 1
            }).execute()
        
        return total_xp
        
    except Exception as e:
        logger.error(f"Failed to award chat XP: {str(e)}")
        return 0
    

# ─────────────────── COLLECTION SCORE SYSTEM ─────────────────────
def calculate_collection_score(user_id: str) -> dict:
    """
    Calculate comprehensive collection score with breakdown - SLOWER PROGRESSION
    Returns dict with score and breakdown for display
    """
    # Get user's companions
    owned_ids = collection_set(user_id)
    if not owned_ids:
        return {"total": 0, "breakdown": {}}
    
    user_companions = [CID2COMP[cid] for cid in owned_ids]
    
    # 1. BASE SCORE: Reduced from full stats to 70% of stats
    base_score = int(sum(
        companion.get("total_stats", sum(companion.get("stats", {}).values())) * 0.7
        for companion in user_companions
    ))
    
    # 2. SYNERGY BONUSES: Higher threshold, smaller bonus
    synergy_bonus = 0
    stat_groups = {}
    
    for companion in user_companions:
        for stat_name, value in companion.get("stats", {}).items():
            if value >= 80:  # Raised from 70 to 80
                if stat_name not in stat_groups:
                    stat_groups[stat_name] = []
                stat_groups[stat_name].append(companion["name"])
    
    # Award bonuses for 2+ companions with same high stat (reduced bonus)
    for stat_name, companions in stat_groups.items():
        if len(companions) >= 2:
            bonus = len(companions) * 25  # Reduced from 50 to 25
            synergy_bonus += bonus
    
    # 3. RARITY MULTIPLIERS: Reduced bonuses
    rarity_bonus = 0
    rarity_counts = {"Common": 0, "Rare": 0, "Legendary": 0}
    
    for companion in user_companions:
        rarity = get_actual_rarity(companion)
        rarity_counts[rarity] += 1
        
        # Reduced bonus points for rare companions
        if rarity == "Rare":
            rarity_bonus += 50  # Reduced from 100
        elif rarity == "Legendary":
            rarity_bonus += 125  # Reduced from 250
    
    # 4. ACHIEVEMENT BONUSES: Higher thresholds, smaller bonuses
    achievement_bonus = 0
    achievements_earned = []
    
    # Collection size milestones - HIGHER THRESHOLDS
    collection_size = len(user_companions)
    if collection_size >= 15:  # Raised from 10
        achievement_bonus += 300  # Reduced from 500
        achievements_earned.append("Mega Collector (15+ companions)")
    elif collection_size >= 8:  # Raised from 5
        achievement_bonus += 100  # Reduced from 200
        achievements_earned.append("Growing Collection (8+ companions)")
    elif collection_size >= 3:  # New smaller milestone
        achievement_bonus += 50
        achievements_earned.append("First Steps (3+ companions)")
    
    # Rarity achievements - HIGHER THRESHOLDS
    if rarity_counts["Legendary"] >= 5:  # Raised from 3
        achievement_bonus += 500  # Reduced from 1000
        achievements_earned.append("Legend Master (5+ Legendaries)")
    elif rarity_counts["Legendary"] >= 2:  # Raised from 1
        achievement_bonus += 150  # Reduced from 300
        achievements_earned.append("Legend Collector (2+ Legendaries)")
    elif rarity_counts["Legendary"] >= 1:
        achievement_bonus += 75
        achievements_earned.append("First Legend")
    
    # Diversity achievement (one of each rarity)
    if all(count > 0 for count in rarity_counts.values()):
        achievement_bonus += 200  # Reduced from 400
        achievements_earned.append("Rarity Master (All tiers)")
    
    # High stats achievement - HIGHER THRESHOLD
    for companion in user_companions:
        if any(value >= 95 for value in companion.get("stats", {}).values()):  # Raised from 90
            achievement_bonus += 100  # Reduced from 200
            achievements_earned.append("Stat Perfectionist (95+ stat)")
            break
    
    # TOTAL CALCULATION
    total_score = base_score + synergy_bonus + rarity_bonus + achievement_bonus
    
    return {
        "total": total_score,
        "breakdown": {
            "base_score": base_score,
            "synergy_bonus": synergy_bonus,
            "rarity_bonus": rarity_bonus,
            "achievement_bonus": achievement_bonus,
            "stat_groups": stat_groups,
            "rarity_counts": rarity_counts,
            "achievements_earned": achievements_earned
        }
    }

def get_collection_level_info(collection_score: int) -> tuple[int, str, int, int]:
    """
    Calculate collection level and title from score
    Returns: (level, title, score_for_current_level, score_for_next_level)
    """
    if collection_score < 1000:
        return 1, "Rookie Collector", 0, 1000
    elif collection_score < 3000:
        return 2, "Bond Enthusiast", 1000, 3000
    elif collection_score < 6000:
        return 3, "Collection Curator", 3000, 6000
    elif collection_score < 10000:
        return 4, "Master Collector", 6000, 10000
    elif collection_score < 15000:
        return 5, "Collection Legend", 10000, 15000
    else:
        return 6, "Grandmaster", 15000, 25000

def update_user_collection_score(user_id: str) -> dict:
    """Update user's collection score in database"""
    try:
        score_data = calculate_collection_score(user_id)
        collection_score = score_data["total"]
        level, title, _, _ = get_collection_level_info(collection_score)
        
        # Update database
        updated_user = SRS.table("users").update({
            "collection_score": collection_score,
            "collection_level": level,
            "collection_title": title
        }).eq("auth_uid", user_id).execute().data[0]
        
        logger.info(f"Updated collection score for {user_id}: {collection_score} ({title})")
        return updated_user
        
    except Exception as e:
        logger.error(f"Failed to update collection score: {str(e)}")
        return None

def display_collection_score(user_id: str):
    """Display collection score with beautiful breakdown"""
    score_data = calculate_collection_score(user_id)
    
    if score_data["total"] == 0:
        return
    
    total = score_data["total"]
    breakdown = score_data["breakdown"]
    
    # Get actual companion count
    owned_ids = collection_set(user_id)
    
    # Main score display
    st.markdown(f"""
<div style='background: linear-gradient(45deg, #FF6B9D, #4ECDC4); 
            padding: 16px; border-radius: 12px; margin: 15px 0;
            border: 2px solid #FFD700; box-shadow: 0 4px 15px rgba(0,0,0,0.2);'>
    <div style='color: white; text-align: center; font-size: 1.3rem; font-weight: 700;'>
        🏆 Collection Score: {total:,} 🏆
    </div>
    <div style='color: white; text-align: center; font-size: 0.9rem; margin-top: 5px;'>
        {len(owned_ids)} Companions • {len(breakdown["achievements_earned"])} Achievements
    </div>
</div>
""", unsafe_allow_html=True)
    
    # Expandable breakdown
    with st.expander("📊 Score Breakdown", expanded=False):
        col1, col2 = st.columns(2)
        
        with col1:
            st.markdown("**Score Components:**")
            st.markdown(f"• Base Stats: **{breakdown['base_score']:,}**")
            st.markdown(f"• Synergy Bonus: **+{breakdown['synergy_bonus']:,}**")
            st.markdown(f"• Rarity Bonus: **+{breakdown['rarity_bonus']:,}**")
            st.markdown(f"• Achievements: **+{breakdown['achievement_bonus']:,}**")
            
            st.markdown("**Rarity Distribution:**")
            for rarity, count in breakdown["rarity_counts"].items():
                if count > 0:
                    st.markdown(f"• {rarity}: **{count}**")
        
        with col2:
            if breakdown["stat_groups"]:
                st.markdown("**Synergy Groups (70+ stats):**")
                for stat, companions in breakdown["stat_groups"].items():
                    if len(companions) >= 2:
                        st.markdown(f"• {stat.title()}: {len(companions)} companions (+{len(companions)*50})")
            
            if breakdown["achievements_earned"]:
                st.markdown("**Achievements Unlocked:**")
                for achievement in breakdown["achievements_earned"]:
                    st.markdown(f"• {achievement}")

def create_user_row(auth_uid: str, username: str, email: str = None) -> dict:
    """Updated version with Bond XP and Collection Score fields"""
    try:
        result = SRS.table("users").insert({
            "id": auth_uid,
            "auth_uid": auth_uid,
            "username": username,
            "email": email,
            "tokens": 1000,
            "last_airdrop": None,
            "bond_xp": 0,                    # Bond XP field
            "bond_level": 1,                 # Bond XP field  
            "bond_title": "Bond Newbie",     # Bond XP field
            "collection_score": 0,           # NEW: Collection Score field
            "collection_level": 1,           # NEW: Collection Level field
            "collection_title": "Rookie Collector"  # NEW: Collection Title field
        }).execute()
        logger.info(f"Created user row for: {auth_uid} with username: {username}, email: {email}")
        return result.data[0]
    except Exception as e:
        logger.error(f"Failed to create user row: {str(e)}")
        raise

# ────────── CUSTOM EMAIL CONFIRMATION HANDLER ─────────────

params = st.query_params

# Handle our custom email confirmation (direct SendGrid)
if "confirm_email" in params:
    # More robust parameter parsing
    user_id = params.get("confirm_email")
    email = params.get("email")
    
    # Convert from list to string if needed (Streamlit sometimes returns lists)
    if isinstance(user_id, list):
        user_id = user_id[0] if user_id else ""
    if isinstance(email, list):
        email = email[0] if email else ""
    
    # Ensure we have strings, not None
    user_id = str(user_id) if user_id else ""
    email = str(email) if email else ""
    
    logger.info(f"Email confirmation attempt: user_id={user_id}, email={email}")
    
    if user_id and email:
        try:
            # Show progress to user
            with st.spinner("Confirming your email..."):
                # Confirm the user in Supabase Auth
                SRS.auth.admin.update_user_by_id(user_id, {"email_confirm": True})
                logger.info(f"Successfully confirmed user in Supabase Auth: {user_id}")
                
                # Check if user row exists, create if needed
                existing_user = get_user_row(user_id)
                if not existing_user:
                     # Normalize email for lookup
                    normalized_email = email.lower().strip()
                    pending = get_pending_signup(normalized_email)
                    if pending:
                        username = pending["username"]
                        logger.info(f"Creating user row for confirmed user: {email} with username: {username}")
                        
                        create_user_row(user_id, username, email)
                        
                        # Update invite status
                        try:
                            SRS.table("invitees").update({"claimed": True}).ilike("email", email.lower().strip()).execute()
                        except Exception as invite_error:
                            logger.warning(f"Could not update invite status: {invite_error}")
                        
                        # Clean up pending signup
                        cleanup_pending_signup(email)
                        
                        st.success("✅ Your email has been confirmed successfully!")
                        st.balloons()  # Add some celebration!
                        
                    else:
                        logger.warning(f"No pending signup found for confirmed email: {email}")
                        st.error("❌ Confirmation link expired or invalid.")
                        st.info("💡 Try signing up again if needed.")
                else:
                    logger.info(f"User row already exists for: {user_id}")
                    st.success("✅ Your email is already confirmed!")
                
                # Clear the URL parameters and provide continue button
                st.markdown("---")
                col1, col2, col3 = st.columns([1, 2, 1])
                with col2:
                    if st.button("🚀 Continue to Sign In", use_container_width=True):
                        # Clear all query parameters
                        st.query_params.clear()
                        st.rerun()
                        
        except Exception as e:
            logger.error(f"Custom email confirmation error: {str(e)}")
            st.error(f"❌ Email confirmation error: {str(e)}")
            
            # Enhanced debug info
            with st.expander("🔧 Debug Info", expanded=False):
                st.json({
                    "user_id": user_id,
                    "email": email,
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "auth_uid_format_check": {
                        "is_valid_uuid_format": len(user_id) == 36 and user_id.count('-') == 4,
                        "is_not_empty": bool(user_id),
                        "length": len(user_id)
                    }
                })
                
            # Offer retry option
            if st.button("🔄 Try Again"):
                st.rerun()
                
    else:
        logger.error(f"Invalid confirmation parameters: user_id='{user_id}', email='{email}'")
        st.error("❌ Invalid confirmation link - missing or empty parameters.")
        
        # Enhanced debug info for troubleshooting
        with st.expander("🔧 Debug Info", expanded=True):
            st.json({
                "user_id": user_id,
                "email": email,
                "user_id_type": type(user_id).__name__,
                "email_type": type(email).__name__,
                "all_params": dict(params),
                "params_keys": list(params.keys())
            })
        
        st.info("💡 Make sure you clicked the complete link from your email.")

# Keep original Supabase confirmation as fallback (rest of your existing code...)
elif "type" in params and params.get("type"):
    # Your existing Supabase confirmation code here...
    pass


# ─────────────────── CALLBACKS (FINAL VERSION) ────────────────────────────────────
def bond_and_chat(cid: str, comp: dict):
    ok, new_user = buy(st.session_state.user, comp)
    if ok:
        st.session_state.user = new_user
        st.session_state.page = "Chat"
        st.session_state.chat_cid = cid
        st.session_state.flash = f"Bonded with {comp['name']}!"
        # Use st.switch_page() or rerun AFTER setting everything
        st.rerun()
    else:
        st.warning(new_user)

def goto_chat(cid: str):
    st.session_state.page = "Chat"
    st.session_state.chat_cid = cid
    st.session_state.flash = None
    # Use st.switch_page() or rerun AFTER setting everything
    st.rerun()

# ─────────────────── ADMIN PANEL (DEVELOPMENT ONLY) ─────────────
# PUT THIS RIGHT AFTER YOUR CALLBACKS AND BEFORE THE LOGIN SECTION

if DEV_MODE:
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
# ─────────────────── PERSISTENT LOGIN CHECK (SIMPLE & RELIABLE) ───────────────────
# Check for auto-login from successful sign-in
if "user" not in st.session_state:
    # Check URL parameter for auto-login
    user_id = None
    if "auto_login" in st.query_params:
        user_id = st.query_params.get("auto_login")
        if isinstance(user_id, list):
            user_id = user_id[0] if user_id else ""
    
    if user_id:
        try:
            # Try to get user data
            user = get_user_row(user_id)
            if user:
                # Restore session
                user = apply_daily_airdrop(user)
                st.session_state.user = user
                st.session_state.spent = 0
                st.session_state.matches = []
                st.session_state.hist = {}
                st.session_state.page = "Find matches"
                st.session_state.chat_cid = None
                st.session_state.flash = None
                st.session_state.show_resend = False
                
                # Keep the URL parameter for future refreshes
                # DON'T clear it this time
                st.rerun()
        except Exception as e:
            # If auto-login fails, clear the parameter and continue to normal login
            logger.warning(f"Auto-login failed for {user_id}: {str(e)}")
            st.query_params.clear()

# ─────────────────── LOGIN / SIGN‑UP ───────────────────────────────
# PUT THIS IMMEDIATELY AFTER THE ADMIN PANEL

if "user" not in st.session_state:
    if Path(LOGO).is_file():
        # Bigger logo for login impact
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            st.image(LOGO, width=380)
        
        st.markdown(
            f"<p style='text-align:center;margin-top:5px;font-size:1.05rem;"
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

        # NORMALIZE EMAIL - Convert to lowercase for consistency
        email = email.lower().strip()
        
        try:
            # Make invite checking case-insensitive
            invite = SRS.table("invitees")\
                        .select("claimed")\
                        .ilike("email", email)\
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
                            
                            st.markdown("""
                            **Where to look for your email:**
                            - 📥 **Primary inbox** (Gmail main tab)
                            - 🎯 **Promotions tab** (most likely location)  
                            - 🚫 **Spam folder** (check here too)
                            - 🔍 **Search** for "BONDIGO" if you can't find it
                            """)
                            
                            # Only show technical details in dev mode
                            if DEV_MODE:
                                with st.expander("🔧 Technical Details", expanded=False):
                                    st.json({
                                        "email": email,
                                        "auth_uid": res.user.id,
                                        "signup_time": datetime.now().isoformat(),
                                        "email_method": "direct_sendgrid_api",
                                        "status": "email_sent_successfully"
                                    })
                            
                        else:
                            # Show user-friendly error message
                            st.error("❌ Account created but email failed to send.")
                            st.info("**Options:**")
                            st.markdown("- Contact support for manual confirmation")
                            st.markdown("- Try signing up again")
                            
                            # Only show debug info in dev mode
                            if DEV_MODE:
                                with st.expander("🔧 Debug Info", expanded=True):
                                    st.json({
                                        "email": email,
                                        "username": uname,
                                        "auth_uid": res.user.id,
                                        "sendgrid_api_key_present": bool(os.environ.get('SENDGRID_API_KEY')),
                                        "error": "send_confirmation_email_direct returned False"
                                    })
                        
                    except Exception as e:
                        logger.error(f"Email sending exception: {str(e)}")
                        st.error("❌ Account created but email failed to send. Please contact support.")
                        
                        # Only show technical details in dev mode
                        if DEV_MODE:
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

        # Store session in both URL and sessionStorage for persistence
        st.query_params["auto_login"] = user["auth_uid"]
        st.markdown(f"""
        <script>
        sessionStorage.setItem('bondigo_user_id', '{user["auth_uid"]}');
        </script>
        """, unsafe_allow_html=True)
        
        st.rerun()

    st.stop()




# ─────────────────── ENSURE STATE KEYS & VARIABLES ────────────────────────────
# PUT THIS RIGHT AFTER THE LOGIN SECTION AND BEFORE THE NAVIGATION

for k,v in {
    "spent":0, "matches":[], "hist":{},
    "chat_cid":None, "flash":None, 
    "show_resend":False
}.items():
    st.session_state.setdefault(k, v)

if "show_companion_details" not in st.session_state:
    st.session_state.show_companion_details = None

user   = st.session_state.user
colset = collection_set(user["id"])

# ─────────────────── APP HEADER & NAVIGATION ────────────────────
if Path(LOGO).is_file():
    # Smaller, more compact for main app
    col1, col2, col3 = st.columns([1.5, 1, 1.5])
    with col2:
        st.image(LOGO, width=280)  # Smaller: 280px vs 380px
    
    st.markdown(
        f"<p style='text-align:center;margin-top:2px;font-size:0.9rem;"  # Smaller text
        f"color:#FFC8D8'>{TAGLINE}</p>",
        unsafe_allow_html=True,
    )
# Get Bond XP info
bond_xp = user.get('bond_xp', 0)
bond_title = user.get('bond_title', 'Bond Newbie')
level, title, current_level_xp, next_level_xp = get_bond_level_info(bond_xp)

# Get Collection info
collection_score = user.get('collection_score', 0)
collection_title = user.get('collection_title', 'Rookie Collector')

# Wallet Display
st.markdown(
    f"<span style='background:linear-gradient(45deg, #ff6b9d, #ff8a5c);padding:6px 12px;border-radius:8px;display:inline-block;"
    f"font-size:1.25rem;color:white;font-weight:600;margin-right:8px;text-shadow: 0 1px 2px rgba(0,0,0,0.3);'>"
    f"{user['username']}'s Wallet</span>"
    f"<span style='background:#000;color:#57C784;padding:6px 12px;border-radius:8px;"
    f"display:inline-block;font-size:1.25rem;margin-right:8px;'>{user['tokens']} 💎</span>",
    unsafe_allow_html=True,
)

# Dual Progress Display
st.markdown(f"""
<div style='display: flex; gap: 10px; margin-bottom: 15px; flex-wrap: wrap;'>
    <div style='background:linear-gradient(45deg, #8B5CF6, #06D6A0);padding:8px 16px;border-radius:10px;
                color:white;font-weight:600;text-shadow: 0 1px 2px rgba(0,0,0,0.3); flex: 1; min-width: 200px;'>
        ✨ Bond XP: {bond_xp:,} | {bond_title}
    </div>
    <div style='background:linear-gradient(45deg, #FF6B9D, #4ECDC4);padding:8px 16px;border-radius:10px;
                color:white;font-weight:600;text-shadow: 0 1px 2px rgba(0,0,0,0.3); flex: 1; min-width: 200px;'>
        🏆 Collection: {collection_score:,} | {collection_title}
    </div>
</div>
""", unsafe_allow_html=True)



# ─────────────────── NAVIGATION WITH LOGOUT ────────────────────
if "page" not in st.session_state:
    st.session_state.page = "Find matches"

# 4-column layout with logout
col1, col2, col3, col4 = st.columns([2, 2, 2, 1])

with col1:
    if st.button("🔍 Find matches", 
                key="nav_find", 
                use_container_width=True,
                type="primary" if st.session_state.page == "Find matches" else "secondary"):
        st.session_state.page = "Find matches"
        st.rerun()

with col2:
    if st.button("💬 Chat", 
                key="nav_chat", 
                use_container_width=True,
                type="primary" if st.session_state.page == "Chat" else "secondary"):
        st.session_state.page = "Chat"
        st.rerun()

with col3:
    if st.button("❤️ My Collection", 
                key="nav_collection", 
                use_container_width=True,
                type="primary" if st.session_state.page == "My Collection" else "secondary"):
        st.session_state.page = "My Collection"
        st.rerun()

with col4:
    if st.button("🚪Exit", key="logout_btn", help="Logout", type="secondary", use_container_width=True):
        # Clear all session state
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        
        # Clear URL parameters
        st.query_params.clear()
        
        # Rerun to show login screen
        st.rerun()

# ─────────────────── ENHANCED POPUP STATE MANAGEMENT ────────────────────
# Handle popup state changes more reliably
if "popup_just_opened" not in st.session_state:
    st.session_state.popup_just_opened = False

# Clear companion details popup when navigating between pages
if "previous_page" not in st.session_state:
    st.session_state.previous_page = st.session_state.page

if st.session_state.previous_page != st.session_state.page:
    st.session_state.show_companion_details = None
    st.session_state.popup_just_opened = False
    st.session_state.previous_page = st.session_state.page

# CHECK FOR COMPANION DETAILS POPUP - ENHANCED VERSION
if st.session_state.show_companion_details:
    # Force aggressive scroll for all cases
    st.markdown("""
    <script>
    // Multiple scroll attempts with different timings
    window.scrollTo(0, 0);
    setTimeout(() => window.scrollTo({top: 0, behavior: 'instant'}), 1);
    setTimeout(() => window.scrollTo({top: 0, behavior: 'instant'}), 10);
    setTimeout(() => window.scrollTo({top: 0, behavior: 'smooth'}), 50);
    setTimeout(() => window.scrollTo({top: 0, behavior: 'smooth'}), 150);
    setTimeout(() => window.scrollTo({top: 0, behavior: 'smooth'}), 300);
    </script>
    """, unsafe_allow_html=True)
    
    show_companion_details_popup(st.session_state.show_companion_details)
    st.markdown("---")
    
    

# ─────────────────── FIND MATCHES (FIXED) ────────────────────────────────
if st.session_state.page == "Find matches":
    if st.session_state.flash:
        st.success(st.session_state.flash)
        st.session_state.flash = None

    # Show updated mystery box pricing info
    display_mystery_tier_info()

    
    
    # Existing match finding logic
    hobby = st.selectbox("Pick a hobby", ["space","foodie","gaming","music","art",
                       "sports","reading","travel","gardening","coding"])
    trait = st.selectbox("Pick a trait", ["curious","adventurous","night‑owl","chill",
                       "analytical","energetic","humorous","kind","bold","creative"])
    vibe = st.selectbox("Pick a vibe", ["witty","caring","mysterious","romantic",
                      "sarcastic","intellectual","playful","stoic","optimistic","pragmatic"])
    scene = st.selectbox("Pick a scene", ["beach","forest","cafe","space‑station",
                        "cyberpunk‑city","medieval‑castle","mountain","underwater",
                        "neon‑disco","cozy‑library"])
    
    if st.button("Show matches"):
        st.session_state.matches = (
           [c for c in COMPANIONS if all(t in c["tags"] for t in (hobby,trait,vibe,scene))]
           or random.sample(COMPANIONS, 5)
        )

    # FIX: Initialize matches if they don't exist (this is the key fix!)
    if "matches" not in st.session_state or not st.session_state.matches:
        st.session_state.matches = random.sample(COMPANIONS, 5)

    # Display matches with HYBRID system (now guaranteed to have matches)
    for c in st.session_state.matches:
        owned = c["id"] in colset
        show_identity = should_show_companion_identity(c)
        
        # Create columns
        c1, c2, c3 = st.columns([1, 5, 2])
        
        if owned:
            # If owned, always show full identity
            with c1:
                # Add view details button
                if st.button("👁️", key=f"view_{c['id']}", help="View full details", use_container_width=True):
                    st.session_state.show_companion_details = c
                    st.session_state.popup_just_opened = True
                    st.rerun()
                st.image(c.get("photo", PLACEHOLDER), width=90)
            rarity, clr = get_actual_rarity(c), CLR[get_actual_rarity(c)]
            c2.markdown(
                f"<span style='background:{clr};color:black;padding:2px 6px;"
                f"border-radius:4px;font-size:0.75rem'>{rarity}</span> "
                f"**{c['name']}**<br>"
                f"<span style='font-size:0.85rem;font-style:italic;'>{c['bio']}</span>",
                unsafe_allow_html=True,
            )
            if c3.button("💬 Chat", key=f"chat-{c['id']}", use_container_width=True):
                goto_chat(c["id"])
                
        elif show_identity:
            # Show this companion's true identity - can buy specifically
            with c1:
                # Add view details button
                if st.button("👁️", key=f"view_{c['id']}", help="View full details", use_container_width=True):
                    st.session_state.show_companion_details = c
                    st.session_state.popup_just_opened = True
                    st.rerun()
                st.image(c.get("photo", PLACEHOLDER), width=90)
            rarity, clr = get_actual_rarity(c), CLR[get_actual_rarity(c)]
            mystery_tier = get_mystery_tier_from_companion(c)
            price = MYSTERY_COST[mystery_tier]
            
            c2.markdown(
                f"<span style='background:{clr};color:black;padding:2px 6px;"
                f"border-radius:4px;font-size:0.75rem'>{rarity}</span> "
                f"**{c['name']}** • {price} 💎<br>"
                f"<span style='font-size:0.85rem;font-style:italic;'>{c['bio']}</span>",
                unsafe_allow_html=True,
            )
            
            if c3.button(f"🎯 Buy\n{price} 💎", key=f"buy-{c['id']}", use_container_width=True):
                # Buy this specific companion
                ok, result, companion = buy_mystery_box_hybrid(user, mystery_tier, c)
                if ok:
                    st.session_state.user = result
                    st.session_state.page = "Chat"
                    st.session_state.chat_cid = c["id"]
                    st.session_state.flash = f"Bonded with {c['name']}!"
                    st.rerun()
                else:
                    st.warning(result)
        else:
            # Mystery box - don't reveal identity
            c1.markdown("<div style='font-size: 60px; text-align: center; margin: 10px 0;'>❓</div>", unsafe_allow_html=True)
            
            c2.markdown(
                f"**Mystery Companion** 🎁<br>"
                f"<span style='font-size:0.85rem;font-style:italic;'>Choose your risk level!</span>",
                unsafe_allow_html=True,
            )
            
            # Show three mystery tier options
            with c3:
                col_a, col_b, col_c = st.columns(3)
                with col_a:
                    if st.button("🎁\n50💎", key=f"basic-{c['id']}", use_container_width=True, help="Basic Bond"):
                        ok, result, companion = buy_mystery_box_hybrid(user, "Basic Bond")
                        if ok:
                            st.session_state.user = result
                            st.session_state.page = "Chat"
                            st.session_state.chat_cid = companion["id"]
                            st.session_state.flash = f"Mystery Bond purchased! 🎁"
                            st.rerun()
                        else:
                            st.warning(result)
                
                with col_b:
                    if st.button("✨\n150💎", key=f"premium-{c['id']}", use_container_width=True, help="Premium Bond"):
                        ok, result, companion = buy_mystery_box_hybrid(user, "Premium Bond")
                        if ok:
                            st.session_state.user = result
                            st.session_state.page = "Chat"
                            st.session_state.chat_cid = companion["id"]
                            st.session_state.flash = f"Premium Bond purchased! Chat to reveal your companion! ✨"
                            st.rerun()
                        else:
                            st.warning(result)
                
                with col_c:
                    if st.button("🏆\n400💎", key=f"elite-{c['id']}", use_container_width=True, help="Elite Bond"):
                        ok, result, companion = buy_mystery_box_hybrid(user, "Elite Bond")
                        if ok:
                            st.session_state.user = result
                            st.session_state.page = "Chat"
                            st.session_state.chat_cid = companion["id"]
                            st.session_state.flash = f"Elite Bond purchased! Chat to reveal your companion! 🏆"
                            st.rerun()
                        else:
                            st.warning(result)

# ─────────────────── UPDATED CHAT SECTION WITH MYSTERY REVEAL ─────────────────────

elif st.session_state.page == "Chat":
    if st.session_state.flash:
        st.success(st.session_state.flash)
        st.session_state.flash = None
    if not colset:
        st.info("Bond first!")
    else:
        options = [CID2COMP[i]["name"] for i in colset]
        default = CID2COMP.get(st.session_state.chat_cid, {}).get("name")
        sel     = st.selectbox("Choose companion", options,
                    index=options.index(default) if default else 0)
        cid = next(k for k,v in CID2COMP.items() if v["name"]==sel)
        st.session_state.chat_cid = cid

        comp = CID2COMP[cid]
        
        # Check if this is the first time chatting (reveal moment!)
        if not is_companion_revealed(user["id"], cid):
            reveal_info = reveal_companion_stats(user["id"], cid)
            if reveal_info:
                show_stats_reveal_animation(comp, reveal_info)
                st.markdown("---")
        
        # DISPLAY COMPANION HEADER WITH STATS (now always revealed)
        col1, col2 = st.columns([1, 4])
        with col1:
            # Add view details button in chat too
            if st.button("👁️ View Details", key=f"chat_view_{cid}", use_container_width=True):
                st.session_state.show_companion_details = comp
                st.session_state.popup_just_opened = True
                st.rerun()
            st.image(comp.get("photo", PLACEHOLDER), width=100)
        with col2:
            st.markdown(format_companion_card_enhanced_hybrid(comp, show_stats=True), unsafe_allow_html=True)
        
        st.markdown("---")

        # Clear history button
        col_clear1, col_clear2, col_clear3 = st.columns([1, 1, 1])
        with col_clear2:
            if st.button("🗑️ Clear History", key=f"clear_history_{cid}", type="secondary"):
                # Clear from database
                try:
                    SRS.table("messages").delete().eq("user_id", user["id"]).eq("companion_id", cid).execute()
                    # Clear from session state
                    if cid in st.session_state.hist:
                        # Keep only the system message
                        base = [{"role":"system","content":
                                f"You are {CID2COMP[cid]['name']}. {CID2COMP[cid]['bio']} Be flirty, bold, and true to your personality. Match the user's energy and interest level. Be sexual."}]
                        st.session_state.hist[cid] = base
                    st.success("💫 Chat history cleared!")
                    st.rerun()
                except Exception as e:
                    logger.error(f"Failed to clear history: {str(e)}")
                    st.error("❌ Failed to clear history. Please try again.")

        st.markdown("---")

        # Rest of your existing chat logic stays the same...
        hist = st.session_state.hist.get(cid)
        if hist is None:
            rows = (SRS.table("messages")
                      .select("role,content,created_at")
                      .eq("user_id", user["id"])
                      .eq("companion_id", cid)
                      .order("created_at")
                      .execute().data)
            base = [{"role":"system","content":
                     f"You are {CID2COMP[cid]['name']}. {CID2COMP[cid]['bio']} Be flirty, bold, and true to your personality. Match the user's energy and interest level. Be sexual."}]
            hist = base + [{"role":r["role"],"content":r["content"]} for r in rows]
            st.session_state.hist[cid] = hist

        for msg in hist[1:]:
            st.chat_message("assistant" if msg["role"]=="assistant" else "user")\
              .write(msg["content"])
        if st.session_state.spent >= MAX_TOKENS:
            st.warning("Daily token budget hit.")
        else:
            user_input = st.chat_input("Say something…")
            if user_input:
                # Show user message immediately
                st.chat_message("user").write(user_input)
                
                hist.append({"role":"user","content":user_input})
                try:
                    resp  = OA.chat.completions.create(
                        model="gpt-4o-mini", messages=hist, max_tokens=120
                    )
                    reply = resp.choices[0].message.content
                    st.session_state.spent += resp.usage.prompt_tokens + resp.usage.completion_tokens
                    hist.append({"role":"assistant","content":reply})
                    st.chat_message("assistant").write(reply)

                    # Award XP for the interaction
                    xp_earned = award_chat_xp(user["auth_uid"], cid, len(user_input))

                    # Original database insertions
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

                    # Show XP notification
                    if xp_earned > 0:
                        st.success(f"💫 +{xp_earned} Bond XP earned!")
                        # Refresh user data to show updated XP
                        fresh_user = get_user_row(user["auth_uid"])
                        if fresh_user:
                            st.session_state.user = fresh_user
                            
                except RateLimitError:
                    st.warning("OpenAI rate‑limit.")
                except OpenAIError as e:
                    st.error(str(e))

# ─────────────────── MY COLLECTION ───────────────────────────────
elif st.session_state.page == "My Collection":
    # ENHANCED COLLECTION HEADER
    st.markdown(f"""
    <div style='text-align: center; margin-bottom: 20px;'>
        <h1 style='background: linear-gradient(45deg, #FF6B9D, #4ECDC4, #FFAA33); 
                   -webkit-background-clip: text; -webkit-text-fill-color: transparent;
                   font-size: 2.5rem; font-weight: 800; margin: 10px 0;
                   text-shadow: 2px 2px 4px rgba(0,0,0,0.3);'>
            ✨ {user['username']}'s BONDIGO Collection ✨
        </h1>
        <div style='background: linear-gradient(45deg, #667eea, #764ba2); 
                    color: white; padding: 8px 20px; border-radius: 25px; 
                    display: inline-block; font-weight: 600; font-size: 1.1rem;
                    box-shadow: 0 4px 15px rgba(0,0,0,0.2);'>
            🏆 Collector Level: {user.get('collection_level', 1)} • {user.get('collection_title', 'Rookie Collector')}
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    # Display collection score
    display_collection_score(user["id"])
    
    colset = collection_set(user["id"])
    if not colset:
        st.info("No Bonds yet.")
    else:
        for cid in sorted(colset):
            c   = CID2COMP[cid]
            rar = get_actual_rarity(c); clr = CLR[rar]
            
            # Create columns: image, info, chat button
            col1, col2, col3 = st.columns([1, 4, 1])
            
            with col1:
                # Add view details button
                if st.button("👁️", key=f"collection_view_{cid}", help="View full details", use_container_width=True):
                    st.session_state.show_companion_details = c
                    st.session_state.popup_just_opened = True
                    st.rerun()
                col1.image(c.get("photo",PLACEHOLDER), width=80)
            
            with col2:
                col2.markdown(format_companion_card_enhanced_hybrid(c, show_stats=True), unsafe_allow_html=True)
            
            with col3:
                # Simple chat button
                if col3.button("💬 Chat", key=f"collection_chat_{cid}", use_container_width=True):
                    goto_chat(cid)

