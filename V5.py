import streamlit as st
import pandas as pd
import requests
import datetime as dt

API_KEY = st.secrets["YOUTUBE_API_KEY"]

MODE = st.radio("수집 모드 선택", [
    "전체 트렌드 (정치/뉴스)", "화이트리스트 채널", "키워드(검색어) 기반"
], horizontal=True)

country = st.selectbox("국가(regionCode)", ["KR", "US", "JP", "GB", "DE"], index=0)
hour_limit = st.selectbox("최신 N시간 이내", [12, 24], index=1)
length_sec = st.selectbox("숏츠 최대 길이(초)", [60, 90, 120, 180, 240, 300], index=3)
max_results = 50

# ------- (키워드 입력창: 모드3에서만 노출) -------
keyword = ""
if MODE == "키워드(검색어) 기반":
    keyword = st.text_input("검색어(뉴스/정치 관련 단어 입력)", value="")

# ------- (화이트리스트 관리, 모드2에서만 노출: 탭 예시) -------
if "whitelist" not in st.session_state:
    st.session_state.whitelist = []
if MODE == "화이트리스트 채널":
    tabs = st.tabs(["CSV 업로드", "수동 입력"])
    with tabs[0]:
        upl = st.file_uploader("CSV(channel_id/handle/url)", type="csv")
        if upl:
            df = pd.read_csv(upl)
            ch_ids = df.iloc[:, 0].apply(lambda x: str(x).strip()).tolist()
            st.session_state.whitelist = ch_ids
            st.success(f"{len(ch_ids)}개 채널 반영됨")
    with tabs[1]:
        manual = st.text_area("채널 직접 입력", height=100)
        if st.button("수동 채널 반영"):
            ch_ids = [x.strip() for x in manual.replace(",", "\n").split("\n") if x.strip()]
            st.session_state.whitelist = ch_ids
            st.success(f"{len(ch_ids)}개 채널 반영됨")

published_after = (dt.datetime.utcnow() - dt.timedelta(hours=hour_limit)).isoformat("T") + "Z"

# ------- (실행 버튼: 딱 한 번만 선언) -------
if st.button("최신 숏츠 트렌드 추출"):
    ids = []

    if MODE == "전체 트렌드 (정치/뉴스)":
        # 전체 트렌드 조회 코드
        vcat = "25"  # 정치/뉴스 카테고리
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
            r = requests.get("https://www.googleapis.com/youtube/v3/search", params=params)
            data = r.json()
            ids += [it["id"]["videoId"] for it in data.get("items",[])]
            page_token = data.get("nextPageToken")
            if not page_token or len(ids) >= max_results: break

    elif MODE == "화이트리스트 채널":
        out = []
        for ch in st.session_state.whitelist:
            API = "https://www.googleapis.com/youtube/v3/channels"
            r = requests.get(API, params={
                "key": API_KEY,
                "part": "contentDetails",
                "forUsername" if not ch.startswith("UC") else "id": ch.lstrip("@")
            })
            items = r.json().get("items",[])
            if not items: continue
            pid = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
            plist_api = "https://www.googleapis.com/youtube/v3/playlistItems"
            r2 = requests.get(plist_api, params={
                "key": API_KEY,"playlistId": pid,
                "part": "snippet,contentDetails", "maxResults": 10
            })
            vids = [it["contentDetails"]["videoId"] for it in r2.json().get("items",[])]
            out += vids
        ids = out[:max_results]

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
            r = requests.get("https://www.googleapis.com/youtube/v3/search", params=params)
            data = r.json()
            ids += [it["id"]["videoId"] for it in data.get("items",[])]
            page_token = data.get("nextPageToken")
            if not page_token or len(ids) >= max_results: break

    # --- stats 조회 (통합)
    def iso8601_to_seconds(iso):
        import re
        m = re.match(r'PT((\d+)M)?((\d+)S)?', iso)
        return int(m.group(2) or 0)*60 + int(m.group(4) or 0) if m else 0

    stats = []
    for i in range(0, len(ids), 50):
        batch = ids[i:i+50]
        params = {
            "key": API_KEY,
            "id": ",".join(batch),
            "part": "contentDetails,statistics,snippet"
        }
        r = requests.get("https://www.googleapis.com/youtube/v3/videos", params=params)
        for item in r.json().get("items", []):
            s = item.get("statistics", {})
            c = item.get("contentDetails", {})
            snip = item.get("snippet", {})
            sec = iso8601_to_seconds(c.get("duration",""))
            stats.append({
                "title": snip.get("title", ""),
                "viewCount": int(s.get("viewCount",0)),
                "channelTitle": snip.get("channelTitle",""),
                "publishedAt": snip.get("publishedAt",""),
                "length_sec": sec,
                "url": f"https://youtu.be/{item['id']}"
            })

    # 필터 및 표시 (길이, 업로드시간)
    filtered = [
        v for v in stats
        if v["length_sec"] <= length_sec
        and v["publishedAt"] >= published_after
    ]
    filtered = sorted(filtered, key=lambda x: x["viewCount"], reverse=True)[:20]

    df = pd.DataFrame(filtered)
    show_cols = ["title","viewCount","channelTitle","publishedAt","length_sec","url"]
    if df.empty:
        st.info("조건에 맞는 최신 숏츠가 없습니다.")
    else:
        st.dataframe(df[show_cols], use_container_width=True)
        csv = df[show_cols].to_csv(index=False, encoding="utf-8-sig")
        st.download_button("CSV로 다운로드", csv, file_name="shorts_trend.csv", mime="text/csv")
        st.success(f"{len(df)}개 TOP 숏츠 (조회수 순)")
