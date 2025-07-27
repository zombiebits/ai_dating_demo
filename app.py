import os, json, random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import streamlit as st
from streamlit.runtime.scriptrunner import RerunException
from openai import OpenAI, OpenAIError, RateLimitError
from dotenv import load_dotenv
from supabase import create_client
from postgrest.exceptions import APIError

# ─────────────────── ENVIRONMENT & CLIENTS ─────────────────────────
load_dotenv()
SB  = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
SRS = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
OA  = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

# Persist JWT for RLS reads/writes
if "user_jwt" in st.session_state:
    SB.postgrest.headers["Authorization"] = f"Bearer {st.session_state.user_jwt}"

# ─────────────────── STREAMLIT CONFIG ──────────────────────────────
st.set_page_config(
    page_title="BONDIGO",
    page_icon="🩷",
    layout="centered",
    menu_items={"Get Help": None, "Report a bug": None, "About": None},
)
# Hide default Streamlit header, footer and sidebar
st.markdown(
    """
    <style>
      #MainMenu, footer, [data-testid="stSidebar"] {
        visibility: hidden;
        height: 0;
      }
    </style>
    """,
    unsafe_allow_html=True,
)

# ─────────────────── CONSTANTS ─────────────────────────────────────
MAX_TOKENS    = 10_000
DAILY_AIRDROP = 150
COST          = {"Common": 50, "Rare": 200, "Legendary": 700}

PLACEHOLDER = "assets/placeholder.png"
LOGO        = "assets/bondigo_banner.png"
TAGLINE     = "Talk the Lingo · Master the Bond · Dominate the Game"
CLR         = {"Common":"#bbb","Rare":"#57C7FF","Legendary":"#FFAA33"}

COMPANIONS = json.load(open("companions.json", encoding="utf-8-sig"))
CID2COMP   = {c["id"]: c for c in COMPANIONS}


# ─────────────────── DATABASE HELPERS ──────────────────────────────
def profile_upsert(auth_uid: str, username: str) -> dict:
    tbl = SRS.table("users")
    rows = tbl.select("*").eq("auth_uid", auth_uid).execute().data
    if rows:
        user = rows[0]
        if user["username"] != username:
            try:
                user = (
                    tbl.update({"username": username})
                       .eq("auth_uid", auth_uid)
                       .execute().data[0]
                )
            except APIError:
                pass
    else:
        try:
            user = (
                tbl.insert({
                    "id":       auth_uid,
                    "auth_uid": auth_uid,
                    "username": username,
                    "tokens":   1000
                }).execute().data[0]
            )
        except APIError as e:
            if "duplicate key" in str(e):
                raise ValueError("username_taken")
            raise

    # daily airdrop
    last = user["last_airdrop"] or user["created_at"]
    last = datetime.fromisoformat(last.replace("Z", "+00:00"))
    if datetime.now(timezone.utc) - last >= timedelta(hours=24):
        user = (
            tbl.update({
                "tokens":       user["tokens"] + DAILY_AIRDROP,
                "last_airdrop": datetime.now(timezone.utc).isoformat()
            })
            .eq("auth_uid", auth_uid)
            .execute().data[0]
        )
    return user

def collection_set(user_id: str) -> set[str]:
    rows = (
        SRS.table("collection")
           .select("companion_id")
           .eq("user_id", user_id)
           .execute().data
    )
    return {r["companion_id"] for r in rows}

def buy(user: dict, comp: dict):
    price = COST[comp.get("rarity","Common")]
    if price > user["tokens"]:
        return False, "Not enough 💎"
    owned = (
        SRS.table("collection")
           .select("companion_id")
           .eq("user_id", user["id"])
           .eq("companion_id", comp["id"])
           .execute().data
    )
    if owned:
        return False, "Already owned"
    # debit & mint
    SRS.table("users").update({"tokens":user["tokens"]-price}).eq("id",user["id"]).execute()
    SRS.table("collection").insert({
        "user_id":      user["id"],
        "companion_id": comp["id"]
    }).execute()
    # resync
    return True, profile_upsert(user["auth_uid"], user["username"])


# ─────────────────── LOGIN / SIGN‑UP ───────────────────────────────
if "user" not in st.session_state:
    if Path(LOGO).is_file():
        st.image(LOGO, width=380)
        st.markdown(
            f"<p style='text-align:center;margin-top:-2px;"
            f"font-size:1.05rem;color:#FFC8D8'>{TAGLINE}</p>",
            unsafe_allow_html=True,
        )

    st.title("🔐 Sign in / Sign up to **BONDIGO**")
    mode  = st.radio("Choose", ["Sign in","Sign up"], horizontal=True)
    uname = st.text_input("Username", max_chars=20)
    pwd   = st.text_input("Password", type="password")
    if not st.button("Go ➜"):
        st.stop()

    if not uname or not pwd:
        st.warning("Fill both fields."); st.stop()

    if mode == "Sign up":
        conflict = SRS.table("users").select("id").eq("username", uname).execute().data
        if conflict:
            st.error("That username’s taken."); st.stop()

    email = f"{uname.lower()}@bondigo.local"
    try:
        if mode == "Sign up":
            SB.auth.sign_up({"email":email,"password":pwd})
        sess = SB.auth.sign_in_with_password({"email":email,"password":pwd})
    except Exception as e:
        st.error(f"Auth error: {e}"); st.stop()

    token = getattr(sess.session, "access_token", None) if hasattr(sess, "session") else getattr(sess, "access_token", None)
    if not token:
        st.error("Couldn’t find access token."); st.stop()

    st.session_state.user_jwt = token
    SB.postgrest.headers["Authorization"] = f"Bearer {token}"

    try:
        st.session_state.user = profile_upsert(sess.user.id, uname)
    except ValueError:
        st.error("Username conflict; try another."); SB.auth.sign_out(); st.stop()

    st.session_state.spent   = 0
    st.session_state.matches = []
    st.session_state.hist    = {}

    raise RerunException(rerun_data=None)


# ─────────────────── STATE BOOTSTRAP ──────────────────────────────
st.session_state.setdefault("spent",   0)
st.session_state.setdefault("matches", [])
st.session_state.setdefault("hist",    {})

user   = st.session_state.user
colset = collection_set(user["id"])


# ─────────────────── HEADER & BADGES ─────────────────────────────
if Path(LOGO).is_file():
    st.image(LOGO, width=380)
    st.markdown(
        f"<p style='text-align:center;margin-top:-2px;"
        f"font-size:1.05rem;color:#FFC8D8'>{TAGLINE}</p>",
        unsafe_allow_html=True,
    )

st.markdown(
    # username badge
    f"<span style='background:#f93656; padding:6px 12px;"
    f"border-radius:8px; display:inline-block; font-size:1.25rem;"
    f"color:#000; font-weight:600; margin-right:8px;'>"
    f"{user['username']}'s Wallet</span>"
    # token badge
    f"<span style='background:#000; color:#57C784; padding:6px 12px;"
    f"border-radius:8px; display:inline-block; font-size:1.25rem;'>"
    f"{user['tokens']} 💎</span>",
    unsafe_allow_html=True,
)


# ─────────────────── ON‑PAGE NAVIGATION ─────────────────────────
page = st.radio(
    "", 
    ["Find matches","Chat","My Collection"],
    index=0 if "page" not in st.session_state else 
          ["Find matches","Chat","My Collection"].index(st.session_state.page),
    horizontal=True,
)
st.session_state.page = page


# ─────────────────── FIND MATCHES ────────────────────────────────
if page == "Find matches":
    st.image("assets/bondcosts.png", width=380)  # your legend graphic
    hobby = st.selectbox("Pick a hobby",   [...])
    trait = st.selectbox("Pick a trait",   [...])
    vibe  = st.selectbox("Pick a vibe",    [...])
    scene = st.selectbox("Pick a scene",   [...])
    if st.button("Show matches"):
        st.session_state.matches = (
           [c for c in COMPANIONS if all(t in c["tags"] for t in [hobby,trait,vibe,scene])]
           or random.sample(COMPANIONS, 5)
        )
    for c in st.session_state.matches:
        rarity, clr = c.get("rarity","Common"), CLR[c.get("rarity","Common")]
        c1,c2,c3 = st.columns([1,3,2])
        c1.image(c.get("photo",PLACEHOLDER),width=90)
        c2.markdown(
          f"<span style='background:{clr}; color:black; padding:2px 6px;"
          f"border-radius:4px; font-size:0.75rem'>{rarity}</span> "
          f"**{c['name']}** • {COST[rarity]} 💎  \n"
          f"<span class='match-bio'>{c['bio']}</span>",
          unsafe_allow_html=True,
        )
        if c3.button("💖 Bond", key=f"bond-{c['id']}"):
            ok,new = buy(user,c)
            if ok:
                st.session_state.user = new
                st.success("Bonded!")
            else:
                st.warning(new)
            st.stop()

# ─────────────────── CHAT ────────────────────────────────────────
elif page == "Chat":
    if not colset:
        st.info("Bond first!"); st.stop()
    sel = st.selectbox("Choose companion", [CID2COMP[i]["name"] for i in colset])
    cid = next(k for k,v in CID2COMP.items() if v["name"]==sel)
    if cid not in st.session_state.hist:
        rows = ( SRS.table("messages")
                   .select("role,content,created_at")
                   .eq("user_id",user["id"])
                   .eq("companion_id",cid)
                   .order("created_at")
                   .execute().data )
        base = [{"role":"system","content":
                 f"You are {CID2COMP[cid]['name']}. {CID2COMP[cid]['bio']} Speak in first person, PG‑13."}]
        st.session_state.hist[cid] = base + [{"role":r["role"],"content":r["content"]} for r in rows]
    hist = st.session_state.hist[cid]
    st.image(CID2COMP[cid].get("photo",PLACEHOLDER),width=180)
    st.subheader(f"Chatting with **{CID2COMP[cid]['name']}**")
    if st.button("🗑️ Clear history"):
        st.session_state.hist[cid] = hist[:1]
        SRS.table("messages").delete().eq("user_id",user["id"]).eq("companion_id",cid).execute()
        st.success("Chat cleared."); st.stop()
    for msg in hist[1:]:
        st.chat_message("assistant" if msg["role"]=="assistant" else "user").write(msg["content"])
    if st.session_state.spent >= MAX_TOKENS:
        st.warning("Daily token budget hit."); st.stop()
    ui = st.chat_input("Say something…")
    if ui:
        hist.append({"role":"user","content":ui})
        try:
            resp  = OA.chat.completions.create(model="gpt-4o-mini",messages=hist,max_tokens=120)
            reply=resp.choices[0].message.content
            st.session_state.spent += resp.usage.prompt_tokens+resp.usage.completion_tokens
            hist.append({"role":"assistant","content":reply})
            st.chat_message("assistant").write(reply)
            SRS.table("messages").insert({"user_id":user["id"],"companion_id":cid,"role":"user","content":ui}).execute()
            SRS.table("messages").insert({"user_id":user["id"],"companion_id":cid,"role":"assistant","content":reply}).execute()
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
        c = CID2COMP[cid]
        rar,clr = c.get("rarity","Common"),CLR[c.get("rarity","Common")]
        a,b = st.columns([1,5])
        a.image(c.get("photo",PLACEHOLDER),width=80)
        b.markdown(
          f"<span style='background:{clr}; color:black; padding:2px 6px;"
          f"border-radius:4px; font-size:0.75rem'>{rar}</span> "
          f"**{c['name']}**\n<span style='font-size:0.85rem'>{c['bio']}</span>",
          unsafe_allow_html=True,
        )
