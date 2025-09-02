# -*- coding: utf-8 -*-
# 📺 24시간 유튜브 숏츠 트렌드 대시보드 (정치·뉴스)
import streamlit as st
import pandas as pd
import numpy as np
import requests, re, json, time
from collections import Counter
from pathlib import Path
import datetime as dt
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
from sklearn.feature_extraction.text import TfidfVectorizer
from typing import List, Tuple

# ───────── 기본 설정 ─────────
st.set_page_config(page_title="K-Politics/News Shorts Trend Board", page_icon="📺", layout="wide")

API_KEY = st.secrets.get("YOUTUBE_API_KEY", "")
if not API_KEY:
    st.error("⚠️ API 키가 없습니다. App → Settings → Secrets 에 `YOUTUBE_API_KEY = \"발급키\"` 를 넣어주세요.")
    st.stop()

KST = ZoneInfo("Asia/Seoul")
PT  = ZoneInfo("America/Los_Angeles")
SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
DAILY_QUOTA = 10_000

# ───────── 쿼터(일일) 영구 누적 저장 ─────────
DATA_DIR   = Path(".")
QUOTA_FILE = DATA_DIR / "quota_usage.json"

def _today_pt_str():
    return dt.datetime.now(PT).strftime("%Y-%m-%d")

def load_quota_used():
    today = _today_pt_str()
    if QUOTA_FILE.exists():
        try:
            data = json.loads(QUOTA_FILE.read_text(encoding="utf-8"))
            if data.get("pt_date") == today:
                return int(data.get("used", 0))
        except Exception:
            pass
    return 0

def save_quota_used(value: int):
    data = {"pt_date": _today_pt_str(), "used": int(value)}
    QUOTA_FILE.write_text(json.dumps(data), encoding="utf-8")

def add_quota(cost: int):
    used = st.session_state.get("quota_used", 0) + int(cost)
    st.session_state["quota_used"] = used
    cur = load_quota_used()
    save_quota_used(cur + int(cost))

if "quota_used" not in st.session_state:
    st.session_state["quota_used"] = load_quota_used()

# ───────── 시간창: 최근 24시간(KST) ─────────
def kst_window_last_24h():
    now_kst = dt.datetime.now(KST)
    start_kst = now_kst - dt.timedelta(hours=24)
    start_utc = start_kst.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    end_utc   = now_kst.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    return start_utc, end_utc, now_kst

# ───────── API helpers ─────────
def api_get(url, params, cost):
    r = requests.get(url, params=params, timeout=20)
    add_quota(cost)
    r.raise_for_status()
    return r.json()

def parse_iso8601_duration(s):
    if not s or not s.startswith("PT"): return None
    s2 = s[2:]; h=m=sec=0; num=""
    for ch in s2:
        if ch.isdigit(): num += ch
        else:
            if ch=="H": h=int(num or 0)
            elif ch=="M": m=int(num or 0)
            elif ch=="S": sec=int(num or 0)
            num=""
    return h*3600 + m*60 + sec

def fmt_hms(seconds):
    if seconds is None: return ""
    h = seconds//3600; m=(seconds%3600)//60; s=seconds%60
    return f"{h:02d}:{m:02d}:{s:02d}" if h>0 else f"{m:02d}:{s:02d}"

def to_kst(iso_str):
    if not iso_str: return ""
    t = dt.datetime.fromisoformat(iso_str.replace("Z","+00:00")).astimezone(KST)
    return t.strftime("%Y-%m-%d %H:%M:%S")

def to_kst_dt(iso_str):
    return dt.datetime.fromisoformat(iso_str.replace("Z","+00:00")).astimezone(KST) if iso_str else None

# ───────── 데이터 수집 (캐시) ─────────
@st.cache_data(show_spinner=False)
def fetch_shorts_df(pages:int=1, bucket:int=0):
    _ = bucket
    start_iso, end_iso, _now = kst_window_last_24h()

    vids, token = [], None
    for _ in range(pages):
        params = {
            "key": API_KEY, "part": "snippet", "type":"video", "order":"date",
            "publishedAfter": start_iso, "publishedBefore": end_iso,
            "maxResults": 50, "videoDuration":"short",
            "regionCode":"KR", "relevanceLanguage":"ko", "safeSearch":"moderate",
            "q": "뉴스 OR 정치 OR 속보 OR 브리핑"
        }
        if token: params["pageToken"] = token
        data = api_get(SEARCH_URL, params, cost=100)
        ids = [it.get("id",{}).get("videoId") for it in data.get("items",[]) if it.get("id",{}).get("videoId")]
        vids.extend(ids)
        token = data.get("nextPageToken")
        if not token: break

    seen=set(); ordered=[]
    for v in vids:
        if v not in seen: ordered.append(v); seen.add(v)

    details=[]
    for i in range(0, len(ordered), 50):
        chunk = ordered[i:i+50]
        if not chunk: continue
        params = {"key": API_KEY, "part": "snippet,contentDetails,statistics", "id": ",".join(chunk)}
        data = api_get(VIDEOS_URL, params, cost=1)
        details.extend(data.get("items", []))

    rows=[]
    for it in details:
        vid = it.get("id")
        sn  = it.get("snippet", {})
        cd  = it.get("contentDetails", {})
        stt = it.get("statistics", {})
        secs = parse_iso8601_duration(cd.get("duration",""))
        if secs is None or secs>60: continue
        pub_iso = sn.get("publishedAt","")
        rows.append({
            "video_id": vid,
            "title": sn.get("title",""),
            "description": sn.get("description",""),
            "view_count": int(pd.to_numeric(stt.get("viewCount","0"), errors="coerce") or 0),
            "like_count": int(pd.to_numeric(stt.get("likeCount","0"), errors="coerce") or 0),
            "comment_count": int(pd.to_numeric(stt.get("commentCount","0"), errors="coerce") or 0),
            "length": fmt_hms(secs),
            "length_seconds": secs,
            "channel": sn.get("channelTitle",""),
            "url": f"https://www.youtube.com/watch?v={vid}",
            "published_at_kst": to_kst(pub_iso),
            "published_dt_kst": to_kst_dt(pub_iso),
        })
    df = pd.DataFrame(rows)

    now_kst = dt.datetime.now(KST)
    if not df.empty:
        df["hours_since_upload"] = (now_kst - pd.to_datetime(df["published_dt_kst"])).dt.total_seconds() / 3600.0
        df["hours_since_upload"] = df["hours_since_upload"].clip(lower=(1.0/60.0))
        df["views_per_hour"] = (df["view_count"] / df["hours_since_upload"]).round(1)
    else:
        df["hours_since_upload"] = []
        df["views_per_hour"] = []
    return df

# ───────── (공용) 금지어/불용어 & 문장 금칙/시간표현 컷 ─────────
COMMON_STOPWORDS = {
    # 도메인/플랫폼/형식
    "http","https","www","com","co","kr","net","org","youtube","shorts","watch","tv","cctv","sns",
    # 뉴스 군더더기
    "기사","단독","속보","영상","전문","라이브","기자","보도","헤드라인","데스크","전체보기","더보기",
    # 시점/시간
    "오늘","어제","최근","방금","방금전","아침","오전","오후","밤","새벽","첫날","뉴스top10",
    # 내용 빈약/상투
    "관련","논란","논쟁","상황","사건","이슈","분석","전망","브리핑","발언","발표","입장",
    # 지명/기관(상투)
    "서울","한국","국내","해외","정부","여당","야당","당국","위원장","장관","대통령","총리","국회","검찰",
    # 네가 요청한 공용 블랙리스트(유튜브/트렌드 공통)
    "구독","정치","대통령실","채널","news","대법원","특검","이잼",
    # 방송사/매체 상수
    "sbs","kbs","mbc","jtbc","tv조선","mbn","연합뉴스","mbc뉴스",
    # 자주 뜨는 군더더기
    "시작","사고","전문","사진",
    # 과거에 공유했던 것들
    "다큐디깅","나는","절로"
}

# “석방하라” 등 문장 전체 금칙 + “달 만에/주째…” 같은 시간 표현 컷
COMMON_BANNED_PAT = re.compile(r"(석방 ?하라|입 ?닥치고|무슨 ?일|수 있(을까|나)|수 없나)", re.I)
TEMPORAL_BAD_PAT = re.compile(
    r"""(
        \b\d+\s*(년|개월|달|주|주일|일|시간)\s*(만에|째|동안)\b
      | \b(이후|이전|전|후)\b
      | \b(오늘|어제)\s*(오전|오후)?\b
    )""",
    re.X | re.I
)

def _contains_common_banned(s: str) -> bool:
    s = s.lower()
    if COMMON_BANNED_PAT.search(s): return True
    if TEMPORAL_BAD_PAT.search(s):  return True
    return False

# ───────── 키워드 추출 ─────────
KO_JOSA = ("는","이","가","을","를","의","에","에서","에게","께","와","과","으로","로","도","만","까지","부터","마다","조차","라도","마저","밖에","처럼","뿐","께서","채")
KO_SUFFIX = ("하기","하세요","십시오","해주세요","합니다","했다","중","관련","영상","채널","뉴스","보기","등록","구독","홈페이지","됩니다")

def strip_korean_suffixes(t: str) -> str:
    for suf in KO_SUFFIX:
        if t.endswith(suf) and len(t) > len(suf)+1: t = t[:-len(suf)]
    for j in KO_JOSA:
        if t.endswith(j) and len(t) > len(j)+1: t = t[:-len(j)]
    return t

def tokenize_ko_en(text: str):
    text = str(text or "")
    if _contains_common_banned(text): return []
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"www\.\S+", " ", text)
    raw = re.findall(r"[0-9A-Za-z가-힣]+", text.lower())
    out=[]
    for t in raw:
        if not t or t.isdigit(): continue
        if re.fullmatch(r"[가-힣]+", t): t = strip_korean_suffixes(t)
        if t in COMMON_STOPWORDS or len(t) < 2: continue
        if re.fullmatch(r"[a-z]+", t) and len(t) <= 2: continue
        if t.endswith("tv") and len(t) > 2:
            t = t[:-2]
            if t in COMMON_STOPWORDS or len(t) < 2: continue
        out.append(t)
    return out

def top_keywords_from_df(df: pd.DataFrame, topk:int=10):
    corpus = (df["title"].fillna("") + " " + df["description"].fillna("")).tolist()
    cnt = Counter()
    for line in corpus: cnt.update(tokenize_ko_en(line))
    items = [(w,c) for w,c in cnt.most_common() if not re.fullmatch(r"\d+", w)]
    return items[:topk]

# ───────── UI ─────────
st.title("📺 24시간 유튜브 숏츠 트렌드 대시보드 (정치·뉴스)")

with st.sidebar:
    st.header("수집 옵션")
    size = st.selectbox("수집 규모", [50, 100, 200], index=1)
    pages = {50:1, 100:2, 200:4}[size]
    ttl_choice = st.selectbox("캐시 TTL", ["15분","30분(추천)","60분"], index=1)
    ttl_map = {"15분":900,"30분(추천)":1800,"60분":3600}
    ttl_sec = ttl_map[ttl_choice]
    rank_mode = st.radio("정렬 기준", ["상승속도(뷰/시간)","조회수(총합)"], horizontal=True, index=0)
    sort_order = st.radio("정렬 순서", ["내림차순","오름차순"], horizontal=True, index=0)
    show_speed_cols = st.checkbox("상승속도/경과시간 표시", value=True)
    run = st.button("새로고침")

bucket = int(time.time() // ttl_sec)
if run:
    st.cache_data.clear()
    st.success("데이터 새로고침!")

df = fetch_shorts_df(pages=pages, bucket=bucket)
base_col = "views_per_hour" if rank_mode.startswith("상승속도") else "view_count"
ascending_flag = (sort_order=="오름차순")
df_pool = df.sort_values(base_col, ascending=ascending_flag, ignore_index=True)

# 키워드 Top10 (공용 금지어/문장금칙 반영)
yt_kw = top_keywords_from_df(df_pool, topk=10)
yt_kw_words = [w for w,_ in yt_kw]
st.session_state["yt_kw_words"] = yt_kw_words

left, right = st.columns(2)
with left:
    st.subheader("📈 유튜브 키워드 Top10")
    if yt_kw:
        df_kw = pd.DataFrame(yt_kw, columns=["keyword","count"])
        st.bar_chart(df_kw.set_index("keyword")["count"])
        st.dataframe(df_kw, use_container_width=True, hide_index=True)
        st.download_button("CSV 다운로드",
            df_kw.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"),
            file_name="yt_keywords_top10.csv", mime="text/csv")
    else:
        st.info("키워드 부족")

with right:
    st.subheader("🎬 숏츠 리스트")
    cols = ["title","view_count","length","channel","like_count","comment_count","url","published_at_kst"]

    df_show = df_pool.copy()
    # 안전 처리: 없는 컬럼은 NaN/빈 문자열로 채워주기
    safe_cols = [c for c in cols if c in df_show.columns]
    for c in cols:
        if c not in df_show.columns:
            if c in ("view_count","like_count","comment_count"):
                df_show[c] = 0
            elif c == "length":
                df_show[c] = ""
            else:
                df_show[c] = ""
    df_show = df_show[cols]  # 순서 보장

    st.dataframe(df_show, use_container_width=True, hide_index=True)
    st.download_button("CSV 다운로드",
        df_show.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"),
        file_name="shorts_list.csv", mime="text/csv")

# ───────── 쿼터 표시 ─────────
now_pt = dt.datetime.now(PT)
reset_pt = (now_pt+dt.timedelta(days=1)).replace(hour=0,minute=0,second=0,microsecond=0)
remain_td = reset_pt - now_pt
used = st.session_state.get("quota_used", 0)
remaining = max(0, DAILY_QUOTA-used)
pct = min(1.0, used/DAILY_QUOTA)

q1,q2=st.columns([2,1])
with q1:
    st.subheader("🔋 오늘 쿼터(추정)")
    st.progress(pct, text=f"사용 {used} / {DAILY_QUOTA} (남은 {remaining})")
with q2:
    st.metric("남은 쿼터", value=f"{remaining:,}", delta=f"리셋까지 {str(remain_td).split('.')[0]}")
