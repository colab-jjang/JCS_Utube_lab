import streamlit as st
import```ndas as pd```port requests```port datetime``` dt
import```on
import re```om urllib.parse```port unquote```PI_KEY = st```crets["YOUTUBE_API_KEY"]
```T_ID = st.se```ts["GIST_ID"]
```T_TOKEN = st```crets["GIST_TOKEN"]
```T_FILENAME =```hitelist_channels```on"
GIST_QUOTA =```uota.json"
```_MAX_QUOTA =```000  # <--```시 네 실제 일일 할```로 변경

# ----```ota 관리 ----```f get_quota```age(GIST_ID, G```_TOKEN, filename```uota.json"):
   ```l = f"https```api.github.com/gists/{```T_ID}"
   ```aders = {"```horization": f```ken {GIST_TOKEN```
    r = requests```t(url, headers```aders, timeout```)
    if r```atus_code !=```0:
       ```turn {"date```"", "count```0}
    files```r.json().get```iles", {})
``` if filename```t in files```       return```date": "",```ount": 0}
``` content =```les[filename]['content']
``` data = json```ads(content)
   ```turn data

``` set_quota```age(GIST_ID, G```_TOKEN, count```ilename="quota```on"):
    now```t = dt.datetime```cnow() + dt```medelta(hours=9)
   ```ta = {
       ```ate": now_k```strftime("%Y-%m-%d"),
```     "count```count
    }
``` url = f"https```api.github.com/gists/{```T_ID}"
   ```aders = {"```horization": f```ken {GIST_TOKEN```
    payload```{ "files":```filename: {"```tent": json```mps(data, ensure```cii=False, indent```} } }
    r```requests.patch```l, json=payload```eaders=headers```imeout=10)
``` return r.status```de == 200

```--- KST(now```리셋 시각, 진행bar```--
KST = dt```mezone(dt.timedelta(hours```)
now_utc =```.datetime.utcnow```replace(tzinfo=dt.time```e.utc)
now_kst =```w_utc.astime```e(KST)
reset_today_kst```now_kst.replace```ur=16, minute``` second=0,```crosecond=```if now_kst``` reset_today```t:
    reset```me_kst = reset```day_kst + dt```medelta(days=1)
else:
``` reset_time```t = reset_today```t
remain =```set_time_k```- now_kst
```ta_info = get```ota_usage(GIST_ID, G```_TOKEN, GIST```OTA)
today_kst_str =```w_kst.strftime```Y-%m-%d")
if quota```fo["date"]``` today_kst```r:
    set```ota_usage(GIST_ID, G```_TOKEN, 0,```ST_QUOTA)
``` used_quota```0
else:
   ```ed_quota =```ota_info.get```ount", 0)
```gress = min```ed_quota /```I_MAX_QUOTA```.0)
st.mark```n(f"### You```e API 일일 사용```{used_quota```API_MAX_QUOTA}")
st.progress```ogress)
st.markdown(f"```음 리셋(한국 오후```):** {reset```me_kst.strftime('%Y-%m``` %H:%M:%S')```남은 시간: {str```main).split('.')[0]```
st.markdown(f"(지금: {```_kst.strftime('%Y-%m-%```H:%M:%S KST```)")

# ----```st 연동 ----```f save_whitelist```_gist(whitelist, G```_ID, GIST_TOKEN```ilename=GIST```LENAME):
   ```l = f"https```api.github.com/gists/{```T_ID}"
   ```aders = {"```horization": f```ken {GIST_TOKEN```
    payload```{
        "```es": {
           ```lename: {"```tent": json```mps(sorted(list(whitelist``` ensure_ascii```lse, indent```}
        }
``` }
    r =```quests.patch```l, json=payload```eaders=headers```imeout=20)
``` return r.status```de == 200

``` load_whitelist```om_gist(GIST_ID, G```_TOKEN, filename```ST_FILENAME):
   ```l = f"https```api.github.com/gists/{```T_ID}"
   ```aders = {"```horization": f```ken {GIST_TOKEN```
    r = requests```t(url, headers```aders, timeout```)
    if r```atus_code !=```0:
       ```turn []
``` files = r```on().get("files", {})
``` if filename```t in files```       return```
``` content =```les[filename]['content']
``` data = json```ads(content)
   ```turn data if```instance(data```ist) else []

```--- quota count```작 ----
def```ota_requests```t(*args, **```rgs):
    global```ed_quota
   ```= requests```t(*args, **```rgs)
    used```ota += 1
   ```t_quota_usage```ST_ID, GIST```KEN, used_qu```, GIST_QUOTA```   return r``` ---- 채널명 추```수 ----
def```t_channel_title```annel_token):
   ```y: 
       ```ken = str(channel```ken)
       ```annel_id =```ne
       ``` token.startswith```C") and len```ken) > 10:
```         channel``` = token
       ```if token.startswith```"):
           ```ndle = unquote```ken.lstrip("@"))
           ```= quota_requests```t("https://www.googleapis```m/youtube/v3/channels",```rams={
               ```ey": API_KEY```forHandle":```ndle, "part```"snippet"
```         },```meout=10)
```         items```r.json().get```tems", [])
```         if```ems: channel``` = items[0]["id"]
```     elif "```tube.com/" in```ken:
           ```= re.search```/@([^/?]+``` token)
           ``` m:
               ```ndle = unquote```group(1))
               ```= quota_requests```t("https://www.googleapis```m/youtube/v3/channels",```rams={
                   ```ey": API_KEY```forHandle":```ndle, "part```"snippet"
```            ``` timeout=10```              ```ems = r.json```get("items", [])
```            ``` items: channel``` = items[0]["id"]
```         else```              ```= re.search```/channel/(UC[\w-]+)", token)
                if m```hannel_id =```group(1)
       ``` channel_id```          ```= quota_requests```t("https://www.googleapis```m/youtube/v3/channels",```rams={
               ```ey": API_KEY```id": channel```, "part": "```ppet"
           ``` timeout=10```          ```ems = r.json```get("items", [])
```         if```ems:
               ```turn items```["snippet"]["title"]
```         else```              ```turn "(추출 실패``` + token
       ```turn "(추출 실패``` + token
   ```cept Exception``` e:
       ```turn f"(추출```) {channel```ken}"

def iso```1_to_seconds(iso):
   ```= re.match```PT((\d+)M)?((\d+)S)?', iso)
    return int(m.group``` or 0)*60 +```t(m.group(```or 0) if m```se 0

# ----```/세션 초기화 ----``` "whitelist```ot in st.session```ate or not```.session_state```itelist:
   ```aded = load```itelist_from_gist(GIST```, GIST_TOKEN```IST_FILENAME```   st.session```ate.whitelist =```aded
    if```hitelist_titles```ot in st.session```ate:
       ```.session_state```itelist_titles =```
    unmapped``` [x for x in loaded if x not in st.session_state.whitelist_titles]
``` for token``` unmapped:
```     try: 
```         st```ssion_state.whitelist_titles```ken]```get_channel```tle(token)
       ```cept Exception```          ```.session_state```itelist_titles[token]```"(API실패)" +```r(token)
        
```"whitelist```ot in st.session```ate:
    st```ssion_state.whitelist =```
```"whitelist```tles" not in```.session_state```   st.session```ate.whitelist_titles =```

# ---- UI``` ----
st.title```신 유튜브 뉴스·정```츠 수집기")
MODE```st.radio("```모드 선택", [
    "전체 트렌드 (정치/뉴스)", "화이트리스트 채널", "키워드(검색어) 기반"
],```rizontal=True```ax_results =```

if MODE !=```이트리스트 채널":
``` country =```.selectbox```가(regionCode)", ["KR", "US", "JP", "GB", "DE"],```dex=0)
   ```ur_limit =```.selectbox```신 N시간 이내",```2, 24],```dex=1)
   ```ngth_sec =```.selectbox```츠 최대 길이(초)",```0, 90, 120, 180, 240, 300],```dex=3)
   ```blished_after```(dt.datetime```cnow() - dt```medelta(hours=hour_limit```isoformat("T") +```"
else:
   ```blished_after```None
    country```None
    length```c = None

```word = ""
```MODE == "키```검색어) 기반":
``` keyword =```.text_input```색어(뉴스/정치 관련``` 입력)", value```)

# ==========```트리스트 관리 UI```채널 리스트 표시(``` =========```f MODE == "```리스트 채널":
   ```.subheader```이트리스트 업로드·```저장")
    tab```tab2 = st.tabs```CSV 업로드", "수동 입력"])

``` with tab1```       upl```st.file_uploader```SV(channel_id/handle/url``` type="csv```        if```l:
           ``` = pd.read```v(upl)
           ```_ids = df.iloc``` 0].```ly(lambda x```tr(x).strip```.tolist()
           ```.session_state```itelist = list```rted(set(ch_ids)))
           ```mapped = [x for x in st.session_state.whitelist if x not in st.session_state.whitelist_titles]
```         for```ken in unm```ed:
               ```.session_state```itelist_titles[token]```get_channel```tle(token)
           ```.success(f```en(ch_ids)}개 채```영됨")

    with```b2:
       ```nual = st.text```ea("채널 직접 입력```꿈/쉼표가능)", height```0)
       ``` st.button```동 채널 반영"):
```         ch```s = [x.strip() for x in manual.replace(",", "\n").split("\n") if x.strip()]
            st.session```ate.whitelist =```st(sorted(set```_ids)))
           ```mapped = [x for x in st.session_state.whitelist if x not in st.session_state.whitelist_titles]
```         for```ken in unm```ed:
               ```.session_state```itelist_titles[token]```get_channel```tle(token)
           ```.success(f```en(ch_ids)}개 채```영됨")

    st```bheader("리스트 관리```    wh = st```ssion_state.whitelist
``` titles = st```ssion_state.whitelist_titles```  selected```st.multiselect```       "채널``` 선택", wh, default``````       format```nc=lambda cid```itles.get(cid```id)
    )
``` col1, col```col3, col4```st.columns```
    with col```        if```.button("선```제") and selected```          ```.session_state```itelist = [x for x in wh if x not in set(selected)]
```         for```d in selected```              ```.session_state```itelist_titles.pop(cid```one)
           ```.success("```완료")
    with```l2:
       ``` st.button```체 비우기"):
           ```.session_state```itelist = []
```         st```ssion_state.whitelist_titles```{}
           ```.info("전체 삭제```!")
    with```l3:
       ``` st.button```장(GitHub에)"):
           ``` = save_wh```list_to_gist(
               ```.session_state```itelist, G```_ID, GIST_TOKEN```ilename=GIST```LENAME
           ```           ``` ok:
               ```.success("```완료(GitHub G```)")
           ```se:
               ```.error("Gist``` 실패")
    with```l4:
       ``` st.button```장된 리스트 불러오기```
           ```aded = load```itelist_from_gist(GIST```, GIST_TOKEN```IST_FILENAME```          ```.session_state```itelist = loaded```          unm```ed = [x for x in loaded if x not in st.session_state.whitelist_titles]
```         for```ken in unm```ed:
               ```.session_state```itelist_titles[token]```get_channel```tle(token)
           ```.success(f```옴: {len(loaded```")

    # --------```번: 현재 등록 채``` ----------
``` wh = st.session```ate.whitelist
   ```tles = st.session```ate.whitelist_titles
   ``` wh:
       ```_list = []
```     for cid``` wh:
           ```me = titles```d]``` cid in titles```se "(API실패```cid
           ```_list.append```               ```널명": name,
```            ```널 ID": cid```          })
```     df = pd```taFrame(df_list)
       ```.dataframe```, use_container```dth=True)
   ```se:
       ```.info("등록된```이 없습니다.")

```==============``` 숏츠 추출, 표,```로드 버튼 등은 기존```일 ===============```f st.button```신 숏츠 트렌드 추```:
    ids =```
``` filtered =```
``` if MODE ==```체 트렌드 (정치/```":
       ```at = "25"
```     page_token```None
       ```ile len(ids``` max_results```          ```rams = {
               ```ey": API_KEY```              ```art": "snippet```               ```ype": "video```               ```rder": "date```               ```ublishedAfter```published_after```              ```ideoDuration```"short",
               ```ideoCategory```: vcat,
               ```egionCode":```untry,
               ```axResults":```
           ```           ``` page_token```arams["pageToken"]```page_token```          r```quota_requests```t("https://www.googleapis```m/youtube/v3/search", params```rams)
           ```ta = r.json```           ```s += [it["id"]["videoId"]```r it in data```t("items", [])`````         page```ken = data```t("nextPageToken")
           ``` not page_token``` len(ids) >=```x_results:
```            ```eak
    elif```DE == "화이트``` 채널":
       ```r ch in st```ssion_state.whitelist:
```         API```"https://www```ogleapis.com/youtube/v```hannels"
           ```y_type = "``` if ch.startswith```C") else "```Username"
           ```= quota_requests```t(API, params```               ```ey": API_KEY```              ```art": "content```ails",
               ```y_type: ch```trip("@")
           ```
           ```ems = r.json```get("items", [])
```         if```t items: continue```          pid```items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
```         plist```i = "https```www.googleapis.com/youtube```/playlistItems"
           ``` = quota_requests```t(plist_api, params```               ```ey": API_KEY```playlistId```pid,
               ```art": "snippet```ntentDetails", "```Results": ```           ```
           ```ds = [it["contentDetails"]["videoId"]```r it in r2```on().get("items",[]``````         ids``` vids
    elif```DE == "키워드```어) 기반":
       ``` not keyword```rip():
           ```.warning("```를 입력해주세요.")
```         st```op()
       ```ge_token =```ne
       ```ile len(ids``` max_results```          ```rams = {
               ```ey": API_KEY```              ```art": "snippet```               ```ype": "video```               ```rder": "date```               ```ublishedAfter```published_after```              ```ideoDuration```"short",
               ```": keyword```              ```egionCode":```untry,
               ```axResults":```
           ```           ``` page_token```arams["pageToken"]```page_token```          r```quota_requests```t("https://www.googleapis```m/youtube/v3/search", params```rams)
           ```ta = r.json```           ```s += [it["id"]["videoId"]```r it in data```t("items", [])`````         page```ken = data```t("nextPageToken")
           ``` not page_token``` len(ids) >=```x_results:
```            ```eak
    stats```[]
``` for i in range``` len(ids),```):
       ```tch = ids[i:i+50]
```     params```{
           ```ey": API_KEY```          ```d": ",".join```tch),
           ```art": "content```ails,statistics,snippet```       }
       ```= quota_requests```t("https://www.googleapis```m/youtube/v3/videos", params```rams)
       ```r item in r```on().get("items", []```           ```= item.get```tatistics", {})
```         c```item.get("```tentDetails", {})
```         sn```= item.get```nippet", {})
```         sec```iso8601_to```conds(c.get("duration",```))
           ```ats.append```               ```itle": snip```t("title", ""),
```            ```iewCount":```t(s.get("view```nt", 0)),
```            ```hannelTitle```snip.get("```nnelTitle", ""),
```            ```ublishedAt```snip.get("```lishedAt", ""),
```            ```ength_sec":```c,
               ```rl": f"https```youtu.be/{item['id']```           ```
    if MODE``` "화이트리스트 채```
        filtered```stats
    else```       filtered``` [
            v for v in stats
            if v["length_sec"]``` length_sec```          and```"publishedAt"]``` published```ter
       `````` filtered =```rted(filtered```ey=lambda x```["viewCount"],```verse=True```20]
``` df = pd.Data```me(filtered)
   ```ow_cols = ["title", "viewCount", "channelTitle", "publishedAt", "length_sec", "url"]
``` if df.empty```       st.info```건에 맞는 최신 숏```없습니다.")
   ```se:
       ```.dataframe```[show_cols],```e_container```dth=True)
       ```v = df[show_cols].```
