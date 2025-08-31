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

def render_problem_list(problem_df):
    """문제 목록을 화면에 렌더링"""
    st.header("📝 문제 목록")
    if problem_df.empty:
        st.warning("아직 등록된 문제가 없습니다. 새 문제를 만들어보세요!")
        return

    # 카테고리 필터링
    categories = ["전체"] + sorted(problem_df["category"].unique().tolist())
    selected_category = st.selectbox("카테고리 선택", categories)

    if selected_category == "전체":
        filtered_df = problem_df
    else:
        filtered_df = problem_df[problem_df["category"] == selected_category]

    if filtered_df.empty:
        st.info(f"'{selected_category}' 카테고리에는 아직 문제가 없습니다.")
        return

    # 문제 목록 표시
    for _, problem in filtered_df.iterrows():
        with st.container(border=True):
            col1, col2 = st.columns([4, 1])
            with col1:
                st.subheader(f"{problem['title']}")
                st.caption(f"카테고리: {problem['category']} | 작성자: {problem.get('creator_name', '익명')}")
            with col2:
                if st.button("문제 풀기", key=f"solve_{problem['id']}", use_container_width=True):
                    st.session_state.selected_problem_id = problem['id']
                    st.session_state.page = "상세"
                    st.rerun()

def render_problem_detail(problem, supabase, user_info):
    """선택된 문제의 상세 정보와 풀이 화면을 렌더링"""
    st.header(problem['title'])
    st.info(f"**카테고리**: {problem['category']} | **작성자**: {problem.get('creator_name', '익명')}")
    st.markdown("---")

    # 문제 내용
    st.subheader("문제")
    if problem.get("question_image_url"):
        st.image(problem["question_image_url"])
    st.write(problem['question'])

    # 보기 (객관식/주관식)
    options = [problem.get(f'option{i}') for i in range(1, 5) if problem.get(f'option{i}')]
    user_answer = None

    if problem.get('question_type') == '객관식' and options:
        user_answer = st.radio("정답을 선택하세요:", options, index=None, key=f"answer_{problem['id']}")
    else:  # 주관식 또는 보기 없는 경우
        user_answer = st.text_input("정답을 입력하세요:", key=f"answer_{problem['id']}")

    if st.button("제출", key=f"submit_{problem['id']}"):
        if user_answer is not None:
            is_correct = str(user_answer).strip() == str(problem['answer']).strip()
            
            if is_correct:
                st.success("정답입니다! 🎉")
                # 풀이 기록 저장
                solution_data = {
                    "problem_id": problem["id"],
                    "user_email": user_info["email"],
                    "user_name": user_info["name"],
                    "solved_at": datetime.now().isoformat()
                }
                save_solution_to_db(supabase, solution_data)
            else:
                st.error("오답입니다. 다시 시도해보세요. 🤔")

            # 해설 표시
            with st.expander("해설 보기"):
                if problem.get("explanation_image_url"):
                    st.image(problem["explanation_image_url"])
                st.write(problem.get('explanation', '해설이 없습니다.'))
        else:
            st.warning("답을 선택하거나 입력해주세요.")

    #문제 수정
    if user_info['email'] == problem.get('creator_email') or is_admin(supabase, user_info["email"]):
        st.divider()
        st.subheader("🔒 문제 관리")
        if st.button("🗑️ 문제 삭제하기", type="secondary"):
            delete_problem(sheets['problems'], drive_service, problem)
            st.success("문제가 삭제되었습니다."); st.session_state.page = "목록"; st.rerun()

    st.markdown("---")
    # 👉 자동 이동 대신, 원하는 경우에만 눌러서 이동
    if st.button("목록으로 돌아가기", key=f"back_{problem['id']}"):
        st.session_state.page = "목록"
        st.rerun()
        
def render_creation_form(supabase, user_info):
    st.header("✍️ 새로운 문제 만들기")
    question_type = st.radio("📋 문제 유형", ('객관식', '주관식'))

    with st.form("creation_form"):
        title = st.text_input("📝 문제 제목")
        category = st.selectbox(
            "📚 분류", 
            ["수학2", "확률과 통계", "독서", "영어", "물리학1", "화학1", "생명과학1", "지구과학1", "사회문화", "윤리와사상", "기타"],
            index=None
        )
        question = st.text_area("❓ 문제 내용")
        question_image = st.file_uploader("🖼️ 문제 이미지 추가 (선택)", type=['png', 'jpg', 'jpeg'])
        explanation = st.text_area("📝 문제 풀이/해설")
        explanation_image = st.file_uploader("🖼️ 해설 이미지 추가 (선택)", type=['png', 'jpg', 'jpeg'])

        options = ["", "", "", ""]
        answer_payload = None

        if question_type == '객관식':
            st.subheader("📝 선택지 입력")
            options = [st.text_input(f"선택지 {i+1}") for i in range(4)]
            answer_payload = st.radio("✅ 정답 선택", [f"선택지 {i+1}" for i in range(4)], index=None)
        else:
            answer_payload = st.text_input("✅ 정답 입력")

        submitted = st.form_submit_button("문제 제출하기", type="primary")

    if submitted:
        # 정답 매핑
        final_answer = ""
        if question_type == '객관식':
            if answer_payload:
                selected_idx = int(answer_payload.split(" ")[1]) - 1
                final_answer = options[selected_idx]
        else:
            final_answer = answer_payload

        if not all([title, category, question, final_answer]):
            st.warning("이미지를 제외한 모든 필수 필드를 채워주세요!")
            return

        with st.spinner('처리 중...'):
            # 파일이 있을 때만 업로드 시도
            q_img_url, err1 = (None, None)
            if question_image:
                q_img_url, err1 = upload_image_to_storage(supabase, SUPABASE_BUCKET_NAME, question_image)
                if err1: st.error(err1); return

            e_img_url, err2 = (None, None)
            if explanation_image:
                e_img_url, err2 = upload_image_to_storage(supabase, SUPABASE_BUCKET_NAME, explanation_image)
                if err2: st.error(err2); return

            new_problem = {
                "title": title,
                "category": category,
                "question": question,
                "option1": options[0], "option2": options[1], "option3": options[2], "option4": options[3],
                "answer": final_answer,
                "creator_name": user_info["name"],
                "creator_email": user_info["email"],
                "explanation": explanation,
                "question_type": question_type,
                "question_image_url": q_img_url,
                "explanation_image_url": e_img_url,
                "created_at": datetime.now().isoformat()
            }
            save_problem_to_db(supabase, new_problem)
            st.success("🎉 문제가 성공적으로 만들어졌습니다!")
            st.balloons()
            st.session_state.page = "목록"
            st.rerun()
            
def render_dashboard(problem_df, solution_df):
    """관리자용 대시보드 렌더링"""
    st.header("📊 관리자 대시보드")
    st.write("이곳에서 문제 및 풀이 통계를 확인할 수 있습니다.")
    
    tab1, tab2 = st.tabs(["문제 관리", "풀이 통계"])

    with tab1:
        st.subheader("등록된 문제 목록")
        st.dataframe(problem_df)

    with tab2:
        st.subheader("사용자 풀이 기록")
        st.dataframe(solution_df)

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
    elif page == "대시보드" and is_admin(supabase, user_info['email']):
        render_dashboard(problem_df, solution_df)
    else:
        st.session_state.page = "목록"
        st.rerun()


def main():
    st.set_page_config(page_title="2학년 문제 공유 게시판", layout="wide")
    st.title("📝 2학년 문제 공유 게시판")

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

        if result and "token" in result:
            st.session_state.token = result.get("token")
            st.rerun()

    # 2️⃣ 로그인 된 경우
    else:
        token_details = st.session_state.get("token", {})


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
