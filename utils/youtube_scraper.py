import requests
import json
from datetime import datetime, timedelta
import os
import streamlit as st

class YouTubeScraper:
    def __init__(self):
        self.api_key = st.secrets.get("YOUTUBE_API_KEY") or os.getenv("YOUTUBE_API_KEY")
        self.base_url = "https://www.googleapis.com/youtube/v3"

    def get_uploads_playlist_id(self, channel_id):
        """ 
        유튜브 채널ID→해당 채널의 업로드 플레이리스트ID 반환
        """
        if not self.api_key:
            return None
        url = f"{self.base_url}/channels"
        params = {
            'part': 'contentDetails',
            'id': channel_id,
            'key': self.api_key
        }
        try:
            resp = requests.get(url, params=params).json()

            print("channels API 응답:", resp)  # ← 진단 로그 추가, 되면 삭제
            # 안전하게 items 키와 값 존재 확인!
            if 'items' not in resp or not resp['items']:
                print(f"채널 ID 오류 혹은 응답 비어있음: {channel_id}")
                return None
            
            return resp['items'][0]['contentDetails']['relatedPlaylists']['uploads']
        except Exception as e:
            print(f"get_uploads_playlist_id error: {e}")
            return None
    
    def get_category_shorts(self, category_id, hours=24, max_results=50):
        """카테고리별 인기 Shorts 가져오기"""
        if not self.api_key:
            return []
            
        published_after = (datetime.now() - timedelta(hours=hours)).isoformat() + "Z"
        
        search_url = f"{self.base_url}/search"
        params = {
            'part': 'id,snippet',
            'key': self.api_key,
            'type': 'video',
            'order': 'viewCount',
            'publishedAfter': published_after,
            'maxResults': max_results,
            'videoDuration': 'short'
        }
        
        try:
            response = requests.get(search_url, params=params)
            data = response.json()

            #error_status = (data.get('error') or {}).get('status', '')
                
            shorts_data = []
            for item in data['items']:
                video_id = item['id']['videoId']
                video_details = self.get_video_details(video_id)
                
                if video_details and self.is_short_video(video_details):
                    shorts_data.append({
                        'video_id': video_id,
                        'title': item['snippet']['title'],
                        'channel': item['snippet']['channelTitle'],
                        'published_at': item['snippet']['publishedAt'],
                        'thumbnail': item['snippet']['thumbnails']['medium']['url'],
                        'view_count': video_details.get('viewCount', 0),
                        'like_count': video_details.get('likeCount', 0),
                        'duration': video_details.get('duration', '')
                    })
            
            shorts_data.sort(key=lambda x: int(x['view_count']), reverse=True)
            return shorts_data
            
        except Exception as e:
            print(f"Error: {e}")
            return []
    
    def get_keyword_shorts(self, keyword, hours=24, max_results=50):
        """키워드 기반 Shorts 검색"""
        
        if not self.api_key:
            return []
            
        published_after = (datetime.now() - timedelta(hours=hours)).isoformat() + "Z"
        
        search_url = f"{self.base_url}/search"
        params = {
            'part': 'id,snippet',
            'key': self.api_key,
            'q': keyword + " #shorts",
            'type': 'video',
            'order': 'viewCount',
            'publishedAfter': published_after,
            'maxResults': max_results,
            'videoDuration': 'short'
        }
        
        try:
            response = requests.get(search_url, params=params)
            data = response.json()   
                
            shorts_data = []
            for item in data['items']:
                video_id = item['id']['videoId']
                video_details = self.get_video_details(video_id)
                
                if video_details and self.is_short_video(video_details):
                    shorts_data.append({
                        'video_id': video_id,
                        'title': item['snippet']['title'],
                        'channel': item['snippet']['channelTitle'],
                        'published_at': item['snippet']['publishedAt'],
                        'thumbnail': item['snippet']['thumbnails']['medium']['url'],
                        'view_count': video_details.get('viewCount', 0),
                        'like_count': video_details.get('likeCount', 0),
                        'duration': video_details.get('duration', '')
                    })
            
            shorts_data.sort(key=lambda x: int(x['view_count']), reverse=True)
            return shorts_data
            
        except Exception as e:
            print(f"Error: {e}")
            return []

    def parse_ISO8601_duration_to_seconds(self, duration_str):
        import re
        if not duration_str:
            return 0
        m = re.match(r"PT((?P<m>\d+)M)?((?P<s>\d+)S)?", duration_str)
        if not m:
            return 0
        return int(m.group("m") or 0) * 60 + int(m.group("s") or 0)

    def is_short_video(self, video_details):
        duration = video_details['duration']             # PT50S 혹은 PT1M2S 등
        total_seconds = self.parse_ISO8601_duration_to_seconds(duration)
        return total_seconds <= 120

    def get_shorts_from_uploads_playlist(self, channel_id, hours=24, max_results=50):
        uploads_id = self.get_uploads_playlist_id(channel_id)
        st.write("채널 id:", channel_id) #되면 삭제
        if not uploads_id:
            return []
        url = f'{self.base_url}/playlistItems'
        params = {
            'part': 'contentDetails',
            'playlistId': uploads_id,
            'maxResults': 50,   # 필요시 더 늘릴 수 있음
            'key': self.api_key
        }
        res = requests.get(url, params=params).json()
        video_ids = [item['contentDetails']['videoId'] for item in res.get('items', [])]
        
        # 상세정보 조회 (최대 50개, 여러 번 나눠서 할수도 있음)
        shorts = []
        published_after = (datetime.now() - timedelta(hours=hours))
        for batch in [video_ids[i:i+50] for i in range(0, len(video_ids), 50)]:
            details_url = f"{self.base_url}/videos"
            details_params = {
                'part': 'snippet,contentDetails,statistics',
                'id': ','.join(batch),
                'key': self.api_key
            }
            details_res = requests.get(details_url, params=details_params).json()
            for v in details_res.get('items', []):
                # 길이 파싱
                duration = v['contentDetails'].get('duration', '')
                published_at_str = v['snippet'].get('publishedAt')
                try:
                    published_at = datetime.strptime(published_at_str, "%Y-%m-%dT%H:%M:%SZ")
                except:
                    published_at = None
                sec = self.parse_ISO8601_duration_to_seconds(duration)
                if sec <= 60 and published_at and published_at > published_after:
                    shorts.append({
                        'video_id': v['id'],
                        'title': v['snippet']['title'],
                        'channel': v['snippet']['channelTitle'],
                        'published_at': published_at_str,
                        'thumbnail': v['snippet']['thumbnails']['medium']['url'],
                        'view_count': v['statistics'].get('viewCount', 0),
                        'like_count': v['statistics'].get('likeCount', 0),
                        'duration': duration
                    })
        shorts.sort(key=lambda x: int(x['view_count']), reverse=True)
        return shorts[:max_results]


    def get_channel_shorts(self, channel_ids, hours=24, max_results=50):
        """특정 채널들의 Shorts 가져오기"""
        if not self.api_key:
            return []
            
        all_shorts = []

        published_after = (datetime.now() - timedelta(hours=hours)).isoformat() + "Z"
        
        for channel_id in channel_ids:
            try:
                search_url = f"{self.base_url}/search"
                params = {
                        'part': 'id,snippet',
                        'type': 'video',
                        'channelId': channel_id,
                        'order': 'date',
                        'publishedAfter': published_after,
                        #'videoDuration': 'short',
                        'maxResults': 20,           # 쿼터 절약
                        'key': api_key
                }
                
                response = requests.get(search_url, params=params)
                data = response.json()  

                if 'items' in data:
                    for item in data['items']:
                        video_id = item['id']['videoId']
                        video_details = self.get_video_details(video_id)
                        
                        if video_details and self.is_short_video(video_details):
                            all_shorts.append({
                                'video_id': video_id,
                                'title': item['snippet']['title'],
                                'channel': item['snippet']['channelTitle'],
                                'published_at': item['snippet']['publishedAt'],
                                'thumbnail': item['snippet']['thumbnails']['medium']['url'],
                                'view_count': video_details.get('viewCount', 0),
                                'like_count': video_details.get('likeCount', 0),
                                'duration': video_details.get('duration', '')
                            })
            
            except Exception as e:
                print(f"Error processing channel {channel_id}: {e}")
                continue
        
        all_shorts.sort(key=lambda x: int(x['view_count']), reverse=True)
        return all_shorts[:max_results]
    
    def get_video_details(self, video_id):
        """비디오 상세 정보 가져오기"""
        if not self.api_key:
            return None
            
        try:
            video_url = f"{self.base_url}/videos"
            params = {
                'part': 'statistics,contentDetails',
                'id': video_id,
                'key': self.api_key
            }
            
            response = requests.get(video_url, params=params)
            data = response.json()
            
            if 'items' in data and data['items']:
                item = data['items'][0]
                return {
                    'viewCount': item['statistics'].get('viewCount', 0),
                    'likeCount': item['statistics'].get('likeCount', 0),
                    'duration': item['contentDetails'].get('duration', '')
                }
            return None
            
        except Exception as e:
            print(f"Error getting video details: {e}")
            return None
    
    def is_short_video(self, video_details):
        """Shorts인지 확인 (60초 이하)"""
        duration = video_details.get('duration', '')
        if not duration:
            return False
            
        try:
            if 'PT' in duration:
                duration = duration.replace('PT', '')
                total_seconds = 0
                
                if 'M' in duration:
                    parts = duration.split('M')
                    total_seconds += int(parts[0]) * 60
                    duration = parts[1] if len(parts) > 1 else ''
                
                if 'S' in duration:
                    total_seconds += int(duration.replace('S', ''))
                
                return total_seconds <= 60
            
        except:
            pass
            
        return False

    @staticmethod
    def get_category_list():
        """YouTube 카테고리 목록"""
        return {
            "10": "음악",
            "15": "애완동물",
            "17": "스포츠", 
            "19": "여행 & 이벤트",
            "20": "게임",
            "22": "사람 & 블로그",
            "23": "코미디",
            "24": "엔터테인먼트",
            "25": "뉴스 & 정치",
            "26": "교육",
            "27": "과학 & 기술",
            "28": "자동차"
        }
    
    @staticmethod
    def handle_to_channel_id(handle, api_key):
        url = "https://www.googleapis.com/youtube/v3/channels"
        params = {'part': 'id', 'forHandle': handle.lstrip('@'), 'key': api_key}
        resp = requests.get(url, params=params).json()
        if 'items' in resp and resp['items']:
            return resp['items'][0]['id']

        return None

