import streamlit as st
import pandas as pd
import datetime as dt
from zoneinfo import ZoneInfo
import requests

# ====== Settings ======

API\_KEY = st.secrets.get("YOUTUBE\_API\_KEY", "")
REGION\_CODE = "KR"         # 한국 결과 우선
RELEVANCE\_LANG = "ko"      # 한국어 우선
KST = ZoneInfo("Asia/Seoul")
PT  = ZoneInfo("America/Los\_Angeles")
DAILY\_QUOTA = 10\_000       # YouTube Data API 기본 일일 쿼터

# 쿼터 세션 누적시킴

import json, os
from pathlib import Path

DATA\_DIR = Path(".")
QUOTA\_FILE = DATA\_DIR / "quota\_usage.json"   # 앱 폴더에 저장 (앱이 살아있는 한 유지)

def \_today\_pt\_str():
from zoneinfo import ZoneInfo
PT = ZoneInfo("America/Los\_Angeles")
now\_pt = dt.datetime.now(PT)
return now\_pt.strftime("%Y-%m-%d")

def load\_quota\_used():
"""파일에서 오늘(PT) 사용량을 읽어온다. 날짜 다르면 0으로 리셋."""
today = \_today\_pt\_str()
if QUOTA\_FILE.exists():
try:
data = json.loads(QUOTA\_FILE.read\_text(encoding="utf-8"))
if data.get("pt\_date") == today:
return int(data.get("used", 0))
except Exception:
pass
return 0

def save\_quota\_used(value):
"""오늘(PT) 사용량을 파일에 저장."""
data = {"pt\_date": \_today\_pt\_str(), "used": int(value)}
QUOTA\_FILE.write\_text(json.dumps(data), encoding="utf-8")

def add\_quota(cost):
"""쿼터를 누적(파일+세션 모두)"""
\# 세션(화면 표시용)
st.session\_state\["quota\_used"] = st.session\_state.get("quota\_used", 0) + int(cost)
\# 파일(영구 누적)
current\_file\_val = load\_quota\_used()
save\_quota\_used(current\_file\_val + int(cost))

# ====== Time window (마지막 48시간, KST 기준) ======

def kst\_window\_last\_48h():
now\_kst = dt.datetime.now(KST)
start\_kst = now\_kst - dt.timedelta(hours=48)
start\_utc = start\_kst.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
end\_utc   = now\_kst.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
return start\_utc, end\_utc, now\_kst

# ====== ISO8601 PT-duration -> seconds ======

def parse\_iso8601\_duration(s):
if not s or not s.startswith("PT"):
return None
s2 = s\[2:]; h=m=sec=0; num=""
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

def fmt\_hms(seconds):
if seconds is None: return ""
h = seconds//3600; m=(seconds%3600)//60; s=seconds%60
return f"{h:02d}:{m:02d}:{s:02d}" if h>0 else f"{m:02d}:{s:02d}"

# ====== API helper (쿼터 카운트 포함) ======

def api\_get(url, params, cost):
r = requests.get(url, params=params, timeout=20)
\# 성공/실패와 무관, 유효/무효 요청 모두 비용 발생 -> 문서 규정
add\_quota(cost)
r.raise\_for\_status()
return r.json()

SEARCH\_URL = "[https://www.googleapis.com/youtube/v3/search](https://www.googleapis.com/youtube/v3/search)"
VIDEOS\_URL = "[https://www.googleapis.com/youtube/v3/videos](https://www.googleapis.com/youtube/v3/videos)"

def search\_ids(keyword, max\_pages=1):
start\_iso, end\_iso, \_ = kst\_window\_last\_48h()
vids, token, pages = \[], None, 0
while True:
params = {
"key": API\_KEY,
"part": "snippet",
"q": keyword,
"type": "video",
"order": "date",
"publishedAfter": start\_iso,
"publishedBefore": end\_iso,
"maxResults": 50,
"videoDuration": "short",
"regionCode": REGION\_CODE,
"relevanceLanguage": RELEVANCE\_LANG,
}
if token: params\["pageToken"] = token
data = api\_get(SEARCH\_URL, params, cost=100)  # search.list = 100
ids = \[it.get("id", {}).get("videoId") for it in data.get("items", \[]) if it.get("id", {}).get("videoId")]
vids.extend(ids)
token = data.get("nextPageToken"); pages += 1
if not token or pages >= max\_pages or len(vids) >= 200:
break
\# de-dup
seen, ordered = set(), \[]
for v in vids:
if v not in seen:
ordered.append(v); seen.add(v)
return ordered

def fetch\_details(video\_ids):
out=\[]
for i in range(0, len(video\_ids), 50):
chunk = video\_ids\[i\:i+50]
params = {"key": API\_KEY, "part": "snippet,contentDetails,statistics", "id": ",".join(chunk)}
data = api\_get(VIDEOS\_URL, params, cost=1)  # videos.list = 1
out.extend(data.get("items", \[]))
return out

def to\_kst(iso\_str):
t = dt.datetime.fromisoformat(iso\_str.replace("Z", "+00:00")).astimezone(KST)
return t.strftime("%Y-%m-%d %H:%M:%S (%Z)")

def make\_dataframe(keyword, max\_pages=1):
ids = search\_ids(keyword, max\_pages=max\_pages)
details = fetch\_details(ids)
rows=\[]
for item in details:
vid=item.get("id",""); sn=item.get("snippet",{}); cd=item.get("contentDetails",{}); stt=item.get("statistics",{})
secs = parse\_iso8601\_duration(cd.get("duration",""))
if secs is None or secs>60:  # Shorts만
continue
rows.append({
"title": sn.get("title",""),
"view\_count": stt.get("viewCount",""),
"length": fmt\_hms(secs),
"channel": sn.get("channelTitle",""),
"url": f"[https://www.youtube.com/watch?v={vid}](https://www.youtube.com/watch?v={vid})",
"published\_at\_kst": to\_kst(sn.get("publishedAt","")) if sn.get("publishedAt") else "",
})
df = pd.DataFrame(rows, columns=\["title","view\_count","length","channel","url","published\_at\_kst"])
df\["view\_count"] = pd.to\_numeric(df\["view\_count"], errors="coerce").fillna(0).astype(int)
return df

def next\_reset\_info():
now\_pt = dt.datetime.now(PT)
reset\_pt = (now\_pt + dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
remaining = reset\_pt - now\_pt
reset\_kst = reset\_pt.astimezone(KST)
return reset\_pt, reset\_kst, remaining

# ====== 쿼터 카운트 ======

# 세션 상태에 쿼터 카운터 준비

if "quota\_used" not in st.session\_state:
    st.session\_state\["quota\_used"] = load\_quota\_used()
else:
    #날짜가 바뀌었을 수도 있으니 재동기화
    st.session\_state\["quota\_used"] = load\_quota\_used()

# ====== UI ======

st.set\_page\_config(page\_title="YouTube Shorts 48h Finder", page\_icon="📺", layout="wide")
st.title("📺 48시간 이내 업로드된 YouTube Shorts 찾기 (KR)")

if not API\_KEY:
st.error("⚠️ API 키가 설정되지 않았습니다. 좌측 메뉴(▶) > Settings > Secrets 에 YOUTUBE\_API\_KEY를 추가하세요.")
st.stop()

with st.sidebar:
st.header("설정")
keyword = st.text\_input("검색어", "")
max\_pages = st.radio("검색 페이지 수(쿼터 절약)", options=\[1,2], index=0)
st.caption("범위: 현재 시각(KST) 기준 **지난 48시간**")
run\_btn = st.button("검색 실행")

# 쿼터 패널

used = st.session\_state\["quota\_used"]
remaining = max(0, DAILY\_QUOTA - used)
pct = min(1.0, used / DAILY\_QUOTA) if DAILY\_QUOTA else 0.0

reset\_pt, reset\_kst, remaining\_td = next\_reset\_info()

quota\_col1, quota\_col2 = st.columns(\[2,1])
with quota\_col1:
st.subheader("🔋 쿼터 사용량(추정)")
st.progress(pct, text=f"사용 {used} / {DAILY\_QUOTA}  (남은 {remaining})")
with quota\_col2:
st.metric("남은 쿼터(추정)", value=f"{remaining:,}", delta=f"리셋까지 {remaining\_td}".replace("days","일").replace("day","일"))
st.caption(f"※ 일일 쿼터는 PT 자정(한국시간 다음날 16\~17시)에 리셋")

# 실행

if run\_btn:
with st.spinner("검색 중… ⏳"):
df = make\_dataframe(keyword, max\_pages=max\_pages)
df\_top = df.sort\_values("view\_count", ascending=False, ignore\_index=True).head(20)
st.success(f"검색 완료: 후보 {len(df)}개 중 상위 20개 표시")

```
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
```


