# -*- coding: utf-8 -*-
# 📺 48시간 유튜브 숏츠 트렌드 대시보드 (정치·뉴스)
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
    """pages: 1≈50, 2≈100, 4≈200 / bucket: TTL 분리용 키"""
    _ = bucket
    start_iso, end_iso, _now = kst_window_last_48h()

    # 1) search.list (100/호출)
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

    # 2) videos.list (1/호출, 50개씩)
    details=[]
    for i in range(0, len(ordered), 50):
        chunk = ordered[i:i+50]
        if not chunk: continue
        params = {"key": API_KEY, "part": "snippet,contentDetails,statistics", "id": ",".join(chunk)}
        data = api_get(VIDEOS_URL, params, cost=1)
        details.extend(data.get("items", []))

    # 3) DF
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

    now_kst = dt.datetime.now(KST)
    if not df.empty:
        df["hours_since_upload"] = (now_kst - pd.to_datetime(df["published_dt_kst"])).dt.total_seconds() / 3600.0
        df["hours_since_upload"] = df["hours_since_upload"].clip(lower=(1.0/60.0))
        df["views_per_hour"] = (df["view_count"] / df["hours_since_upload"]).round(1)
    else:
        df["hours_since_upload"] = []
        df["views_per_hour"] = []
    return df

# ───────── (공용) 금지어/형식어 & 금칙구/시간표현 ─────────
COMMON_STOPWORDS = {
    "http","https","www","com","co","kr","net","org","youtube","shorts","watch","tv","cctv","sns",
    "기사","단독","속보","영상","전문","라이브","기자","보도","헤드라인","데스크","전체보기","더보기",
    "오늘","어제","금일","최근","방금","방금전","아침","오전","오후","밤","새벽","첫날",
    "관련","논란","논쟁","상황","사건","이슈","분석","전망","브리핑","발언","발표","입장",
    "서울","한국","국내","해외","정부","여당","야당","당국","위원장","장관","대통령","총리","국회","검찰",
    "구독","정치","대통령실","채널","news","대법원","특검","민주당","국민의힘","이잼",
    "sbs","kbs","mbc","jtbc","tv조선","mbn","연합뉴스","mbc뉴스","yonhapnews","yonhap","다큐멘터리","내 정보"
    "시작","사고","전문","사진","이제","최고","짧은","긴","최고","내정보"
}

COMMON_BANNED_PAT = re.compile(
    r"(석방 ?하라|입 ?닥치고|무슨 ?일|수 있을까|수 있나|수 없나)",
    re.I
)
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

# ───────── 유튜브 텍스트 토크나이저(키워드) ─────────
STOPWORDS = set("""
그리고 그러나 그래서 또한 또는 및 먼저 지금 바로 매우 정말 그냥 너무 보다 보다도 때는 라는 이런 저런 그런
합니다 했다 했다가 하는 하고 하며 하면 대한 위해 에서 에게 에도 에는 으로 로 를 은 는 이 가 도 의 에 와 과 시작
""".split())
STOPWORDS |= {
    "속보","브리핑","단독","현장","영상","뉴스","기자","리포트","라이브","연합뉴스",
    "채널","구독","대통령","유튜브","정치","홈페이지","대한민국","금지","시사","모아","답해주세요",
    "다큐디깅","나는","절로","석방하라","석방","전문","사고","news","대법원","특검","mbc뉴스","이잼","첫날","뉴스top10"
}
STOPWORDS |= COMMON_STOPWORDS

KO_JOSA = ("은","는","이","가","을","를","의","에","에서","에게","께","와","과","으로","로","도","만","까지","부터","마다","조차","라도","마저","밖에","처럼","뿐","께서","채","고")
KO_SUFFIX = ("하기","하세요","십시오","해주세요","합니다","했다","중","관련","영상","채널","뉴스","보기","등록","구독","홈페이지","됩니다")

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
    if _contains_common_banned(text):
        return []
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
    words = [w for w,_ in items[:topk*3]]
    words = _postprocess_phrases(words, topk=topk)
    cnt2 = Counter()
    for line in corpus:
        toks = tokenize_ko_en(line)
        for t in toks:
            if t in words:
                cnt2[t] += 1
    final = [(w, cnt2[w]) for w in words]
    return final[:topk]

# ───────── Trends 전용 규칙 ─────────
TREND_STOPWORDS = COMMON_STOPWORDS.copy()
_BAD_START = {"무슨","어떤","왜","어째서","어디","누가","누구","언제","얼마나","이번","지난","현직","전직","현재","향후","내년","올해"}
_POSTPOSITION_SUFFIXES = ("으로","로","에게","에서","보다","까지","부터","만","조차","라도","처럼","뿐","께","에","와","과","랑","하고","밖에","이라","라","이라도","은","는","이","가","채","만에","고","려")
_BAD_END_VERB_PAT = re.compile(r"(하라|해라|해주세요|합시다|하자|됐다|된다|더니|해|졌다)$")
_BAD_END_QUESTION_PAT = re.compile(r"(까|나|냐|일까|을까|였나|였을까|나요|\?$)$")
_BAD_END_INTERROGATIVE = {"어디","누구","누가","무엇","뭐","왜","언제","얼마나"}
_WEAK_LAST_TOKENS = {"차림","열람","논쟁","논란","발언","발표","입장","사실","상황","사건","문제","의혹","의심","행위","제기","제안","요청","요구","우려","가능성","목소리","후","전","안","밖","속","중","쪽","내","외","등","건","부분","측면","자료"}
_BANNED_PHRASES = {"무슨 일","입 닥치고","석방하라","석방 하라"}
_BANNED_TOKENS  = {"석방하라","하라"}
_TOKEN_PAT = r"[0-9A-Za-z가-힣]+"

PHRASE_SIM_STOP = {
    "발견","공개","사진","영상","입고","착용","총상","숨진","숨져","사망","사람","채","관련","등","등의",
    "사실","상황","사건","문제","논란","논쟁","발언","입장","보도","당시","현장","추정","추락","폭행","체포","구속","내 정보","떼 쓰려고","jong kim","내정보"
}

def _strip_postposition(token: str) -> str:
    for suf in _POSTPOSITION_SUFFIXES:
        if token.endswith(suf) and len(token) > len(suf):
            stem = token[:-len(suf)]
            if len(stem) >= 2:
                return stem
    return token

def _tok_line_for_trends(s: str) -> List[str]:
    if _contains_common_banned(s):
        return []
    s = s.lower()
    s = re.sub(r"https?://\S+"," ",s); s = re.sub(r"www\.\S+"," ",s)
    toks = re.findall(_TOKEN_PAT, s)
    out=[]
    for t in toks:
        if t.isdigit(): continue
        if t in TREND_STOPWORDS: continue
        if re.fullmatch(r"[a-z]+", t) and len(t)<=2: continue
        out.append(t)
    return out

def _is_bad_phrase(ph: str) -> bool:
    if ph in _BANNED_PHRASES: 
        return True
    ws = ph.split()
    if len(ws) < 2:
        return True
    if ws[0] in _BAD_START:
        return True
    if any(t in _BANNED_TOKENS or t.endswith("하라") for t in ws):
        return True
    last_raw = ws[-1]
    last = _strip_postposition(last_raw)
    if last in _BAD_END_INTERROGATIVE: 
        return True
    if _BAD_END_VERB_PAT.search(last) or _BAD_END_QUESTION_PAT.search(last): 
        return True
    for i, t in enumerate(ws[:-1]):
        if t == "수":
            nxt = ws[i+1] if i+1 < len(ws) else ""
            if nxt.startswith(("있","없")): 
                return True
    if last in _WEAK_LAST_TOKENS: 
        return True
    if len(ws)==2:
        w0 = ws[0]; w1 = last
        if (w0 in TREND_STOPWORDS or w1 in TREND_STOPWORDS or
            w0 in _BAD_START or w1 in _WEAK_LAST_TOKENS):
            return True
    if re.search(r"(어|아)$", last): 
        return True
    return False

def _normalize_phrase(ph: str) -> str:
    ws = ph.split()
    ws[-1] = _strip_postposition(ws[-1])
    if len(ws)==2:
        return " ".join(sorted(ws))
    return " ".join(ws)

def _phrase_signature(ph: str) -> List[str]:
    toks = [_strip_postposition(w) for w in re.findall(r"[0-9A-Za-z가-힣]+", ph.lower())]
    core = []
    for t in toks:
        if not t or t.isdigit():
            continue
        if t in COMMON_STOPWORDS or t in TREND_STOPWORDS or t in PHRASE_SIM_STOP:
            continue
        if len(t) <= 1:
            continue
        core.append(t)
    core.sort(key=lambda x: (-len(x), x))
    return core[:3]

def _collapse_similar(ranked_pairs: List[Tuple[str,float]], topk: int) -> List[str]:
    kept: List[str] = []
    used: List[Tuple[set, str, float]] = []

    def jacc(a: set, b: set) -> float:
        if not a and not b: return 1.0
        return len(a & b) / max(1, len(a | b))

    for ph, sc in ranked_pairs:
        sig = set(_phrase_signature(ph))
        if not sig:
            continue

        dedup = False
        for s, _ph, _sc in used:
            if sig == s or sig.issubset(s) or s.issubset(sig):
                dedup = True; break
            if jacc(sig, s) >= 0.6:
                dedup = True; break
            a = re.sub(r"\s+","", ph)
            b = re.sub(r"\s+","", _ph)
            if a in b or b in a:
                dedup = True; break

        if not dedup:
            kept.append(ph)
            used.append((sig, ph, sc))
        if len(kept) >= topk:
            break

    return kept

def _postprocess_phrases(candidates: List[str], topk: int = 10) -> List[str]:
    cand = [c for c in candidates if not _contains_common_banned(str(c))]
    cand = [re.sub(r"\s+"," ", str(x)).strip() for x in cand if str(x).strip()]
    seen = set(); cand = [x for x in cand if not (x in seen or seen.add(x))]
    if not cand:
        return []
    pairs = [(c, float(len(c))) for c in cand]
    merged = _collapse_similar(pairs, topk=topk)
    return merged

def _extract_top_phrases(lines: List[str], topk: int = 10) -> List[str]:
    docs = []
    for x in lines:
        x = re.sub(r"\s+"," ", (x or "").replace("\u200b"," ")).strip()
        if _contains_common_banned(x):
            continue
        if x and len(x) > 4:
            toks = _tok_line_for_trends(x)
            if len(toks) >= 2:
                docs.append(" ".join(toks))
    if not docs:
        return []
    vec = TfidfVectorizer(
        tokenizer=lambda s: s.split(),
        token_pattern=None, lowercase=False,
        ngram_range=(2,3), min_df=2
    )
    X = vec.fit_transform(docs)
    ngrams = vec.get_feature_names_out()
    scores = np.asarray(X.sum(axis=0)).ravel()

    cand = []
    for ph, sc in zip(ngrams, scores):
        if _is_bad_phrase(ph):
            continue
        norm = _normalize_phrase(ph)
        cand.append((norm, float(sc)))

    if not cand:
        return []

    ranked_pairs = sorted(cand, key=lambda x: x[1], reverse=True)
    return _collapse_similar(ranked_pairs, topk)

# ───────── NAVER: 헤드라인 → 명사구 Top10 ─────────
def _fetch_trends_naver(add_log=None) -> Tuple[List[str], str]:
    headers = {"User-Agent": "Mozilla/5.0"}
    urls = [
        "https://news.naver.com/main/ranking/popularDay.naver",
        "https://news.naver.com/section/100",
        "https://news.naver.com/",
    ]
    selectors = [
        "ol.ranking_list a","div.rankingnews_box a",
        "ul.sa_list a.sa_text_title","a.sa_text_title_link",
        "a.cluster_text_headline","a[href*='/read?']",
    ]
    titles = []
    for u in urls:
        try:
            r = requests.get(u, headers=headers, timeout=12)
            if add_log: add_log(f"[naver] {u} status={r.status_code}")
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "lxml")
            got = []
            for css in selectors:
                for a in soup.select(css):
                    t = a.get_text(" ", strip=True)
                    if t: got.append(t)
            if got:
                titles.extend(got)
        except Exception as e:
            if add_log: add_log(f"[naver] error: {e}")
            continue
    if not titles:
        return [], "none"
    phrases = _extract_top_phrases(titles, topk=20)
    phrases = _postprocess_phrases(phrases, topk=10)
    return phrases, ("naver" if phrases else "none")

# ───────── GOOGLE: Daily RSS → HTML fallback ─────────
@st.cache_data(show_spinner=False, ttl=900)
def google_trends_top(debug_log: bool = False, source_mode: str = "auto"):
    logs = []
    def add(msg):
        if debug_log: logs.append(str(msg))

    def _google_try() -> Tuple[List[str], str]:
        headers = {"User-Agent":"Mozilla/5.0"}
        bases = ["https://trends.google.com", "https://trends.google.co.kr"]

        # A) RSS
        for base in bases:
            try:
                url = f"{base}/trends/trendingsearches/daily/rss?geo=KR&hl=ko"
                r = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
                add(f"[google rss] {r.status_code} {url}")
                r.raise_for_status()
                root = ET.fromstring(r.content)
                titles = []
                for item in root.findall(".//item"):
                    t = (item.findtext("title") or "").strip()
                    if t: titles.append(t)
                    if len(titles) >= 30: break
                titles = [p for p in titles if not _contains_common_banned(p)]
                if titles:
                    phrases = _extract_top_phrases(titles, topk=20)
                    phrases = _postprocess_phrases(phrases, topk=10)
                    if phrases:
                        return phrases, "google-rss"
            except Exception as e:
                add(f"[google rss] error: {e}")

        # B) HTML fallback
        try:
            url = "https://trends.google.com/trends/trendingsearches/daily?geo=KR&hl=ko"
            r = requests.get(url, headers=headers, timeout=15)
            add(f"[google html] {r.status_code} {url}")
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "lxml")
            raw = [a.get_text(strip=True) for a in soup.select("div.feed-item h2 a")]
            raw = [p for p in raw if not _contains_common_banned(p)]
            if raw:
                phrases = _extract_top_phrases(raw, topk=20)
                phrases = _postprocess_phrases(phrases, topk=10)
                if phrases:
                    return phrases, "google-html"
        except Exception as e:
            add(f"[google html] error: {e}")

        return [], "none"

    def _youtube_fallback() -> Tuple[List[str], str]:
        try:
            words = st.session_state.get("yt_kw_words", [])
            words = [w for w in words if not _contains_common_banned(w)]
            words = _postprocess_phrases(words, topk=10)
            return (words[:10], "youtube-fallback") if words else ([], "none")
        except Exception:
            return [], "none"

    if source_mode == "google":
        kws, src = _google_try()
        return kws, src, logs
    if source_mode == "naver":
        kws, src = _fetch_trends_naver(add)
        return kws, src, logs
    if source_mode == "youtube":
        kws, src = _youtube_fallback()
        return kws, src, logs

    # auto
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
    size = st.selectbox("수집 규모(Shorts 후보 수)", [50, 100, 200], index=1)
    pages = {50:1, 100:2, 200:4}[size]

    ttl_choice = st.selectbox("캐시 TTL(자동 절약)", ["15분","30분(추천)","60분"], index=1)
    ttl_map = {"15분":900, "30분(추천)":1800, "60분":3600}
    ttl_sec = ttl_map[ttl_choice]

    rank_mode = st.radio("정렬 기준", ["상승속도(뷰/시간)", "조회수(총합)"], horizontal=True, index=0)
    sort_order = st.radio("정렬 순서", ["내림차순", "오름차순"], horizontal=True, index=0)
    show_speed_cols = st.checkbox("상승속도/경과시간 컬럼 표시", value=True)

    trend_source = st.radio("트렌드 소스 선택", ["자동(구글→네이버)", "구글만", "네이버만", "유튜브만"], index=0)
    trend_debug = st.checkbox("트렌드 디버그 보기", value=False)

    # ✅ 빈결과 시 외부 검색(쿼터 사용) 허용
    allow_live_search = st.checkbox("빈결과일 때 외부 검색(유튜브 API 사용)", value=False)

    run = st.button("새로고침(데이터 수집)")

bucket = int(time.time() // ttl_sec)
if run:
    st.cache_data.clear()
    st.success("데이터 새로고침 시작!")

df = fetch_shorts_df(pages=pages, bucket=bucket)

base_col = "views_per_hour" if rank_mode.startswith("상승속도") else "view_count"
ascending_flag = (sort_order == "오름차순")
base_pool_n = max(50, len(df))
df_pool = df.sort_values(base_col, ascending=ascending_flag, ignore_index=True).head(base_pool_n)

# 유튜브 키워드 Top10 (공용 후처리 반영)
yt_kw = top_keywords_from_df(df_pool, topk=10)
yt_kw_words = [w for w, _ in yt_kw]
yt_kw_words = _postprocess_phrases(yt_kw_words, topk=10)
cnt_vis = Counter()
for _, row in df_pool.iterrows():
    for t in tokenize_ko_en((row["title"] or "") + " " + (row["description"] or "")):
        if t in yt_kw_words:
            cnt_vis[t] += 1
yt_kw = [(w, cnt_vis[w]) for w in yt_kw_words]
st.session_state["yt_kw_words"] = yt_kw_words  # 유튜브 fallback용

# 트렌드 소스 모드
mode_map = {"자동(구글→네이버)":"auto","구글만":"google","네이버만":"naver","유튜브만":"youtube"}
source_mode = mode_map[trend_source]

# 트렌드 키워드
g_kw, g_src, g_logs = google_trends_top(source_mode=source_mode, debug_log=trend_debug)
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
st.caption("※ YouTube Data API 일일 쿼터는 PT 자정(=KST 오후 4~5시경)에 리셋됩니다.")

# ───────── 상단 보드 ─────────
left, right = st.columns(2)
with left:
    st.subheader("📈 유튜브(48h·상위 풀) 키워드 Top10")
    if yt_kw:
        df_kw = pd.DataFrame(yt_kw, columns=["keyword","count"])
        df_kw_sorted = df_kw.sort_values("count", ascending=False)
        st.bar_chart(df_kw_sorted.set_index("keyword")["count"])
        st.dataframe(df_kw_sorted, use_container_width=True, hide_index=True)
        st.download_button("유튜브 키워드 CSV",
                           df_kw_sorted.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"),
                           file_name="yt_keywords_top10.csv", mime="text/csv")
    else:
        st.info("키워드를 추출할 데이터가 부족합니다. 수집 규모/페이지를 늘려보세요.")

with right:
    st.subheader("🌐 Trends Top10")
    if trend_debug:
        with st.expander("🔎 트렌드 디버그 로그/원본"):
            st.write(f"source_mode={source_mode}, src={g_src}")
            st.write(f"raw keywords({len(g_kw)}):", g_kw)
            if g_logs:
                st.code("\n".join(g_logs[-40:]), language="text")

    if g_kw:
        df_g = pd.DataFrame({"keyword": g_kw}).dropna()
        df_g["keyword"] = df_g["keyword"].astype(str).str.strip()
        df_g = df_g[df_g["keyword"] != ""].drop_duplicates("keyword").head(10)
        if len(df_g) >= 1:
            df_g["rank"]  = np.arange(1, len(df_g) + 1, dtype=int)
            df_g["score"] = (len(df_g) + 1) - df_g["rank"]  # 1등=최대
            st.bar_chart(df_g.set_index("keyword")[["score"]])
            st.dataframe(df_g[["rank","keyword"]], use_container_width=True, hide_index=True)
            st.download_button("트렌드 키워드 CSV",
                               df_g[["rank","keyword"]].to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"),
                               file_name="trends_top10.csv", mime="text/csv")
        else:
            st.info("트렌드 키워드가 비어 있습니다. (중복/공백 제거 후 0개)")
    else:
        st.info("선택한 소스에서 트렌드 키워드를 가져오지 못했습니다. (모드를 바꾸거나 디버그로 로그를 확인하세요)")

# ───────── 교집합 ─────────
def _norm(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[^\w가-힣]", "", s)
    return s

yt_norm = [_norm(w) for w in yt_kw_words]
g_norm  = [_norm(g) for g in (g_kw or [])]
hot = []
for raw_y, y in zip(yt_kw_words, yt_norm):
    for g in g_norm:
        if y and g and (y in g or g in y):
            hot.append(raw_y); break
_seen=set()
hot_intersection = [x for x in hot if not (x in _seen or _seen.add(x))]

st.subheader("🔥 교집합(둘 다 뜨는 키워드)")
st.write(", ".join(f"`{w}`" for w in hot_intersection) if hot_intersection else "현재 교집합 키워드가 없습니다.")

# ───────── 라이브 검색 (빈결과 시 즉시 검색) ─────────
def live_search_youtube(q: str, max_items: int = 20) -> pd.DataFrame:
    """
    df_pool에 결과가 없을 때, 같은 48h 창으로 유튜브 API를 즉시 검색해 가져옴.
    search.list(100) + videos.list(1) → 쿼터 사용.
    """
    if not q.strip():
        return pd.DataFrame()
    start_iso, end_iso, _ = kst_window_last_48h()

    # 1) search.list
    params = {
        "key": API_KEY, "part": "snippet", "type":"video", "order":"date",
        "publishedAfter": start_iso, "publishedBefore": end_iso,
        "maxResults": min(50, max_items), "videoDuration":"short",
        "regionCode":"KR", "relevanceLanguage":"ko", "safeSearch":"moderate",
        "q": q.strip(),
    }
    data = api_get(SEARCH_URL, params, cost=100)
    ids = [it.get("id",{}).get("videoId") for it in data.get("items",[]) if it.get("id",{}).get("videoId")]
    ids = list(dict.fromkeys(ids))[:max_items]
    if not ids:
        return pd.DataFrame()

    # 2) videos.list
    params = {"key": API_KEY, "part": "snippet,contentDetails,statistics", "id": ",".join(ids)}
    data = api_get(VIDEOS_URL, params, cost=1)
    rows=[]
    for it in data.get("items", []):
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
    df_live = pd.DataFrame(rows)
    if not df_live.empty:
        now_kst = dt.datetime.now(KST)
        df_live["hours_since_upload"] = (now_kst - pd.to_datetime(df_live["published_dt_kst"])).dt.total_seconds() / 3600.0
        df_live["hours_since_upload"] = df_live["hours_since_upload"].clip(lower=(1.0/60.0))
        df_live["views_per_hour"] = (df_live["view_count"] / df_live["hours_since_upload"]).round(1)
    return df_live

# ───────── 하단: 결과 테이블 ─────────
st.subheader("🎬 관련 숏츠 리스트")
default_kw = (hot_intersection[0] if hot_intersection else (yt_kw_words[0] if yt_kw_words else ""))
pick_kw = st.text_input("키워드로 필터(부분 일치)", value=default_kw)

df_show = df_pool.copy()
if pick_kw.strip():
    pat = re.compile(re.escape(pick_kw.strip()), re.IGNORECASE)
    mask = df_show["title"].str.contains(pat) | df_show["description"].str.contains(pat)
    df_show = df_show[mask]

    # ✅ 빈결과면: 외부 라이브 검색(옵션 ON일 때)
    if df_show.empty and allow_live_search:
        df_live = live_search_youtube(pick_kw.strip(), max_items=20)
        if not df_live.empty:
            st.info("수집 풀에 없어 **외부 실시간 검색 결과**를 보여줍니다. (쿼터 사용)")
            df_show = df_live

cols = ["title","view_count","length","channel","url","published_at_kst"]
if show_speed_cols:
    cols = ["title","view_count","views_per_hour","hours_since_upload","length","channel","url","published_at_kst"]

df_show = df_show.sort_values(base_col, ascending=ascending_flag, ignore_index=True)[cols]

st.session_state["df_show_frozen"] = df_show.copy()
st.dataframe(st.session_state["df_show_frozen"], use_container_width=True)

csv_bytes = st.session_state["df_show_frozen"].to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
st.download_button("현재 표 CSV 다운로드", data=csv_bytes, file_name="shorts_ranked.csv", mime="text/csv", key="dl_df_show")

st.markdown("""
---
**참고**
- 유튜브 API는 업로더 국가를 확정 제공하지 않습니다. 본 앱은 `regionCode=KR`, `relevanceLanguage=ko`로 한국 우선 결과를 가져옵니다.
- 쿼터 비용(추정): `search.list = 100/호출`, `videos.list = 1/호출(50개 단위)`. 수집 규모가 커질수록 비용이 늘어납니다.
- 캐시 TTL을 길게 설정하면 쿼터 사용량을 크게 줄일 수 있습니다.
- 트렌드 소스는 *구글(RSS→HTML)* 실패 시 *네이버 인기뉴스* 또는 *유튜브 키워드*로 자동 대체됩니다.
""")
