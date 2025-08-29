import streamlit as st
import pandas as pd
import datetime as dt
from zoneinfo import ZoneInfo
import requests

# ====== Settings ======
API_KEY = st.secrets.get("YOUTUBE_API_KEY", "")
REGION_CODE = "KR"         # 한국 결과 우선
RELEVANCE_LANG = "ko"      # 한국어 우선
KST = ZoneInfo("Asia/Seoul")
PT  = ZoneInfo("America/Los_Angeles")
DAILY_QUOTA = 10_000       # YouTube Data API 기본 일일 쿼터

# 쿼터 세션 누적시킴

import json, os
from pathlib import Path

DATA_DIR = Path(".")
QUOTA_FILE = DATA_DIR / "quota_usage.json"   # 앱 폴더에 저장 (앱이 살아있는 한 유지)

def _today_pt_str():
    from zoneinfo import ZoneInfo
    PT = ZoneInfo("America/Los_Angeles")
    now_pt = dt.datetime.now(PT)
    return now_pt.strftime("%Y-%m-%d")

def load_quota_used():
    """파일에서 오늘(PT) 사용량을 읽어온다. 날짜 다르면 0으로 리셋."""
    today = _today_pt_str()
    if QUOTA_FILE.exists():
        try:
            data = json.loads(QUOTA_FILE.read_text(encoding="utf-8"))
            if data.get("pt_date") == today:
                return int(data.get("used", 0))
        except Exception:
            pass
    return 0

def save_quota_used(value):
    """오늘(PT) 사용량을 파일에 저장."""
    data = {"pt_date": _today_pt_str(), "used": int(value)}
    QUOTA_FILE.write_text(json.dumps(data), encoding="utf-8")

def add_quota(cost):
    """쿼터를 누적(파일+세션 모두)"""
    # 세션(화면 표시용)
    st.session_state["quota_used"] = st.session_state.get("quota_used", 0) + int(cost)
    # 파일(영구 누적)
    current_file_val = load_quota_used()
    save_quota_used(current_file_val + int(cost))

# ====== Time window (마지막 48시간, KST 기준) ======
def kst_window_last_48h():
    now_kst = dt.datetime.now(KST)
    start_kst = now_kst - dt.timedelta(hours=48)
    start_utc = start_kst.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    end_utc   = now_kst.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    return start_utc, end_utc, now_kst

# ====== ISO8601 PT-duration -> seconds ======
def parse_iso8601_duration(s):
    if not s or not s.startswith("PT"):
        return None
    s2 = s[2:]; h=m=sec=0; num=""
    for ch in s2:
        if ch.isdigit(): num += ch
        else:
            if ch == "H":
                h = int(num or 0)
            elif ch == "M":
                m = int(num or 0)
            elif ch == "S":
                sec = int(num or 0)
            num = ""
    return h*3600 + m*60 + sec

def fmt_hms(seconds):
    if seconds is None: return ""
    h = seconds//3600; m=(seconds%3600)//60; s=seconds%60
    return f"{h:02d}:{m:02d}:{s:02d}" if h>0 else f"{m:02d}:{s:02d}"

# ====== API helper (쿼터 카운트 포함) ======
def api_get(url, params, cost):
    r = requests.get(url, params=params, timeout=20)
    # 성공/실패와 무관, 유효/무효 요청 모두 비용 발생 -> 문서 규정
    add_quota(cost)
    r.raise_for_status()
    return r.json()

SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"

def search_ids(keyword, max_pages=1):
    start_iso, end_iso, _ = kst_window_last_48h()
    vids, token, pages = [], None, 0
    while True:
        params = {
            "key": API_KEY,
            "part": "snippet",
            "q": keyword,
            "type": "video",
            "order": "date",
            "publishedAfter": start_iso,
            "publishedBefore": end_iso,
            "maxResults": 50,
            "videoDuration": "short",
            "regionCode": REGION_CODE,
            "relevanceLanguage": RELEVANCE_LANG,
        }
        if token: params["pageToken"] = token
        data = api_get(SEARCH_URL, params, cost=100)  # search.list = 100
        ids = [it.get("id", {}).get("videoId") for it in data.get("items", []) if it.get("id", {}).get("videoId")]
        vids.extend(ids)
        token = data.get("nextPageToken"); pages += 1
        if not token or pages >= max_pages or len(vids) >= 200:
            break
    # de-dup
    seen, ordered = set(), []
    for v in vids:
        if v not in seen:
            ordered.append(v); seen.add(v)
    return ordered

def fetch_details(video_ids):
    out=[]
    for i in range(0, len(video_ids), 50):
        chunk = video_ids[i:i+50]
        params = {"key": API_KEY, "part": "snippet,contentDetails,statistics", "id": ",".join(chunk)}
        data = api_get(VIDEOS_URL, params, cost=1)  # videos.list = 1
        out.extend(data.get("items", []))
    return out

def to_kst(iso_str):
    t = dt.datetime.fromisoformat(iso_str.replace("Z", "+00:00")).astimezone(KST)
    return t.strftime("%Y-%m-%d %H:%M:%S (%Z)")

def make_dataframe(keyword, max_pages=1):
    ids = search_ids(keyword, max_pages=max_pages)
    details = fetch_details(ids)
    rows=[]
    for item in details:
        vid=item.get("id",""); sn=item.get("snippet",{}); cd=item.get("contentDetails",{}); stt=item.get("statistics",{})
        secs = parse_iso8601_duration(cd.get("duration",""))
        if secs is None or secs>60:  # Shorts만
            continue
        rows.append({
            "title": sn.get("title",""),
            "view_count": stt.get("viewCount",""),
            "length": fmt_hms(secs),
            "channel": sn.get("channelTitle",""),
            "url": f"https://www.youtube.com/watch?v={vid}",
            "published_at_kst": to_kst(sn.get("publishedAt","")) if sn.get("publishedAt") else "",
        })
    df = pd.DataFrame(rows, columns=["title","view_count","length","channel","url","published_at_kst"])
    df["view_count"] = pd.to_numeric(df["view_count"], errors="coerce").fillna(0).astype(int)
    return df

def next_reset_info():
    now_pt = dt.datetime.now(PT)
    reset_pt = (now_pt + dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    remaining = reset_pt - now_pt
    reset_kst = reset_pt.astimezone(KST)
    return reset_pt, reset_kst, remaining

# ====== 쿼터 카운트 ======
# 세션 상태에 쿼터 카운터 준비
if "quota_used" not in st.session_state:
    st.session_state["quota_used"] = load_quota_used()
else:
#날짜가 바뀌었을 수도 있으니 재동기화
    st.session_state["quota_used"] = load_quota_used()

# ====== UI ======
st.set_page_config(page_title="YouTube Shorts 48h Finder", page_icon="📺", layout="wide")
st.title("📺 48시간 이내 업로드된 YouTube Shorts 찾기 (KR)")

if not API_KEY:
    st.error("⚠️ API 키가 설정되지 않았습니다. 좌측 메뉴(▶) > Settings > Secrets 에 YOUTUBE_API_KEY를 추가하세요.")
    st.stop()

with st.sidebar:
    st.header("설정")
    keyword = st.text_input("검색어", "")
    max_pages = st.radio("검색 페이지 수(쿼터 절약)", options=[1,2], index=0)
    st.caption("범위: 현재 시각(KST) 기준 **지난 48시간**")
    run_btn = st.button("검색 실행")

# 쿼터 패널
used = st.session_state["quota_used"]
remaining = max(0, DAILY_QUOTA - used)
pct = min(1.0, used / DAILY_QUOTA) if DAILY_QUOTA else 0.0

reset_pt, reset_kst, remaining_td = next_reset_info()

quota_col1, quota_col2 = st.columns([2,1])
with quota_col1:
    st.subheader("🔋 쿼터 사용량(추정)")
    st.progress(pct, text=f"사용 {used} / {DAILY_QUOTA}  (남은 {remaining})")
with quota_col2:
    st.metric("남은 쿼터(추정)", value=f"{remaining:,}", delta=f"리셋까지 {remaining_td}".replace("days","일").replace("day","일"))
st.caption(f"※ 일일 쿼터는 PT 자정(한국시간 다음날 16~17시)에 리셋")

# 실행
if run_btn:
    with st.spinner("검색 중… ⏳"):
        df = make_dataframe(keyword, max_pages=max_pages)
        df_top = df.sort_values("view_count", ascending=False, ignore_index=True).head(20)
    st.success(f"검색 완료: 후보 {len(df)}개 중 상위 20개 표시")

    sort_col = st.selectbox("정렬 컬럼", ["view_count","title","length","channel","published_at_kst"])
    sort_order = st.radio("정렬 순서", ["내림차순","오름차순"], horizontal=True, index=0)
    asc = (sort_order == "오름차순")
    df_show = df_top.sort_values(sort_col, ascending=asc, ignore_index=True)

    df_show = df_show[["title","view_count","length","channel","url","published_at_kst"]]

    st.dataframe(df_show, use_container_width=True)

    csv_bytes = df_show.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
    st.download_button("CSV 다운로드", data=csv_bytes,
                       file_name=f"shorts_48h_{keyword}.csv", mime="text/csv")

    st.info(f"이번 실행으로 추정 사용량: search.list {100 * (max_pages)} + videos.list {1}")
