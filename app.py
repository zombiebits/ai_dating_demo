# app.py

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
SB = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
OA = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

# ─── Persist the user JWT across reruns ─────────────────────────────
if "user_jwt" in st.session_state:
    SB.postgrest.headers["Authorization"] = f"Bearer {st.session_state.user_jwt}"

# ─────────────────── CONSTANTS ─────────────────────────────────────
MAX_TOKENS    = 10_000
DAILY_AIRDROP = 150
COST          = {"Common": 50, "Rare": 200, "Legendary": 700}

PLACEHOLDER   = "assets/placeholder.png"
LOGO          = "assets/bondigo_banner.png"
TAGLINE       = "Talk the Lingo · Master the Bond · Dominate the Game"
CLR           = {"Common":"#bbb","Rare":"#57C7FF","Legendary":"#FFAA33"}

COMPANIONS    = json.load(open("companions.json", encoding="utf-8-sig"))
CID2COMP      = {c["id"]: c for c in COMPANIONS}


# ─────────────────── DATABASE HELPERS ──────────────────────────────
def profile_upsert(auth_uid: str, username: str) -> dict:
    tbl = SB.table("users")
    rows = tbl.select("*").eq("auth_uid", auth_uid).execute().data
    if rows:
        user = rows[0]
        if user["username"] != username:
            try:
                user = (
                    tbl.update({"username": username})
                       .eq("auth_uid", auth_uid)
                       .execute()
                       .data[0]
                )
            except APIError:
                pass
    else:
        try:
            user = (
                tbl.insert({
                    "id":         auth_uid,
                    "auth_uid":   auth_uid,
                    "username":   username,
                    "tokens":     1000
                })
                .execute()
                .data[0]
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
            .execute()
            .data[0]
        )
    return user

def collection_set(user_id: str) -> set[str]:
    rows = (
        SB.table("collection")
          .select("companion_id")
          .eq("user_id", user_id)
          .execute()
          .data
    )
    return {r["companion_id"] for r in rows}

def buy(user: dict, comp: dict):
    price = COST[comp.get("rarity","Common")]
    if price > user["tokens"]:
        return False, "Not enough 💎"
    owned = (
        SB.table("collection")
          .select("companion_id")
          .eq("user_id", user["id"])
          .eq("companion_id", comp["id"])
          .execute()
          .data
    )
    if owned:
        return False, "Already owned"

    # debit & mint
    SB.table("users")\
      .update({"tokens": user["tokens"] - price})\
      .eq("id", user["id"])\
      .execute()
    SB.table("collection").insert({
        "user_id":      user["id"],
        "companion_id": comp["id"]
    }).execute()

    return True, profile_upsert(user["auth_uid"], user["username"])


# ─────────────────── STREAMLIT CONFIG ─────────────────────────────
st.set_page_config("BONDIGO", "🩷", layout="centered")
st.markdown("""
<style>
[data-testid="stSidebar"] div[role="radiogroup"] label {
  font-size:1.25rem; line-height:1.5rem; padding:8px 0 8px 4px;
}
.match-name { font-size:1.2rem; font-weight:700; }
.match-bio  { font-size:1.0rem; line-height:1.5; }
</style>
""", unsafe_allow_html=True)


# ─────────────────── LOGIN / SIGN‑UP FORM ──────────────────────────
if "user" not in st.session_state:
    st.title("🔐 Sign in / Sign up to **BONDIGO**")

    with st.form("login_form"):
        mode  = st.radio("Choose", ["Sign in","Sign up"], horizontal=True)
        uname = st.text_input("Username", max_chars=20)
        pwd   = st.text_input("Password", type="password")
        go    = st.form_submit_button("Go ➜")

    if not go:
        st.stop()
    if not uname or not pwd:
        st.warning("Fill both fields."); st.stop()

    if mode == "Sign up":
        conflict = SB.table("users").select("id")\
                     .eq("username", uname).execute().data
        if conflict:
            st.error("Username already taken."); st.stop()

    email = f"{uname.lower()}@bondigo.local"
    try:
        if mode == "Sign up":
            SB.auth.sign_up({"email": email, "password": pwd})
        sess = SB.auth.sign_in_with_password({
            "email": email, "password": pwd
        })
    except Exception as e:
        st.error(f"Auth error: {e}"); st.stop()

    # ── capture and persist the real JWT ────────────────────────────
    token = None
    if hasattr(sess, "session") and sess.session:
        token = sess.session.access_token
    elif hasattr(sess, "access_token"):
        token = sess.access_token

    if not token:
        st.error("Could not find access token."); st.stop()

    st.session_state.user_jwt = token
    # immediately inject for the remainder of this run
    SB.postgrest.headers["Authorization"] = f"Bearer {token}"

    # upsert wallet & bootstrap
    try:
        st.session_state.user = profile_upsert(sess.user.id, uname)
    except ValueError:
        st.error("Username conflict; pick another."); SB.auth.sign_out(); st.stop()

    st.session_state.col     = collection_set(st.session_state.user["id"])
    st.session_state.hist    = {}
    st.session_state.spent   = 0
    st.session_state.matches = []

    # rerun now that state is set
    raise RerunException(rerun_data=None)


# ─────────────────── USER CONTEXT ─────────────────────────────────
user   = st.session_state.user
colset = st.session_state.col

# HEADER & NAV
if Path(LOGO).is_file():
    st.image(LOGO, width=380)
    st.markdown(
      f"<p style='text-align:center;margin-top:-2px; "
      f"font-size:1.05rem;color:#FFC8D8'>{TAGLINE}</p>",
      unsafe_allow_html=True
    )
st.markdown(f"**Wallet:** `{user['tokens']} 💎`")

tabs = ["Find matches","Chat","My Collection"]
page = st.sidebar.radio("Navigation", tabs, key="nav")


# ─────────────────── FIND MATCHES ────────────────────────────────
if page == "Find matches":
    hobby = st.selectbox("Pick a hobby",
        ["space","foodie","gaming","music","art","sports","reading",
         "travel","gardening","coding"])
    trait = st.selectbox("Pick a trait",
        ["curious","adventurous","night‑owl","chill","analytical",
         "energetic","humorous","kind","bold","creative"])
    vibe  = st.selectbox("Pick a vibe",
        ["witty","caring","mysterious","romantic","sarcastic",
         "intellectual","playful","stoic","optimistic","pragmatic"])
    scene = st.selectbox("Pick a scene",
        ["beach","forest","cafe","space‑station","cyberpunk‑city",
         "medieval‑castle","mountain","underwater","neon‑disco",
         "cozy‑library"])

    if st.button("Show matches"):
        st.session_state.matches = (
            [c for c in COMPANIONS
               if all(t in c["tags"] for t in [hobby,trait,vibe,scene])]
            or random.sample(COMPANIONS, 5)
        )

    for c in st.session_state.matches:
        rarity = c.get("rarity","Common")
        clr    = CLR[rarity]
        cols = st.columns([1,3,2])
        cols[0].image(c.get("photo",PLACEHOLDER), width=90)
        cols[1].markdown(
          f"<span style='background:{clr};padding:2px 6px;"
          f"border-radius:4px;font-size:0.75rem'>{rarity}</span> "
          f"**{c['name']}** • {COST[rarity]} 💎  \n"
          f"<span class='match-bio'>{c['bio']}</span>",
          unsafe_allow_html=True
        )
        if cols[2].button("💖 Bond", key=f"bond-{c['id']}"):
            ok, new_user = buy(user, c)
            if ok:
                st.session_state.user = new_user
                colset.add(c["id"])
                st.success("Bonded!")
            else:
                st.warning(new_user)
            st.stop()


# ─────────────────── CHAT ────────────────────────────────────────
elif page == "Chat":
    if not colset:
        st.info("Bond with a companion first."); st.stop()

    names = [CID2COMP[x]["name"] for x in colset]
    sel   = st.selectbox("Choose companion", names)
    cid   = next(k for k,v in CID2COMP.items() if v["name"] == sel)

    if cid not in st.session_state.hist:
        rows = (
          SB.table("messages")
            .select("role,content,created_at")
            .eq("user_id", user["id"])
            .eq("companion_id", cid)
            .order("created_at", {"ascending": True})
            .execute()
            .data
        )
        base = [{
          "role":    "system",
          "content": f"You are {CID2COMP[cid]['name']}. {CID2COMP[cid]['bio']} Speak in first person, PG‑13."
        }]
        st.session_state.hist[cid] = base + [
          {"role": r["role"], "content": r["content"]} for r in rows
        ]

    hist = st.session_state.hist[cid]

    st.image(CID2COMP[cid].get("photo",PLACEHOLDER), width=180)
    st.subheader(f"Chatting with **{CID2COMP[cid]['name']}**")

    if st.button("🗑️ Clear history"):
        st.session_state.hist[cid] = hist[:1]
        SB.table("messages")\
          .delete()\
          .eq("user_id", user["id"])\
          .eq("companion_id", cid)\
          .execute()
        st.success("Chat history cleared."); st.stop()

    for msg in hist[1:]:
        st.chat_message("assistant" if msg["role"]=="assistant" else "user")\
          .write(msg["content"])

    if st.session_state.spent >= MAX_TOKENS:
        st.warning("Daily token budget hit."); st.stop()

    user_input = st.chat_input("Say something…")
    if user_input:
        hist.append({"role":"user","content":user_input})
        try:
            resp = OA.chat.completions.create(
                model="gpt-4o-mini", messages=hist, max_tokens=120
            )
            reply = resp.choices[0].message.content
            usage = resp.usage
            st.session_state.spent += usage.prompt_tokens + usage.completion_tokens
            hist.append({"role":"assistant","content":reply})
            st.chat_message("assistant").write(reply)

            SB.table("messages").insert({
                "user_id":      user["id"],
                "companion_id": cid,
                "role":         "user",
                "content":      user_input
            }).execute()
            SB.table("messages").insert({
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
    if not colset:
        st.info("No Bonds yet.")
    for cid in sorted(colset):
        c   = CID2COMP[cid]
        rar = c.get("rarity","Common")
        clr = CLR[rar]
        col1, col2 = st.columns([1,5])
        col1.image(c.get("photo",PLACEHOLDER), width=80)
        col2.markdown(
          f"<span style='background:{clr};padding:2px 6px;border-radius:4px;"
          f"font-size:0.75rem'>{rar}</span> **{c['name']}**  \n"
          f"<span style='font-size:0.85rem'>{c['bio']}</span>",
          unsafe_allow_html=True
        )
