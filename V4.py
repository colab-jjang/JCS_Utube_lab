import os
import io
import re
import json
import datetime as dt
from typing import List, Dict, Tuple, Optional
from zoneinfo import ZoneInfo

import requests
import pandas as pd
import streamlit as st

from urllib.parse import unquote

# ==== Cloud backend (read-only, Gist) ====
def _gist_headers():
    tok = st.secrets.get("GH_TOKEN", "")
    return {"Authorization": f"token {tok}"} if tok else {}

def _gist_endpoint(gist_id: str) -> str:
    return f"https://api.github.com/gists/{gist_id}"

def cloud_load_whitelist() -> Optional[set] :
    gist_id = st.secrets.get("GIST_ID")
    fname = st.secrets.get("GIST_FILENAME", "whitelist_channels.json")
    if not gist_id:
        return None
    try:
        r = requests.get(_gist_endpoint(gist_id), headers=_gist_headers(), timeout=20)
        if r.status_code != 200:
            return None
        files = r.json().get("files", {})
        if fname not in files:
            return None
        content = files[fname].get("content", "") or "[]"
        data = json.loads(content)
        if isinstance(data, dict) and "channel_id" in data:
            data = data["channel_id"]
        if isinstance(data, list):
            return set(str(x) for x in data)
    except Exception:
        return None

def cloud_save_whitelist(ch_ids: set) -> bool:
    gist_id = st.secrets.get("GIST_ID")
    fname = st.secrets.get("GIST_FILENAME", "whitelist_channels.json")
    if not gist_id:
        return False
    payload = {
        "files": {
            fname: {
                "content": json.dumps(sorted(list(ch_ids)), ensure_ascii=False, indent=2)
            }
        }
    }
    try:
        r = requests.patch(_gist_endpoint(gist_id),
            headers={**_gist_headers(), "Accept": "application/vnd.github+json"},
            json=payload,
            timeout=20,)
        if r.status_code != 200:
            st.error(f"Gist 저장 실패: {r.status_code} {r.text[:200]}")
        return r.status_code == 200
    except Exception as e:
        st.error(f"Gist 저장 예외: {e}")
        return False

# =========================================================
# 기본 상수/환경
# =========================================================
APP_TITLE = "유튜브 숏츠 키워드/영상 랭킹 v4"
KST = ZoneInfo("Asia/Seoul")
PT = ZoneInfo("America/Los_Angeles")
TTL_SECS_DEFAULT = 3600
COL_ORDER = [
    "title","view_count","length_mmss","channel",
    "like_count","comment_count","published_at_kst",
]

YOUTUBE_API_KEY = (
    (st.secrets.get("YOUTUBE_API_KEY", "") if hasattr(st, "secrets") else "")
    or os.getenv("YOUTUBE_API_KEY", "")
).strip()

API_BASE = "https://www.googleapis.com/youtube/v3"

# =========================================================
# UI 테마(간이 다크/폰트 스케일)
# =========================================================
def apply_theme(dark_mode: bool, font_scale: float):
    st.markdown(
        f"""
        <style>
          html, body, [data-testid=\"stAppViewContainer\"] * {{
            font-size: {font_scale:.2f}rem !important;
          }}
        </style>
        """,
        unsafe_allow_html=True,
    )
    if dark_mode:
        st.markdown(
            """
            <style>
              [data-testid=\"stHeader\"] { background: #111 !important; }
            </style>
            """,
            unsafe_allow_html=True,
        )

# =========================================================
# 강화된 한국어 금지 패턴/단어 (v3 확장)
# =========================================================
COMMON_BANNED_PAT = [
    r"석방 ?하라", r"입 ?닥치고", r"무슨 ?일", r"수 ?있(?:을까|나)", r"수 ?없나",
    r"(?<!\w)#[\w가-힣_]+", r"(?<!\w)@[\w가-힣_]+", r"\b\d{1,2}:\d{2}(?::\d{2})?\b",
    r"\[(?:자막|ENG|SUB|KOR|KO|JP|CN|FULL|4K|LIVE|생방|실시간)\]",
    r"[ㅋㅠㅎ]{2,}", r"[!?]{2,}", r"헉|헐|와우|풉|ㄷㄷ|ㄹㅇ|레전드|실화냐",
    r"충격|경악|소름|대참사|초유의|핵폭탄|대폭로|충격고백|미친|미쳤|초대형|초특급",
    r"단독|속보|긴급|긴급속보|방금전|지금난리|최후통첩|전말|전격|실시간폭로",
    r"왜 ?그랬|어떻게 ?이럴|말이 ?되나|사실 ?인가|믿기 ?어렵|아니 ?근데",
    r"구독|좋아요|알림설정|댓글 ?달|클릭|링크|공유|신청|참여|확인 ?해보|시청 ?해보",
    r"제휴|광고|스폰서|쿠폰|할인코드|프로모션|이벤트|문의 ?주세요|링크 ?고정",
    r"오늘|어제|내일|방금|현재|지금|곧|금일|금주|이번 ?주|이번 ?달|올해|작년|내년",
]
COMMON_STOPWORDS = {
    "다큐디깅","나는 절로","전문","사고","sbs","kbs","mbc","jtbc","tv조선","mbn",
    "것","거","거의","수","등","및","및등","제","그","이","저","요","네","자","우리","저희","너희","당신","여러분","본인","자신",
    "현장","영상","사진","화면","장면","부분","내용","관련","자료","문서","기사","제목","설명","본문","요약","링크","원문","출처","캡처","썸네일","댓글창","채팅",
    "구독","좋아요","알림","알림설정","댓글","클릭","공유","신청","참여","확인","시청","재생","재생목록","플레이리스트","업로드","링크고정","고정댓글",
    "오늘","어제","내일","방금","지금","현재","곧","금일","이번주","이번달","올해","작년","내년","새벽","오전","오후","방송중","생방","실시간",
    "헉","헐","와우","풉","레전드","실화냐","ㄷㄷ","ㄹㅇ","ㅋㅋ","ㅎㅎ","ㅠㅠ",
    "충격","경악","소름","대참사","초유의","핵폭탄","대폭로","충격고백","미친","초대형","초특급","단독","속보","긴급","긴급속보","방금전","난리","최후통첩","전말","전격",
    "제휴","광고","스폰서","쿠폰","할인","할인코드","프로모션","이벤트","문의",
    "사실","이슈","문제","상황","사건","의혹","논란","발표","소식","전망","예상","가능성","확률","계획","보고","분석","점검","검토","결과","진행","현황","공지","공지사항",
    "채널","구독자","조회수","좋아요수","댓글수","조회","좋아요","댓글","업로더","제작진",
    "…","..",".","—","-","_","/",":",";","!","?","#","@",
    "ytn","연합뉴스","연합","한겨레","경향","국민일보","동아일보","조선일보","중앙일보","뉴시스","뉴스1","오마이뉴스","프레시안","sbs뉴스","kbs뉴스","mbc뉴스","jtbc뉴스",
    "관련영상","전체영상","풀영상","풀버전","요약본","다시보기","보도","특집","단신","단독보도","속보보도","생중계","중계","현장중계","인터뷰","직캠","클립","쇼츠",
    "shorts","short","live","full","eng","kor","sub","subs","4k","처리","언론","이미지","추석전","추석 전","미국","시작한다","본회","보도","freepik","전하겠다",
    "영상","뉴스","관련","조회","채널","news",
}
COMMON_STOPWORDS.update({
    "한다","하기","합니다","했다","하였다","시작한다","시작합니다",
    "전한다","전합니다","처리","언론","이미지","freepik",
    "추석전","추석 전"
})
KO_JOSA = [
    "은","는","이","가","을","를","에","에서","에게","께","와","과","도","으로","로","에게서",
    "마다","부터","까지","조차","만","뿐","처럼","같이","보다","의","이라","라","이나","나",
    "든지","라도","라도요","랑","야","요","께서","이나마","부터가","으로서","으로써","로서",
    "로써","마저","밖에","이며","하며","하고","해서","인데","인데요","인데다","인데다가",
    "께요","데요","들","들에","들로","들도","들은","채","하기"
]
EN_STOP = {
    "the","a","an","and","or","but","to","of","for","on","in","at","by","with","from","as","is","are","was","were",
    "be","been","being","it","this","that","these","those","you","your","i","we","they","he","she","him","her","them",
    "my","our","their","me","us","do","does","did","done","can","will","would","should","could","if","so","not"
}

# =========================================================
# 쿼터 추정 (파일에 저장해서 유지)
# =========================================================
DATA_DIR = "."
QUOTA_FILE = os.path.join(DATA_DIR, "quota_usage.json")

def _today_pt_str():
    now_pt = dt.datetime.now(PT)
    return now_pt.strftime("%Y-%m-%d")

def load_quota_used() -> int:
    today = _today_pt_str()
    if os.path.exists(QUOTA_FILE):
        try:
            data = json.loads(open(QUOTA_FILE, "r", encoding="utf-8").read())
            if data.get("pt_date") == today:
                return int(data.get("used", 0))
        except Exception:
            pass
    return 0

def save_quota_used(value: int):
    data = {"pt_date": _today_pt_str(), "used": int(value)}
    with open(QUOTA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

class QuotaMeter:
    COST = {"channels.list": 1, "playlistItems.list": 1,
            "videos.list": 1, "search.list": 100}
    def __init__(self, daily_budget: int = 10000):
        self.daily_budget = daily_budget
        self.used_units = load_quota_used()
    def add(self, api_name: str, calls: int = 1):
        self.used_units += self.COST.get(api_name, 1) * max(1, calls)
        save_quota_used(self.used_units)
    @property
    def remaining(self):
        return max(0, self.daily_budget - self.used_units)
    @staticmethod
    def next_reset_pt():
        now_pt = dt.datetime.now(PT)
        tomorrow = (now_pt + dt.timedelta(days=1)).date()
        return dt.datetime.combine(tomorrow, dt.time(0,0,0), tzinfo=PT)

def get_quota():
    if "quota" not in st.session_state:
        st.session_state["quota"] = QuotaMeter()
    return st.session_state["quota"]

# =========================================================
# 화이트리스트(기본/저장/Secrets) 부트스트랩 + 저장
# =========================================================
DEFAULT_WHITELIST = [
    # 준비되면 실제 채널ID(UC...)를 이 리스트에 채워 넣으세요.
]
WL_STORE_PATH = ".whitelist_channels.json"

def load_whitelist_bootstrap() -> set:
    # (0) 클라우드 우선
    try:
        wl = cloud_load_whitelist()
        if wl:
            return wl
    except Exception:
        pass
    # (1) 저장파일
    try:
        if os.path.exists(WL_STORE_PATH):
            with open(WL_STORE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return set(data)
    except Exception:
        pass
    # (2) secrets
    try:
        if "WHITELIST_CHANNELS" in st.secrets:
            sec = st.secrets["WHITELIST_CHANNELS"]
            if isinstance(sec, list):
                return set(sec)
            if isinstance(sec, str):
                toks = [t.strip() for t in re.split(r"[\n,]+", sec) if t.strip()]
                return set(toks)
    except Exception:
        pass
    # (3) 코드 기본값
    return set(DEFAULT_WHITELIST)


def persist_whitelist(ch_ids: set):
    # 1) 클라우드 먼저
    if cloud_save_whitelist(ch_ids):
        st.success("화이트리스트를 클라우드(Gist)에 저장했습니다.")
        # 캐시용으로 로컬에도 써두기(선택)
        try:
            with open(WL_STORE_PATH, "w", encoding="utf-8") as f:
                json.dump(sorted(list(ch_ids)), f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        return
    # 2) 폴백: 로컬 저장
    try:
        with open(WL_STORE_PATH, "w", encoding="utf-8") as f:
            json.dump(sorted(list(ch_ids)), f, ensure_ascii=False, indent=2)
        st.success("클라우드 저장 실패 → 로컬에 저장했습니다.")
    except Exception as e:
        st.warning(f"화이트리스트 저장 중 경고: {e}")


# =========================================================
# 유틸/파서
# =========================================================
@st.cache_data(show_spinner=False, ttl=TTL_SECS_DEFAULT)
def trending_news_politics(region_code: str, max_pages: int = 1) -> Dict[str, dict]:
    """뉴스·정치(25) mostPopular → 후단에서 24h + Shorts(≤60s) 필터"""
    if not YOUTUBE_API_KEY:
        return {}
    quota = get_quota()
    url = f"{API_BASE}/videos"
    params = {
    "key": YOUTUBE_API_KEY,
    "q": "query",
    "type": "video",
    "part": "snippet",
    "maxResults": 50,
    "order": "date",
    "publishedAfter": published_after_utc,
    "videoDuration": "short",
    "relevanceLanguage": "ko",
    "regionCode": "KR",
    "videoCategoryId": "25",
    "safeSearch": "none",
    }

    out: Dict[str, dict] = {}
    page = 0
    next_token = None
    try:
        while True:
            if next_token:
                params["pageToken"] = next_token
            r = requests.get(url, params=params, timeout=20)
            quota.add("videos.list")
            if r.status_code != 200:
                break
            data = r.json()
            for it in data.get("items", []):
                out[it["id"]] = it
            next_token = data.get("nextPageToken")
            page += 1
            if not next_token or page >= max_pages:
                break
    except Exception as e:
        st.warning(f"트렌드 조회 경고: {e}")
    return out

def iso8601_to_seconds(iso_duration: str) -> int:
    if not iso_duration or not iso_duration.startswith("PT"):
        return 0
    h = m = s = 0
    num = ""
    for ch in iso_duration[2:]:
        if ch.isdigit():
            num += ch
        else:
            if ch == "H":
                h = int(num or 0)
            elif ch == "M":
                m = int(num or 0)
            elif ch == "S":
                s = int(num or 0)
            num = ""
    return h * 3600 + m * 60 + s


def sec_to_mmss(seconds: int) -> str:
    m = seconds // 60
    s = seconds % 60
    return f"{m:02d}:{s:02d}"


def parse_channel_input(text: str) -> List[str]:
    return [t.strip() for t in re.split(r"[\n,]+", text or "") if t.strip()]


@st.cache_data(show_spinner=False, ttl=TTL_SECS_DEFAULT)
def resolve_handle_to_channel_id(handle_or_name: str) -> Optional[str]:
    """@handle 또는 커스텀 URL → 채널 ID 변환 (쿼터 1U만 소모)"""
    if not YOUTUBE_API_KEY:
        return None
    quota = get_quota()
    try:
        r = requests.get(
            f"{API_BASE}/channels",
            params={
                "key": YOUTUBE_API_KEY,
                "forHandle": handle_or_name.lstrip("@"),
                "part": "id",
            },
            timeout=15,
        )
        quota.add("channels.list")  # 1U 소모
        if r.status_code == 200:
            items = r.json().get("items", [])
            if items:
                return items[0]["id"]
    except Exception as e:
        st.warning(f"채널 해석 경고: {e}")
    return None

def extract_channel_id(token: str) -> Optional[str]:
    raw = (token or "").strip()
    print(f"[DEBUG] 입력 token: {raw}")   # 디버그 로그

    token = unquote(raw).strip()
    token = re.sub(r"[/?#]+$", "", token)
    print(f"[DEBUG] 정리된 token: {token}")

    # --- @handle URL 처리 ---
    if "youtube.com/@" in token:
        handle = token.split("youtube.com/@", 1)[1]
        print(f"[DEBUG] handle URL 감지 → {handle}")
        cid = resolve_handle_to_channel_id(handle)
        print(f"[DEBUG] API 변환 결과: {cid}")
        return cid

    if token.startswith("UC") and len(token) >= 10:
        print(f"[DEBUG] 직접 UC ID 감지 → {token}")
        return token

    m = re.search(r"youtube\.com/(channel/|c/|user/|@)([^/?#]+)", token)
    if m:
        kind, key = m.group(1), m.group(2)
        print(f"[DEBUG] 일반 URL 매치 → kind={kind}, key={key}")
        if kind == "channel/":
            return key
        cid = resolve_handle_to_channel_id(key)
        print(f"[DEBUG] API 변환 결과: {cid}")
        return cid

    if token.startswith("@"):
        print(f"[DEBUG] 단순 handle → {token}")
        cid = resolve_handle_to_channel_id(token[1:])
        print(f"[DEBUG] API 변환 결과: {cid}")
        return cid

    # fallback
    print(f"[DEBUG] 기타 케이스 → {token}")
    cid = resolve_handle_to_channel_id(token)
    print(f"[DEBUG] API 변환 결과: {cid}")
    return cid


@st.cache_data(show_spinner=False, ttl=TTL_SECS_DEFAULT)
def playlist_recent_video_ids(playlist_id: str, published_after_utc: str) -> List[Tuple[str, str]]:
    if not YOUTUBE_API_KEY:
        return []
    quota = get_quota()
    out = []
    url = f"{API_BASE}/playlistItems"
    params = {
        "key": YOUTUBE_API_KEY,
        "playlistId": playlist_id,
        "part": "snippet,contentDetails",
        "maxResults": 50,
    }
    try:
        while True:
            r = requests.get(url, params=params, timeout=20)
            quota.add("playlistItems.list")
            if r.status_code != 200:
                break
            data = r.json()
            for it in data.get("items", []):
                vid = it["contentDetails"]["videoId"]
                pub = it["contentDetails"].get("videoPublishedAt") or it["snippet"].get("publishedAt")
                if pub and pub >= published_after_utc:
                    out.append((vid, pub))
            token = data.get("nextPageToken")
            if not token or len(out) > 500:
                break
            params["pageToken"] = token
    except Exception as e:
        st.warning(f"플레이리스트 조회 경고: {e}")
    return out


@st.cache_data(show_spinner=False, ttl=TTL_SECS_DEFAULT)
def fetch_videos_details(video_ids: List[str]) -> Dict[str, dict]:
    if not YOUTUBE_API_KEY or not video_ids:
        return {}
    quota = get_quota()
    url = f"{API_BASE}/videos"
    results: Dict[str, dict] = {}
    try:
        for i in range(0, len(video_ids), 50):
            batch = video_ids[i : i + 50]
            r = requests.get(
                url,
                params={
                    "key": YOUTUBE_API_KEY,
                    "id": ",".join(batch),
                    "part": "snippet,contentDetails,statistics",
                },
                timeout=20,
            )
            quota.add("videos.list")
            if r.status_code != 200:
                continue
            for it in r.json().get("items", []):
                results[it["id"]] = it
    except Exception as e:
        st.warning(f"비디오 상세 조회 경고: {e}")
    return results


@st.cache_data(show_spinner=False, ttl=TTL_SECS_DEFAULT)
def global_search_recent(query: str, published_after_utc: str, max_pages: int = 1) -> List[str]:
    if not YOUTUBE_API_KEY or not query:
        return []
    quota = get_quota()
    out_ids: List[str] = []
    url = f"{API_BASE}/search"
    params = {
        "key": YOUTUBE_API_KEY,
        "q": query,
        "type": "video",
        "part": "snippet",
        "maxResults": 50,
        "order": "date",
        "publishedAfter": published_after_utc,
        "videoDuration": "short",  # <=4분, 후단에서 60초로 재필터
    }
    try:
        page = 0
        while True:
            r = requests.get(url, params=params, timeout=20)
            quota.add("search.list")
            if r.status_code != 200:
                break
            data = r.json()
            for it in data.get("items", []):
                out_ids.append(it["id"]["videoId"])
            token = data.get("nextPageToken")
            page += 1
            if not token or page >= max_pages:
                break
            params["pageToken"] = token
    except Exception as e:
        st.warning(f"전역 검색 경고: {e}")
    return out_ids

@st.cache_data(show_spinner=False, ttl=24*3600)
def fetch_channel_titles(channel_ids: list[str]) -> pd.DataFrame:
    """채널 ID 리스트 → 채널명 매핑 DataFrame.
       YouTube API의 channels.list 호출 (50개씩 배치).
       24시간 캐시 적용."""
    if not channel_ids or not YOUTUBE_API_KEY:
        return pd.DataFrame(columns=["channel_id", "channel_title"])
    
    out = []
    url = f"{API_BASE}/channels"
    quota = get_quota()
    
    for i in range(0, len(channel_ids), 50):
        batch = channel_ids[i:i+50]
        try:
            r = requests.get(
                url,
                params={
                    "key": YOUTUBE_API_KEY,
                    "id": ",".join(batch),
                    "part": "snippet",
                },
                timeout=15,
            )
            quota.add("channels.list")
            if r.status_code == 200:
                for it in r.json().get("items", []):
                    out.append({
                        "channel_id": it.get("id", ""),
                        "channel_title": (it.get("snippet", {}) or {}).get("title", ""),
                    })
        except Exception as e:
            st.warning(f"채널명 조회 경고: {e}")
    
    return pd.DataFrame(out)

# =========================================================
# 키워드(명사구) 추출
# =========================================================

def normalize_text(s: str) -> str:
    s = re.sub(r"https?://\S+", " ", s or "")
    s = re.sub(r"[^\w\s@#\-가-힣A-Za-z0-9]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def trim_josa_ko(token: str) -> str:
    for j in sorted(KO_JOSA, key=len, reverse=True):
        if token.endswith(j) and len(token) > len(j) + 1:
            return token[: -len(j)]
    return token


def extract_keywords(text: str, banned_patterns: List[str], banned_words: set, top_k: int = 20):
    if not text:
        return []
    text = normalize_text(text)
    for pat in banned_patterns:
        text = re.sub(pat, " ", text, flags=re.IGNORECASE)
    tokens = text.split()
    ko_tokens, en_tokens = [], []
    for t in tokens:
        if re.search(r"[가-힣]", t):
            ko_tokens.append(trim_josa_ko(t))
        else:
            tt = t.lower()
            if tt in EN_STOP:
                continue
            en_tokens.append(tt)

     # === 추가 필터링 ===
    def is_valid_word(w: str) -> bool:
        if len(w) < 2:  # 1글자 제거
            return False
        if w.isdigit():  # 숫자 제거
            return False
        if re.match(r"^[0-9]+[a-zA-Z]*$", w):  # ex: 00, 24h, 5g
            return False
        if w in banned_words:  # stopwords
            return False
        return True

    # stopwords 적용 + 최소 길이 필터
    ko_tokens = [t for t in ko_tokens if len(t) >= 2 and t not in banned_words]
    en_tokens = [t for t in en_tokens if len(t) >= 2 and t not in banned_words]

    phrases = ko_tokens + en_tokens   # 🚨 여기서 ngrams 제거!

    def canon(p):
        c = re.sub(r"\s+", "", p.lower())
        c = re.sub(r"[^\w가-힣]", "", c)
        c = trim_josa_ko(c)
        return c

    freq: Dict[str, int] = {}
    display: Dict[str, str] = {}
    for p in set(phrases):
        key = canon(p)
        if not key or key in banned_words or len(key) < 2:
            continue
        if key.endswith(("다","한다","하기","합니다","하였다")):  # 동사형 제거
            continue
        freq[key] = freq.get(key, 0) + 1
        display.setdefault(key, p)

    ranked = sorted(freq.items(), key=lambda x: x[1], reverse=True)[: top_k]
    return [(display[k], c) for k, c in ranked]

def aggregate_keywords(rows: List[dict], banned_patterns: List[str], banned_words: set, top_k: int = 20):
    blob = []
    for r in rows:
        blob.append(r.get("title", ""))
        blob.append(r.get("description", ""))
    pairs = extract_keywords("\n".join(blob), banned_patterns, banned_words, top_k=top_k)
    return pd.DataFrame([{"keyword": k, "count": c} for k, c in pairs])

# --- Helper: 화이트리스트 키워드 랭킹 생성 ---
def build_keyword_ranking(rows_all: List[Dict], banned_patterns: List[str], banned_words: set, top_k: int = 300) -> pd.DataFrame:
    """화이트리스트 채널에서 24h 수집된 모든 Shorts를 기반으로 키워드 랭킹 구성.
    각 키워드:
      - channel_overlap: 키워드가 등장한 '서로 다른 채널' 수
      - top_view_count: 그 키워드 포함 영상 중 최대 조회수
      - top_channel/top_url: 최대 조회수 영상의 채널/URL
    정렬: top_view_count 오름차순.
    """
    keyword_to_channels: Dict[str, set] = {}
    keyword_to_best: Dict[str, Tuple[int, str, str]] = {}
    for r in rows_all:
        title = r.get("title", "")
        desc = r.get("description", "")
        ch = r.get("channel", "")
        url = r.get("url", "")
        views = int(r.get("view_count", 0) or 0)
        kv_pairs = extract_noun_phrases(f"{title}\n{desc}", banned_patterns, banned_words, top_k=top_k)
        for kw in {k for k,_ in kv_pairs}:  # 비디오 내 중복 제거
            keyword_to_channels.setdefault(kw, set()).add(ch)
            best = keyword_to_best.get(kw)
            if best is None or views > best[0]:
                keyword_to_best[kw] = (views, ch, url)
    recs = []
    for kw, ch_set in keyword_to_channels.items():
        best_views, best_ch, best_url = keyword_to_best.get(kw, (0, "", ""))
        recs.append({
            "keyword": kw,
            "channel_overlap": len(ch_set),
            "top_view_count": int(best_views),
            "top_channel": best_ch,
            "top_url": best_url,
        })
    dfk = pd.DataFrame(recs)
    if dfk.empty:
        return dfk
    return dfk.sort_values(["top_view_count", "channel_overlap", "keyword"], ascending=[True, False, True]).reset_index(drop=True)

# =========================================================
# Streamlit 시작
# =========================================================
st.set_page_config(page_title=APP_TITLE, layout="wide")
st.title(APP_TITLE)

# 세션 부트스트랩
if "quota" not in st.session_state:
    st.session_state["quota"] = QuotaMeter()
if "whitelist_ids" not in st.session_state:
    st.session_state["whitelist_ids"] = load_whitelist_bootstrap()

# ---------------------------------------------------------
# 사이드바
# ---------------------------------------------------------
with st.sidebar:
    st.subheader("데이터 소스")
    data_source = st.radio(
        "수집 모드",
        ["전체 트렌드(뉴스·정치)", "등록 채널 랭킹", "전역 키워드 검색"],
        index=0,
    )

    st.subheader("정렬")
    metric = st.selectbox("정렬 기준", ["view_count", "views_per_hour", "comment_count", "like_count"], index=0)
    ascending = st.toggle("오름차순 정렬", value=False)

    st.caption("캐시 TTL: 1시간(고정) • 수집 창: 최근 24시간(고정) • Shorts ≤ 60초(고정)")

    if data_source == "등록 채널 랭킹":
        st.subheader("화이트리스트 관리")
        wl_ids = set(st.session_state.get("whitelist_ids", set()))
            
       # (1) 클라우드에서 불러오기 버튼
        if st.button("저장된 화이트리스트 보기", use_container_width=True):
            wl_cloud = cloud_load_whitelist()
            if wl_cloud is None:
                st.error("클라우드(Gist)에서 불러올 수 없습니다. (토큰/GIST_ID/파일명/네트워크 확인)")
            else:
                st.caption(f"클라우드 화이트리스트 채널 수: {len(wl_cloud)}개")
        
                if len(wl_cloud) == 0:
                    st.info("클라우드에 현재 채널이 0개입니다. (저장 버튼으로 채널을 올려주세요)")
                else:
                    df_view = fetch_channel_titles(sorted(list(wl_cloud)))
                    if not df_view.empty:
                        st.dataframe(df_view[["channel_title"]], use_container_width=True, height=250)
                    else:
                        # API 키 없거나 매핑 실패하면 ID라도 표시
                        st.dataframe(pd.DataFrame({"channel_title": sorted(list(wl_cloud))}),
                                     use_container_width=True, height=250)

        # (2) 업로드 (CSV/XLSX)
        wl_file = st.file_uploader(
            "CSV 또는 XLSX 업로드 (channel_id / handle / url)", 
            type=["csv", "xlsx"], key="whitelist_file"
        )
        if wl_file:
            try:
                if wl_file.name.lower().endswith(".csv"):
                    df_w = pd.read_csv(wl_file)
                else:
                    df_w = pd.read_excel(wl_file)
        
                cols = {c.strip().lower(): c for c in df_w.columns}
        
                if "channel_id" in cols:
                    raw_list = [str(x) for x in df_w[cols["channel_id"]].dropna().tolist()]
                elif "handle" in cols:
                    raw_list = [f"@{str(x).lstrip('@')}" for x in df_w[cols["handle"]].dropna().tolist()]
                elif "url" in cols:
                    raw_list = [str(x) for x in df_w[cols["url"]].dropna().tolist()]
                else:
                    st.warning("CSV/XLSX에 channel_id / handle / url 컬럼 중 하나가 필요합니다.")
        
                # === [여기 삽입] 파일 컬럼/원본 리스트 확인 ===
                #st.write("파일 컬럼명:", df_w.columns.tolist())
                #st.write("raw_list (원본):", raw_list)
        
                # === 여기서 ID 변환 ===
                added = []
                for tok in raw_list: 
                    cid = extract_channel_id(tok) 
                    if cid: 
                        added.append(cid)
        
                # === [여기 삽입] 변환 결과 확인 ===
                #st.write("추출된 채널ID (added):", added)
                
                st.caption(f"추가된 채널 수: {len(added)} (총 {len(wl_ids)})")
                
                wl_ids.update(added)
                st.session_state["whitelist_ids"] = wl_ids
        
                # === ID → 채널명 매핑 ===
                df_titles = fetch_channel_titles(list(added))
                if not df_titles.empty:
                    st.session_state["_id2title"] = {r["channel_id"]: r["channel_title"] for _, r in df_titles.iterrows()}
        
                st.caption(f"추가된 채널 수: {len(added)} (총 {len(wl_ids)})")
        
            except Exception as e:
                st.warning(f"화이트리스트 파일 파싱 오류: {e}")
        
        # (3) 수동 추가/제거 UI (버튼 분리)
        new_tokens = st.text_area("수동 추가 (@handle / URL / channel_id)", height=80, placeholder="@KBSNEWS, https://www.youtube.com/@jtbcnews")
        if st.button("선택 추가", use_container_width=True):
            added = []
            for tok in parse_channel_input(new_tokens):
                cid = extract_channel_id(tok)
                if cid:
                    added.append(cid)
            wl_ids.update(added)
            st.session_state["whitelist_ids"] = wl_ids
            st.success(f"추가 완료: {len(added)}개 (총 {len(wl_ids)})")
        
        # 선택 삭제 (리스트에서 고르기)
        # 이름으로 보이는 멀티셀렉트(내부 값은 ID)
        id2title = st.session_state.get("_id2title", {})
        selected_remove = st.multiselect(
            "삭제할 채널 선택",
            options=sorted(list(wl_ids)),
            format_func=lambda cid: id2title.get(cid, cid)
        )
        col_rm1, col_rm2 = st.columns(2)
        with col_rm1:
            if st.button("선택 삭제", use_container_width=True, disabled=not bool(selected_remove)):
                before = len(wl_ids)
                wl_ids = {x for x in wl_ids if x not in set(selected_remove)}
                st.session_state["whitelist_ids"] = wl_ids
                st.success(f"제거 완료: {before - len(wl_ids)}개 (총 {len(wl_ids)})")
        with col_rm2:
            if st.button("전체 비우기", use_container_width=True, disabled=not bool(wl_ids)):
                st.session_state["whitelist_ids"] = set()
                wl_ids = set()
                st.success("화이트리스트를 모두 비웠습니다.")
        
        c1, c2 = st.columns(2)
        with c1:
            if st.button("화이트리스트 저장", use_container_width=True):
                ids_to_save = st.session_state.get("whitelist_ids", set())
                st.caption(f"저장 시도: {len(ids_to_save)}개를 클라우드에 저장합니다.")
                persist_whitelist(ids_to_save)
        with c2:
            if wl_ids:
                wl_csv = io.StringIO()
                pd.DataFrame({"channel_id": sorted(list(wl_ids))}).to_csv(wl_csv, index=False, encoding="utf-8-sig")
                st.download_button("CSV 내려받기", wl_csv.getvalue().encode("utf-8-sig"), file_name="whitelist_channels.csv", mime="text/csv", use_container_width=True)
        
        st.caption(f"현재 적용 채널 수: **{len(wl_ids)}**")
        #------------- 여기까지------------------------------

# API 키 상태 배지(진단용)    
    st.caption(f"YouTube API Key: {'✅ 설정됨' if bool(YOUTUBE_API_KEY) else '❌ 없음'}")

#토큰 잘 들어가는지 확인하는 부분
    st.caption(
        f"Gist Secrets ▶ ID: {'✅' if (st.secrets.get('GIST_ID') or '').strip() else '❌'} · "
        f"Token: {'✅' if (st.secrets.get('GH_TOKEN') or '').strip() else '❌'} · "
        f"File: {st.secrets.get('GIST_FILENAME','(default)')}"
    )


    # 모드별 입력
if data_source == "등록 채널 랭킹":
    mode = st.radio("채널 입력 방식", ["수동 입력", "파일 업로드(CSV/XLSX)"], horizontal=True)
elif data_source == "전역 키워드 검색":
    st.subheader("전역 키워드 검색")
    global_query = st.text_input("검색어(24h 내, Shorts)", placeholder="예) 국회, 대선, 경제, 외교, 안보 ...")
    max_pages = st.slider("검색 페이지 수(쿼터 주의)", 1, 5, 1)
else:
    st.subheader("전체 트렌드(뉴스·정치)")
    region_code = st.selectbox("지역(Region)", ["KR", "US", "JP", "TW", "VN", "TH", "DE", "FR", "GB", "BR"], index=0)
    trend_pages = st.slider("트렌드 페이지 수(×50개)", 1, 5, 1)

    # (UI 숨김) 키워드 금지어 섹션 제거 → 기본값 사용
user_patterns = COMMON_BANNED_PAT
user_stops = COMMON_STOPWORDS

# ---------------------------------------------------------
# 본문: 실행/수집
# ---------------------------------------------------------
now_utc = dt.datetime.now(dt.timezone.utc)
published_after_utc = (now_utc - dt.timedelta(hours=24)).isoformat()
go = st.button("수집/갱신 실행", type="primary")

# 등록 채널 입력(필요 시)
channel_inputs: List[str] = []
if data_source == "등록 채널 랭킹":
    if mode == "수동 입력":
        st.markdown("**채널 입력**: 채널ID(UC…), URL, @handle, 채널명 허용. 쉼표/줄바꿈 구분")
        manual_text = st.text_area(
            "채널 목록",
            placeholder="@KBSNEWS, https://www.youtube.com/@jtbcnews, UCxxxxxxxxxxxxxxxxxxxx",
            height=240,
        )
        channel_inputs = parse_channel_input(manual_text) if manual_text else []
    else:
        up = st.file_uploader(
            "CSV/XLSX 업로드(컬럼: channel_id 또는 url 또는 handle)", type=["csv", "xlsx"], key="channels_csv"
        )
        if up:
            try:
                if up.name.lower().endswith(".csv"):
                    df_up = pd.read_csv(up)
                else:
                    df_up = pd.read_excel(up)
                cols = [c.lower() for c in df_up.columns]
                if "channel_id" in cols:
                    channel_inputs = [str(x) for x in df_up["channel_id"].dropna().tolist()]
                elif "url" in cols:
                    channel_inputs = [str(x) for x in df_up["url"].dropna().tolist()]
                elif "handle" in cols:
                    channel_inputs = [f"@{str(x).lstrip('@')}" for x in df_up["handle"].dropna().tolist()]
                else:
                    st.warning("파일에 channel_id / url / handle 컬럼 중 하나가 필요합니다.")
            except Exception as e:
                st.warning(f"채널 목록 파일 파싱 오류: {e}")

if go:
    if not YOUTUBE_API_KEY:
        st.error("YouTube API 키가 없습니다. .streamlit/secrets.toml 또는 환경변수에 설정하세요.")
        st.stop()

    rows: List[Dict] = []
    try:
        if data_source == "전체 트렌드(뉴스·정치)":
            url = f"{API_BASE}/videos"
            params = {
                "key": YOUTUBE_API_KEY,
                "part": "snippet,contentDetails,statistics",
                "chart": "mostPopular",
                "videoCategoryId": "25",   # 뉴스·정치
                "regionCode": region_code,
                "maxResults": 50,
            }
            video_ids = []
            for _ in range(trend_pages):
                r = requests.get(url, params=params, timeout=20)
                get_quota().add("videos.list")
                if r.status_code != 200:
                    break
                data = r.json()
                for it in data.get("items", []):
                    vid = it["id"]
                    cd = it.get("contentDetails", {})
                    sp = it.get("snippet", {}) or {}
                    stt = it.get("statistics", {}) or {}
        
                    dur = iso8601_to_seconds(cd.get("duration", "PT0S"))
                    if dur > 120:   # Shorts 120초 이하
                        continue
                    pub = sp.get("publishedAt")
                    if not pub:
                        continue
                    pub_dt = dt.datetime.fromisoformat(pub.replace("Z", "+00:00"))
                    #if pub_dt < dt.datetime.fromisoformat(published_after_utc):
                        #continue
        
                    views = int(stt.get("viewCount", 0) or 0)
                    likes = int(stt.get("likeCount", 0) or 0)
                    comments = int(stt.get("commentCount", 0) or 0)
                    hours = max((now_utc - pub_dt).total_seconds() / 3600, 1 / 60)
                    vph = views / hours
                    pub_kst = pub_dt.astimezone(KST)
        
                    rows.append(
                        {
                            "video_id": vid,
                            "title": sp.get("title", ""),
                            "description": sp.get("description", ""),
                            "channel": sp.get("channelTitle", ""),
                            "length_sec": dur,
                            "length_mmss": sec_to_mmss(dur),
                            "view_count": views,
                            "like_count": likes,
                            "comment_count": comments,
                            "views_per_hour": vph,
                            "published_at_kst": pub_kst.strftime("%Y-%m-%d %H:%M:%S"),
                            "url": f"https://www.youtube.com/watch?v={vid}",
                        }
                    )
                token = data.get("nextPageToken")
                if not token:
                    break
                params["pageToken"] = token

        elif data_source == "전역 키워드 검색":
            if not global_query:
                st.warning("전역 검색 모드에서는 검색어가 필요합니다.")
            else:
                ids = global_search_recent(global_query, published_after_utc, max_pages=max_pages)
                details = fetch_videos_details(ids)
                for vid, it in details.items():
                    cd = it.get("contentDetails", {})
                    sp = it.get("snippet", {}) or {}
                    stt = it.get("statistics", {}) or {}
                    dur = iso8601_to_seconds(cd.get("duration", "PT0S"))
                    if dur > 180:
                        continue
                    pub = sp.get("publishedAt")
                    if not pub:
                        continue
                    pub_dt = dt.datetime.fromisoformat(pub.replace("Z", "+00:00"))
                    ch_id = sp.get("channelId", "")

                    views = int(stt.get("viewCount", 0) or 0)
                    likes = int(stt.get("likeCount", 0) or 0)
                    comments = int(stt.get("commentCount", 0) or 0)
                    hours = max((now_utc - pub_dt).total_seconds() / 3600, 1 / 60)
                    vph = views / hours
                    pub_kst = pub_dt.astimezone(KST)

                    rows.append(
                        {
                            "video_id": vid,
                            "title": sp.get("title", ""),
                            "description": sp.get("description", ""),
                            "channel": sp.get("channelTitle", ""),
                            "length_sec": dur,
                            "length_mmss": sec_to_mmss(dur),
                            "view_count": views,
                            "like_count": likes,
                            "comment_count": comments,
                            "views_per_hour": vph,
                            "published_at_kst": pub_kst.strftime("%Y-%m-%d %H:%M:%S"),
                            "url": f"https://www.youtube.com/watch?v={vid}",
                        }
                    )

        else:  # 등록 채널 랭킹
            if not channel_inputs:
                st.warning("채널을 입력(또는 업로드)하세요.")
            else:
                def channel_title_and_uploads(cid: str):
                    r = requests.get(
                        f"{API_BASE}/channels",
                        params={"key": YOUTUBE_API_KEY, "id": cid, "part": "snippet,contentDetails"},
                        timeout=15,
                    )
                    get_quota().add("channels.list")
                    if r.status_code == 200:
                        items = r.json().get("items", [])
                        if items:
                            return (
                                items[0]["snippet"]["title"],
                                items[0]["contentDetails"]["relatedPlaylists"]["uploads"],
                            )
                    return None, None

                ch_ids: List[str] = []
                for token in channel_inputs:
                    cid = extract_channel_id(token)
                    if cid:
                        ch_ids.append(cid)
                    else:
                        st.warning(f"채널 해석 실패: {token}")
                ch_ids = list(dict.fromkeys(ch_ids))

                all_video_pairs: List[Tuple[str, str]] = []
                ch_name_map: Dict[str, str] = {}
                for cid in ch_ids:
                    try:
                        ch_title, pid = channel_title_and_uploads(cid)
                        if not pid:
                            st.warning(f"업로드 목록을 찾지 못했습니다: {cid}")
                            continue
                        ch_name_map[cid] = ch_title or cid
                        pairs = playlist_recent_video_ids(pid, published_after_utc)
                        all_video_pairs.extend(pairs)
                    except Exception as e:
                        st.warning(f"채널 처리 경고({cid}): {e}")

                details = fetch_videos_details([v for v, _ in all_video_pairs])
                for vid, pub in all_video_pairs:
                    it = details.get(vid)
                    if not it:
                        continue
                    cd = it.get("contentDetails", {})
                    sp = it.get("snippet", {}) or {}
                    stt = it.get("statistics", {}) or {}
                    dur = iso8601_to_seconds(cd.get("duration", "PT0S"))
                    if dur > 180:
                        continue
                    ch_id = sp.get("channelId", "")
                    wl = st.session_state.get("whitelist_ids", set())
                    if wl and ch_id not in wl:
                        continue
                    title = sp.get("title", "")
                    desc = sp.get("description", "")
                    ch = sp.get("channelTitle", "") or ch_name_map.get(ch_id, "")
                    pub_kst = dt.datetime.fromisoformat(pub.replace("Z", "+00:00")).astimezone(KST)
                    views = int(stt.get("viewCount", 0) or 0)
                    likes = int(stt.get("likeCount", 0) or 0)
                    comments = int(stt.get("commentCount", 0) or 0)
                    hours = max((now_utc - dt.datetime.fromisoformat(pub.replace("Z", "+00:00"))).total_seconds() / 3600, 1 / 60)
                    vph = views / hours
                    rows.append(
                        {
                            "video_id": vid,
                            "title": title,
                            "description": desc,
                            "channel": ch,
                            "length_sec": dur,
                            "length_mmss": sec_to_mmss(dur),
                            "view_count": views,
                            "like_count": likes,
                            "comment_count": comments,
                            "views_per_hour": vph,
                            "published_at_kst": pub_kst.strftime("%Y-%m-%d %H:%M:%S"),
                            "url": f"https://www.youtube.com/watch?v={vid}",
                        }
                    )

        # ---- 출력/CSV/키워드 ----

        df = pd.DataFrame(rows)
        if df.empty:
            st.info("조건에 맞는 24시간 내 Shorts 데이터가 없습니다.")
        else:
            sort_by = metric if metric in df.columns else "view_count"
            df_sorted = df.sort_values(by=sort_by, ascending=ascending, kind="mergesort").reset_index(drop=True)
            df_top = df_sorted.head(20)

            # ---- 등록 채널 전체 중 조회수 Top N ----
            if data_source == "등록 채널 랭킹" and not df.empty:
                st.subheader("등록 채널 전체 조회수 Top 영상")
                df_views = df.sort_values(by="view_count", ascending=False).reset_index(drop=True)
                df_top_views = df_views.head(20)   # Top 20
                
                show_cols_views = [c for c in COL_ORDER if c in df_top_views.columns] + ["url"]
                st.dataframe(df_top_views[show_cols_views], use_container_width=True)
    
                # CSV 다운로드 버튼
                csv_buf2 = io.StringIO()
                df_top_views[show_cols_views].to_csv(csv_buf2, index=False, encoding="utf-8-sig")
                st.download_button(
                    "등록 채널 Top20 조회수 CSV 다운로드",
                    data=csv_buf2.getvalue().encode("utf-8-sig"),
                    file_name="registered_channels_top20_views.csv",
                    mime="text/csv",
                )

            
            # ===== 화이트리스트 전체 영상 중 조회수 Top N =====
#            st.subheader("화이트리스트 전체 조회수 Top 영상")
 #           df_views = df.sort_values(by="view_count", ascending=False).reset_index(drop=True)
  #          df_top_views = df_views.head(20)
   # 
    #        show_cols_views = [c for c in COL_ORDER if c in df_top_views.columns] + ["url"]
     #       st.dataframe(df_top_views[show_cols_views], use_container_width=True)
    
            # CSV 다운로드 버튼
            #csv_buf2 = io.StringIO()
            #df_top_views[show_cols_views].to_csv(csv_buf2, index=False, encoding="utf-8-sig")
           # st.download_button(
             #   "화이트리스트 Top20 조회수 CSV 다운로드",
            #    data=csv_buf2.getvalue().encode("utf-8-sig"),
           #     file_name="whitelist_top20_views.csv",
            #    mime="text/csv",
           # )
    
            # ===== 기존 Top 20 랭킹 =====
            show_cols = [c for c in COL_ORDER if c in df_top.columns]
            st.subheader("Top 20 랭킹")
            st.dataframe(df_top[show_cols], use_container_width=True)
    
            # 영상 Top20 CSV (링크 포함)
            csv_buf = io.StringIO()
            out_cols = [
                "title",
                "view_count",
                "length_mmss",
                "channel",
                "like_count",
                "comment_count",
                "published_at_kst",
                "url",
            ]
            df_top[out_cols].to_csv(csv_buf, index=False, encoding="utf-8-sig")
            st.download_button(
                "영상 Top20 CSV 다운로드",
                data=csv_buf.getvalue().encode("utf-8-sig"),
                file_name="shorts_top20.csv",
                mime="text/csv",
            )
    
            # ===== 키워드 Top20 =====
            kw_df = aggregate_keywords(
                rows=df_top.to_dict(orient="records"),
                banned_patterns=user_patterns,
                banned_words=user_stops,
            )
            st.subheader("키워드 Top20")
            st.dataframe(kw_df, use_container_width=True)
            kw_buf = io.StringIO()
            kw_df.to_csv(kw_buf, index=False, encoding="utf-8-sig")
            st.download_button(
                "키워드 Top20 CSV 다운로드",
                data=kw_buf.getvalue().encode("utf-8-sig"),
                file_name="keywords_top20.csv",
                mime="text/csv",
            )
    
            # ===== 화이트리스트 키워드 랭킹 =====
            if data_source == "등록 채널 랭킹":
                st.subheader("화이트리스트 키워드 랭킹 (24h, 조회수 오름차순)")
                kw_rank_df = build_keyword_ranking(rows, user_patterns, user_stops, top_k=300)
                if kw_rank_df.empty:
                    st.info("키워드가 추출되지 않았습니다.")
                else:
                    st.dataframe(kw_rank_df, use_container_width=True)
                    kwr_buf = io.StringIO()
                    kw_rank_df.to_csv(kwr_buf, index=False, encoding="utf-8-sig")
                    st.download_button(
                        "키워드 랭킹 CSV 다운로드",
                        data=kwr_buf.getvalue().encode("utf-8-sig"),
                        file_name="keyword_ranking_24h.csv",
                        mime="text/csv",
                    )
    
    except Exception as e:
        st.warning(f"실행 도중 경고: {e}")


# ---------------------------------------------------------
# 쿼터 현황(세션 추정)
# ---------------------------------------------------------
st.divider()
st.subheader("API 쿼터 현황(세션 추정)")
quota = get_quota()
reset = QuotaMeter.next_reset_pt()
now_pt = dt.datetime.now(PT)
remain_sec = max(0, int((reset - now_pt).total_seconds()))
h, m, s = remain_sec // 3600, (remain_sec % 3600) // 60, remain_sec % 60

used = int(quota.used_units)
budget = int(quota.daily_budget)
remaining = max(0, budget - used)
pct = 0 if budget == 0 else min(used / budget, 1.0)

c1, c2, c3 = st.columns([3,2,2])
with c1:
    st.write("사용량")
    st.progress(pct, text=f"{used:,} / {budget:,}U  ({pct*100:.1f}%)")
with c2:
    st.metric("남은량", f"{remaining:,}U")
with c3:
    st.metric("리셋까지", f"{h:02d}:{m:02d}:{s:02d}")

st.caption("※ 실제 쿼터는 Google Cloud Console 기준이며, 이 값은 세션 내 추정치입니다.")
st.caption("© v4 · Shorts 전용, 24시간 내 업로드, 캐시 TTL=1h, 오류 시 경고만 출력")
