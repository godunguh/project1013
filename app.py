import streamlit as st
from google.oauth2.service_account import Credentials
import pandas as pd
import uuid
import os
import json
import base64
import streamlit.components.v1 as components
from io import BytesIO
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from googleapiclient.errors import HttpError
from streamlit_oauth import OAuth2Component
from datetime import datetime
import requests
import jwt

from supabase import create_client, Client

# --- 상수 및 기본 설정 ---
SUPABASE_BUCKET_NAME = "images"

# Google Sheets 헤더 정의
PROBLEM_HEADERS = [
    "id", "title", "category", "question", "option1", "option2", "option3", "option4", 
    "answer", "creator_name", "creator_email", "explanation", "question_image_id", 
    "explanation_image_id", "question_type", "created_at"
]
SOLUTION_HEADERS = ["problem_id", "user_email", "user_name", "solved_at"]
DRIVE_FOLDER_NAME = "MyQuizApp Images"

# OAuth2 설정 (secrets.toml 파일 사용)
CLIENT_ID = st.secrets.get("oauth_credentials", {}).get("CLIENT_ID")
CLIENT_SECRET = st.secrets.get("oauth_credentials", {}).get("CLIENT_SECRET")
if not CLIENT_ID:
    CLIENT_ID = os.getenv("CLIENT_ID")
if not CLIENT_SECRET:
    CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REDIRECT_URI = st.secrets.get("oauth_credentials", {}).get("REDIRECT_URI", "https://study-inside.onrender.com")
AUTHORIZE_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"
SUPABASE_URL = st.secrets.get("SUPABASE_URL") or os.getenv("SUPABASE_URL")
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY") or os.getenv("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- 관리자 확인 함수 ---
def is_admin(supabase, email: str) -> bool:
    """Supabase의 admin_emails 테이블에서 관리자 여부 확인"""
    try:
        res = supabase.table("admin_emails").select("email").eq("email", email).execute()
        return len(res.data) > 0
    except Exception as e:
        st.error(f"관리자 확인 오류: {e}")
        return False

# --- CSS 스타일 ---
def apply_custom_css():
    st.markdown(r"""
        <style>
            .st-emotion-cache-10trblm { background-color: #0d6efd; color: white; padding: 1rem;
                border-radius: 0.5rem; text-align: center;
            }
            h1 { color: white; font-size: 2.2rem; }
            h2 { border-bottom: 2px solid #0d6efd; padding-bottom: 0.5rem; color: #0d6efd; }
            @media (max-width: 768px) { h1 { font-size: 1.8rem; } }
        </style>
    """, unsafe_allow_html=True)

# --- 상태 관리 함수 ---
def initialize_app_state():
    if 'page' not in st.session_state: 
        st.session_state.page = "목록"
    if 'selected_problem_id' not in st.session_state: 
        st.session_state.selected_problem_id = None
    if 'token' not in st.session_state: 
        st.session_state.token = None
    if 'user_info' not in st.session_state: 
        st.session_state.user_info = None

# --- 구글 API 연결 함수 ---
@st.cache_resource
def get_google_creds():
    scopes = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    if "gcp_service_account" in st.secrets:
        return Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scopes)
    
    creds_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "credentials.json")
    if os.path.exists(creds_path):
        return Credentials.from_service_account_file(creds_path, scopes=scopes)
    
    st.error("🚨 구글 서비스 계정 정보를 찾을 수 없습니다.")
    st.stop()

# --- Supabase 연결 함수 ---
@st.cache_resource
def init_supabase_client():
    try:
        supabase_url = st.secrets["SUPABASE_URL"]
        supabase_key = st.secrets["SUPABASE_KEY"]
    except KeyError:
        st.error("🚨 secrets.toml 파일에 SUPABASE_URL과 SUPABASE_KEY를 설정해야 합니다.")
        st.stop()
    
    if not supabase_url or not supabase_key:
        st.error("🚨 Supabase URL 또는 Key 값이 비어있습니다. secrets.toml 파일을 확인하세요.")
        st.stop()
        
    return create_client(supabase_url, supabase_key)

# --- Supabase Storage (파일) 처리 함수 ---
def upload_image_to_storage(supabase: Client, bucket_name: str, image_file):
    if not image_file: return None, "파일이 없습니다."
    try:
        bytes_data = image_file.getvalue()
        file_path = f"{uuid.uuid4().hex}.png"
        supabase.storage.from_(bucket_name).upload(file=bytes_data, path=file_path, file_options={"content-type": "image/png"})
        res = supabase.storage.from_(bucket_name).get_public_url(file_path)
        return res, None
    except Exception as e:
        return None, f"이미지 업로드 오류: {e}"

def delete_image_from_storage(supabase: Client, bucket_name: str, image_url: str):
    if not image_url or not isinstance(image_url, str): return
    try:
        file_path = image_url.split(f'{bucket_name}/')[-1]
        if file_path:
            supabase.storage.from_(bucket_name).remove([file_path])
    except Exception as e:
        st.warning(f"Storage 파일 삭제 오류 (URL: {image_url}): {e}")

# --- Supabase DB (데이터) 처리 함수 ---
@st.cache_data(ttl=300)
def load_data_from_db(_supabase: Client, table_name: str):
    try:
        response = _supabase.table(table_name).select("*").order("created_at", desc=True).execute()
        return pd.DataFrame(response.data)
    except Exception as e:
        st.error(f"{table_name} 데이터 로딩 오류: {e}")
        return pd.DataFrame()

def save_solution_to_db(supabase: Client, solution_data: dict):
    try:
        supabase.table("solutions").insert(solution_data).execute()
        st.cache_data.clear()
    except Exception as e:
        st.error(f"풀이 기록 저장 오류: {e}")

def save_problem_to_db(supabase: Client, problem_data: dict):
    try:
        supabase.table("problems").insert(problem_data).execute()
        st.cache_data.clear()
    except Exception as e:
        st.error(f"문제 저장 오류: {e}")

def delete_problem_from_db(supabase: Client, problem: dict):
    try:
        delete_image_from_storage(supabase, SUPABASE_BUCKET_NAME, problem.get('question_image_url'))
        delete_image_from_storage(supabase, SUPABASE_BUCKET_NAME, problem.get('explanation_image_url'))
        supabase.table("problems").delete().eq("id", problem["id"]).execute()
        st.cache_data.clear()
    except Exception as e:
        st.error(f"문제 삭제 오류: {e}")

# --- UI 렌더링 함수 ---
def render_sidebar(user_info, supabase):
    with st.sidebar:
        st.header(f"👋 {user_info['name']}님")
        st.write(f"_{user_info['email']}_")
        st.divider()
        
        if is_admin(supabase, user_info["email"]):
            if st.button("📊 관리자 대시보드", key="sidebar_btn_dashboard", use_container_width=True):
                st.session_state.page = "대시보드"
                st.rerun()

        if st.button("📝 문제 목록", key="sidebar_btn_list", use_container_width=True):
            st.session_state.page = "목록"
            st.rerun()

        if st.button("✍️ 새로운 문제 만들기", key="sidebar_btn_create", use_container_width=True):
            st.session_state.page = "만들기"
            st.rerun()

        if st.button("🚪 로그아웃", key="sidebar_btn_logout", use_container_width=True):
            st.session_state.clear()
            st.rerun()

# (나머지 render_problem_list, render_problem_detail, render_creation_form, render_dashboard, run_app, main 함수는 동일, 단 ADMIN_EMAIL 대신 is_admin 호출로 변경됨)

# --- 앱 실행 로직 ---
def run_app(supabase, user_info):
    """로그인 후 실행되는 메인 애플리케이션 로직"""
    # 1. 데이터 로드
    problem_df = load_data_from_db(supabase, "problems")
    solution_df = load_data_from_db(supabase, "solutions")

    # 2. 사이드바 렌더링 ✅ (여기만 남김)
    render_sidebar(user_info, supabase)

    # 3. 페이지 상태에 따라 다른 UI 렌더링
    page = st.session_state.get("page", "목록")

    if page == "목록":
        render_problem_list(problem_df)
    elif page == "상세":
        problem_id = st.session_state.get("selected_problem_id")
        if problem_id and not problem_df.empty:
            selected_problem = problem_df[problem_df['id'] == problem_id].iloc[0].to_dict()
            render_problem_detail(selected_problem, supabase, user_info)
        else:
            st.warning("문제를 찾을 수 없거나 선택되지 않았습니다. 목록으로 돌아갑니다.")
            st.session_state.page = "목록"
            st.rerun()
    elif page == "만들기":
        render_creation_form(supabase, user_info)
    elif page == "대시보드" and user_info['email'] == ADMIN_EMAIL:
        render_dashboard(problem_df, solution_df)
    else:
        st.session_state.page = "목록"
        st.rerun()


def main():
    st.set_page_config(page_title="2학년 문제 공유 게시판", layout="wide")
    st.title("📝 2학년 문제 공유 게시판")

    # 🔍 OAuth 설정 확인
    st.write("CLIENT_ID:", CLIENT_ID)
    st.write("CLIENT_SECRET 설정됨:", bool(CLIENT_SECRET))

    if not all([CLIENT_ID, CLIENT_SECRET]):
        st.error("OAuth2.0 클라이언트 ID와 시크릿이 secrets.toml 파일에 설정되지 않았습니다.")
        return  # 🚨 st.stop() 대신 return

    oauth2 = OAuth2Component(
        CLIENT_ID, CLIENT_SECRET,
        AUTHORIZE_ENDPOINT, TOKEN_ENDPOINT, TOKEN_ENDPOINT, REVOKE_ENDPOINT
    )

    # 1️⃣ 로그인 안 된 경우
    if 'token' not in st.session_state or st.session_state.token is None:
        st.write("👉 아직 로그인 안 됨")
        result = oauth2.authorize_button(
            name="구글 계정으로 로그인",
            icon="https://www.google.com/favicon.ico",
            redirect_uri="https://study-inside.onrender.com",  # 여기가 redirect_uri mismatch 잘남
            scope="openid email profile",
            key="google_login",
            use_container_width=True,
        )
        st.write("authorize_button 결과:", result)  # 🔍 디버깅
        if result and "token" in result:
            st.session_state.token = result.get("token")
            st.rerun()

    # 2️⃣ 로그인 된 경우
    else:
        st.write("👉 로그인 성공. 세션 token 존재.")
        token_details = st.session_state.get("token", {})
        st.json(token_details)  # 🔍 디버깅 출력

        user_details = {}
        if "id_token" in token_details:
            try:
                decoded = jwt.decode(
                    token_details["id_token"],
                    options={"verify_signature": False}
                )
                user_details = {
                    "name": decoded.get("name"),
                    "email": decoded.get("email"),
                    "picture": decoded.get("picture"),
                }
            except Exception as e:
                st.error(f"ID Token 디코딩 실패: {e}")

        st.write("user_details:", user_details)  # 🔍 디버깅 출력

        if not user_details:
            st.error("사용자 정보를 가져오는 데 실패했습니다. 다시 로그인해주세요.")
            if st.button("로그인 페이지로 돌아가기", key="btn_back_to_login"):
                st.session_state.clear()
                st.rerun()
            return

        # ✅ 로그인 성공 시 UI 실행
        st.session_state.user_info = user_details
        st.success(f"환영합니다, {user_details['name']}님!")
        if user_details.get("picture"):
            st.image(user_details["picture"], width=100)
        st.write("이메일:", user_details["email"])

        run_app(supabase, user_details)
        
if __name__ == "__main__":
    initialize_app_state()
    main()
