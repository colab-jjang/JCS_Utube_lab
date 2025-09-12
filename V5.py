import streamlit as st
import pandas as pd
import requests
import datetime as dt
import json
import re
from urllib.parse import unquote

# --- secrets 및 quota 한도 ---
API_KEY = st.secrets["YOUTUBE_API_KEY"]
GIST_ID = st.secrets["GIST_ID"]
GIST_TOKEN = st.secrets["GIST_TOKEN"]
GIST_FILENAME = "whitelist_channels.json"
GIST_QUOTA = "quota.json"
API_MAX_QUOTA = 10000  # 반드시 구글 콘솔에 맞게 조정!

# --- quota API 함수 ---
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
    data = {
        "date": dt.datetime.utcnow().strftime("%Y-%m-%d"),
        "count": count
    }
    url = f"https://api.github.com/gists/{GIST_ID}"
    headers = {"Authorization": f"token {GIST_TOKEN}"}
    payload = { "files": { filename: {"content": json.dumps(data, ensure_ascii=False, indent=2)} } }
    r = requests.patch(url, json=payload, headers=headers, timeout=10)
    return r.status_code == 200

# --- (한국시간 9시 기준) quota UI 표시 ---
KST = dt.timezone(dt.timedelta(hours=9))
now_utc = dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc)
now_kst = now_utc.astimezone(KST)
reset_today_kst = now_kst.replace(hour=9, minute=0, second=0, microsecond=0)
if now_kst >= reset_today_kst:
    reset_time_kst = reset_today_kst + dt.timedelta(days=1)
else:
    reset_time_kst = reset_today_kst
remain = reset_time_kst - now_kst

quota_info = get_quota_usage(GIST_ID, GIST_TOKEN, GIST_QUOTA)
today_kst = now_kst.strftime("%Y-%m-%d")
if quota_info["date"] != today_kst:
    set_quota_usage(GIST_ID, GIST_TOKEN, 0, GIST_QUOTA)
    used_quota = 0
else:
    used_quota = quota_info.get("count", 0)
progress = min(used_quota / API_MAX_QUOTA, 1.0)

st.markdown(f"### YouTube API 일일 사용량: {used_quota}/{API_MAX_QUOTA}")
st.progress(progress)
st.markdown(f"**다음 리셋(한국 기준 오전 9시):** {reset_time_kst.strftime('%Y-%m-%d %H:%M:%S')} (남은 시간: {str(remain).split('.')[0]})")

# --- Gist 연동(whitelist 등) ---
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

# --- 채널명 추출, 모든 YouTube API 호출 때 quota 증가 ---
def api_call_with_quota(request_func):
    global used_quota
    result = request_func()
    used_quota += 1
    set_quota_usage(GIST_ID, GIST_TOKEN, used_quota, GIST_QUOTA)
    return result

def get_channel_title(channel_token):
    token = str(channel_token)
    channel_id = None
    if token.startswith("UC") and len(token) > 10:
        channel_id = token
    elif token.startswith("@"):
        handle = unquote(token.lstrip("@"))
        def req(): 
            return requests.get("https://www.googleapis.com/youtube/v3/channels", params={
                "key": API_KEY,
                "forHandle": handle,
                "part": "snippet"
            }, timeout=10)
        r = api_call_with_quota(req)
        items = r.json().get("items", [])
        if items: channel_id = items[0]["id"]
    elif "youtube.com/" in token:
        m = re.search(r"/@([^/?]+)", token)
        if m:
            handle = unquote(m.group(1))
            def req(): 
                return requests.get("https://www.googleapis.com/youtube/v3/channels", params={
                    "key": API_KEY,
                    "forHandle": handle,
                    "part": "snippet"
                }, timeout=10)
            r = api_call_with_quota(req)
            items = r.json().get("items", [])
            if items: channel_id = items[0]["id"]
        else:
            m2 = re.search(r"/channel/(UC[\w-]+)", token)
            if m2: channel_id = m2.group(1)
    if channel_id:
        def req(): 
            return requests.get("https://www.googleapis.com/youtube/v3/channels", params={
                "key": API_KEY, "id": channel_id, "part": "snippet"
            }, timeout=10)
        r = api_call_with_quota(req)
        items = r.json().get("items", [])
        if items:
            return items[0]["snippet"]["title"]
        else:
            return "(추출 실패) " + token
    return "(추출 실패) " + token

def iso8601_to_seconds(iso):
    m = re.match(r'PT((\d+)M)?((\d+)S)?', iso)
    return int(m.group(2) or 0)*60 + int(m.group(4) or 0) if m else 0

# --- 앱 상태 초기화 (자동 복원) ---
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

# ------- UI 이하 기존 코드 그대로 배치 (모드 선택, 업로드, 리스트 관리, 실행 등) -------
# 모드, 업로드, 수동입력, 리스트관리, 멀티셀렉트, 실행 버튼 등
# 여기서는 기존 코드 그대로, 각 API요청(requests)하는 부분만
# 반드시 requests.get → api_call_with_quota(lambda: requests.get(...)) 구조 사용

# (예시) 숏츠 영상 데이터 조회에서:
# requests.get("https://www.googleapis.com/youtube/v3/search", ...) → api_call_with_quota(lambda: requests.get("https://www.googleapis.com/youtube/v3/search", ...))

# 결과 표 출력:
wh = st.session_state.whitelist
titles = st.session_state.whitelist_titles
if wh:
    df_list = []
    for cid in wh:
        df_list.append({
            "채널명": titles.get(cid, cid),
            "채널 ID": cid
        })
    df = pd.DataFrame(df_list)
    st.dataframe(df, use_container_width=True)
else:
    st.info("등록된 채널이 없습니다.")

# 이하, 기존 전체 흐름에서 requests가 YouTube API면 모두 api_call_with_quota로 래핑해주기!
