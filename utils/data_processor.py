import pandas as pd
import io
from datetime import datetime
import streamlit as st
import re
import requests  # YouTube API 호출용
import gspread
from google.oauth2.service_account import Credentials


class DataProcessor:
    
    @staticmethod
    def create_dataframe(shorts_data):
        """Shorts 데이터를 DataFrame으로 변환"""
        if not shorts_data:
            return pd.DataFrame()
            
        df = pd.DataFrame(shorts_data)
        
        df['view_count'] = pd.to_numeric(df['view_count'], errors='coerce').fillna(0)
        df['like_count'] = pd.to_numeric(df['like_count'], errors='coerce').fillna(0)
        
        df['video_url'] = df['video_id'].apply(lambda x: f"https://www.youtube.com/shorts/{x}")
        df['published_at'] = pd.to_datetime(df['published_at']).dt.strftime('%Y-%m-%d %H:%M')
        
        df['formatted_views'] = df['view_count'].apply(DataProcessor.format_number)
        df['formatted_likes'] = df['like_count'].apply(DataProcessor.format_number)
        
        desired_order = [
                'title',         # 영상 제목
                'channel',       # 채널명
                'view_count',    # 조회수(원본)
                'like_count',    # 좋아요(원본)
                'published_at',  # 게시날짜
                'video_url',     # 영상 링크
                # 그 외 원하는 컬럼이 있으면 추가
        ]
        # 실제 존재하는 컬럼만 추려서 순서 유지
        order = [col for col in desired_order if col in df.columns] + \
                [col for col in df.columns if col not in desired_order]
        df = df[order]

        return df
    
    @staticmethod
    def format_number(num):
        """숫자 포맷팅 (1000 → 1K)"""
        try:
            num = int(num)
            if num >= 1000000:
                return f"{num/1000000:.1f}M"
            elif num >= 1000:
                return f"{num/1000:.1f}K"
            else:
                return str(num)
        except:
            return "0"
    
    @staticmethod
    def load_channel_list(uploaded_file, api_key=None):
        """채널 url만 있는 파일도 처리하도록 개선"""
        try:
            if uploaded_file.name.endswith('.csv'):
                df = pd.read_csv(uploaded_file)
            elif uploaded_file.name.endswith(('.xlsx', '.xls')):
                df = pd.read_excel(uploaded_file)
            else:
                return None, "지원하지 않는 파일 형식입니다."
            
            if 'channel_id' in df.columns and 'channel_name' in df.columns:
                return df, None
            elif 'channel_url' in df.columns and api_key:
                return DataProcessor.process_uploaded_channel_urls(df, api_key)
            else:
                return None, "필수 컬럼이 없습니다: channel_id, channel_name 혹은 channel_url"
            
        except Exception as e:
            return None, f"파일 읽기 오류: {str(e)}"

    
    @staticmethod
    def create_download_csv(df):
        """다운로드용 CSV 생성"""
        if df.empty:
            return None
            
        download_df = df[['title', 'channel', 'formatted_views', 'formatted_likes', 
                         'published_at', 'video_url']].copy()
        
        download_df.columns = ['제목', '채널명', '조회수', '좋아요', '발행일', 'URL']
        
        output = io.StringIO()
        download_df.to_csv(output, index=False, encoding='utf-8-sig')
        return output.getvalue()
    
    @staticmethod
    def extract_channel_id_and_name_from_url(url, api_key):
        # 1. 채널 id 추출 (channel/id)
        match = re.search(r'channel/([A-Za-z0-9_\-]{24})', url)
        if match:
            channel_id = match.group(1)
        else:
            # 2. '@핸들' 타입이면 YouTube API로 id 조회 필요
            if '@' in url:
                handle = url.strip('/').split('@')[-1]
                search_url = f"https://www.googleapis.com/youtube/v3/channels?part=id,snippet&forHandle=@{handle}&key={api_key}"
                r = requests.get(search_url)
                res = r.json()
                if "items" in res and res["items"]:
                    channel_id = res["items"][0]["id"]
                else:
                    channel_id = None
            else:
                channel_id = None

        # 3. 채널명 조회 (id 확보된 경우)
        if channel_id:
            info_url = f"https://www.googleapis.com/youtube/v3/channels?part=snippet&id={channel_id}&key={api_key}"
            r = requests.get(info_url)
            res = r.json()
            if "items" in res and res["items"]:
                name = res["items"][0]["snippet"]["title"]
                return channel_id, name
        return None, None

    @staticmethod
    def process_uploaded_channel_urls(df, api_key):
        """channel_url 컬럼에서 id/name 자동 추출, 새 df 리턴"""
        results = []
        for _, row in df.iterrows():
            url = row['channel_url']
            channel_id, name = DataProcessor.extract_channel_id_and_name_from_url(url, api_key)
            if channel_id and name:
                results.append({'channel_id': channel_id, 'channel_name': name, 'channel_url': url})
        new_df = pd.DataFrame(results)
        if new_df.empty:
            return None, "유효한 채널이 없습니다."
        return new_df, None
    
    @staticmethod
    def gsheet_connect(credentials_data, spreadsheet_key, sheet_name):
        # 구글시트와 연결해서 worksheet 객체 리턴
        scopes = ["https://www.googleapis.com/auth/spreadsheets",
                  "https://www.googleapis.com/auth/drive"]
        if isinstance(credentials_data, dict):
            credentials = Credentials.from_service_account_info(credentials_data, scopes=scopes)
        else:
            credentials = Credentials.from_service_account_file(credentials_data, scopes=scopes)
        gc = gspread.authorize(credentials)
        sh = gc.open_by_key(spreadsheet_key)
        worksheet = sh.worksheet(sheet_name)
        return worksheet

    @staticmethod
    def save_df_to_gsheet(df, worksheet):
        # df를 worksheet에 저장 (헤더 포함 덮어쓰기)
        worksheet.clear()
        worksheet.update([df.columns.values.tolist()] + df.values.tolist())

    @staticmethod
    def load_df_from_gsheet(worksheet):
        # worksheet에서 데이터 읽어서 df로 반환
        rows = worksheet.get_all_values()
        if not rows:
            return None
        df = pd.DataFrame(rows[1:], columns=rows[0])  # 첫 행은 컬럼명
        return df
