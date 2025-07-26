import json, os, random, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import streamlit as st
from openai import OpenAI, OpenAIError, RateLimitError
from dotenv import load_dotenv
from supabase import create_client

# ─────────────────────────── config ────────────────────────────
load_dotenv()
SUPABASE = create_client(os.environ["SUPABASE_URL"],
                         os.environ["SUPABASE_KEY"])

MAX_TOKENS_PER_USER = 10_000
DAILY_AIRDROP       = 150                      # 💎 / 24 h
COST                = {"Common": 50, "Rare": 200, "Legendary": 700}

PLACEHOLDER = "assets/placeholder.png"
LOGO        = "assets/bondigo_banner.png"
TAGLINE     = "Talk the Lingo · Master the Bond · Dominate the Game."
RARITY_CLR  = {"Common": "#bbb", "Rare": "#57C7FF", "Legendary": "#FFAA33"}

COMPANIONS     = json.load(open("companions.json", encoding="utf-8-sig"))
COMPANION_MAP  = {c["id"]: c for c in COMPANIONS}

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ────────────────────── helper: Supabase wrappers ──────────────
def sp_get_user(username: str):
    """Fetch (or create) a user row and handle daily airdrop."""
    res = SUPABASE.table("users").select("*").eq("username", username).execute()
    if res.data:
        user = res.data[0]
    else:                                                           # first visit
        user = SUPABASE.table("users").insert({
            "username": username,
            "tokens": 1000,
        }).execute().data[0]

    # daily airdrop
    last = user["last_airdrop"] or user["created_at"]
    last = datetime.fromisoformat(last.replace("Z", "+00:00"))
    if datetime.now(timezone.utc) - last >= timedelta(hours=24):
        user = SUPABASE.table("users").update({
            "tokens": user["tokens"] + DAILY_AIRDROP,
            "last_airdrop": datetime.now(timezone.utc).isoformat()
        }).eq("id", user["id"]).execute().data[0]
    return user

def sp_buy(user, companion):
    """Attempt purchase, return (success:bool, updated_user or reason)."""
    cost = COST[companion["rarity"]]
    if cost > user["tokens"]:
        return False, "Not enough BONDIGO 💎"

    # already owned?
    owned = SUPABASE.table("collection").select("companion_id") \
             .eq("user_id", user["id"]).eq("companion_id", companion["id"]).execute()
    if owned.data:
        return False, "Already in your collection"

    # atomic update
    SUPABASE.table("users").update({"tokens": user["tokens"]-cost}) \
             .eq("id", user["id"]).execute()
    SUPABASE.table("collection").insert({
        "user_id": user["id"], "companion_id": companion["id"]
    }).execute()
    # return fresh user row
    return True, sp_get_user(user["username"])

def sp_user_collection(user_id):
    rows = SUPABASE.table("collection").select("companion_id") \
           .eq("user_id", user_id).execute().data
    return {r["companion_id"] for r in rows}

# ───────────────────────── Streamlit state ─────────────────────
if "user" not in st.session_state:
    # first page load → ask for username
    st.set_page_config(page_title="BONDIGO")
    st.title("👋 Welcome to **BONDIGO**")
    username = st.text_input("Pick a unique username to begin", max_chars=20)
    if st.button("Enter ➜") and username.strip():
        st.session_state.user = sp_get_user(username.strip())
        st.session_state.collection = sp_user_collection(st.session_state.user["id"])
        st.experimental_rerun()
    st.stop()

user = st.session_state.user            # shorthand

# ──────────────────────────── UI header ────────────────────────
if Path(LOGO).is_file():
    st.image(LOGO, width=380)
    st.markdown(f"<p style='text-align:center; margin-top:-2px; "
                f"font-size:1.1rem; color:#FFC8D8'>{TAGLINE}</p>",
                unsafe_allow_html=True)

st.markdown(f"**Wallet:** `{user['tokens']} 💎`")

# ─────────────────────────── navigation ────────────────────────
opts = ["Find matches", "Chat", "My Collection"]
page = st.sidebar.radio("Navigation", opts, key="nav",
                        index=opts.index(st.session_state.get("nav", opts[0])))

# save nav choice
st.session_state["nav"] = page

# ────────────────────────── FIND MATCHES ───────────────────────
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

    for c in st.session_state.get("matches", []):
        col1,col2,col3 = st.columns([1,3,2])
        with col1:
            path = c.get("photo", PLACEHOLDER)
            st.image(path if Path(path).is_file() else PLACEHOLDER, width=90)
        with col2:
            badge = c["rarity"]; clr = RARITY_CLR[badge]
            st.markdown(
                f"<span style='background:{clr};padding:2px 6px;"
                f"border-radius:4px;font-size:0.75rem'>{badge}</span> "
                f"**{c['name']}** &nbsp;• {COST[badge]} 💎  \n"
                f"<span class='match-bio'>{c['bio']}</span>",
                unsafe_allow_html=True)
        with col3:
            if st.button("💖 Mint", key=f"mint-{c['id']}"):
                ok, res = sp_buy(user, c)
                if ok:
                    st.session_state.user = res          # update wallet
                    st.session_state.collection.add(c["id"])
                    st.success(f"Added **{c['name']}** to your collection!")
                else:
                    st.warning(res)

# ───────────────────────────── CHAT ────────────────────────────
elif page == "Chat":
    if not st.session_state.collection:
        st.info("Mint a companion first.")
        st.stop()

    my_names = [COMPANION_MAP[c]["name"] for c in st.session_state.collection]
    cur_id   = next(iter(st.session_state.collection))
    sel_name = st.selectbox("Choose companion", my_names,
                            index=my_names.index(COMPANION_MAP[cur_id]["name"]))
    cid      = next(k for k,v in COMPANION_MAP.items() if v["name"]==sel_name)

    st.session_state.chat_id = cid
    ensure_history(cid)
    comp = COMPANION_MAP[cid]

    st.image(comp.get("photo", PLACEHOLDER), width=180)
    st.subheader(f"Chatting with **{comp['name']}**")
    if st.button("🗑️ Clear history"):
        st.session_state.histories[cid] = st.session_state.histories[cid][:1]

    for m in st.session_state.histories[cid][1:]:
        st.chat_message("assistant" if m["role"]=="assistant" else "user").write(m["content"])

    # token limit guard
    if st.session_state.spent >= MAX_TOKENS_PER_USER:
        st.warning("Daily token budget reached—try tomorrow!")
        st.stop()

    user_msg = st.chat_input("Say something…")
    if user_msg:
        msgs = st.session_state.histories[cid]
        msgs.append({"role":"user","content":user_msg})
        try:
            resp = client.chat.completions.create(
                model="gpt-4o-mini", messages=msgs, max_tokens=120
            )
            reply = resp.choices[0].message.content
            usage = resp.usage
            st.session_state.spent += usage.prompt_tokens + usage.completion_tokens
            msgs.append({"role":"assistant","content":reply})
            st.chat_message("assistant").write(reply)
            # optionally persist to messages table
            SUPABASE.table("messages").insert({
                "user_id": user["id"], "companion_id": cid,
                "role":"user", "content":user_msg
            }).execute()
            SUPABASE.table("messages").insert({
                "user_id": user["id"], "companion_id": cid,
                "role":"assistant", "content":reply
            }).execute()
        except RateLimitError:
            st.error("OpenAI rate‑limit; please wait.")
        except OpenAIError as e:
            st.error(str(e))

# ─────────────────────── MY COLLECTION ─────────────────────────
elif page == "My Collection":
    st.header("My BONDIGO Collection")
    if not st.session_state.collection:
        st.info("You haven’t minted anything yet.")
    for cid in sorted(st.session_state.collection):
        c   = COMPANION_MAP[cid]
        col = st.columns([1,5])
        col[0].image(c.get("photo", PLACEHOLDER), width=80)
        badge = c["rarity"]; clr = RARITY_CLR[badge]
        col[1].markdown(
            f"<span style='background:{clr};padding:2px 6px;"
            f"border-radius:4px;font-size:0.75rem'>{badge}</span> "
            f"**{c['name']}**  <br><span style='font-size:0.85rem'>{c['bio']}</span>",
            unsafe_allow_html=True)
