import json, os, random, jwt
from datetime import datetime, timedelta, timezone
from pathlib import Path

import streamlit as st
from openai import OpenAI, OpenAIError, RateLimitError
from dotenv import load_dotenv
from supabase import create_client
from postgrest.exceptions import APIError

# ─────────────────── ENV / CLIENTS ─────────────────────────────
load_dotenv()
SB = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
OA = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

# ─────────────────── CONSTANTS ──────────────────────────────────
MAX_TOKENS    = 10_000
DAILY_AIRDROP = 150
COST          = {"Common": 50, "Rare": 200, "Legendary": 700}

PLACEHOLDER = "assets/placeholder.png"
LOGO        = "assets/bondigo_banner.png"
TAGLINE     = "Talk the Lingo · Master the Bond · Dominate the Game."
CLR         = {"Common":"#bbb","Rare":"#57C7FF","Legendary":"#FFAA33"}

COMPANIONS = json.load(open("companions.json", encoding="utf-8-sig"))
CID2COMP   = {c["id"]: c for c in COMPANIONS}

# ─────────────────── DB HELPERS ────────────────────────────────
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
                    "auth_uid": auth_uid,
                    "username": username,
                    "tokens": 1000
                })
                .execute()
                .data[0]
            )
        except APIError as e:
            msg = str(e)
            if "users_username_idx" in msg or "duplicate key" in msg:
                raise ValueError("username_taken")
            raise

    last = user["last_airdrop"] or user["created_at"]
    last = datetime.fromisoformat(last.replace("Z","+00:00"))
    if datetime.now(timezone.utc) - last >= timedelta(hours=24):
        user = (
            tbl.update({
                "tokens": user["tokens"] + DAILY_AIRDROP,
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
    cost = COST[comp.get("rarity","Common")]
    if cost > user["tokens"]:
        return False, "Not enough 💎"
    already = (
        SB.table("collection")
          .select("companion_id")
          .eq("user_id", user["id"])
          .eq("companion_id", comp["id"])
          .execute()
          .data
    )
    if already:
        return False, "Already owned"

    SB.table("users").update({"tokens":user["tokens"] - cost}) \
      .eq("id", user["id"]).execute()
    SB.table("collection").insert({
        "user_id": user["id"], "companion_id": comp["id"]
    }).execute()

    return True, profile_upsert(user["auth_uid"], user["username"])

# ─────────────────── STREAMLIT PAGE CONFIG ────────────────────
st.set_page_config("BONDIGO","🩷",layout="centered")
st.markdown("""
<style>
[data-testid="stSidebar"] div[role="radiogroup"] label{
  font-size:1.25rem;line-height:1.5rem;padding:8px 0 8px 4px;}
.match-bio {font-size:1rem;line-height:1.4;}
</style>""",unsafe_allow_html=True)

# ─────────────────── LOGIN / SIGN‑UP ───────────────────────────
if "user" not in st.session_state:
    st.title("🔐 Sign in / Sign up to **BONDIGO**")

    mode  = st.radio("Choose", ["Sign in","Sign up"], horizontal=True)
    uname = st.text_input("Username", max_chars=20)
    pwd   = st.text_input("Password", type="password")

    if st.button("Go ➜"):
        if not uname or not pwd:
            st.warning("Fill both fields."); st.stop()

        if mode=="Sign up":
            taken = SB.table("users").select("id") \
                      .eq("username", uname).execute().data
            if taken:
                st.error("Username already taken."); st.stop()

        pseudo_email = f"{uname.lower()}@bondigo.local"
        try:
            if mode=="Sign up":
                SB.auth.sign_up({"email":pseudo_email,"password":pwd})
            sess = SB.auth.sign_in_with_password({
                "email":pseudo_email,"password":pwd
            })
        except Exception as e:
            st.error(f"Auth error: {e}"); st.stop()

        try:
            st.session_state.user = profile_upsert(sess.user.id, uname)
        except ValueError:
            st.error("Username was just taken; pick another."); SB.auth.sign_out(); st.stop()

        st.session_state.col     = collection_set(st.session_state.user["id"])
        st.session_state.hist    = {}
        st.session_state.spent   = 0
        st.session_state.matches = []
        st.rerun()

    st.stop()

# ─────────────────── APP BODY ─────────────────────────────────
user    = st.session_state.user
colset  = st.session_state.col
hist    = st.session_state.hist
spent   = st.session_state.spent
matches = st.session_state.matches

if Path(LOGO).is_file():
    st.image(LOGO, width=380)
    st.markdown(
      f"<p style='text-align:center;margin-top:-2px;font-size:1.05rem;"
      f"color:#FFC8D8'>{TAGLINE}</p>",
      unsafe_allow_html=True
    )
st.markdown(f"**Wallet:** `{user['tokens']} 💎`")

tabs = ["Find matches","Chat","My Collection"]
page = st.sidebar.radio("Navigation", tabs, key="nav")

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
            c for c in COMPANIONS
            if all(t in c["tags"] for t in [hobby,trait,vibe,scene])
        ] or random.sample(COMPANIONS, 5)

    for c in matches:
        r, clr = c.get("rarity","Common"), CLR[r]
        c1,c2,c3 = st.columns([1,3,2])
        c1.image(c.get("photo",PLACEHOLDER),width=90)
        c2.markdown(
          f"<span style='background:{clr};padding:2px 6px;"
          f"border-radius:4px;font-size:0.75rem'>{r}</span> "
          f"**{c['name']}** • {COST[r]} 💎  \n"
          f"<span class='match-bio'>{c['bio']}</span>",
          unsafe_allow_html=True)

        if c3.button("💖 Mint", key=f"mint-{c['id']}"):
            ok,res = buy(user,c)
            if ok:
                st.session_state.user = res
                colset.add(c["id"])
                st.success("Minted!")
            else:
                st.warning(res)
            st.rerun()

elif page == "Chat":
    if not colset:
        st.info("Mint a companion first."); st.stop()

    names = [CID2COMP[c]["name"] for c in colset]
    default = next(iter(colset))
    sel     = st.selectbox("Choose companion", names,
               index=names.index(CID2COMP[default]["name"]))
    cid = next(k for k,v in CID2COMP.items() if v["name"]==sel)
    st.session_state.chat_id = cid

    history = hist.setdefault(cid, [{
        "role":"system",
        "content": f"You are {CID2COMP[cid]['name']}. "
                   f"{CID2COMP[cid]['bio']} Speak in first person, PG‑13."
    }])

    comp = CID2COMP[cid]
    st.image(comp.get("photo",PLACEHOLDER),width=180)
    st.subheader(f"Chatting with **{comp['name']}**")

    if st.button("🗑️ Clear history"):
        history[:] = history[:1]
        st.rerun()

    for msg in history[1:]:
        st.chat_message("assistant" if msg["role"]=="assistant" else "user") \
          .write(msg["content"])

    if spent >= MAX_TOKENS:
        st.warning("Daily budget hit."); st.stop()

    u = st.chat_input("Say something…")
    if u:
        history.append({"role":"user","content":u})
        try:
            resp  = OA.chat.completions.create(
                      model="gpt-4o-mini", messages=history, max_tokens=120)
            reply = resp.choices[0].message.content
            usage = resp.usage
            st.session_state.spent += usage.prompt_tokens + usage.completion_tokens
            history.append({"role":"assistant","content":reply})
            st.chat_message("assistant").write(reply)

            SB.table("messages").insert({
                "user_id": user["id"], "companion_id": cid,
                "role":"user","content":u
            }).execute()
            SB.table("messages").insert({
                "user_id": user["id"], "companion_id": cid,
                "role":"assistant","content":reply
            }).execute()

        except RateLimitError:
            st.warning("OpenAI rate‑limit; try again in a moment.")
        except OpenAIError as e:
            st.error(str(e))

else:  # My Collection
    st.header("My BONDIGO Collection")
    if not colset:
        st.info("Nothing minted yet."); st.stop()

    for cid in sorted(colset):
        c = CID2COMP[cid]; clr = CLR[c["rarity"]]
        c1,c2 = st.columns([1,5])
        c1.image(c.get("photo",PLACEHOLDER),width=80)
        c2.markdown(
          f"<span style='background:{clr};padding:2px 6px;border-radius:4px;"
          f"font-size:0.75rem'>{c['rarity']}</span> **{c['name']}**  \n"
          f"<span style='font-size:0.85rem'>{c['bio']}</span>",
          unsafe_allow_html=True)
