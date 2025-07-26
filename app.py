import json, os, random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt                           # PyJWT  (supabase‑py relies on it too)
import streamlit as st
from dotenv import load_dotenv
from openai import OpenAI, OpenAIError, RateLimitError
from supabase import create_client

# ─────────────────────────── ENV / CONST ────────────────────────────
load_dotenv()

sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
openai_client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

MAX_TOKENS      = 10_000
DAILY_AIRDROP   = 150
COST            = {"Common": 50, "Rare": 200, "Legendary": 700}

PLACEHOLDER = "assets/placeholder.png"
LOGO        = "assets/bondigo_banner.png"
TAGLINE     = "Talk the Lingo · Master the Bond · Dominate the Game."
CLR         = {"Common": "#bbb", "Rare": "#57C7FF", "Legendary": "#FFAA33"}

COMPANIONS     = json.load(open("companions.json", encoding="utf-8-sig"))
COMPANION_MAP  = {c["id"]: c for c in COMPANIONS}

# ───────────────────── helper – wallet logic ────────────────────────
def get_or_create_wallet(uid: str):
    """
    id == auth.uid – ensures every Supabase auth user has a wallet row.
    Also performs the 24 h airdrop check.
    """
    row = sb.table("users").select("*").eq("id", uid).execute().data
    if row:
        user = row[0]
    else:
        # first time -> give starter 1000 💎
        user = sb.table("users").insert({"id": uid, "tokens": 1000}).execute().data[0]

    last_ts = user.get("last_airdrop") or user["created_at"]
    last_ts = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
    if datetime.now(timezone.utc) - last_ts >= timedelta(hours=24):
        user = (
            sb.table("users")
            .update(
                {
                    "tokens": user["tokens"] + DAILY_AIRDROP,
                    "last_airdrop": datetime.now(timezone.utc).isoformat(),
                }
            )
            .eq("id", uid)
            .execute()
            .data[0]
        )
    return user


def buy_companion(user, comp):
    cost = COST[comp.get("rarity", "Common")]
    if user["tokens"] < cost:
        return False, "Not enough BONDIGO 💎"

    already = (
        sb.table("collection")
        .select("companion_id")
        .eq("user_id", user["id"])
        .eq("companion_id", comp["id"])
        .execute()
        .data
    )
    if already:
        return False, "Already owned"

    # atomic-ish: debit first then insert
    sb.table("users").update({"tokens": user["tokens"] - cost}).eq("id", user["id"]).execute()
    sb.table("collection").insert({"user_id": user["id"], "companion_id": comp["id"]}).execute()
    return True, get_or_create_wallet(user["id"])


def owned_set(uid: str):
    rows = sb.table("collection").select("companion_id").eq("user_id", uid).execute().data
    return {r["companion_id"] for r in rows}


# ────────────────────────── LOGIN FLOW ──────────────────────────────
st.set_page_config("BONDIGO", page_icon="🩷", layout="centered")

# 1️⃣  Catch magic‑link redirect (?access_token & refresh_token)
q = st.experimental_get_query_params()
if "access_token" in q and "uid" not in st.session_state:
    session = sb.auth.set_session(q["access_token"][0], q["refresh_token"][0])
    st.session_state.uid = session.user.id
    # clean the URL (replace query‑params with '/')
    st.experimental_set_query_params()

# 2️⃣  If not logged‑in show email box
if "uid" not in st.session_state:
    st.title("📧  Login to **BONDIGO**")
    email = st.text_input("Email")
    if st.button("Send magic link") and email:
        sb.auth.sign_in_with_otp(
            {
                "email": email,
                "options": {
                    "email_redirect_to": os.environ["SITE_URL"]  # set SITE_URL="https://yourapp.streamlit.app"
                },
            }
        )
        st.success("Check your inbox!")
    st.stop()

# 3️⃣  Have uid  →  get/create wallet & state
if "user" not in st.session_state:
    st.session_state.user        = get_or_create_wallet(st.session_state.uid)
    st.session_state.collection  = owned_set(st.session_state.uid)
    st.session_state.histories   = {}
    st.session_state.spent       = 0
    st.session_state.matches     = []

user = st.session_state.user

# ──────────────────────────── HEADER ────────────────────────────────
if Path(LOGO).is_file():
    st.image(LOGO, width=380)
    st.markdown(
        f"<p style='text-align:center;margin-top:-2px;font-size:1.1rem;color:#FFC8D8'>{TAGLINE}</p>",
        unsafe_allow_html=True,
    )

st.markdown(f"**Wallet:** `{user['tokens']} 💎`")

# ────────────────────────── NAVIGATION ─────────────────────────────
page = st.sidebar.radio("Navigation", ["Find matches", "Chat", "My Collection"], key="nav")
st.session_state.nav = page

# ──────────────────────── FIND MATCHES ─────────────────────────────
if page == "Find matches":
    hobby  = st.selectbox("Pick a hobby",
              ["space","foodie","gaming","music","art","sports","reading","travel","gardening","coding"])
    trait  = st.selectbox("Pick a trait",
              ["curious","adventurous","night‑owl","chill","analytical","energetic",
               "humorous","kind","bold","creative"])
    vibe   = st.selectbox("Pick a vibe",
              ["witty","caring","mysterious","romantic","sarcastic","intellectual",
               "playful","stoic","optimistic","pragmatic"])
    scene  = st.selectbox("Pick a scene",
              ["beach","forest","cafe","space‑station","cyberpunk‑city","medieval‑castle",
               "mountain","underwater","neon‑disco","cozy‑library"])

    if st.button("Show matches"):
        st.session_state.matches = (
            [c for c in COMPANIONS if all(t in c["tags"] for t in [hobby,trait,vibe,scene])]
            or random.sample(COMPANIONS, 5)
        )

    for comp in st.session_state.matches:
        badge = comp.get("rarity", "Common")
        cost  = COST[badge]
        c1,c2,c3 = st.columns([1,4,2])

        with c1:
            p = comp.get("photo", PLACEHOLDER)
            st.image(p if Path(p).is_file() else PLACEHOLDER, width=90)

        with c2:
            st.markdown(
                f"<span style='background:{CLR[badge]};padding:2px 6px;border-radius:4px;"
                f"font-size:0.75rem'>{badge}</span> **{comp['name']}**  "
                f"• {cost} 💎<br><span style='font-size:0.85rem'>{comp['bio']}</span>",
                unsafe_allow_html=True,
            )

        with c3:
            if comp["id"] in st.session_state.collection:
                st.button("Owned ✓", key=f"owned-{comp['id']}", disabled=True)
            else:
                if st.button("💖 Mint", key=f"mint-{comp['id']}"):
                    ok, res = buy_companion(user, comp)
                    if ok:
                        st.session_state.user = res
                        st.session_state.collection.add(comp["id"])
                        st.success(f"{comp['name']} added!")
                    else:
                        st.warning(res)

# ───────────────────────────── CHAT ───────────────────────────────
elif page == "Chat":
    if not st.session_state.collection:
        st.info("Mint a companion first.")
        st.stop()

    # choose companion
    cur_cid = st.session_state.get("chat_id") or next(iter(st.session_state.collection))
    names   = [COMPANION_MAP[c]["name"] for c in st.session_state.collection]
    sel     = st.selectbox("Companion", names, index=names.index(COMPANION_MAP[cur_cid]["name"]))
    cid     = next(cid for cid, d in COMPANION_MAP.items() if d["name"] == sel)
    st.session_state.chat_id = cid

    # ensure history
    if cid not in st.session_state.histories:
        comp = COMPANION_MAP[cid]
        st.session_state.histories[cid] = [{
            "role":"system",
            "content":f"You are {comp['name']}. {comp['bio']} Speak in first person, friendly & flirty but PG‑13."
        }]

    comp = COMPANION_MAP[cid]
    st.image(comp.get("photo", PLACEHOLDER), width=180)
    st.subheader(f"Chatting with **{comp['name']}**")
    if st.button("🗑️ Clear history"):
        st.session_state.histories[cid] = st.session_state.histories[cid][:1]

    # show chat
    for m in st.session_state.histories[cid][1:]:
        st.chat_message("assistant" if m["role"]=="assistant" else "user").write(m["content"])

    # input
    if st.session_state.spent >= MAX_TOKENS:
        st.warning("Daily token budget reached—try tomorrow.")
        st.stop()

    msg = st.chat_input("Say something…")
    if msg:
        msgs = st.session_state.histories[cid]
        msgs.append({"role":"user","content":msg})

        try:
            resp  = openai_client.chat.completions.create(
                      model="gpt-4o-mini", messages=msgs, max_tokens=120)
            reply = resp.choices[0].message.content
            usage = resp.usage
            st.session_state.spent += usage.prompt_tokens + usage.completion_tokens
            msgs.append({"role":"assistant","content":reply})
            st.chat_message("assistant").write(reply)

            # optional persistence
            sb.table("messages").insert(
                {"user_id": user["id"], "companion_id": cid, "role":"user", "content":msg}
            ).execute()
            sb.table("messages").insert(
                {"user_id": user["id"], "companion_id": cid, "role":"assistant", "content":reply}
            ).execute()

        except RateLimitError:
            st.error("OpenAI rate‑limit—please wait.")
        except OpenAIError as e:
            st.error(str(e))

# ──────────────────────── COLLECTION ────────────────────────────
else:
    st.header("My BONDIGO Collection")
    if not st.session_state.collection:
        st.info("Nothing minted yet.")
    for cid in sorted(st.session_state.collection):
        comp = COMPANION_MAP[cid]
        c1,c2 = st.columns([1,5])
        c1.image(comp.get("photo", PLACEHOLDER), width=80)
        badge = comp["rarity"]; clr = CLR[badge]
        c2.markdown(
            f"<span style='background:{clr};padding:2px 6px;border-radius:4px;"
            f"font-size:0.75rem'>{badge}</span> **{comp['name']}**  "
            f"<br><span style='font-size:0.85rem'>{comp['bio']}</span>",
            unsafe_allow_html=True,
        )
