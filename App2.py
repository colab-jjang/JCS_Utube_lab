# -*- coding: utf-8 -*-
import streamlit as st
import pandas as pd
import requests, re, json, time
from collections import Counter
from pathlib import Path
import datetime as dt
from zoneinfo import ZoneInfo

# ───────── 기본 설정 ─────────
API_KEY = st.secrets.get("YOUTUBE_API_KEY", "")
KST = ZoneInfo("Asia/Seoul")
PT  = ZoneInfo("America/Los_Angeles")
SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
DAILY_QUOTA = 10_000

# ───────── 쿼터(일일) 영구 누적 저장: 파일 방식 ─────────
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
    st.session_state["quota_used"] = st.session_state.get("quota_used", 0) + int(cost)
    current = load_quota_used()
    save_quota_used(current + int(cost))

# 세션 초기화(파일과 동기화)
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
    add_quota(cost)  # 유효/무효 호출 모두 비용 발생 규칙 반영
    r.raise_for_status()
    return r.json()

def parse_iso8601_duration(s):
    if not s or not s.startswith("PT"):
        return None
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
    # publishedAt(UTC ISO) → timezone-aware KST datetime
    return dt.datetime.fromisoformat(iso_str.replace("Z","+00:00")).astimezone(KST) if iso_str else None

# ───────── 데이터 수집 (캐시 가능) ─────────
@st.cache_data(show_spinner=False)
def fetch_shorts_df(pages:int=1):
    """pages: 1(≈50개), 2(≈100개), 4(≈200개)"""
    start_iso, end_iso, _ = kst_window_last_48h()

    # 1) ID 수집 (search.list = 100/호출)
    vids, token, p = [], None, 0
    for _ in range(pages):
        params = {
            "key": API_KEY, "part": "snippet", "type":"video", "order":"date",
            "publishedAfter": start_iso, "publishedBefore": end_iso,
            "maxResults": 50, "videoDuration":"short",
            "regionCode":"KR", "relevanceLanguage":"ko", "safeSearch":"moderate",
        }
        if token: params["pageToken"] = token
        data = api_get(SEARCH_URL, params, cost=100)
        ids = [it.get("id",{}).get("videoId") for it in data.get("items",[]) if it.get("id",{}).get("videoId")]
        vids.extend(ids)
        token = data.get("nextPageToken")
        if not token:
            break

    # de-dup
    seen=set(); ordered=[]
    for v in vids:
        if v not in seen: ordered.append(v); seen.add(v)

    # 2) 상세 (videos.list = 1/호출, 50개씩)
    details=[]
    for i in range(0, len(ordered), 50):
        chunk = ordered[i:i+50]
        if not chunk: continue
        params = {"key": API_KEY, "part": "snippet,contentDetails,statistics", "id": ",".join(chunk)}
        data = api_get(VIDEOS_URL, params, cost=1)
        details.extend(data.get("items", []))

    # 3) DF 만들기 (Shorts만)
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
    df = pd.DataFrame(rows, columns=[
        "video_id","title","description","view_count","length","length_seconds",
        "channel","url","published_at_kst","published_dt_kst"
    ])

    # ─ 급상승 지표 계산
    now_kst = dt.datetime.now(KST)
    if not df.empty:
        df["hours_since_upload"] = (now_kst - pd.to_datetime(df["published_dt_kst"])).dt.total_seconds() / 3600.0
        df["hours_since_upload"] = df["hours_since_upload"].clip(lower=(1.0/60.0))  # 최소 1분
        df["views_per_hour"] = (df["view_count"] / df["hours_since_upload"]).round(1)
    else:
        df["hours_since_upload"] = []
        df["views_per_hour"] = []
    return df

# ───────── 키워드 추출 ─────────
STOPWORDS = set("""
그리고 그러나 그래서 또한 또는 및 먼저 지금 바로 매우 정말 그냥 너무 보다 보다도 때는 라는 이런 저런 그런
합니다 했다 했다가 하는 하고 하며 하면 대한 위해 에서 에게 에도 에는 으로 로 를 은 는 이 가 도 의 에 와 과
""".split())
STOPWORDS |= {"속보","브리핑","단독","현장","영상","뉴스","기자","리포트","라이브"}

def tokenize_ko_en(text:str):
    text = re.sub(r"[^0-9A-Za-z가-힣]+", " ", str(text))
    toks = [t.strip().lower() for t in text.split() if t.strip()]
    toks = [t for t in toks if len(t)>=2 and t not in STOPWORDS]
    return toks

def top_keywords_from_df(df: pd.DataFrame, topk:int=10):
    corpus = (df["title"].fillna("") + " " + df["description"].fillna("")).tolist()
    cnt = Counter()
    for line in corpus:
        cnt.update(tokenize_ko_en(line))
    items = [(w,c) for w,c in cnt.most_common() if not re.fullmatch(r"\d+", w)]
    return items[:topk]

# ───────── Google Trends (pytrends) ─────────
@st.cache_data(show_spinner=False, ttl=1800)  # 30분 캐시
def google_trends_top():
    try:
        from pytrends.request import TrendReq
        pytrends = TrendReq(hl='ko', tz=540)
        df = pytrends.trending_searches(pn='south_korea')  # 상위 20
        kws = [str(x).strip() for x in df[0].dropna().tolist()]
        return kws[:10]
    except Exception:
        return []

# ───────── UI ─────────
st.set_page_config(page_title="K-Politics/News Shorts Trend Board", page_icon="📺", layout="wide")
st.title("📺 48시간 유튜브 숏츠 트렌드 대시보드 (정치·뉴스)")

if not API_KEY:
    st.error("⚠️ API 키가 없습니다. App → Settings → Secrets 에 `YOUTUBE_API_KEY = \"발급키\"` 를 넣어주세요.")
    st.stop()

with st.sidebar:
    st.header("수집 옵션")
    size = st.selectbox("수집 규모(Shorts 후보 수)", [50, 100, 200], index=1)
    pages = {50:1, 100:2, 200:4}[size]

    ttl_choice = st.selectbox("캐시 TTL(자동 절약)", ["15분","30분(추천)","60분"], index=1)
    ttl_map = {"15분":900, "30분(추천)":1800, "60분":3600}
    ttl_sec = ttl_map[ttl_choice]

    rank_mode = st.radio("정렬 기준", ["상승속도(뷰/시간)", "조회수(총합)"], horizontal=True, index=0)

    show_speed_cols = st.checkbox("상승속도/경과시간 컬럼 표시", value=True)

    run = st.button("새로고침(데이터 수집)")

# 캐시를 TTL별로 다르게 만들기 위한 버킷(옵션)
bucket = int(time.time() // ttl_sec)
if run:
    st.cache_data.clear()
    st.success("데이터 새로고침 시작!")

with st.spinner("데이터 수집/분석 중…"):
    df = fetch_shorts_df(pages=pages)
    base_col = "views_per_hour" if rank_mode.startswith("상승속도") else "view_count"
    # 분석용 상위 풀(최대 size 또는 100 이상은 100)
    base_pool_n = max(50, size)
    df_pool = df.sort_values(base_col, ascending=False, ignore_index=True).head(min(len(df), base_pool_n))
    # 키워드 Top10
    yt_kw = top_keywords_from_df(df_pool, topk=10)
    yt_kw_words = [w for w,_ in yt_kw]
    # 구글 트렌드
    g_kw = google_trends_top()

# --- improved intersection (bidirectional partial match, normalized) ---
def _norm(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"\s+", "", s)             # 공백 제거
    s = re.sub(r"[^\w가-힣]", "", s)       # 특수문자 제거
    return s

yt_norm = [_norm(w) for w in yt_kw_words]
g_norm  = [_norm(g) for g in g_kw]

hot = []
for raw_y, y in zip(yt_kw_words, yt_norm):
    for g in g_norm:
        if y and g and (y in g or g in y):  # 양방향 부분일치
            hot.append(raw_y)               # 원래 표기를 유지
            break

# 순서 보존하며 중복 제거
_seen = set()
hot_intersection = [x for x in hot if not (x in _seen or _seen.add(x))]

# ───────── 쿼터/리셋 정보 ─────────
now_pt = dt.datetime.now(PT)
reset_pt = (now_pt + dt.timedelta(days=1)).replace(hour=0,minute=0,second=0,microsecond=0)
remain_td = reset_pt - now_pt
used = st.session_state["quota_used"]
remaining = max(0, DAILY_QUOTA - used)
pct = min(1.0, used / DAILY_QUOTA)

quota1, quota2 = st.columns([2,1])
with quota1:
    st.subheader("🔋 오늘 쿼터(추정)")
    st.progress(pct, text=f"사용 {used} / {DAILY_QUOTA}  (남은 {remaining})")
with quota2:
    st.metric("남은 쿼터(추정)", value=f"{remaining:,}", delta=f"리셋까지 {str(remain_td).split('.')[0]}")
st.caption("※ YouTube Data API 일일 쿼터는 매일 PT(미국 서부) 자정에 리셋됩니다. (KST 기준 다음날 16~17시, 서머타임 따라 변동)")

# ───────── 보드 상단: 키워드 뷰 ─────────
left, right = st.columns(2)
with left:
    st.subheader("📈 유튜브(48h·상위 풀) 키워드 Top10")
    if yt_kw:
        df_kw = pd.DataFrame(yt_kw, columns=["keyword","count"])
        st.bar_chart(df_kw.set_index("keyword")["count"])
        st.dataframe(df_kw, use_container_width=True, hide_index=True)
        st.download_button("유튜브 키워드 CSV",
                           df_kw.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"),
                           file_name="yt_keywords_top10.csv", mime="text/csv")
    else:
        st.info("키워드를 추출할 데이터가 부족합니다. 수집 규모/페이지를 늘려보세요.")

with right:
    st.subheader("🌐 Google Trends (KR) Top10")
    if g_kw:
        df_g = pd.DataFrame({"keyword": g_kw})
        st.dataframe(df_g, use_container_width=True, hide_index=True)
        st.download_button("구글 트렌드 CSV",
                           df_g.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"),
                           file_name="google_trends_top10.csv", mime="text/csv")
    else:
        st.info("현재 Google Trends 데이터를 가져오지 못했습니다. 잠시 후 다시 시도해주세요.")

st.subheader("🔥 교집합(둘 다 뜨는 키워드)")
if hot_intersection:
    st.write(", ".join(f"`{w}`" for w in hot_intersection))
else:
    st.write("현재 교집합 키워드가 없습니다.")

# ───────── 하단: 결과 테이블 ─────────
st.subheader("🎬 관련 숏츠 리스트")
default_kw = (hot_intersection[0] if hot_intersection
              else (yt_kw_words[0] if yt_kw_words else ""))
pick_kw = st.text_input("키워드로 필터(부분 일치)", value=default_kw)

df_show = df_pool.copy()
if pick_kw.strip():
    pat = re.compile(re.escape(pick_kw.strip()), re.IGNORECASE)
    mask = df_show["title"].str.contains(pat) | df_show["description"].str.contains(pat)
    df_show = df_show[mask]

# 표: 제목-조회수-영상길이-계정명-링크-업로드날짜(시간포함) (+ 옵션)
cols = ["title","view_count","length","channel","url","published_at_kst"]
if show_speed_cols:
    cols = ["title","view_count","views_per_hour","hours_since_upload","length","channel","url","published_at_kst"]

df_show = df_show.sort_values(base_col, ascending=False, ignore_index=True)[cols]
st.dataframe(df_show, use_container_width=True)
st.download_button("현재 표 CSV 다운로드",
                   df_show.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"),
                   file_name="shorts_ranked.csv", mime="text/csv")

# 하단 안내
st.markdown("""
---
**참고**
- 유튜브 API는 업로더 국가를 확정 제공하지 않습니다. 본 앱은 `regionCode=KR`, `relevanceLanguage=ko`로 한국 우선 결과를 가져옵니다.
- 쿼터 비용(추정): `search.list = 100/호출`, `videos.list = 1/호출(50개 단위)`. 수집 규모가 커질수록 비용이 늘어납니다.
- 캐시 TTL을 길게 설정하면 쿼터 사용량을 크게 줄일 수 있습니다.
""")
