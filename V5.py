import streamlit as st
import requests
import pandas as pd
import datetime as dt

API_KEY = st.secrets["YOUTUBE_API_KEY"]  # secrets.toml에 미리 저장!

# --- Utils ---
def iso8601_to_seconds(iso):
    import re
    m = re.match(r'PT((\d+)M)?((\d+)S)?', iso)
    return int(m.group(2) or 0)*60 + int(m.group(4) or 0) if m else 0

def fetch_search_results(**params):
    API = "https://www.googleapis.com/youtube/v3/search"
    r = requests.get(API, params=params, timeout=10)
    if r.status_code != 200:
        st.error(f"API 오류: {r.status_code}")
        return []
    return r.json().get("items", []), r.json().get("nextPageToken")

def fetch_videos_stats(ids):
    API = "https://www.googleapis.com/youtube/v3/videos"
    stats = []
    for i in range(0, len(ids), 50):
        batch = ids[i:i+50]
        r = requests.get(API, params={
            "key": API_KEY,
            "id": ",".join(batch),
            "part": "contentDetails,statistics,snippet"
        }, timeout=10)
        for item in r.json().get("items", []):
            s = item.get("statistics", {})
            c = item.get("contentDetails", {})
            snip = item.get("snippet", {})
            sec = iso8601_to_seconds(c.get("duration",""))
            stats.append({
                "video_id": item["id"],
                "title": snip.get("title", ""),
                "viewCount": int(s.get("viewCount",0)),
                "channelTitle": snip.get("channelTitle",""),
                "publishedAt": snip.get("publishedAt",""),
                "length_sec": sec,
                "url": f"https://youtu.be/{item['id']}"
            })
    return stats

def filter_shorts(stats, length_limit=180, published_after=None):
    out = []
    for v in stats:
        if v["length_sec"] > length_limit:
            continue
        if published_after and v["publishedAt"] < published_after:
            continue
        out.append(v)
    return out

# --- Streamlit UI ---
st.title("최신 유튜브 뉴스·정치 숏츠 수집기")
MODE = st.radio("수집 모드 선택", [
    "전체 트렌드 (정치/뉴스)", "화이트리스트 채널", "키워드(검색어) 기반"
], horizontal=True)

country = st.selectbox("국가(regionCode)", ["KR","US","JP","GB","DE"], index=0)
hour_limit = st.selectbox("최신 N시간 이내", [12,24], index=1)
length_sec = st.selectbox("숏츠 최대 길이(초)", [60, 90, 120, 180], index=3)
max_results = 50

# 3. "키워드(검색어) 기반"만 검색어 입력창 보이도록!
keyword = ""  # 변수 미리 준비
if MODE == "키워드(검색어) 기반":
    keyword = st.text_input("검색어(뉴스/정치 관련 단어 입력)", value="")  # 반드시 실행 버튼보다 위!

# 4. 실행 버튼
if st.button("최신 숏츠 트렌드 추출"):
    # ... (공통 실행 로직)
    if MODE == "키워드(검색어) 기반" and not keyword.strip():
        st.warning("검색어를 입력해주세요.")
        st.stop()

# --- MODE 2: 화이트리스트 불러오기/업데이트
if "whitelist" not in st.session_state:
    st.session_state.whitelist = []
if MODE == "화이트리스트 채널":
    tab1, tab2 = st.tabs(["CSV 업로드", "수동 입력(아이디, 핸들, url)"])
    with tab1:
        upl = st.file_uploader("화이트리스트 CSV (channel_id, handle 또는 url)", type="csv")
        if upl:
            df = pd.read_csv(upl)
            ch_ids = df.iloc[:,0].apply(lambda x: str(x).strip()).tolist()
            st.session_state.whitelist = ch_ids
            st.success(f"총 {len(ch_ids)}개 채널이 화이트리스트로 반영됨")
    with tab2:
        manual = st.text_area("채널 입력(줄바꿈/쉼표 구분)", height=100)
        if st.button("수동 채널 반영", key="manualadd"):
            ch_ids = [x.strip() for x in manual.replace(",", "\n").split("\n") if x.strip()]
            st.session_state.whitelist = ch_ids
            st.success(f"총 {len(ch_ids)}개 채널 반영됨")

published_after = (dt.datetime.utcnow() - dt.timedelta(hours=hour_limit)).isoformat("T") + "Z"

# --- 실행 버튼
if st.button("최신 숏츠 트렌드 추출"):
    videos, ids = [], []

    if MODE=="전체 트렌드 (정치/뉴스)":
        vcat = "25"  # 정치/뉴스분야
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
            items, page_token = fetch_search_results(**params)
            ids += [it["id"]["videoId"] for it in items]
            if not page_token or len(ids) >= max_results: break
        video_stats = fetch_videos_stats(ids[:max_results])

    elif MODE=="화이트리스트 채널":
        out = []
        for ch in st.session_state.whitelist:
            # 채널 업로드플리 추출
            API = "https://www.googleapis.com/youtube/v3/channels"
            r = requests.get(API, params={
                "key": API_KEY,
                "part": "contentDetails",
                "forUsername" if not ch.startswith("UC") else "id": ch.lstrip("@")
            }, timeout=10)
            items = r.json().get("items",[])
            if not items: continue
            pid = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
            # 최근 업로드 영상
            plist_api = "https://www.googleapis.com/youtube/v3/playlistItems"
            r2 = requests.get(plist_api, params={
                "key": API_KEY,"playlistId": pid,
                "part": "snippet,contentDetails", "maxResults": 10
            }, timeout=10)
            vids = [it["contentDetails"]["videoId"] for it in r2.json().get("items",[])]
            out += vids
        video_stats = fetch_videos_stats(out)

    else:  # MODE==키워드
        kw = st.text_input("검색어(뉴스/정치 연관 추천)",value="")
        if not kw:
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
                "q": kw,
                "regionCode": country,
                "maxResults": 50
            }
            if page_token: params["pageToken"] = page_token
            items, page_token = fetch_search_results(**params)
            ids += [it["id"]["videoId"] for it in items]
            if not page_token or len(ids) >= max_results: break
        video_stats = fetch_videos_stats(ids[:max_results])

    # 최종 필터 (길이, 시간 조건)
    filtered = filter_shorts(video_stats, length_limit=length_sec, published_after=published_after)
    filtered = sorted(filtered, key=lambda x: x["viewCount"], reverse=True)[:20]

    # 결과 표
    df = pd.DataFrame(filtered)
    if df.empty:
        st.info("해당 조건에 맞는 최신 숏츠가 없습니다.")
    else:
        show_cols = ["title","viewCount","channelTitle","publishedAt","length_sec","url"]
        st.dataframe(df[show_cols],use_container_width=True)
        csv = df[show_cols].to_csv(index=False, encoding="utf-8-sig")
        st.download_button("CSV로 다운로드", csv, file_name="shorts_trend.csv", mime="text/csv")
        st.success(f"총 {len(df)}개 Top 숏츠 (조회수 순)")
