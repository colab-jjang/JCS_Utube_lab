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
    # 유효/무효 호출 모두 비용 발생(보수적 추정)
    used = st.session_state.get("quota_used", 0) + int(cost)
    st.session_state["quota_used"] = used
    # 파일 동기화
    cur = load_quota_used()
    save_quota_used(cur + int(cost))

# 세션 초기화(파일과 동기화)
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
    """pages: 1≈50, 2≈100, 4≈200 / bucket: TTL 분리용 키"""
    _ = bucket  # 캐시 키로만 사용
    start_iso, end_iso, _now = kst_window_last_48h()

    # 1) ID 수집 (search.list = 100/호출)
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
STOPWORDS |= {"속보","브리핑","단독","현장","영상","뉴스","기자","리포트","라이브","연합뉴스","채널","구독","대통령","유튜브","정치","홈페이지","대한민국","금지","시사","모아","답해주세요"}
STOPWORDS |= {"http","https","www","com","co","kr","net","org",
              "youtu","youtube","be","shorts","watch","tv",
              "news","live","breaking","official","channel",
              "video","clip","yonhap","yonhapnews"}

KO_JOSA   = ("은","는","이","가","을","를","의","에","에서","에게","께","와","과","으로","로","도","만","까지","부터","마다","조차","라도","마저","밖에","처럼","뿐","께서")
KO_SUFFIX = ("하기","하세요","하십시오","해주세요","합니다","했다","중","관련","영상","채널","뉴스","보기","등록","구독","홈페이지","됩니다")

def strip_korean_suffixes(t: str) -> str:
    for suf in KO_SUFFIX:
        if t.endswith(suf) and len(t) > len(suf)+1:
            t = t[:-len(suf)]
    for j in KO_JOSA:
        if t.endswith(j) and len(t) > len(j)+1:
            t = t[:-len(j)]
    return t

def tokenize_ko_en(text: str):
    text = str(text or "")
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"www\.\S+", " ", text)
    text = re.sub(r"\S+@\S+", " ", text)
    text = re.sub(r"[#@_/\\]", " ", text)

    raw = re.findall(r"[0-9A-Za-z가-힣]+", text.lower())
    out = []
    for t in raw:
        if not t or t.isdigit(): 
            continue

        if re.fullmatch(r"[가-힣]+", t):
            t = strip_korean_suffixes(t)

        if t in STOPWORDS or len(t) < 2:
            continue

        if re.fullmatch(r"[a-z]+", t) and len(t) <= 2:
            continue

        if t in STOPWORDS or len(t) < 2:
            continue

        if t.endswith("tv") and len(t) > 2:
            t = t[:-2]
            if t in STOPWORDS or len(t) < 2:
                continue

        out.append(t)
    return out

def top_keywords_from_df(df: pd.DataFrame, topk:int=10):
    corpus = (df["title"].fillna("") + " " + df["description"].fillna("")).tolist()
    cnt = Counter()
    for line in corpus:
        cnt.update(tokenize_ko_en(line))
    items = [(w,c) for w,c in cnt.most_common() if not re.fullmatch(r"\d+", w)]
    return items[:topk]

# ───────── 트렌드 소스 (구글/네이버/유튜브) ─────────
TREND_STOPWORDS = {
    "https","http","www","com","co","kr","net","org","youtube","shorts","watch",
    "채널","구독","뉴스","라이브","브리핑","속보","영상","기자","리포트","공식",
    "yonhap","yonhapnews","연합뉴스","tv","전문","시작","전문가","네이버","홈페이지"
}

def _clean_words(words):
    out = []
    for w in words:
        w = (w or "").strip()
        if not w: 
            continue
        w_low = w.lower()
        if w_low in TREND_STOPWORDS:
            continue
        if re.fullmatch(r"[a-z]+", w_low) and len(w_low) <= 2:
            continue
        out.append(w)
    seen=set(); uniq=[]
    for x in out:
        if x not in seen:
            uniq.append(x); seen.add(x)
    return uniq[:10]

@st.cache_data(show_spinner=False, ttl=900)
def google_trends_top(source_mode: str = "auto"):
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json,text/plain,*/*",
        "Referer": "https://trends.google.com/",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    }
    bases = ["https://trends.google.com", "https://trends.google.co.kr"]

    def _google_daily():
        for base in bases:
            try:
                url = f"{base}/trends/api/dailytrends"
                r = requests.get(url, headers=headers,
                                 params={"hl":"ko","tz":"540","geo":"KR"},
                                 timeout=15, allow_redirects=True)
                r.raise_for_status()
                data = json.loads(r.text.lstrip(")]}',\n "))
                days = data.get("default", {}).get("trendingSearchesDays", [])
                if not days: 
                    continue
                items = days[0].get("trendingSearches", [])
                kws = [it.get("title", {}).get("query", "") for it in items if it.get("title")]
                return _clean_words(kws), "google-daily"
            except Exception:
                continue
        return [], "none"

    def _google_realtime():
        for base in bases:
            try:
                url = f"{base}/trends/api/realtimetrends"
                r = requests.get(
                    url, headers=headers,
                    params={"hl":"ko","tz":"540","cat":"all","fi":0,"fs":0,"geo":"KR","ri":300,"rs":20},
                    timeout=15, allow_redirects=True
                )
                r.raise_for_status()
                data = json.loads(r.text.lstrip(")]}',\n "))
                stories = data.get("storySummaries", {}).get("trendingStories", [])
                kws = []
                for s in stories:
                    for e in s.get("entityNames", []):
                        e = (e or "").strip()
                        if e and e not in kws:
                            kws.append(e)
                return _clean_words(kws), "google-realtime"
            except Exception:
                continue
        return [], "none"

    def _google_rss():
        for base in bases:
            try:
                url = f"{base}/trends/trendingsearches/daily/rss?geo=KR&hl=ko"
                r = requests.get(url, headers={"User-Agent": headers["User-Agent"], "Accept":"application/rss+xml"},
                                 timeout=15, allow_redirects=True)
                r.raise_for_status()
                root = ET.fromstring(r.content)
                titles = []
                for item in root.findall(".//item"):
                    t = (item.findtext("title") or "").strip()
                    if t: titles.append(t)
                    if len(titles) >= 10: break
                return _clean_words(titles), "google-rss"
            except Exception:
                continue
        return [], "none"

def _naver_fallback():
    """
    네이버 인기/정치 뉴스 여러 페이지를 순차 시도해서 제목을 수집한다.
    페이지 구조 변경에 대비해 다중 셀렉터 사용.
    """
    headers = {"User-Agent": "Mozilla/5.0"}
    urls = [
        # 인기 뉴스 (일간)
        "https://news.naver.com/main/ranking/popularDay.naver",
        # 정치 섹션
        "https://news.naver.com/section/100",
        # 메인 뉴스(클러스터)
        "https://news.naver.com/",
    ]
    selectors = [
        # 인기뉴스 랭킹
        "ol.ranking_list a",
        "div.rankingnews_box a",
        # 정치 섹션 목록/타이틀
        "ul.sa_list a.sa_text_title",
        "a.sa_text_title_link",
        # 메인 클러스터
        "a.cluster_text_headline",
        # 그 외 일반 타이틀
        "a[href*='/read?']",
    ]

    texts = []
    for url in urls:
        try:
            r = requests.get(url, headers=headers, timeout=12)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            got = False
            for css in selectors:
                for a in soup.select(css):
                    t = a.get_text(" ", strip=True)
                    if t:
                        texts.append(t)
                        got = True
            if got:  # 이 URL에서 뭔가 얻었으면 다음 URL은 스킵
                break
        except Exception:
            continue

    if not texts:
        return [], "none"

    # 토큰화 & 카운트
    cnt = Counter()
    for line in texts:
        toks = re.findall(r"[0-9A-Za-z가-힣]+", line.lower())
        for t in toks:
            if t.isdigit(): 
                continue
            if t in TREND_STOPWORDS or len(t) < 2:
                continue
            cnt[t] += 1

    cand = [w for w, _ in cnt.most_common(30)]
    return _clean_words(cand), "naver"

    # 모드별
    if source_mode == "google":
        for fn in (_google_realtime, _google_daily, _google_rss):
            kws, src = fn()
            if kws: return kws, src
        return [], "none"
    if source_mode == "naver":
        kws, src = _naver_fallback()
        return (kws, src) if kws else ([], "none")
    if source_mode == "youtube":
        return [], "youtube"

    # auto: google → naver
    for fn in (_google_realtime, _google_daily, _google_rss):
        kws, src = fn()
        if kws: return kws, src
    kws, src = _naver_fallback()
    if kws: return kws, src
    return [], "none"

# ───────── UI ─────────
st.title("📺 48시간 유튜브 숏츠 트렌드 대시보드 (정치·뉴스)")

with st.sidebar:
    st.header("수집 옵션")
    size = st.selectbox("수집 규모(Shorts 후보 수)", [50, 100, 200], index=1)
    pages = {50:1, 100:2, 200:4}[size]

    ttl_choice = st.selectbox("캐시 TTL(자동 절약)", ["15분","30분(추천)","60분"], index=1)
    ttl_map = {"15분":900, "30분(추천)":1800, "60분":3600}
    ttl_sec = ttl_map[ttl_choice]

    rank_mode = st.radio("정렬 기준", ["상승속도(뷰/시간)", "조회수(총합)"], horizontal=True, index=0)
    sort_order = st.radio("정렬 순서", ["내림차순", "오름차순"], horizontal=True, index=0)
    show_speed_cols = st.checkbox("상승속도/경과시간 컬럼 표시", value=True)

    trend_source = st.radio(
        "트렌드 소스 선택",
        ["자동(구글→네이버)", "구글만", "네이버만", "유튜브만"],
        index=0
    )

    run = st.button("새로고침(데이터 수집)")

# 캐시 구분용 버킷
bucket = int(time.time() // ttl_sec)

# ───────── 데이터 수집 & 가공 ─────────
if run:
    st.cache_data.clear()
    st.success("데이터 새로고침 시작!")

df = fetch_shorts_df(pages=pages, bucket=bucket)

base_col = "views_per_hour" if rank_mode.startswith("상승속도") else "view_count"
ascending_flag = (sort_order == "오름차순")

# 분석용 상위 풀 (최소 50, 최대 df 크기)
base_pool_n = max(50, len(df))
df_pool = df.sort_values(base_col, ascending=ascending_flag, ignore_index=True).head(base_pool_n)

# 유튜브 키워드 Top10
yt_kw = top_keywords_from_df(df_pool, topk=10)
yt_kw_words = [w for w, _ in yt_kw]

# 트렌드 소스 모드 매핑
mode_map = {
    "자동(구글→네이버)": "auto",
    "구글만": "google",
    "네이버만": "naver",
    "유튜브만": "youtube",
}
source_mode = mode_map[trend_source]

# 트렌드 키워드
g_kw, g_src = google_trends_top(source_mode=source_mode)
st.caption(f"트렌드 소스: {g_src if g_kw else 'Unavailable'} · 키워드 {len(g_kw)}개 · 모드={trend_source}")

# ───────── 쿼터/리셋 정보 ─────────
now_pt = dt.datetime.now(PT)
reset_pt = (now_pt + dt.timedelta(days=1)).replace(hour=0,minute=0,second=0,microsecond=0)
remain_td = reset_pt - now_pt
used = st.session_state.get("quota_used", 0)
remaining = max(0, DAILY_QUOTA - used)
pct = min(1.0, used / DAILY_QUOTA)

quota1, quota2 = st.columns([2,1])
with quota1:
    st.subheader("🔋 오늘 쿼터(추정)")
    st.progress(pct, text=f"사용 {used} / {DAILY_QUOTA}  (남은 {remaining})")
with quota2:
    st.metric("남은 쿼터(추정)", value=f"{remaining:,}", delta=f"리셋까지 {str(remain_td).split('.')[0]}")
st.caption("※ YouTube Data API 일일 쿼터는 매일 PT(미국 서부) 자정에 리셋됩니다. (KST 기준 다음날 16~17시, 서머타임 따라 변동)")

# ───────── 상단 보드: 키워드 뷰 ─────────
left, right = st.columns(2)
with left:
    st.subheader("📈 유튜브(48h·상위 풀) 키워드 Top10")
    if yt_kw:
        df_kw = pd.DataFrame(yt_kw, columns=["keyword","count"])
        df_kw_sorted = df_kw.sort_values("count", ascending=ascending_flag)
        st.bar_chart(df_kw_sorted.set_index("keyword")["count"])
        st.dataframe(df_kw_sorted, use_container_width=True, hide_index=True)
        st.download_button("유튜브 키워드 CSV",
                           df_kw_sorted.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"),
                           file_name="yt_keywords_top10.csv", mime="text/csv")
    else:
        st.info("키워드를 추출할 데이터가 부족합니다. 수집 규모/페이지를 늘려보세요.")

with right:
    st.subheader("🌐 Trends Top10")
    if g_kw:
        df_g = pd.DataFrame({"keyword": g_kw})
        st.dataframe(df_g, use_container_width=True, hide_index=True)
        st.download_button("트렌드 키워드 CSV",
                           df_g.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"),
                           file_name="trends_top10.csv", mime="text/csv")
    else:
        st.info("선택한 소스에서 트렌드 키워드를 가져오지 못했습니다. (모드를 바꿔보세요)")

# ───────── 교집합(양쪽 다 뜨는 키워드) ─────────
def _norm(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[^\w가-힣]", "", s)
    return s

yt_norm = [_norm(w) for w in yt_kw_words]
g_norm  = [_norm(g) for g in g_kw]
hot = []
for raw_y, y in zip(yt_kw_words, yt_norm):
    for g in g_norm:
        if y and g and (y in g or g in y):
            hot.append(raw_y); break
_seen=set()
hot_intersection = [x for x in hot if not (x in _seen or _seen.add(x))]

st.subheader("🔥 교집합(둘 다 뜨는 키워드)")
st.write(", ".join(f"`{w}`" for w in hot_intersection) if hot_intersection else "현재 교집합 키워드가 없습니다.")

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

cols = ["title","view_count","length","channel","url","published_at_kst"]
if show_speed_cols:
    cols = ["title","view_count","views_per_hour","hours_since_upload","length","channel","url","published_at_kst"]

df_show = df_show.sort_values(base_col, ascending=ascending_flag, ignore_index=True)[cols]

# 표 유지용(다운로드 후에도 그대로 보이게)
st.session_state["df_show_frozen"] = df_show.copy()
st.dataframe(st.session_state["df_show_frozen"], use_container_width=True)

csv_bytes = st.session_state["df_show_frozen"].to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
st.download_button("현재 표 CSV 다운로드", data=csv_bytes, file_name="shorts_ranked.csv", mime="text/csv", key="dl_df_show")

# ───────── 안내 ─────────
st.markdown("""
---
**참고**
- 유튜브 API는 업로더 국가를 확정 제공하지 않습니다. 본 앱은 `regionCode=KR`, `relevanceLanguage=ko`로 한국 우선 결과를 가져옵니다.
- 쿼터 비용(추정): `search.list = 100/호출`, `videos.list = 1/호출(50개 단위)`. 수집 규모가 커질수록 비용이 늘어납니다.
- 캐시 TTL을 길게 설정하면 쿼터 사용량을 크게 줄일 수 있습니다.
- 트렌드 소스는 *구글(realtime→daily→rss)* 실패 시 *네이버 인기뉴스*로 자동 대체(“자동” 모드)됩니다.
""")
