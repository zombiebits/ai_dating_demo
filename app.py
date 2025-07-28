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
SB  = create_client(os.environ["SUPABASE_URL"],   os.environ["SUPABASE_KEY"])
SRS = create_client(os.environ["SUPABASE_URL"],   os.environ["SUPABASE_SERVICE_KEY"])
OA  = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
if "user_jwt" in st.session_state:
    SB.postgrest.headers["Authorization"] = f"Bearer {st.session_state.user_jwt}"

# ────────── OPTIONAL EMAIL‑CONFIRM BANNER ─────────────
params = st.query_params
if params.get("confirmed", [""])[0] == "true":
    st.success("✅ Your email has been confirmed! Please sign in below.")

# ─────────────────── STREAMLIT CONFIG ──────────────────────────────
st.set_page_config(
    page_title="BONDIGO",
    page_icon="🩷",
    layout="centered",
    menu_items={"Get Help": None, "Report a bug": None, "About": None},
)
st.markdown("""
    <style>
      #MainMenu, header, footer, [data-testid="stSidebar"] {
        visibility: hidden; height: 0;
      }
    </style>
""", unsafe_allow_html=True)

# ─────────────────── CONSTANTS & DATA ─────────────────────────────
MAX_TOKENS    = 10_000
DAILY_AIRDROP = 150
COST          = {"Common": 50, "Rare": 200, "Legendary": 700}

PLACEHOLDER = "assets/placeholder.png"
LOGO        = "assets/bondigo_banner.png"
TAGLINE     = "Talk the Lingo · Master the Bond · Dominate the Game"
CLR         = {"Common":"#bbb","Rare":"#57C7FF","Legendary":"#FFAA33"}

COMPANIONS = json.load(open("companions.json", encoding="utf-8-sig"))
CID2COMP   = {c["id"]: c for c in COMPANIONS}

# ─────────────────── HELPERS ───────────────────────────────────────
def profile_upsert(auth_uid: str, username: str) -> dict:
    """Fetch user; if first time, award initial tokens & possibly daily airdrop."""
    tbl = SRS.table("users")
    rows = tbl.select("*").eq("auth_uid", auth_uid).execute().data
    if rows:
        user = rows[0]
        # daily airdrop check
        last = user["last_airdrop"] or user["created_at"]
        last = datetime.fromisoformat(last.replace("Z","+00:00"))
        if datetime.now(timezone.utc) - last >= timedelta(hours=24):
            user = tbl.update({
                "tokens":       user["tokens"] + DAILY_AIRDROP,
                "last_airdrop": datetime.now(timezone.utc).isoformat()
            }).eq("auth_uid", auth_uid).execute().data[0]
    else:
        # first‑time signup insertion
        user = tbl.insert({
            "id":          auth_uid,
            "auth_uid":    auth_uid,
            "username":    username,
            "tokens":      1000,
            "last_airdrop": None
        }).execute().data[0]
    return user

def collection_set(user_id: str) -> set[str]:
    rows = SRS.table("collection")\
              .select("companion_id")\
              .eq("user_id", user_id)\
              .execute().data
    return {r["companion_id"] for r in rows}

def buy(user: dict, comp: dict):
    price = COST[comp.get("rarity","Common")]
    if price > user["tokens"]:
        return False, "Not enough 💎"
    owned = SRS.table("collection")\
              .select("companion_id")\
              .eq("user_id", user["id"])\
              .eq("companion_id", comp["id"])\
              .execute().data
    if owned:
        return False, "Already owned"
    # debit tokens & record ownership
    SRS.table("users").update({"tokens": user["tokens"] - price})\
       .eq("id", user["id"]).execute()
    SRS.table("collection").insert({
        "user_id":      user["id"],
        "companion_id": comp["id"]
    }).execute()
    return True, profile_upsert(user["auth_uid"], user["username"])

# ─────────────────── CALLBACKS ────────────────────────────────────
def bond_and_chat(cid: str, comp: dict):
    ok, new_user = buy(st.session_state.user, comp)
    if ok:
        st.session_state.user     = new_user
        st.session_state.page     = "Chat"
        st.session_state.chat_cid = cid
        st.session_state.flash    = f"Bonded with {comp['name']}!"
    else:
        st.warning(new_user)

def goto_chat(cid: str):
    st.session_state.page     = "Chat"
    st.session_state.chat_cid = cid

# ─────────────────── LOGIN / SIGN‑UP ───────────────────────────────
if "user" not in st.session_state:
    # Logo & tagline
    if Path(LOGO).is_file():
        st.image(LOGO, width=380)
        st.markdown(
            f"<p style='text-align:center;margin-top:-2px;"
            f"font-size:1.05rem;color:#FFC8D8'>{TAGLINE}</p>",
            unsafe_allow_html=True,
        )

    st.title("🔐 Sign in / Sign up to **BONDIGO**")

    email = st.text_input("Email", key="login_email")
    mode  = st.radio("Choose", ["Sign in","Sign up"], horizontal=True, key="login_mode")
    uname = st.text_input("Username", max_chars=20, key="login_uname")
    pwd   = st.text_input("Password", type="password", key="login_pwd")

    if st.button("Go ➜", key="login_go"):
        # — Basic form validation —
        if not email or not uname or not pwd:
            st.warning("Fill all fields: email, username, and password.")
            st.stop()

        # — Lookup the invitee row —
        invite = (
            SRS.table("invitees")
               .select("email","claimed")
               .eq("email", email)
               .execute()
               .data
        )
        if not invite:
            st.error("🚧 You’re not on the invite list.")
            st.stop()

        # ─── SIGN UP FLOW ─────────────────────
        if mode == "Sign up":
            # block if already claimed
            if invite[0].get("claimed"):
                st.error("🚫 This email has already been used.")
                st.stop()

            try:
                # create auth user & send confirmation email
                SB.auth.sign_up({"email": email, "password": pwd})

                # record the user in your `users` table *now*,
                # so that sign-in can later fetch them
                user = profile_upsert(
                    auth_uid=SB.auth.get_user(SB.auth.session.access_token).user.id,
                    username=uname
                )

                # mark invite claimed
                SRS.table("invitees")\
                   .update({"claimed": True})\
                   .eq("email", email)\
                   .execute()

                st.success("✅ Check your inbox for the confirmation link!")
            except Exception as e:
                st.error(f"Sign‑up error: {e}")
            st.stop()

        # ─── SIGN IN FLOW ─────────────────────
        try:
            sess = SB.auth.sign_in_with_password({"email": email, "password": pwd})
        except Exception as e:
            st.error(f"Sign‑in error: {e}")
            st.stop()

        # ensure they clicked confirm
        user_meta = SB.auth.get_user(sess.session.access_token).user
        if not getattr(user_meta, "confirmed_at", None):
            st.error("📬 Please confirm your email before continuing.")
            st.stop()

        # ensure they actually signed up (row exists)
        rows = SRS.table("users")\
                  .select("*")\
                  .eq("auth_uid", user_meta.id)\
                  .execute().data
        if not rows:
            st.error("❌ No account found. Please Sign up first.")
            st.stop()

        # ensure username matches
        stored = rows[0]
        if stored["username"] != uname:
            st.error("❌ Username does not match your account.")
            st.stop()

        # award daily airdrop if due, grab fresh user
        user = profile_upsert(user_meta.id, uname)

        # set your session state
        token = sess.session.access_token
        st.session_state.user_jwt = token
        SB.postgrest.headers["Authorization"] = f"Bearer {token}"
        st.session_state.user     = user
        st.session_state.spent    = 0
        st.session_state.matches  = []
        st.session_state.hist     = {}
        st.session_state.page     = "Find matches"
        st.session_state.chat_cid = None
        st.session_state.flash    = None

        # finally, rerun into your main app
        raise RerunException(rerun_data=None)

    # before any click, nothing else runs
    st.stop()

# ─────────────────── ENSURE STATE KEYS ────────────────────────────
for k,v in {
    "spent":0, "matches":[], "hist":{},
    "page":"Find matches", "chat_cid":None, "flash":None
}.items():
    st.session_state.setdefault(k, v)

user   = st.session_state.user
colset = collection_set(user["id"])

# ─────────────────── HEADER & NAVIGATION ─────────────────────────
if Path(LOGO).is_file():
    st.image(LOGO, width=380)
    st.markdown(
        f"<p style='text-align:center;margin-top:-2px;"
        f"font-size:1.05rem;color:#FFC8D8'>{TAGLINE}</p>",
        unsafe_allow_html=True,
    )

st.markdown(
    f"<span style='background:#f93656;padding:6px 12px;border-radius:8px;"
    f"display:inline-block;font-size:1.25rem;color:#000;font-weight:600;"
    f"margin-right:8px;'>{user['username']}'s Wallet</span>"
    f"<span style='background:#000;color:#57C784;padding:6px 12px;"
    f"border-radius:8px;display:inline-block;font-size:1.25rem;'>"
    f"{user['tokens']} 💎</span>",
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
    hobby = st.selectbox("Pick a hobby", ["space","foodie","gaming","music","art","sports","reading","travel","gardening","coding"])
    trait = st.selectbox("Pick a trait", ["curious","adventurous","night‑owl","chill","analytical","energetic","humorous","kind","bold","creative"])
    vibe  = st.selectbox("Pick a vibe",  ["witty","caring","mysterious","romantic","sarcastic","intellectual","playful","stoic","optimistic","pragmatic"])
    scene = st.selectbox("Pick a scene", ["beach","forest","cafe","space‑station","cyberpunk‑city","medieval‑castle","mountain","underwater","neon‑disco","cozy‑library"])
    if st.button("Show matches"):
        st.session_state.matches = (
           [c for c in COMPANIONS if all(t in c["tags"] for t in (hobby, trait, vibe, scene))]
           or random.sample(COMPANIONS, 5)
        )

    for c in st.session_state.matches:
        rarity, clr = c.get("rarity","Common"), CLR[c.get("rarity","Common")]
        c1,c2,c3    = st.columns([1,5,2])
        c1.image(c.get("photo",PLACEHOLDER), width=90)
        c2.markdown(
          f"<span style='background:{clr};color:black;padding:2px 6px;"
          f"border-radius:4px;font-size:0.75rem'>{rarity}</span> "
          f"**{c['name']}** • {COST[rarity]} 💎\n"
          f"<span class='match-bio'>{c['bio']}</span>",
          unsafe_allow_html=True,
        )

        if c["id"] in colset:
            c3.button("💬 Chat", key=f"chat-{c['id']}", on_click=goto_chat, args=(c["id"],))
        else:
            c3.button("💖 Bond", key=f"bond-{c['id']}", on_click=bond_and_chat, args=(c["id"], c))

# ─────────────────── CHAT & COLLECTION remain unchanged ───────────────────────


# ─────────────────── CHAT ────────────────────────────────────────
elif page == "Chat":
    if st.session_state.flash:
        st.success(st.session_state.flash)
        st.session_state.flash = None

    if not colset:
        st.info("Bond first!"); st.stop()

    options = [CID2COMP[i]["name"] for i in colset]
    if st.session_state.chat_cid:
        default = CID2COMP[st.session_state.chat_cid]["name"]
        sel     = st.selectbox("Choose companion", options, index=options.index(default))
    else:
        sel     = st.selectbox("Choose companion", options)

    cid = next(k for k,v in CID2COMP.items() if v["name"] == sel)
    st.session_state.chat_cid = cid

    if cid not in st.session_state.hist:
        rows = (SRS.table("messages")
                  .select("role,content,created_at")
                  .eq("user_id", user["id"])
                  .eq("companion_id", cid)
                  .order("created_at")
                  .execute().data)
        base = [{"role":"system","content":
                 f"You are {CID2COMP[cid]['name']}. {CID2COMP[cid]['bio']} Speak PG‑13."}]
        st.session_state.hist[cid] = base + [{"role":r["role"],"content":r["content"]} for r in rows]

    hist = st.session_state.hist[cid]
    st.image(CID2COMP[cid].get("photo",PLACEHOLDER), width=180)
    st.subheader(f"Chatting with **{CID2COMP[cid]['name']}**")

    if st.button("🗑️ Clear history"):
        st.session_state.hist[cid] = hist[:1]
        SRS.table("messages")\
           .delete()\
           .eq("user_id", user["id"])\
           .eq("companion_id", cid)\
           .execute()
        st.success("Chat history cleared."); st.stop()

    for msg in hist[1:]:
        st.chat_message("assistant" if msg["role"]=="assistant" else "user").write(msg["content"])
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
