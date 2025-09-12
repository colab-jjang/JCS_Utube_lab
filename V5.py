import streamlit as st
import pandas as pd
import requests
import datetime as dt
import json
from urllib.parse import unquote

API_KEY = st.secrets["YOUTUBE_API_KEY"]
GIST_ID = st.secrets["GIST_ID"]
GIST_TOKEN = st.secrets["GIST_TOKEN"]
GIST_FILENAME = "whitelist_channels.json"
GIST_QUOTA = "quota.json"
API_MAX_QUOTA = 10000  # 실제 할당량에 맞게 수정!

# ----------- Gist 연동 함수 -----------
def save_whitelist_to_gist(whitelist, GIST_ID, GIST_TOKEN, filename="whitelist_channels.json"):
    url = f"https://api.github.com/gists/{GIST_ID}"
    headers = {"Authorization": f"token {GIST_TOKEN}"}
    payload = {
        "files": {
            filename: {"content": json.dumps(sorted(list(whitelist)), ensure_ascii=False, indent=2)}
        }
    }
    r = requests.patch(url, json=payload, headers=headers, timeout=20)
    return r.status_code == 200

def load_whitelist_from_gist(GIST_ID, GIST_TOKEN, filename="whitelist_channels.json"):
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

# ----------- API Quota 관리 함수 ----------
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

# ---------- API Quota UI 표시 및 누적 관리 ----------
quota_info = get_quota_usage(GIST_ID, GIST_TOKEN, GIST_QUOTA)
today = dt.datetime.utcnow().strftime("%Y-%m-%d")
if quota_info["date"] != today:
    set_quota_usage(GIST_ID, GIST_TOKEN, 0, GIST_QUOTA)
    used_quota = 0
else:
    used_quota = quota_info.get("count", 0)

progress = min(used_quota / API_MAX_QUOTA, 1.0)
now = dt.datetime.utcnow()
reset_time = (now + dt.timedelta(days=1)).replace(hour=0,minute=0,second=0,microsecond=0)
remain = reset_time - now

st.markdown(f"### YouTube API 일일 사용량: {used_quota}/{API_MAX_QUOTA}")
st.progress(progress)
st.markdown(f"**UTC 0시까지 남은 시간:** {str(remain).split('.')[0]}")

# ----------- 채널명 추출 함수 -----------
def get_channel_title(channel_token):
    global used_quota
    token = str(channel_token)
    channel_id = None
    if token.startswith("UC") and len(token) > 10:
        channel_id = token
    elif token.startswith("@"):
        handle = unquote(token.lstrip("@"))
        url = "https://www.googleapis.com/youtube/v3/channels"
        r = requests.get(url, params={
            "key": API_KEY,
            "forHandle": handle,
            "part": "snippet"
        }, timeout=10)
        used_quota += 1; set_quota_usage(GIST_ID, GIST_TOKEN, used_quota, GIST_QUOTA)
        items = r.json().get("items", [])
        if items:
            channel_id = items[0]["id"]
    elif "youtube.com/" in token:
        import re
        m = re.search(r"/@([^/?]+)", token)
        if m:
            handle = unquote(m.group(1))
            url = "https://www.googleapis.com/youtube/v3/channels"
            r = requests.get(url, params={
                "key": API_KEY,
                "forHandle": handle,
                "part": "snippet"
            }, timeout=10)
            used_quota += 1; set_quota_usage(GIST_ID, GIST_TOKEN, used_quota, GIST_QUOTA)
            items = r.json().get("items", [])
            if items:
                channel_id = items[0]["id"]
        else:
            m = re.search(r"/channel/(UC[\w-]+)", token)
            if m:
                channel_id = m.group(1)
    if channel_id:
        url = "https://www.googleapis.com/youtube/v3/channels"
        r = requests.get(url, params={
            "key": API_KEY, "id": channel_id, "part": "snippet"
        }, timeout=10)
        used_quota += 1; set_quota_usage(GIST_ID, GIST_TOKEN, used_quota, GIST_QUOTA)
        items = r.json().get("items", [])
        if items:
            return items[0]["snippet"]["title"]
        else:
            return "(추출 실패) " + token
    return "(추출 실패) " + token

def iso8601_to_seconds(iso):
    import re
    m = re.match(r'PT((\d+)M)?((\d+)S)?', iso)
    return int(m.group(2) or 0)*60 + int(m.group(4) or 0) if m else 0

# ---------- 화이트리스트 및 UI 초기화 ----------
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

# ---------- 이하 기존 채널관리/실행 UI 및 로직 그대로 삽입 ----------

# ... (이후 기존의 모든 UI, upload, manual 입력, 모드분기, 숏츠 추출 등) ...

# 예시 - 화이트리스트 표시 부분:
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

# *각 함수에서 YouTube API 요청 때마다
# used_quota += 1; set_quota_usage(GIST_ID, GIST_TOKEN, used_quota, GIST_QUOTA)
# 꼭! 추가할 것!
