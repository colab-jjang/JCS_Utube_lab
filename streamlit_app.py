from itertools import cycle
import streamlit as st
import pandas as pd
from utils.youtube_scraper import YouTubeScraper
from utils.data_processor import DataProcessor
import os
from datetime import datetime
import pytz
from datetime import timedelta
import json

SHEET_KEY = st.secrets["SHEET_KEY"] 
SHEET_NAME = "Sheet1"              # 실제 시트 이름

if "CREDENTIALS_JSON" in st.secrets:
    creds_dict = json.loads(st.secrets["CREDENTIALS_JSON"])
else:
    creds_path = "credentials.json"
    with open(creds_path) as f:
        creds_dict = json.load(f)

if "channels_df" not in st.session_state or st.session_state.channels_df is None:
    creds_json = st.secrets["CREDENTIALS_JSON"]
    creds_dict = json.loads(creds_json)
    ws = DataProcessor.gsheet_connect(creds_dict, SHEET_KEY, SHEET_NAME)
    channels_df = DataProcessor.load_df_from_gsheet(ws)

    if (
        channels_df is not None
        and not channels_df.empty
        and 'channel_name' in channels_df.columns
    ):
        st.session_state.channels_df = channels_df
    else:
        st.session_state.channels_df = pd.DataFrame(
            columns=['channel_id', 'channel_name', 'channel_url'])

# 페이지 설정
st.set_page_config(
    page_title="YouTube Shorts 랭킹 분석기",
    page_icon="📱",
    layout="wide",
    initial_sidebar_state="expanded"
)

# CSS 스타일링 (파스텔 테마)
st.markdown("""
<style>
    .main-header {
        font-size: 2.5rem;
        color: #FF9999;
        text-align: center;
        margin-bottom: 2rem;
        font-weight: bold;
    }
    .metric-card {
        background-color: #E8F4FD;
        padding: 1rem;
        border-radius: 10px;
        border-left: 4px solid #FF9999;
        margin: 0.5rem 0;
    }
    .stButton > button {
        background-color: #FF9999;
        color: white;
        border: none;
        border-radius: 20px;
        padding: 0.5rem 1rem;
        font-weight: bold;
    }
    .stSelectbox > div > div > div {
        background-color: #F8F9FA;
    }
</style>
""", unsafe_allow_html=True)

# 환경변수에서 API 키 가져오기
api_keys = st.secrets["youtube_api"]["KEYS"]
key_iter = cycle(api_keys)

success = False
for _ in range(len(api_keys)):
    cur_key = next(key_iter)
    os.environ["YOUTUBE_API_KEY"] = cur_key
    try:
        # 여기에 실제 API 호출/실행
        # 예: result = my_api_call_function(...)
        # 만약 여기서 403 등 에러가 발생하면 except로 감!
        scraper = YouTubeScraper(api_key=cur_key)
        result = scraper.some_api_call(...)

        if (
            isinstance(result, dict)
            and 'error' in result
            and result['error'].get('reason') == 'quotaExceeded'
        ):
            continue  # 다음 키로 순환
        # 아니면 정상 종료

        success = True
        break
    except Exception as e:  # 403, quotaExceeded 등
        continue

# 초기화
if 'scraper' not in st.session_state:
    st.session_state.scraper = YouTubeScraper()
if 'data_processor' not in st.session_state:
    st.session_state.data_processor = DataProcessor()


def main():
    st.markdown('<h1 class="main-header">📱 YouTube Shorts 랭킹 분석기</h1>',
                unsafe_allow_html=True)

    # API 키 확인
    if not os.getenv('YOUTUBE_API_KEY'):
        st.error("⚠️ YouTube API 키가 설정되지 않았습니다.")
        st.info("🔧 Streamlit Cloud의 Secrets에서 YOUTUBE_API_KEY를 설정해주세요.")
        return

    # 사이드바 메뉴
    st.sidebar.title("🎯 메뉴 선택")
    page = st.sidebar.selectbox("페이지를 선택하세요",
                                ["🔍 키워드 검색 랭킹", "📋 채널 목록 랭킹"])

    # 시간 범위 설정
    st.sidebar.markdown("---")
    st.sidebar.subheader("⏰ 시간 범위")
    time_range = st.sidebar.selectbox("분석 기간", ["12시간", "24시간"])
    hours = 12 if time_range == "12시간" else 24

    # 결과 개수 설정
    max_results = st.sidebar.slider("최대 결과 개수", 10, 100, 50)

    # --- 사이드바: API 쿼터 현황 (progress bar + 리셋타이머) --- #
    st.sidebar.markdown("### 🔋 API 쿼터 사용 현황")

    # (예시) 쿼터 한도/사용량 세팅 (실제로는 데이터 가공값을 연결)
    quota_limit = st.session_state.get('quota_limit', 10000)
    quota_used = st.session_state.get('quota_used', 4250)

    quota_pct = quota_used / quota_limit if quota_limit else 0
    st.sidebar.progress(
        quota_pct, text=f"오늘 쿼터: {quota_used:,} / {quota_limit:,}")

    # 리셋 시간: 매일 KST 16:00
    kst = pytz.timezone('Asia/Seoul')
    now_kst = datetime.now(kst)

    reset_time = now_kst.replace(hour=16, minute=0, second=0, microsecond=0)
    # 이미 지났으면 내일로 이동
    if now_kst > reset_time:
        reset_time = reset_time + timedelta(days=1)
    time_left = reset_time - now_kst

    hours, remainder = divmod(time_left.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    st.sidebar.info(
        f"🕓 쿼터 리셋(KST)까지 {time_left.days}일 {hours}시간 {minutes}분 {seconds}초 남음")

    # 페이지별 라우팅
    if page == "🔍 키워드 검색 랭킹":
        keyword_search_page(hours, max_results)
    elif page == "📋 채널 목록 랭킹":
        channel_list_page(hours, max_results)

def keyword_search_page(hours, max_results):
    st.header("🔍 키워드 검색 기반 Shorts 랭킹")

    keyword = st.text_input("검색할 키워드를 입력하세요",
                            placeholder="예: 요리, 게임, 댄스, K-pop")

    if keyword and st.button("🔍 검색 시작", key="keyword_search"):
        with st.spinner(f"**{keyword}** 키워드 검색 중..."):
            try:
                shorts_data = st.session_state.scraper.get_keyword_shorts(
                    keyword, hours, max_results)

                if not shorts_data:
                    st.warning("❌ 검색 결과가 없습니다. 다른 키워드나 시간 범위를 시도해보세요.")
                    return

                df = st.session_state.data_processor.create_dataframe(
                    shorts_data)

                st.session_state['analysis_result'] = df
                display_results(df, f'"{keyword}" 키워드')

            except Exception as e:
                st.error(f"오류가 발생했습니다: {str(e)}")

    if 'analysis_result' in st.session_state:
        df = st.session_state['analysis_result']

        # (원하는 형태로 결과 요약, 랭킹 등 출력. 아래는 예시)
        st.success(f"✅ {len(df)}개 Shorts 결과")
        st.dataframe(df)     # ★여기에 display_results 등 상세 표시 넣어도 됨

        csv = df.to_csv(index=False, encoding='utf-8-sig')
        st.download_button(
            label="CSV로 저장",
            data=csv,
            file_name="shorts_result.csv",
            mime="text/csv"
        )


def add_channel_form():
    st.subheader("➕ 채널 수동 추가")
    with st.form("add_channel_form"):
        channel_id = st.text_input("채널 ID (필수)")
        channel_name = st.text_input("채널명 (필수)")
        channel_url = st.text_input("채널 URL (필수)")
        submitted = st.form_submit_button("추가하기")
        if submitted:
            # 필수값 체크
            if not channel_id:
                st.warning("채널 ID는 반드시 입력해야 합니다!")
            else:
                # 현재 df에 행 추가! (없는 경우 대비)
                if 'channels_df' not in st.session_state or st.session_state.channels_df is None:
                    st.session_state.channels_df = pd.DataFrame(
                        columns=['channel_id', 'channel_name', 'channel_url'])
                new_row = {'channel_id': channel_id,
                           'channel_name': channel_name, 'channel_url': channel_url}
                st.session_state.channels_df = pd.concat(
                    [st.session_state.channels_df, pd.DataFrame([new_row])], ignore_index=True)
                # 구글시트 저장까지 동기화!
                creds_json = st.secrets["CREDENTIALS_JSON"]
                creds_dict = json.loads(creds_json)
                ws = DataProcessor.gsheet_connect(
                    creds_dict, SHEET_KEY, SHEET_NAME)
                DataProcessor.save_df_to_gsheet(
                    st.session_state.channels_df, ws)
                st.success("채널이 정상적으로 추가되었습니다!")


def channel_list_page(hours, max_results):
    st.header("📋 채널 목록 기반 Shorts 랭킹")

    # 1. 채널 전체 목록 표 (최상단)
    if ('channels_df' in st.session_state and
        st.session_state.channels_df is not None and
            'channel_name' in st.session_state.channels_df.columns):
        st.markdown("---")
        st.subheader("🗂️ 업로드된 전체 채널 목록")
        df = st.session_state.channels_df.copy()
        st.dataframe(
            df,
            use_container_width=True,
            column_config={
                "channel_url": st.column_config.LinkColumn(
                    "채널 URL",
                    help="채널 페이지로 바로가기",
                    max_chars=30,
                )
            }
        )
    else:
        st.info("⛔ 업로드된 채널이 없거나 'channel_name' 컬럼이 없습니다. 먼저 파일을 업로드 해 주세요.")

    # 랭킹 집계용 데이터 리스트 ---------- 숏츠 검색 안될 때 진단용용
    shortsdata = []

    # 실제 분석 대상 컬럼이 일치하는지 확인 (channel_id)
    channel_ids = st.session_state.channels_df['channel_id'].tolist()

    # ★★★ 가장 중요한 핵심: 각 채널별 Shorts 수집 루프 내부 ★★★
    for cid in channel_ids:
        try:
            # 실제 숏츠 수집 함수명은 프로젝트 환경에 맞게! (아래는 예)
            shorts = st.session_state.scraper.get_shorts_from_uploads_playlist(
                cid, hours=hours, max_results=max_results
            )
            # ⬇⬇★ 반드시 이 줄을 추가! (진단 로그: 몇 개 수집됐는지, cid로 구분)
            st.write(f"채널ID {cid} : 수집된 Shorts {len(shorts)}개")
            shortsdata.extend(shorts)
        except Exception as e:
            # ⬇⬇★ 이 부분도 반드시! (에러 발생시 원인 파악)
            st.write(f"채널ID {cid} 오류 : {str(e)}")

    # 2. '분석' 버튼 (등록된 데이터가 있을 때만)
    if ('channels_df' in st.session_state and
        st.session_state.channels_df is not None and
        not st.session_state.channels_df.empty and
            'channel_id' in st.session_state.channels_df.columns):

        if st.button("🔍 등록된 채널로 분석", key="channel_search_now"):
            raw_channel_ids = st.session_state.channels_df['channel_id'].tolist(
            )
            api_key = os.getenv("YOUTUBE_API_KEY")
            channel_ids = []
            for user_input in raw_channel_ids:
                if str(user_input).strip().startswith('UC'):
                    cid = str(user_input).strip()
                elif str(user_input).strip().startswith('@'):
                    # 변환 함수는 유틸(py) 또는 SCRAPER.py 클래스에 정의되어 있어야 함
                    cid = st.session_state.scraper.handle_to_channel_id(
                        str(user_input).strip(), api_key)
                else:
                    cid = None
                if cid:
                    channel_ids.append(cid)
            # 이제 channel_ids에는 모두 UC~만 남음!

            with st.spinner(f"{len(channel_ids)}개 채널 분석 중..."):
                try:
                    shorts_data = []
                    for cid in channel_ids:
                        shorts = st.session_state.scraper.get_shorts_from_uploads_playlist(
                            cid, hours=hours, max_results=max_results
                        )
                        shorts_data.extend(shorts)
                    shorts_data.sort(key=lambda x: int(
                        x['view_count']), reverse=True)
                    shorts_data = shorts_data[:max_results]

                    if not shorts_data:
                        st.warning("❌ 해당 채널들에서 최근 Shorts를 찾을 수 없습니다.")
                        return

                    df = st.session_state.data_processor.create_dataframe(
                        shorts_data)
                    # 🚩분석 직후 반드시 세션에 저장
                    st.session_state['analysis_result'] = df

                except Exception as e:
                    st.error(f"오류가 발생했습니다: {str(e)}")
    # 🚩항상 화면에 결과 테이블/다운로드 버튼 표시 (if문 안에서 벗어나 있어야 함!)
    if 'analysis_result' in st.session_state:
        df = st.session_state['analysis_result']
        st.subheader("최신 분석 결과")
        st.dataframe(df)
        csv = df.to_csv(index=False, encoding='utf-8-sig')
        st.download_button(
            label="CSV로 저장",
            data=csv,
            file_name="shorts_result.csv",
            mime="text/csv"
        )

    # 3. 업로드 구간
    st.subheader("📁 채널 목록 파일 업로드")
    api_key = os.getenv("YOUTUBE_API_KEY")
    uploaded_file = st.file_uploader(
        "CSV 또는 Excel 파일을 업로드하세요",
        type=['csv', 'xlsx', 'xls'])
    if uploaded_file is not None:
        channels_df, error = st.session_state.data_processor.load_channel_list(
            uploaded_file, api_key=api_key)
        if channels_df is None:
            st.error(f"업로드 에러: {error or '알 수 없는 파일 오류!'}")
            st.stop()
        st.write("업로드된 컬럼명:", channels_df.columns.tolist())
        import numpy as np
        channels_df = channels_df.replace([np.inf, -np.inf], np.nan)
        channels_df = channels_df.fillna(0)
        columns_to_clean = ['channel_id', 'channel_name', 'channel_url']
        for col in columns_to_clean:
            if col in channels_df.columns:
                channels_df[col] = channels_df[col].fillna('')
        if error:
            st.error(f"❌ {error}")
            st.info("💡 파일 형식: channel_id, channel_name 컬럼이 필요합니다")
            example_df = pd.DataFrame({
                'channel_id': ['UCuAXFkgsw1L7xaCfnd5JJOw', 'UC_x5XG1OV2P6uZZ5FSM9Ttw'],
                'channel_name': ['예시 채널 1', '예시 채널 2']
            })
            csv = example_df.to_csv(index=False, encoding='utf-8-sig')
            st.download_button(
                label="📥 예시 파일 다운로드",
                data=csv,
                file_name="example_channels.csv",
                mime="text/csv"
            )
            return
        st.success(f"✅ {len(channels_df)}개 채널이 업로드되었습니다!")
        st.session_state.channels_df = channels_df

        creds_json = st.secrets["CREDENTIALS_JSON"]
        creds_dict = json.loads(creds_json)
        ws = DataProcessor.gsheet_connect(creds_dict, SHEET_KEY, SHEET_NAME)
        DataProcessor.save_df_to_gsheet(channels_df, ws)

    # 4. 수동 채널 추가 폼
    add_channel_form()

    # 5. 삭제 (멀티셀렉트/전체삭제)
    if ('channels_df' in st.session_state and
        st.session_state.channels_df is not None and
            'channel_name' in st.session_state.channels_df.columns):
        all_channels = st.session_state.channels_df['channel_name'].tolist()
        selected = st.multiselect("삭제할 채널을 선택하세요", all_channels)
        if st.button("선택한 채널만 삭제"):
            before = len(st.session_state.channels_df)
            st.session_state.channels_df = st.session_state.channels_df[
                ~st.session_state.channels_df['channel_name'].isin(selected)
            ]
            after = len(st.session_state.channels_df)
            creds_json = st.secrets["CREDENTIALS_JSON"]
            creds_dict = json.loads(creds_json)
            ws = DataProcessor.gsheet_connect(
                creds_dict, SHEET_KEY, SHEET_NAME)
            DataProcessor.save_df_to_gsheet(st.session_state.channels_df, ws)
            st.success(f"선택한 {before-after}개 채널이 삭제되었습니다!")
        if st.button("🗑️ 전체 채널 삭제"):
            st.session_state.channels_df = pd.DataFrame()
            creds_json = st.secrets["CREDENTIALS_JSON"]
            creds_dict = json.loads(creds_json)
            ws = DataProcessor.gsheet_connect(
                creds_dict, SHEET_KEY, SHEET_NAME)
            DataProcessor.save_df_to_gsheet(st.session_state.channels_df, ws)
            st.success("전체 채널이 삭제되었습니다!")


def display_results(df, source_name):
    """결과 표시 함수"""
    if df.empty:
        st.warning("표시할 데이터가 없습니다.")
        return

    st.success(f"✅ **{source_name}**에서 **{len(df)}개**의 Shorts를 찾았습니다!")

    # 통계 정보
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        total_views = df['view_count'].sum()
        st.markdown(f"""
        <div class="metric-card">
            <h4>총 조회수</h4>
            <h2>{st.session_state.data_processor.format_number(total_views)}</h2>
        </div>
        """, unsafe_allow_html=True)

    with col2:
        total_likes = df['like_count'].sum()
        st.markdown(f"""
        <div class="metric-card">
            <h4>총 좋아요</h4>
            <h2>{st.session_state.data_processor.format_number(total_likes)}</h2>
        </div>
        """, unsafe_allow_html=True)

    with col3:
        avg_views = df['view_count'].mean()
        st.markdown(f"""
        <div class="metric-card">
            <h4>평균 조회수</h4>
            <h2>{st.session_state.data_processor.format_number(avg_views)}</h2>
        </div>
        """, unsafe_allow_html=True)

    with col4:
        unique_channels = df['channel'].nunique()
        st.markdown(f"""
        <div class="metric-card">
            <h4>채널 수</h4>
            <h2>{unique_channels}</h2>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("---")

    # 랭킹 테이블
    st.subheader("🏆 Shorts 랭킹")

    display_df = df[['title', 'channel', 'formatted_views', 'formatted_likes',
                     'published_at', 'video_url']].copy()
    display_df.columns = ['제목', '채널명', '조회수', '좋아요', '발행일', 'URL']
    display_df.insert(0, '순위', range(1, len(display_df) + 1))

    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "URL": st.column_config.LinkColumn(
                "YouTube 링크",
                help="Shorts 보러가기",
                max_chars=20
            )
        }
    )

    # CSV 다운로드
    csv_data = st.session_state.data_processor.create_download_csv(df)
    if csv_data:
        current_time = datetime.now().strftime("%Y%m%d_%H%M%S")
        st.download_button(
            label="📥 결과를 CSV로 다운로드",
            data=csv_data,
            file_name=f"youtube_shorts_ranking_{current_time}.csv",
            mime="text/csv",
            key="download_csv"
        )


if __name__ == "__main__":
    main()







