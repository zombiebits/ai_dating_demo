import json, random, os
from pathlib import Path
import streamlit as st
from openai import OpenAI, OpenAIError, RateLimitError
from dotenv import load_dotenv

# ---------- tweak appearance ----------
st.markdown(
    """
<style>
    /* BIGGER sidebar radios */
    [data-testid="stSidebar"] div[role="radiogroup"] label {
        font-size: 1.3rem !important;   /* ← raise from 1.1 rem  */
        line-height: 1.6rem !important;
        padding: 10px 0 10px 6px;       /* taller click‑zone      */
    }
    /* bigger name text in cards */
    .match-name {font-size: 1.25rem; font-weight: 700;}
    /* bigger bio */
    .match-bio  {font-size: 1.05rem; line-height: 1.55;}
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------- constants ----------
MAX_TOKENS_PER_USER = 10_000
PLACEHOLDER = "assets/placeholder.png"

# ---------- setup ----------
load_dotenv(encoding="utf-8-sig")
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

COMPANIONS = json.load(open("companions.json", encoding="utf-8-sig"))
COMPANION_MAP = {c["id"]: c for c in COMPANIONS}

# ---------- session defaults ----------
for k, v in {
    "matches": [], "likes": [], "histories": {}, "chat_id": None,
    "spent": 0, "nav": "Find matches", "switch_to_chat": False
}.items():
    st.session_state.setdefault(k, v)

# ---------- helpers ----------
def ensure_history(cid):
    if cid not in st.session_state["histories"]:
        c = COMPANION_MAP[cid]
        st.session_state["histories"][cid] = [{
            "role": "system",
            "content": f"You are {c['name']}. {c['bio']} Speak in first person, friendly & flirty but PG‑13."
        }]

def like(c):
    if c["id"] not in st.session_state["likes"]:
        st.session_state["likes"].append(c["id"])
    st.session_state["chat_id"] = c["id"]
    ensure_history(c["id"])
    st.session_state["switch_to_chat"] = True

def find_matches(filters):
    return [c for c in COMPANIONS if all(tag in c["tags"] for tag in filters)]

def show_image(path_str, width):
    p = Path(path_str)
    st.image(str(p if p.is_file() else PLACEHOLDER), width=width)

# ---------- nav switch ----------
if st.session_state.pop("switch_to_chat", False):
    st.session_state["nav"] = "Chat"

# ---------- UI ----------
st.title("🤖💕  AI Matchmaker (Demo)")

options = ["Find matches", "Chat"]
page = st.sidebar.radio(
    "Navigation",
    options,
    key="nav",
    index=options.index(st.session_state.get("nav", options[0]))
)

# ======== FIND ========
if page == "Find matches":
    st.header("Tell us about you")

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
        st.session_state["matches"] = (
            find_matches([hobby, trait, vibe, scene]) or random.sample(COMPANIONS, 5)
        )

    for c in st.session_state["matches"]:
        col_img, col_info, col_like, col_chat = st.columns([1, 3, 1, 2])
        with col_img:
            show_image(c.get("photo", PLACEHOLDER), width=100)
        with col_info:
            st.markdown(f"<div class='match-name'>{c['name']}</div>", unsafe_allow_html=True)
            st.markdown(f"<div class='match-bio'>{c['bio']}</div>", unsafe_allow_html=True)
        with col_like:
            st.button("💖", key=f"like-{c['id']}", on_click=like, args=(c,))
        with col_chat:
            if c["id"] in st.session_state["likes"]:
                st.button("Chat now →", key=f"chat-{c['id']}", on_click=like, args=(c,))

    if st.session_state["matches"]:
        st.button("Reset search", on_click=lambda: st.session_state["matches"].clear())

# ======== CHAT ========
elif page == "Chat":
    if not st.session_state["likes"]:
        st.info("Like at least one companion first!")
        st.stop()

    names = [COMPANION_MAP[c]["name"] for c in st.session_state["likes"]]
    idx   = st.session_state["likes"].index(st.session_state.get("chat_id") or st.session_state["likes"][0])
    sel   = st.selectbox("Choose a conversation", names, index=idx)
    cid   = st.session_state["likes"][names.index(sel)]
    st.session_state["chat_id"] = cid
    ensure_history(cid)

    comp = COMPANION_MAP[cid]
    show_image(comp.get("photo", PLACEHOLDER), width=200)
    st.subheader(f"Chatting with **{comp['name']}**")

    for m in st.session_state["histories"][cid][1:]:
        st.chat_message("assistant" if m["role"]=="assistant" else "user").write(m["content"])

    if st.session_state["spent"] >= MAX_TOKENS_PER_USER:
        st.warning("🥲 Daily token budget reached—try again tomorrow!")
        st.stop()

    user_msg = st.chat_input("Say something…")
    if user_msg:
        msgs = st.session_state["histories"][cid]
        msgs.append({"role":"user","content":user_msg})

        with st.spinner("Thinking…"):
            try:
                resp = client.chat.completions.create(
                    model="gpt-4o-mini", messages=msgs, max_tokens=120
                )
                reply = resp.choices[0].message.content
                usage = resp.usage
                st.session_state["spent"] += usage.prompt_tokens + usage.completion_tokens
                msgs.append({"role":"assistant","content":reply})
                st.chat_message("assistant").write(reply)
            except RateLimitError:
                st.error("OpenAI rate‑limit hit—try again later.")
            except OpenAIError as e:
                st.error(f"OpenAI error: {e}")
