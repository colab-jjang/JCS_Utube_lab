# -*- coding: utf-8 -*-
# 📺 48시간 유튜브 숏츠 트렌드 대시보드 (정치·뉴스)

import streamlit as st
import pandas as pd
import requests, re, json, time
from collections import Counter
from pathlib import Path
import datetime as dt
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

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
    now_pt = dt.datetime.now(PT)
    return now_pt.strftime("%Y-%m-%d")

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

# ───────── 시간창: 최근 48시간(KST) ─────────
def kst_window_last_48h():
    now_kst = dt.datetime.now(KST)
    start_kst = now_kst - dt.timedelta(hours=48)
    start_utc = start_kst.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    end_utc   = now_kst.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    return start_utc, end_utc, now_kst

# ───────── YouTube API helpers ─────────
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
    start_iso, end_iso, _now = kst_window_last_48h()

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
        if not token:
            break

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
        if secs is None or secs>60:
            continue
        pub_iso = sn.get("publishedAt","")
        rows.append({
            "video_id": vid,
            "title": sn.get("title",""),
            "description": sn.get("description",""),
            "view_count": int(pd.to_numeric(stt.get("viewCount","0"), errors="coerce") or 0),
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

# ───────── 유튜브 키워드 추출 ─────────
STOPWORDS = {"속보","브리핑","단독","현장","영상","뉴스","기자","리포트","라이브","연합뉴스",
              "채널","구독","대통령","유튜브","정치","홈페이지","대한민국","금지","시사","모아","답해주세요"}

def tokenize_ko_en(text: str):
    text = str(text or "")
    text = re.sub(r"https?://\S+", " ", text)
    raw = re.findall(r"[0-9A-Za-z가-힣]+", text.lower())
    return [t for t in raw if len(t) > 1 and t not in STOPWORDS]

def top_keywords_from_df(df: pd.DataFrame, topk:int=10):
    corpus = (df["title"].fillna("") + " " + df["description"].fillna("")).tolist()
    cnt = Counter()
    for line in corpus:
        cnt.update(tokenize_ko_en(line))
    items = [(w,c) for w,c in cnt.most_common() if not re.fullmatch(r"\d+", w)]
    return items[:topk]

# ───────── Naver 트렌드 ─────────
def _fetch_trends_naver(add_log=None) -> tuple[list[str], str]:
    headers = {"User-Agent": "Mozilla/5.0"}
    urls = [
        "https://news.naver.com/main/ranking/popularDay.naver",
        "https://news.naver.com/section/100",
    ]
    selectors = ["ol.ranking_list a","div.rankingnews_box a","a.cluster_text_headline"]
    titles = []
    for u in urls:
        try:
            r = requests.get(u, headers=headers, timeout=12)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "lxml")
            for css in selectors:
                for a in soup.select(css):
                    t = a.get_text(" ", strip=True)
                    if t: titles.append(t)
        except Exception as e:
            if add_log: add_log(f"[naver] error: {e}")
            continue
    return (titles[:10], "naver") if titles else ([], "none")

# ───────── Google 트렌드 ─────────
@st.cache_data(show_spinner=False, ttl=900)
def google_trends_top(debug_log: bool = False, source_mode: str = "auto"):
    logs = []
    def add(msg): 
        if debug_log: logs.append(str(msg))

    def _google_try():
        headers = {"User-Agent": "Mozilla/5.0"}
        bases = ["https://trends.google.com", "https://trends.google.co.kr"]

        # (A) Daily RSS
        for base in bases:
            try:
                url = f"{base}/trends/trendingsearches/daily/rss?geo=KR&hl=ko"
                r = requests.get(url, headers=headers, timeout=15)
                r.raise_for_status()
                root = ET.fromstring(r.content)
                titles = []
                for item in root.findall(".//item"):
                    t = (item.findtext("title") or "").strip()
                    if t: titles.append(t)
                    if len(titles) >= 10: break
                if titles: return titles, "google-rss"
            except Exception as e:
                add(f"[google rss] error: {e}")

        # (B) HTML fallback
        try:
            url = "https://trends.google.com/trends/trendingsearches/daily?geo=KR&hl=ko"
            r = requests.get(url, headers=headers, timeout=15)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "lxml")
            titles = [a.get_text(strip=True) for a in soup.select("div.feed-item h2 a")]
            if titles: return titles[:10], "google-html"
        except Exception as e:
            add(f"[google html] error: {e}")

        return [], "none"

    def _youtube_fallback():
        words = st.session_state.get("yt_kw_words", [])
        return (words[:10], "youtube-fallback") if words else ([], "none")

    if source_mode == "google": return *_google_try(), logs
    if source_mode == "naver": return *_fetch_trends_naver(add), logs
    if source_mode == "youtube": return *_youtube_fallback(), logs

    kws, src = _google_try()
    if not kws:
        kws, src = _fetch_trends_naver(add)
    if not kws:
        kws, src = _youtube_fallback()
    return (kws or []), (src or "none"), logs

# ───────── UI ─────────
st.title("📺 48시간 유튜브 숏츠 트렌드 대시보드 (정치·뉴스)")

with st.sidebar:
    st.header("수집 옵션")
    size = st.selectbox("수집 규모", [50, 100, 200], index=1)
    pages = {50:1, 100:2, 200:4}[size]
    ttl_choice = st.selectbox("캐시 TTL", ["15분","30분(추천)","60분"], index=1)
    ttl_map = {"15분":900, "30분(추천)":1800, "60분":3600}
    ttl_sec = ttl_map[ttl_choice]
    rank_mode = st.radio("정렬 기준", ["상승속도(뷰/시간)", "조회수(총합)"], horizontal=True)
    sort_order = st.radio("정렬 순서", ["내림차순", "오름차순"], horizontal=True)
    show_speed_cols = st.checkbox("상승속도/경과시간 표시", value=True)
    trend_source = st.radio("트렌드 소스 선택", ["자동(구글→네이버)", "구글만", "네이버만", "유튜브만"])
    trend_debug = st.checkbox("트렌드 디버그 로그 보기", value=False)
    run = st.button("새로고침")

bucket = int(time.time() // ttl_sec)
if run:
    st.cache_data.clear()

df = fetch_shorts_df(pages=pages, bucket=bucket)
base_col = "views_per_hour" if rank_mode.startswith("상승속도") else "view_count"
ascending_flag = (sort_order == "오름차순")

yt_kw = top_keywords_from_df(df, topk=10)
yt_kw_words = [w for w,_ in yt_kw]
st.session_state["yt_kw_words"] = yt_kw_words

mode_map = {"자동(구글→네이버)":"auto","구글만":"google","네이버만":"naver","유튜브만":"youtube"}
g_kw, g_src, g_logs = google_trends_top(source_mode=mode_map[trend_source], debug_log=trend_debug)

# ───────── 시각화 ─────────
left, right = st.columns(2)
with left:
    st.subheader("📈 유튜브 키워드 Top10")
    if yt_kw:
        df_kw = pd.DataFrame(yt_kw, columns=["keyword","count"])
        st.bar_chart(df_kw.set_index("keyword"))
        st.dataframe(df_kw)
with right:
    st.subheader("🌐 Trends Top10")
    if g_kw:
        df_g = pd.DataFrame({"keyword": g_kw})
        df_g = df_g.drop_duplicates().head(10)
        df_g["rank"] = np.arange(1, len(df_g)+1)
        df_g["score"] = (len(df_g)+1) - df_g["rank"]
        st.bar_chart(df_g.set_index("keyword")[["score"]])
        st.dataframe(df_g[["rank","keyword"]])
    else:
        st.info("트렌드 키워드를 가져오지 못했습니다.")
