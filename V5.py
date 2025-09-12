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
API_MAX_QUOTA = 10000

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
    now_kst = dt.datetime.now(dt.timezone.utc).astimezone(dt.timezone(dt.timedelta(hours=9)))
    data = {"date": now_kst.strftime("%Y-%m-%d"), "count": count}
    url = f"https://api.github.com/gists/{GIST_ID}"
    headers = {"Authorization": f"token {GIST_TOKEN}"}
    payload = { "files": { filename: {"content": json.dumps(data, ensure_ascii=False, indent=2)} } }
    r = requests.patch(url, json=payload, headers=headers, timeout=10)
    return r.status_code == 200

def get_channel_id_from_token(token):
    token = str(token).strip()
    if token.startswith("UC"):
        return token
    if token.startswith("@"):
        handle = token.lstrip("@")
        api = "https://www.googleapis.com/youtube/v3/channels"
        r = requests.get(api, params={
            "key": API_KEY,
            "forHandle": handle,
            "part": "id"
        })
        items = r.json().get('items', [])
        if items:
            return items[0]['id']
    if "youtube.com/" in token:
        m = re.search(r"/@([^/?]+)", token)
        if m:
            handle = m.group(1)
            api = "https://www.googleapis.com/youtube/v3/channels"
            r = requests.get(api, params={
                "key": API_KEY,
                "forHandle": handle,
                "part": "id"
            })
            items = r.json().get("items", [])
            if items:
                return items[0]['id']
        else:
            m = re.search(r"/channel/(UC[\w-]+)", token)
            if m:
                return m.group(1)
    return None

def quota_requests_get(*args, **kwargs):
    global used_quota
    r = requests.get(*args, **kwargs)
    used_quota += 1
    set_quota_usage(GIST_ID, GIST_TOKEN, used_quota, GIST_QUOTA)
    return r

def get_channel_title(channel_token):
    try:
        channel_id = get_channel_id_from_token(channel_token)
        if channel_id:
            r = quota_requests_get("https://www.googleapis.com/youtube/v3/channels", params={
                "key": API_KEY, "id": channel_id, "part": "snippet"
            }, timeout=10)
            items = r.json().get("items", [])
            if items:
                return items[0]["snippet"]["title"]
            else:
                return "(추출 실패) " + str(channel_token)
        return "(추출 실패) " + str(channel_token)
    except Exception:
        return f"(추출 실패) {channel_token}"

def iso8601_to_seconds(iso):
    m = re.match(r'PT((\d+)M)?((\d+)S)?', iso)
    return int(m.group(2) or 0)*60 + int(m.group(4) or 0) if m else 0

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

def is_number(val):
    try:
        if val is None:
            return False
        float(val)
        return True
    except (TypeError, ValueError):
        return False

def safe_float_len_sec(v):
    try:
        return float(v["length_sec"])
    except (TypeError, ValueError, KeyError):
        return None

def best_search_trend():
    # 튜닝 대상 파라미터 세트
    region_codes = [None, "KR"]
    hour_limits = [72, 168]               # 3일, 7일
    max_len_secs = [60, 90, 180]          # 1분, 1분반, 3분
    category_ids = [None, "25"]
    
    # 결과 기록
    all_results = []

    for region in region_codes:
        for hours in hour_limits:
            published_after = (
                dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)
            ).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
            for max_len in max_len_secs:
                for cat in category_ids:
                    ids = []
                    # === 1차 영상 아이디 수집 ===
                    params = {
                        "key": API_KEY,
                        "part": "snippet",
                        "type": "video",
                        "order": "date",
                        "publishedAfter": published_after,
                        "videoDuration": "any",
                        "maxResults": 50
                    }
                    if region:
                        params["regionCode"] = region
                    if cat:
                        params["videoCategoryId"] = cat
                    r = requests.get("https://www.googleapis.com/youtube/v3/search", params=params)
                    d = r.json()
                    if "items" not in d or len(d["items"]) == 0:
                        continue
                    ids = [it["id"]["videoId"] for it in d.get("items", []) if "id" in it and "videoId" in it["id"]]
                    # === 2차 상세 수집 (상세 조건 필터) ===
                    stats = []
                    for i in range(0, len(ids), 50):
                        batch = ids[i:i+50]
                        detail_params = {
                            "key": API_KEY,
                            "id": ",".join(batch),
                            "part": "contentDetails,statistics,snippet"
                        }
                        r2 = requests.get("https://www.googleapis.com/youtube/v3/videos", params=detail_params)
                        vj = r2.json()
                        for item in vj.get("items", []):
                            c = item.get("contentDetails", {})
                            snip = item.get("snippet", {})
                            s = item.get("statistics", {})
                            sec = 0
                            try:
                                dstr = c.get("duration","")
                                sec = int(dstr.replace("PT","").replace("M","").replace("S","")) if "M" in dstr else int(dstr.replace("PT","").replace("S",""))
                            except: pass
                            stats.append({
                                "title": snip.get("title",""),
                                "viewCount": int(s.get("viewCount",0)) if s.get("viewCount") else 0,
                                "channelTitle": snip.get("channelTitle", ""),
                                "publishedAt": snip.get("publishedAt", ""),
                                "length_sec": sec,
                                "url": f"https://youtu.be/{item['id']}" if 'id' in item else ""
                            })
                    # 트렌드 영상 적정량(20개 이상), 길이 조건으로 필터
                    filtered = [v for v in stats if (isinstance(v.get("length_sec"), int) and v["length_sec"] <= max_len and v["length_sec"] > 0)]
                    filtered = sorted(filtered, key=lambda x: x["viewCount"], reverse=True)
                    all_results.append({
                        "region": region or "전체",
                        "hours": hours,
                        "max_len_sec": max_len,
                        "category": cat or "전체",
                        "trend_count": len(filtered),
                        "top5_titles": [v["title"] for v in filtered[:5]],
                        "trend_data": filtered
                    })
    # 가장 데이터가 많으면서 적절한 세트 추천
    all_results = sorted(all_results, key=lambda x: (-x["trend_count"], x["max_len_sec"]))
    if not all_results or all_results[0]["trend_count"] == 0:
        st.warning("어떤 조건에서도 충분한 숏츠가 나오지 않습니다. 범위/조건을 더 넓혀보세요.")
        return
    best = all_results[0]
    st.success(f"최적조건: 시간범위 {best['hours']}시간, regionCode {best['region']}, 카테고리 {best['category']}, 최대길이 {best['max_len_sec']}초")
    st.info("대표 영상:\n" + "\n".join(f"- {t}" for t in best["top5_titles"]))
    df = pd.DataFrame(best["trend_data"])
    show_cols = ["title","viewCount","channelTitle","publishedAt","length_sec","url"]
    if not df.empty:
        st.dataframe(df[show_cols], width='stretch')
        csv = df[show_cols].to_csv(index=False, encoding="utf-8-sig")
        st.download_button("CSV로 다운로드", csv, file_name="shorts_trend_best.csv", mime="text/csv")

if st.button("최적 트렌드 검색 자동화 실행"):
    best_search_trend()

def find_best_trend_condition():
    best_result = None
    combis = [
        # (시간, regionCode, 카테고리ID, max_len_sec)
        (72, None, None, 180),
        (168, None, None, 180),
        (72, "KR", "25", 180),
        (72, "KR", "25", 90),
        (168, "KR", "25", 90),
        (72, "KR", None, 90),
        (168, "KR", None, 90),
        # 필요시 더 다양한 조합 추가
    ]
    all_results = []
    for hour, reg, cat, max_len in combis:
        published_after = (
            dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hour)
        ).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
        params = {
            "key": API_KEY,
            "part": "snippet",
            "type": "video",
            "order": "date",
            "publishedAfter": published_after,
            "videoDuration": "any",
            "maxResults": 50,
        }
        if reg:
            params["regionCode"] = reg
        if cat:
            params["videoCategoryId"] = cat
        r = requests.get("https://www.googleapis.com/youtube/v3/search", params=params)
        data = r.json()
        ids = [it["id"]["videoId"] for it in data.get("items", []) if "id" in it and "videoId" in it["id"]]
        stats = []
        for i in range(0, len(ids), 50):
            batch = ids[i:i+50]
            r2 = requests.get("https://www.googleapis.com/youtube/v3/videos", params={
                "key": API_KEY, "id": ",".join(batch),
                "part": "contentDetails,statistics,snippet"
            })
            video_resp = r2.json()
            for item in video_resp.get("items", []):
                s = item.get("statistics", {})
                c = item.get("contentDetails", {})
                snip = item.get("snippet", {})
                sec = iso8601_to_seconds(c.get("duration", "")) if isinstance(c.get("duration", ""), str) else None
                stats.append({
                    "title": snip.get("title", ""),
                    "viewCount": int(s.get("viewCount", 0)) if is_number(s.get("viewCount", 0)) else 0,
                    "channelTitle": snip.get("channelTitle", ""),
                    "publishedAt": snip.get("publishedAt", ""),
                    "length_sec": sec,
                    "url": f"https://youtu.be/{item['id']}" if 'id' in item else ""
                })
        filtered = [v for v in stats if isinstance(v.get("length_sec"), (int,float)) and v["length_sec"] > 0 and v["length_sec"] <= max_len]
        total = len(filtered)
        if total > 0:
            all_results.append((total, filtered, f"{hour}h - reg:{reg} cat:{cat} maxlen:{max_len}"))
        if best_result is None or (total > 0 and total > best_result[0]):
            best_result = (total, filtered, f"{hour}h - reg:{reg} cat:{cat} maxlen:{max_len}")
    if best_result is None:
        st.warning("아무 조건에서도 결과가 없습니다.")
        return
    s, lst, desc = best_result
    df = pd.DataFrame(lst)
    st.success(f"추천최적조건({desc}) 결과 {s}건")
    st.dataframe(df, width='stretch')

if st.button("최적화 트렌드 검색 실행"):
    find_best_trend_condition()


KST = dt.timezone(dt.timedelta(hours=9))
now_utc = dt.datetime.now(dt.timezone.utc)
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

if "whitelist" not in st.session_state or not st.session_state.whitelist:
    loaded = load_whitelist_from_gist(GIST_ID, GIST_TOKEN, GIST_FILENAME)
    st.session_state.whitelist = loaded
    if "whitelist_titles" not in st.session_state:
        st.session_state.whitelist_titles = {}
    unmapped = [x for x in loaded if x not in st.session_state.whitelist_titles]
    for token in unmapped:
        try:
            st.session_state.whitelist_titles[token] = get_channel_title(token)
        except Exception:
            st.session_state.whitelist_titles[token] = "(API실패)" + str(token)
if "whitelist" not in st.session_state:
    st.session_state.whitelist = []
if "whitelist_titles" not in st.session_state:
    st.session_state.whitelist_titles = {}

st.title("최신 유튜브 뉴스·정치 숏츠 수집기")
MODE = st.radio("수집 모드 선택", [
    "전체 트렌드 (정치/뉴스)", "화이트리스트 채널", "키워드(검색어) 기반"
], horizontal=True)
max_results = 50
if MODE != "화이트리스트 채널":
    country = st.selectbox("국가(regionCode)", ["KR", "US", "JP", "GB", "DE"], index=0)
    hour_limit = st.selectbox("최신 N시간 이내", [12, 24], index=1)
    published_after = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hour_limit)).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    max_len_sec = 180
else:
    published_after = None
    country = None
    max_len_sec = None
keyword = ""
if MODE == "키워드(검색어) 기반":
    keyword = st.text_input("검색어(뉴스/정치 관련 단어 입력)", value="")
if MODE == "화이트리스트 채널":
    st.subheader("화이트리스트 업로드·편집·저장")
    tab1, tab2 = st.tabs(["CSV 업로드", "수동 입력"])
    with tab1:
        upl = st.file_uploader("CSV(channel_id/handle/url)", type="csv")
        if upl:
            df = pd.read_csv(upl)
            ch_ids = df.iloc[:, 0].apply(lambda x: str(x).strip()).tolist()
            st.session_state.whitelist = list(sorted(set(ch_ids)))
            unmapped = [x for x in st.session_state.whitelist if x not in st.session_state.whitelist_titles]
            for token in unmapped:
                st.session_state.whitelist_titles[token] = get_channel_title(token)
            st.success(f"{len(ch_ids)}개 채널 반영됨")
    with tab2:
        manual = st.text_area("채널 직접 입력(줄바꿈/쉼표가능)", height=100)
        if st.button("수동 채널 반영"):
            ch_ids = [x.strip() for x in manual.replace(",", "\n").split("\n") if x.strip()]
            st.session_state.whitelist = list(sorted(set(ch_ids)))
            unmapped = [x for x in st.session_state.whitelist if x not in st.session_state.whitelist_titles]
            for token in unmapped:
                st.session_state.whitelist_titles[token] = get_channel_title(token)
            st.success(f"{len(ch_ids)}개 채널 반영됨")
    st.subheader("리스트 관리")
    wh = st.session_state.whitelist
    titles = st.session_state.whitelist_titles
    selected = st.multiselect(
        "채널 삭제 선택", wh, default=[],
        format_func=lambda cid: titles.get(cid, cid)
    )
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        if st.button("선택 삭제") and selected:
            st.session_state.whitelist = [x for x in wh if x not in set(selected)]
            for cid in selected:
                st.session_state.whitelist_titles.pop(cid, None)
            st.success("삭제 완료")
    with col2:
        if st.button("전체 비우기"):
            st.session_state.whitelist = []
            st.session_state.whitelist_titles = {}
            st.info("전체 삭제 완료!")
    with col3:
        if st.button("저장(GitHub에)"):
            ok = save_whitelist_to_gist(
                st.session_state.whitelist, GIST_ID, GIST_TOKEN, filename=GIST_FILENAME
            )
            if ok:
                st.success("저장 완료(GitHub Gist)")
            else:
                st.error("Gist 저장 실패")
    with col4:
        if st.button("저장된 리스트 불러오기"):
            loaded = load_whitelist_from_gist(GIST_ID, GIST_TOKEN, GIST_FILENAME)
            st.session_state.whitelist = loaded
            unmapped = [x for x in loaded if x not in st.session_state.whitelist_titles]
            for token in unmapped:
                st.session_state.whitelist_titles[token] = get_channel_title(token)
            st.success(f"불러옴: {len(loaded)}개")
    wh = st.session_state.whitelist
    titles = st.session_state.whitelist_titles
    if wh:
        df_list = []
        for cid in wh:
            name = titles[cid] if cid in titles else "(API실패)"+cid
            df_list.append({
                "채널명": name,
                "채널 ID": cid
            })
        df = pd.DataFrame(df_list)
        st.dataframe(df, width='stretch')
    else:
        st.info("등록된 채널이 없습니다.")

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
                "videoDuration": "any",
                "videoCategoryId": vcat,
                "regionCode": country,
                "maxResults": 50
            }
            if page_token: params["pageToken"] = page_token
            r = quota_requests_get("https://www.googleapis.com/youtube/v3/search", params=params)
            data = r.json()
            st.write("YouTube Search API 응답:", data)
            ids += [it["id"]["videoId"] for it in data.get("items", []) if "id" in it and "videoId" in it["id"]]
            page_token = data.get("nextPageToken")
            if not page_token or len(ids) >= max_results:
                break
    elif MODE == "화이트리스트 채널":
        for token in st.session_state.whitelist:
            uc_id = get_channel_id_from_token(token)
            if not uc_id:
                continue
            ch_api = "https://www.googleapis.com/youtube/v3/channels"
            r = quota_requests_get(ch_api, params={
                "key": API_KEY,
                "id": uc_id,
                "part": "contentDetails"
            })
            cj = r.json()
            st.write("채널 contentDetails 응답:", cj)
            items = cj.get("items", [])
            if not items:
                continue
            pid = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
            plist_api = "https://www.googleapis.com/youtube/v3/playlistItems"
            r2 = quota_requests_get(plist_api, params={
                "key": API_KEY, "playlistId": pid,
                "part": "snippet,contentDetails", "maxResults": 10
            })
            r2j = r2.json()
            st.write("채널 playlistItems 응답:", r2j)
            vids = [it["contentDetails"]["videoId"] for it in r2j.get("items", []) if "contentDetails" in it and "videoId" in it["contentDetails"]]
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
                "videoDuration": "any",
                "q": keyword,
                "regionCode": country,
                "maxResults": 50
            }
            if page_token: params["pageToken"] = page_token
            r = quota_requests_get("https://www.googleapis.com/youtube/v3/search", params=params)
            data = r.json()
            st.write("키워드 Search API 응답:", data)
            ids += [it["id"]["videoId"] for it in data.get("items", []) if "id" in it and "videoId" in it["id"]]
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
        video_resp = r.json()
        st.write("YouTube Videos API 응답:", video_resp)
        for item in video_resp.get("items", []):
            s = item.get("statistics", {})
            c = item.get("contentDetails", {})
            snip = item.get("snippet", {})
            sec = iso8601_to_seconds(c.get("duration", "")) if isinstance(c.get("duration", ""), str) else None
            stats.append({
                "title": snip.get("title", ""),
                "viewCount": int(s.get("viewCount", 0)) if is_number(s.get("viewCount", 0)) else 0,
                "channelTitle": snip.get("channelTitle", ""),
                "publishedAt": snip.get("publishedAt", ""),
                "length_sec": sec,
                "url": f"https://youtu.be/{item['id']}" if 'id' in item else ""
            })
    st.write("화이트리스트 stats 샘플:", stats[:5])
    st.write("화이트리스트 전체 video publishedAt:", [v.get("publishedAt") for v in stats])
    st.write("전체 length_sec:", [safe_float_len_sec(v) for v in stats])

    filtered = []
    for v in stats:
        sec = v.get("length_sec")
        pub = v.get("publishedAt")
        if (
            isinstance(sec, (int, float)) and sec is not None and max_len_sec is not None
            and pub is not None and isinstance(pub, str) and (published_after is None or pub >= published_after)
            and sec <= max_len_sec
        ):
            filtered.append(v)

    st.write("수집된 ids:", ids)
    st.write("수집된 stats:", stats)
    filtered = sorted(filtered, key=lambda x: x["viewCount"], reverse=True)[:20]
    df = pd.DataFrame(filtered)
    show_cols = ["title", "viewCount", "channelTitle", "publishedAt", "length_sec", "url"]
    if df.empty:
        st.info("조건에 맞는 최신 숏츠가 없습니다.")
    else:
        st.dataframe(df[show_cols], width='stretch')
        csv = df[show_cols].to_csv(index=False, encoding="utf-8-sig")
        st.download_button("CSV로 다운로드", csv, file_name="shorts_trend.csv", mime="text/csv")
        st.success(f"{len(df)}개 TOP 숏츠 (조회수 순)")
