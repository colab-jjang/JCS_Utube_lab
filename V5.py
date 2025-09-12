import streamlit as st
import pandas as pd
import requests
import datetime as dt
import json
import re
from urllib.parse import unquote

API_KEY = st.secrets["YOUTUBE_API_KEY"]
GIST_ID = st.secrets["GIST_ID"]
GIST_TOKEN = st.secrets["GIST_TOKEN"]
GIST_FILENAME = "whitelist_channels.json"
GIST_QUOTA = "quota.json"
API_MAX_QUOTA = 10000  # <-- 반드시 네 실제 일일 할당량으로 변경

# ---- quota 관리 ----
def get_quota_usage(GIST_ID, GIST_TOKEN, filename="quota.json"):
    url = f"https://api.github.com/gists/{GIST_ID}"
    headers = {"Authorization": f"token {GIST_TOKEN}"}
    r = requests.get(url, headers=headers, timeout=10)
    if r.status_code != 200:
        return {"date": "", "count": 0}
    files = r.json().get("files", {})
    if filename not in files:
        return {"date": "", "count": 0}
    content = files[filename]['content']
    data = json.loads(content)
    return data

def set_quota_usage(GIST_ID, GIST_TOKEN, count, filename="quota.json"):
    # 저장 날짜는 한국시간 오전 9시 기준으로 맞춰 저장!
    now_kst = dt.datetime.utcnow() + dt.timedelta(hours=9)
    data = {
        "date": now_kst.strftime("%Y-%m-%d"),
        "count": count
    }
    url = f"https://api.github.com/gists/{GIST_ID}"
    headers = {"Authorization": f"token {GIST_TOKEN}"}
    payload = { "files": { filename: {"content": json.dumps(data, ensure_ascii=False, indent=2)} } }
    r = requests.patch(url, json=payload, headers=headers, timeout=10)
    return r.status_code == 200

# ---- KST(now), 리셋 시각, 진행bar ----
KST = dt.timezone(dt.timedelta(hours=9))
now_utc = dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc)
now_kst = now_utc.astimezone(KST)
reset_today_kst = now_kst.replace(hour=16, minute=0, second=0, microsecond=0)
if now_kst >= reset_today_kst:
    reset_time_kst = reset_today_kst + dt.timedelta(days=1)
else:
    reset_time_kst = reset_today_kst
remain = reset_time_kst - now_kst

quota_info = get_quota_usage(GIST_ID, GIST_TOKEN, GIST_QUOTA)
today_kst_str = now_kst.strftime("%Y-%m-%d")
if quota_info["date"] != today_kst_str:
    set_quota_usage(GIST_ID, GIST_TOKEN, 0, GIST_QUOTA)
    used_quota = 0
else:
    used_quota = quota_info.get("count", 0)
progress = min(used_quota / API_MAX_QUOTA, 1.0)

st.markdown(f"### YouTube API 일일 사용량: {used_quota}/{API_MAX_QUOTA}")
st.progress(progress)
st.markdown(f"**다음 리셋(한국 오후 4시):** {reset_time_kst.strftime('%Y-%m-%d %H:%M:%S')}, 남은 시간: {str(remain).split('.')[0]}")
st.markdown(f"(지금: {now_kst.strftime('%Y-%m-%d %H:%M:%S KST')})")

# ---- Gist 연동 ----
def save_whitelist_to_gist(whitelist, GIST_ID, GIST_TOKEN, filename=GIST_FILENAME):
    url = f"https://api.github.com/gists/{GIST_ID}"
    headers = {"Authorization": f"token {GIST_TOKEN}"}
    payload = {
        "files": {
            filename: {"content": json.dumps(sorted(list(whitelist)), ensure_ascii=False, indent=2)}
        }
    }
    r = requests.patch(url, json=payload, headers=headers, timeout=20)
    return r.status_code == 200

def load_whitelist_from_gist(GIST_ID, GIST_TOKEN, filename=GIST_FILENAME):
    url = f"https://api.github.com/gists/{GIST_ID}"
    headers = {"Authorization": f"token {GIST_TOKEN}"}
    r = requests.get(url, headers=headers, timeout=20)
    if r.status_code != 200:
        return []
    files = r.json().get("files", {})
    if filename not in files:
        return []
    content = files[filename]['content']
    data = json.loads(content)
    return data if isinstance(data, list) else []

# ---- quota count도 동작 ----
def quota_requests_get(*args, **kwargs):
    global used_quota
    r = requests.get(*args, **kwargs)
    used_quota += 1
    set_quota_usage(GIST_ID, GIST_TOKEN, used_quota, GIST_QUOTA)
    return r

# ---- 채널명 추출 함수 ----
def get_channel_title(channel_token):
    token = str(channel_token)
    channel_id = None
    if token.startswith("UC") and len(token) > 10:
        channel_id = token
    elif token.startswith("@"):
        handle = unquote(token.lstrip("@"))
        r = quota_requests_get("https://www.googleapis.com/youtube/v3/channels", params={
            "key": API_KEY, "forHandle": handle, "part": "snippet"
        }, timeout=10)
        items = r.json().get("items", [])
        if items: channel_id = items[0]["id"]
    elif "youtube.com/" in token:
        m = re.search(r"/@([^/?]+)", token)
        if m:
            handle = unquote(m.group(1))
            r = quota_requests_get("https://www.googleapis.com/youtube/v3/channels", params={
                "key": API_KEY, "forHandle": handle, "part": "snippet"
            }, timeout=10)
            items = r.json().get("items", [])
            if items: channel_id = items[0]["id"]
        else:
            m = re.search(r"/channel/(UC[\w-]+)", token)
            if m: channel_id = m.group(1)
    if channel_id:
        r = quota_requests_get("https://www.googleapis.com/youtube/v3/channels", params={
            "key": API_KEY, "id": channel_id, "part": "snippet"
        }, timeout=10)
        items = r.json().get("items", [])
        if items:
            return items[0]["snippet"]["title"]
        else:
            return "(추출 실패) " + token
    return "(추출 실패) " + token

def iso8601_to_seconds(iso):
    m = re.match(r'PT((\d+)M)?((\d+)S)?', iso)
    return int(m.group(2) or 0)*60 + int(m.group(4) or 0) if m else 0

# ---- 상태/세션 초기화 ----
if "whitelist" not in st.session_state or not st.session_state.whitelist:
    loaded = load_whitelist_from_gist(GIST_ID, GIST_TOKEN, GIST_FILENAME)
    st.session_state.whitelist = loaded
    if "whitelist_titles" not in st.session_state:
        st.session_state.whitelist_titles = {}
    unmapped = [x for x in loaded if x not in st.session_state.whitelist_titles]
    for token in unmapped:
        st.session_state.whitelist_titles[token] = get_channel_title(token)
if "whitelist" not in st.session_state:
    st.session_state.whitelist = []
if "whitelist_titles" not in st.session_state:
    st.session_state.whitelist_titles = {}

# ---- UI 이하 기존 코드 유지, API 요청부 quota_requests_get으로 변경 ----
st.title("최신 유튜브 뉴스·정치 숏츠 수집기")
MODE = st.radio("수집 모드 선택", [
    "전체 트렌드 (정치/뉴스)", "화이트리스트 채널", "키워드(검색어) 기반"
], horizontal=True)
max_results = 50

if MODE != "화이트리스트 채널":
    country = st.selectbox("국가(regionCode)", ["KR", "US", "JP", "GB", "DE"], index=0)
    hour_limit = st.selectbox("최신 N시간 이내", [12, 24], index=1)
    length_sec = st.selectbox("숏츠 최대 길이(초)", [60, 90, 120, 180, 240, 300], index=3)
    published_after = (dt.datetime.utcnow() - dt.timedelta(hours=hour_limit)).isoformat("T") + "Z"
else:
    published_after = None
    country = None
    length_sec = None

keyword = ""
if MODE == "키워드(검색어) 기반":
    keyword = st.text_input("검색어(뉴스/정치 관련 단어 입력)", value="")

# (화이트리스트 업로드/수동입력/관리 등은 생략 - 그대로 유지)

if st.button("최신 숏츠 트렌드 추출"):
    ids = []
    filtered = []
    if MODE == "전체 트렌드 (정치/뉴스)":
        vcat = "25"
        page_token = None
        while len(ids) < max_results:
            params = {
                "key": API_KEY,
                "part": "snippet",
                "type": "video",
                "order": "date",
                "publishedAfter": published_after,
                "videoDuration": "short",
                "videoCategoryId": vcat,
                "regionCode": country,
                "maxResults": 50
            }
            if page_token: params["pageToken"] = page_token
            r = quota_requests_get("https://www.googleapis.com/youtube/v3/search", params=params)
            data = r.json()
            ids += [it["id"]["videoId"] for it in data.get("items", [])]
            page_token = data.get("nextPageToken")
            if not page_token or len(ids) >= max_results:
                break
    elif MODE == "화이트리스트 채널":
        for ch in st.session_state.whitelist:
            API = "https://www.googleapis.com/youtube/v3/channels"
            key_type = "id" if ch.startswith("UC") else "forUsername"
            r = quota_requests_get(API, params={
                "key": API_KEY,
                "part": "contentDetails",
                key_type: ch.lstrip("@")
            })
            items = r.json().get("items", [])
            if not items: continue
            pid = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
            plist_api = "https://www.googleapis.com/youtube/v3/playlistItems"
            r2 = quota_requests_get(plist_api, params={
                "key": API_KEY, "playlistId": pid,
                "part": "snippet,contentDetails", "maxResults": 10
            })
            vids = [it["contentDetails"]["videoId"] for it in r2.json().get("items",[])]
            ids += vids
    elif MODE == "키워드(검색어) 기반":
        if not keyword.strip():
            st.warning("검색어를 입력해주세요.")
            st.stop()
        page_token = None
        while len(ids) < max_results:
            params = {
                "key": API_KEY,
                "part": "snippet",
                "type": "video",
                "order": "date",
                "publishedAfter": published_after,
                "videoDuration": "short",
                "q": keyword,
                "regionCode": country,
                "maxResults": 50
            }
            if page_token: params["pageToken"] = page_token
            r = quota_requests_get("https://www.googleapis.com/youtube/v3/search", params=params)
            data = r.json()
            ids += [it["id"]["videoId"] for it in data.get("items", [])]
            page_token = data.get("nextPageToken")
            if not page_token or len(ids) >= max_results:
                break
    stats = []
    for i in range(0, len(ids), 50):
        batch = ids[i:i+50]
        params = {
            "key": API_KEY,
            "id": ",".join(batch),
            "part": "contentDetails,statistics,snippet"
        }
        r = quota_requests_get("https://www.googleapis.com/youtube/v3/videos", params=params)
        for item in r.json().get("items", []):
            s = item.get("statistics", {})
            c = item.get("contentDetails", {})
            snip = item.get("snippet", {})
            sec = iso8601_to_seconds(c.get("duration", ""))
            stats.append({
                "title": snip.get("title", ""),
                "viewCount": int(s.get("viewCount", 0)),
                "channelTitle": snip.get("channelTitle", ""),
                "publishedAt": snip.get("publishedAt", ""),
                "length_sec": sec,
                "url": f"https://youtu.be/{item['id']}"
            })
    if MODE == "화이트리스트 채널":
        filtered = stats
    else:
        filtered = [
            v for v in stats
            if v["length_sec"] <= length_sec
            and v["publishedAt"] >= published_after
        ]
    filtered = sorted(filtered, key=lambda x: x["viewCount"], reverse=True)[:20]
    df = pd.DataFrame(filtered)
    show_cols = ["title", "viewCount", "channelTitle", "publishedAt", "length_sec", "url"]
    if df.empty:
        st.info("조건에 맞는 최신 숏츠가 없습니다.")
    else:
        st.dataframe(df[show_cols], use_container_width=True)
        csv = df[show_cols].to_csv(index=False, encoding="utf-8-sig")
        st.download_button("CSV로 다운로드", csv, file_name="shorts_trend.csv", mime="text/csv")
        st.success(f"{len(df)}개 TOP 숏츠 (조회수 순)")
