###############################################################################
# BONDIGO – virtual companion demo with Supabase e‑mail magic‑link auth
###############################################################################
import json, os, random
from datetime import datetime, timedelta, timezone
from pathlib import Path
import urllib.parse

import streamlit as st
import streamlit.components.v1 as components           # ← new
from openai import OpenAI, OpenAIError, RateLimitError
from dotenv import load_dotenv
from supabase import create_client

# ─────────────────────────── ENV / CLIENTS ──────────────────────────
load_dotenv()
SUPABASE = create_client(os.environ["SUPABASE_URL"],
                         os.environ["SUPABASE_KEY"])
OPENAI   = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

# ─────────────────────────── CONSTANTS  ─────────────────────────────
MAX_TOKENS_PER_USER = 10_000
DAILY_AIRDROP       = 150
COST                = {"Common": 50, "Rare": 200, "Legendary": 700}

PLACEHOLDER = "assets/placeholder.png"
LOGO        = "assets/bondigo_banner.png"
TAGLINE     = "Talk the Lingo · Master the Bond · Dominate the Game."
CLR         = {"Common":"#bbb", "Rare":"#57C7FF", "Legendary":"#FFAA33"}

COMPANIONS = json.load(open("companions.json", encoding="utf-8-sig"))
CID2COMPANION = {c["id"]: c for c in COMPANIONS}

# ────────────────────── SUPABASE HELPERS ────────────────────────────
def sb_get_or_create_user(email: str) -> dict:
    """Create a profile row if missing + handle 24 h token airdrop."""
    rows = SUPABASE.table("users").select("*").eq("email", email).execute().data
    if rows:
        user = rows[0]
    else:
        user = SUPABASE.table("users").insert(
            {"email": email, "tokens": 1000}
        ).execute().data[0]

    last = user["last_airdrop"] or user["created_at"]
    last = datetime.fromisoformat(last.replace("Z", "+00:00"))
    if datetime.now(timezone.utc) - last >= timedelta(hours=24):
        user = SUPABASE.table("users").update(
            {"tokens": user["tokens"] + DAILY_AIRDROP,
             "last_airdrop": datetime.now(timezone.utc).isoformat()}
        ).eq("id", user["id"]).execute().data[0]
    return user

def sb_collection(user_id:str) -> set[str]:
    rows = SUPABASE.table("collection").select("companion_id") \
           .eq("user_id", user_id).execute().data
    return {r["companion_id"] for r in rows}

def sb_buy(user:dict, comp:dict):
    cid  = comp["id"]
    cost = COST[comp.get("rarity","Common")]
    if cost > user["tokens"]:
        return False, "Not enough 💎"
    owned = SUPABASE.table("collection").select("companion_id") \
            .eq("user_id", user["id"]).eq("companion_id", cid).execute()
    if owned.data:
        return False, "Already owned"

    # atomic-ish
    SUPABASE.table("users").update({"tokens": user["tokens"]-cost}) \
             .eq("id", user["id"]).execute()
    SUPABASE.table("collection").insert(
        {"user_id": user["id"], "companion_id": cid}
    ).execute()
    return True, sb_get_or_create_user(user["email"])

# ────────────────────────── PAGE CONFIG ─────────────────────────────
st.set_page_config(page_title="BONDIGO", page_icon="🩷", layout="centered")
st.markdown("""
<style>
[data-testid="stSidebar"] div[role="radiogroup"] label{
    font-size:1.25rem; line-height:1.5rem; padding:8px 0 8px 4px;
}
.match-name{font-size:1.2rem;font-weight:700;}
.match-bio {font-size:1.0rem;line-height:1.5;}
</style>
""", unsafe_allow_html=True)

# ─────────────────────── LOGIN / AUTH BLOCK ─────────────────────────
if "user" not in st.session_state:

    # 0️⃣ – header
    st.title("📧 Login to **BONDIGO**")

    # 1️⃣ – send magic link
    email = st.text_input("Email")
    if st.button("Send magic link") and email:
        SUPABASE.auth.sign_in_with_otp(
            {"email": email},
            options={"email_redirect_to": "https://ai-matchmaker-demo.streamlit.app/"},
        )
        st.success("Check your mailbox!")

    # 2️⃣ – tiny JS: if URL fragment contains tokens, move them to ?query
    components.html("""
    <script>
    const h = window.location.hash;
    if (h.startsWith("#access_token=")){
        const q = new URLSearchParams(h.slice(1));      // remove '#'
        const url = new URL(window.location);
        url.hash   = "";
        url.search = q.toString();
        window.history.replaceState({}, "", url);
        window.parent.postMessage({type:"streamlit:rerun"}, "*");
    }
    </script>
    """, height=0)

    # 3️⃣ – catch tokens now in query string
    qp = st.query_params
    if "access_token" in qp and "user" not in st.session_state:
        session = SUPABASE.auth.set_session(qp["access_token"], qp["refresh_token"])
        st.query_params.clear()              # clean URL
        st.session_state.user       = sb_get_or_create_user(session.user.email)
        st.session_state.collection = sb_collection(st.session_state.user["id"])
        st.session_state.histories  = {}
        st.session_state.spent      = 0
        st.rerun()
    st.stop()

# ─────────────────────── SHARED SHORT‑HANDS ────────────────────────
user       = st.session_state.user
collection = st.session_state.collection

# ─────────────────────────── HEADER  ───────────────────────────────
if Path(LOGO).is_file():
    st.image(LOGO, width=380)
    st.markdown(f"<p style='text-align:center;margin-top:-2px;font-size:1.05rem;"
                f"color:#FFC8D8'>{TAGLINE}</p>", unsafe_allow_html=True)
st.markdown(f"**Wallet:** `{user['tokens']} 💎`")

# ─────────────────────────── NAV  ──────────────────────────────────
tabs = ["Find matches", "Chat", "My Collection"]
page = st.sidebar.radio("Navigation", tabs, key="nav",
                        index=tabs.index(st.session_state.get("nav", tabs[0])))
st.session_state.nav = page

# ───────────────────────── FIND MATCHES ────────────────────────────
if page == "Find matches":
    hobby = st.selectbox("Pick a hobby",
        ["space","foodie","gaming","music","art","sports","reading","travel","gardening","coding"])
    trait = st.selectbox("Pick a trait",
        ["curious","adventurous","night‑owl","chill","analytical","energetic",
         "humorous","kind","bold","creative"])
    vibe  = st.selectbox("Pick a vibe",
        ["witty","caring","mysterious","romantic","sarcastic","intellectual",
         "playful","stoic","optimistic","pragmatic"])
    scene = st.selectbox("Pick a scene",
        ["beach","forest","cafe","space‑station","cyberpunk‑city","medieval‑castle",
         "mountain","underwater","neon‑disco","cozy‑library"])

    if st.button("Show matches"):
        st.session_state.matches = [
            c for c in COMPANIONS if all(t in c["tags"] for t in [hobby,trait,vibe,scene])
        ] or random.sample(COMPANIONS, 5)

    for c in st.session_state.get("matches", []):
        b = c.get("rarity","Common"); clr = CLR[b]
        with st.container():
            col1,col2,col3 = st.columns([1,3,2])
            col1.image(c.get("photo", PLACEHOLDER), width=90)
            col2.markdown(
                f"<span style='background:{clr};padding:2px 6px;border-radius:4px;"
                f"font-size:0.75rem'>{b}</span> **{c['name']}** • {COST[b]} 💎  \n"
                f"<span class='match-bio'>{c['bio']}</span>",
                unsafe_allow_html=True)
            if col3.button("💖 Mint", key=f"mint-{c['id']}"):
                ok, res = sb_buy(user, c)
                if ok:
                    st.session_state.user = res
                    collection.add(c["id"])
                    st.success("Minted!")
                else:
                    st.warning(res)
                st.rerun()

# ───────────────────────────── CHAT ────────────────────────────────
elif page == "Chat":
    if not collection:
        st.info("Mint a companion first.")
        st.stop()

    names = [CID2COMPANION[c]["name"] for c in collection]
    default_cid = next(iter(collection))
    sel_name = st.selectbox("Choose companion", names,
                            index=names.index(CID2COMPANION[default_cid]["name"]))
    cid = next(k for k,v in CID2COMPANION.items() if v["name"]==sel_name)
    st.session_state.chat_id = cid

    # ensure history
    hist = st.session_state.histories.setdefault(cid, [{
        "role":"system",
        "content": f"You are {CID2COMPANION[cid]['name']}. "
                   f"{CID2COMPANION[cid]['bio']} Speak in first person, PG‑13."
    }])

    comp = CID2COMPANION[cid]
    st.image(comp.get("photo", PLACEHOLDER), width=180)
    st.subheader(f"Chatting with **{comp['name']}**")
    if st.button("🗑️ Clear history"):
        st.session_state.histories[cid] = hist[:1]
        st.rerun()

    for m in hist[1:]:
        st.chat_message("assistant" if m["role"]=="assistant" else "user").write(m["content"])

    # daily token limit
    if st.session_state.spent >= MAX_TOKENS_PER_USER:
        st.warning("Daily token budget hit.")
        st.stop()

    prompt = st.chat_input("Say something…")
    if prompt:
        hist.append({"role":"user","content":prompt})
        try:
            resp   = OPENAI.chat.completions.create(
                       model="gpt-4o-mini", messages=hist, max_tokens=120)
            reply  = resp.choices[0].message.content
            usage  = resp.usage
            st.session_state.spent += usage.prompt_tokens + usage.completion_tokens
            hist.append({"role":"assistant","content":reply})
            st.chat_message("assistant").write(reply)

            # persist (optional)
            SUPABASE.table("messages").insert(
                {"user_id": user["id"], "companion_id": cid,
                 "role":"user","content":prompt}).execute()
            SUPABASE.table("messages").insert(
                {"user_id": user["id"], "companion_id": cid,
                 "role":"assistant","content":reply}).execute()

        except RateLimitError:
            st.warning("OpenAI rate‑limit; wait a moment.")
        except OpenAIError as e:
            st.error(str(e))

# ───────────────────────── COLLECTION ─────────────────────────────
elif page == "My Collection":
    st.header("My BONDIGO Collection")
    if not collection:
        st.info("Nothing minted yet.")
    for cid in sorted(collection):
        c   = CID2COMPANION[cid]
        clr = CLR[c["rarity"]]
        col1,col2 = st.columns([1,5])
        col1.image(c.get("photo", PLACEHOLDER), width=80)
        col2.markdown(
            f"<span style='background:{clr};padding:2px 6px;border-radius:4px;"
            f"font-size:0.75rem'>{c['rarity']}</span> **{c['name']}**  \n"
            f"<span style='font-size:0.85rem'>{c['bio']}</span>",
            unsafe_allow_html=True)
