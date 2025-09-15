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

# --- 과목 및 단원 데이터 ---
# 이 사전을 수정하여 과목과 단원을 관리하세요.
CHAPTERS_BY_CATEGORY = {
    "수학2": ["함수의 극한과 연속", "미분", "적분", "기타"],
    "확률과 통계": ["경우의 수", "확률", "통계", "기타"],
    "독서": ["르르쌤", "재경쌤", "기타"],
    "영어": ["교과서 본문", "모의고사", "기타"],
    "물리학1": ["역학과 에너지", "물질과 전자기장", "파동과 정보 통신", "기타"],
    "화학1": ["화학의 첫걸음", "원자의 세계", "화학 결합과 분자의 세계", "기타"],
    "생명과학1": ["사람의 물질대사", "항상성과 몸의 조절", "유전", "생태계", "기타"],
    "지구과학1": ["고체 지구의 변화", "대기와 해양의 변화", "우주의 구성과 변화", "기타"],
    "사회문화": ["기타"],
    "기타": ["일반선택", "진로선택", "기타", "공부 외"]
}

# Google Sheets 헤더 정의
PROBLEM_HEADERS = [
    "id", "title", "category", "chapter", "difficulty", "question", "option1", "option2", "option3", "option4", 
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

            /* Sidebar toggle button styling */
            [data-testid="stSidebarNavToggler"] {
                border: 2px solid #0d6efd; /* 파란색 테두리 */
                background-color: #f0f2f6; /* 밝은 회색 배경 */
                border-radius: 5px;
                transition: background-color 0.2s;
                animation: pulse-border 2.5s infinite; /* 애니메이션 적용 */
            }
            [data-testid="stSidebarNavToggler"]:hover {
                background-color: #e0e2e6; /* 호버 시 약간 어두운 회색 */
            }
            
            /* 버튼 주위에 파란색 그림자가 깜빡이는 애니메이션 */
            @keyframes pulse-border {
                0% { box-shadow: 0 0 0 0 rgba(13, 110, 253, 0.7); }
                70% { box-shadow: 0 0 0 10px rgba(13, 110, 253, 0); }
                100% { box-shadow: 0 0 0 0 rgba(13, 110, 253, 0); }
            }
        </style>
    """, unsafe_allow_html=True)

# --- 상태 관리 함수 ---
def initialize_app_state():
    if 'page' not in st.session_state: 
        st.session_state.page = "목록"
    if 'selected_problem_id' not in st.session_state: 
        st.session_state.selected_problem_id = None
    if 'problem_to_edit' not in st.session_state:
        st.session_state.problem_to_edit = None
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

def update_problem_in_db(supabase: Client, problem_id: int, new_data: dict, old_problem: dict):
    """Supabase의 problems 테이블에서 특정 문제를 업데이트합니다."""
    try:
        # 새 이미지 파일이 있으면 기존 이미지 삭제 후 새 이미지 업로드
        if new_data.get("question_image_url") != old_problem.get("question_image_url"):
            delete_image_from_storage(supabase, SUPABASE_BUCKET_NAME, old_problem.get("question_image_url"))
        
        if new_data.get("explanation_image_url") != old_problem.get("explanation_image_url"):
            delete_image_from_storage(supabase, SUPABASE_BUCKET_NAME, old_problem.get("explanation_image_url"))

        supabase.table("problems").update(new_data).eq("id", problem_id).execute()
        st.cache_data.clear()
    except Exception as e:
        st.error(f"문제 업데이트 오류: {e}")

# --- 한글 초성 정렬 함수 ---
def korean_sort_key(s):
    if not isinstance(s, str):
        return (4, s) # 문자열이 아닌 경우 맨 뒤로

    CHOSUNG_LIST = ['ㄱ', 'ㄲ', 'ㄴ', 'ㄷ', 'ㄸ', 'ㄹ', 'ㅁ', 'ㅂ', 'ㅃ', 'ㅅ', 'ㅆ', 'ㅇ', 'ㅈ', 'ㅉ', 'ㅊ', 'ㅋ', 'ㅌ', 'ㅍ', 'ㅎ']
    
    result = []
    for char in s.lower(): # 영문 대소문자 구분 없이
        if '가' <= char <= '힣':
            char_code = ord(char) - ord('가')
            chosung_index = char_code // (21 * 28)
            result.append((2, CHOSUNG_LIST[chosung_index], char)) # 한글
        elif 'a' <= char <= 'z':
            result.append((1, char)) # 영어
        elif '0' <= char <= '9':
            result.append((0, char)) # 숫자
        else:
            result.append((3, char)) # 기타
    return result

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

    # 제목을 기준으로 초성 정렬 (숫자 > 영어 > 한글)
    problem_df['sort_key'] = problem_df['title'].apply(korean_sort_key)
    problem_df = problem_df.sort_values(by='sort_key').drop(columns=['sort_key']).reset_index(drop=True)

    # --- 필터링 UI ---
    col1, col2 = st.columns(2)
    with col1:
        categories = ["전체"] + sorted(problem_df["category"].unique().tolist())
        selected_category = st.selectbox("카테고리 선택", categories)
    with col2:
        search_query = st.text_input("문제 제목으로 검색", placeholder="검색어를 입력하세요...")

    # --- 데이터 필터링 ---
    # 1. 카테고리 필터링
    if selected_category == "전체":
        filtered_df = problem_df
    else:
        filtered_df = problem_df[problem_df["category"] == selected_category]

    # 2. 검색어 필터링
    if search_query:
        filtered_df = filtered_df[filtered_df['title'].str.contains(search_query, case=False, na=False)]

    if filtered_df.empty:
        st.info("조건에 맞는 문제가 없습니다.")
        return

    # 문제 목록 표시
    st.write(f"총 {len(filtered_df)}개의 문제를 찾았습니다.")
    for _, problem in filtered_df.iterrows():
        with st.container(border=True):
            col1, col2 = st.columns([4, 1])
            with col1:
                chapter_text = problem.get('chapter', '단원 미지정')
                difficulty_text = problem.get('difficulty', '난이도 미지정')
                st.subheader(f"{problem['title']} | {chapter_text}({difficulty_text})")
                st.caption(f"분류: {problem.get('category', '미지정')} | 작성자: {problem.get('creator_name', '익명')}")
            with col2:
                if st.button("문제 풀기", key=f"solve_{problem['id']}", use_container_width=True):
                    st.session_state.selected_problem_id = problem['id']
                    st.session_state.page = "상세"
                    st.rerun()

def render_problem_detail(problem, supabase, user_info):
    """선택된 문제의 상세 정보와 풀이 화면을 렌더링"""
    st.header(problem['title'])
    
    chapter_text = problem.get('chapter', '미지정')
    difficulty_text = problem.get('difficulty', '미지정')
    st.info(f"**분류**: {problem.get('category', '미지정')} > {chapter_text} | **난이도**: {difficulty_text} | **작성자**: {problem.get('creator_name', '익명')}")
    
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

    # 문제 관리 (작성자 및 관리자)
    if user_info['email'] == problem.get('creator_email') or is_admin(supabase, user_info["email"]):
        st.divider()
        st.subheader("🔒 문제 관리")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("✏️ 문제 수정하기", use_container_width=True):
                st.session_state.problem_to_edit = problem
                st.session_state.page = "수정"
                st.rerun()
        with col2:
            if st.button("🗑️ 문제 삭제하기", type="secondary", use_container_width=True):
                delete_problem_from_db(supabase, problem)
                st.success("문제가 삭제되었습니다. 목록으로 돌아갑니다.")
                st.session_state.page = "목록"
                st.rerun()

    st.markdown("---")
    if st.button("목록으로 돌아가기", key=f"back_{problem['id']}"):
        st.session_state.page = "목록"
        st.rerun()

def render_edit_form(supabase: Client, problem: dict):
    """문제 수정을 위한 폼을 렌더링합니다."""
    st.header("✍️ 문제 수정하기")

    # 위젯 키의 유일성을 보장하기 위해 problem id를 사용
    key_prefix = f"edit_{problem['id']}_"

    # st.form을 사용하지 않고 각 위젯을 직접 렌더링
    title = st.text_input("📝 문제 제목", value=problem.get("title", ""), key=f"{key_prefix}title")

    # --- 카테고리, 단원, 난이도 ---
    categories = list(CHAPTERS_BY_CATEGORY.keys())
    try:
        default_category_index = categories.index(problem.get("category"))
    except (ValueError, TypeError):
        default_category_index = None
    category = st.selectbox("📚 분류", categories, index=default_category_index, key=f"{key_prefix}category")

    chapter = None
    if category:
        chapters = CHAPTERS_BY_CATEGORY[category]
        try:
            # 사용자가 카테고리를 변경했을 경우, 이전 단원이 새 카테고리에 없을 수 있으므로 예외 처리
            default_chapter_index = chapters.index(problem.get("chapter")) if problem.get("chapter") in chapters else None
        except (ValueError, TypeError):
            default_chapter_index = None
        chapter = st.selectbox("📖 단원", chapters, index=default_chapter_index, key=f"{key_prefix}chapter")

    difficulties = ["하", "중", "상"]
    try:
        default_difficulty_index = difficulties.index(problem.get("difficulty"))
    except (ValueError, TypeError):
        default_difficulty_index = None
    difficulty = st.selectbox("📊 난이도", difficulties, index=default_difficulty_index, key=f"{key_prefix}difficulty")
    # ---

    question = st.text_area("❓ 문제 내용", value=problem.get("question", ""), key=f"{key_prefix}question")
    
    st.write("🖼️ 현재 문제 이미지")
    if problem.get("question_image_url"):
        st.image(problem["question_image_url"])
    new_question_image = st.file_uploader("🔄️ 새로운 문제 이미지로 교체 (선택)", type=['png', 'jpg', 'jpeg'], key=f"{key_prefix}q_image")

    explanation = st.text_area("📝 문제 풀이/해설", value=problem.get("explanation", ""), key=f"{key_prefix}explanation")

    st.write("🖼️ 현재 해설 이미지")
    if problem.get("explanation_image_url"):
        st.image(problem["explanation_image_url"])
    new_explanation_image = st.file_uploader("🔄️ 새로운 해설 이미지로 교체 (선택)", type=['png', 'jpg', 'jpeg'], key=f"{key_prefix}e_image")

    question_type = problem.get("question_type", "객관식")
    options = [problem.get(f"option{i+1}", "") for i in range(4)]
    
    if question_type == '객관식':
        st.subheader("📝 선택지 수정")
        options = [st.text_input(f"선택지 {i+1}", value=opt, key=f"{key_prefix}opt{i}") for i, opt in enumerate(options)]
        
        try:
            current_answer_index = options.index(problem.get("answer")) if problem.get("answer") in options else None
        except ValueError:
            current_answer_index = None
        answer_payload = st.radio("✅ 정답 선택", [f"선택지 {i+1}" for i in range(4)], index=current_answer_index, key=f"{key_prefix}answer_radio")
    else: # 주관식
        answer_payload = st.text_input("✅ 정답 입력", value=problem.get("answer", ""), key=f"{key_prefix}answer_text")

    if st.button("문제 수정 완료", type="primary", key=f"{key_prefix}submit"):
        final_answer = ""
        if question_type == '객관식':
            if answer_payload:
                selected_idx = int(answer_payload.split(" ")[1]) - 1
                final_answer = options[selected_idx]
        else:
            final_answer = answer_payload

        if not all([title, category, chapter, difficulty, question, final_answer]):
            st.warning("제목, 분류, 단원, 난이도, 문제 내용, 정답은 필수 항목입니다.")
            return

        with st.spinner('업데이트 중...'):
            updated_data = {
                "title": title, "category": category, "chapter": chapter, "difficulty": difficulty,
                "question": question, "option1": options[0], "option2": options[1], "option3": options[2], "option4": options[3],
                "answer": final_answer, "explanation": explanation,
            }

            q_img_url = problem.get("question_image_url")
            if new_question_image:
                q_img_url, err1 = upload_image_to_storage(supabase, SUPABASE_BUCKET_NAME, new_question_image)
                if err1: st.error(err1); return
            updated_data["question_image_url"] = q_img_url

            e_img_url = problem.get("explanation_image_url")
            if new_explanation_image:
                e_img_url, err2 = upload_image_to_storage(supabase, SUPABASE_BUCKET_NAME, new_explanation_image)
                if err2: st.error(err2); return
            updated_data["explanation_image_url"] = e_img_url
            
            update_problem_in_db(supabase, problem["id"], updated_data, problem)
            st.success("🎉 문제가 성공적으로 수정되었습니다!")
            st.session_state.page = "상세"
            st.rerun()

def render_creation_form(supabase, user_info):
    st.header("✍️ 새로운 문제 만들기")
    question_type = st.radio("📋 문제 유형", ('객관식', '주관식'), key="create_q_type")

    title = st.text_input("📝 문제 제목", key="create_title")
    
    categories = list(CHAPTERS_BY_CATEGORY.keys())
    category = st.selectbox("📚 분류", categories, index=None, placeholder="과목을 선택하세요.", key="create_category")
    
    chapter = None
    if category:
        chapters = CHAPTERS_BY_CATEGORY[category]
        chapter = st.selectbox("📖 단원", chapters, index=None, placeholder="단원을 선택하세요.", key="create_chapter")

    difficulty = st.selectbox("📊 난이도", ["하", "중", "상"], index=None, placeholder="난이도를 선택하세요.", key="create_difficulty")

    question = st.text_area("❓ 문제 내용", key="create_question")
    question_image = st.file_uploader("🖼️ 문제 이미지 추가 (선택)", type=['png', 'jpg', 'jpeg'], key="create_q_image")
    explanation = st.text_area("📝 문제 풀이/해설", key="create_explanation")
    explanation_image = st.file_uploader("🖼️ 해설 이미지 추가 (선택)", type=['png', 'jpg', 'jpeg'], key="create_e_image")

    options = ["", "", "", ""]
    answer_payload = None

    if question_type == '객관식':
        st.subheader("📝 선택지 입력")
        options = [st.text_input(f"선택지 {i+1}", key=f"create_opt{i}") for i in range(4)]
        answer_payload = st.radio("✅ 정답 선택", [f"선택지 {i+1}" for i in range(4)], index=None, key="create_answer_radio")
    else:
        answer_payload = st.text_input("✅ 정답 입력", key="create_answer_text")

    if st.button("문제 제출하기", type="primary", key="create_submit"):
        final_answer = ""
        if question_type == '객관식':
            if answer_payload:
                selected_idx = int(answer_payload.split(" ")[1]) - 1
                final_answer = options[selected_idx]
        else:
            final_answer = answer_payload

        if not all([title, category, chapter, difficulty, question, final_answer]):
            st.warning("제목, 분류, 단원, 난이도, 문제 내용, 정답은 필수 항목입니다.")
            return

        with st.spinner('처리 중...'):
            q_img_url, err1 = (None, None)
            if question_image:
                q_img_url, err1 = upload_image_to_storage(supabase, SUPABASE_BUCKET_NAME, question_image)
                if err1: st.error(err1); return

            e_img_url, err2 = (None, None)
            if explanation_image:
                e_img_url, err2 = upload_image_to_storage(supabase, SUPABASE_BUCKET_NAME, explanation_image)
                if err2: st.error(err2); return

            new_problem = {
                "title": title, "category": category, "chapter": chapter, "difficulty": difficulty,
                "question": question, "option1": options[0], "option2": options[1], "option3": options[2], "option4": options[3],
                "answer": final_answer, "creator_name": user_info["name"], "creator_email": user_info["email"],
                "explanation": explanation, "question_type": question_type, "question_image_url": q_img_url,
                "explanation_image_url": e_img_url, "created_at": datetime.now().isoformat()
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

    # 사용자별 통계 데이터 가공
    user_stats = pd.DataFrame()
    if not problem_df.empty:
        creation_counts = problem_df.groupby(['creator_name', 'creator_email']).size().reset_index(name='문제 생성 수')
        creation_counts = creation_counts.rename(columns={'creator_name': '이름', 'creator_email': '이메일'})
    else:
        creation_counts = pd.DataFrame(columns=['이름', '이메일', '문제 생성 수'])

    if not solution_df.empty:
        solution_counts = solution_df.groupby(['user_name', 'user_email']).size().reset_index(name='문제 풀이 수')
        solution_counts = solution_counts.rename(columns={'user_name': '이름', 'user_email': '이메일'})
    else:
        solution_counts = pd.DataFrame(columns=['이름', '이메일', '문제 풀이 수'])

    if not creation_counts.empty or not solution_counts.empty:
        user_stats = pd.merge(
            creation_counts,
            solution_counts,
            on=['이름', '이메일'],
            how='outer'
        ).fillna(0)
        user_stats['문제 생성 수'] = user_stats['문제 생성 수'].astype(int)
        user_stats['문제 풀이 수'] = user_stats['문제 풀이 수'].astype(int)

    tab1, tab2, tab3 = st.tabs(["사용자별 통계", "문제 통계", "풀이 통계"])

    with tab1:
        st.subheader("사용자별 활동 요약")
        if not user_stats.empty:
            st.dataframe(user_stats.sort_values(by=['문제 생성 수', '문제 풀이 수'], ascending=False).reset_index(drop=True))
        else:
            st.warning("활동 기록이 없습니다.")

    with tab2:
        st.subheader("등록된 문제 목록")
        if not problem_df.empty:
            problem_display_df = problem_df[[
                'title', 'category', 'chapter', 'difficulty', 'creator_name', 'created_at'
            ]].rename(columns={
                'title': '제목',
                'category': '과목',
                'chapter': '단원',
                'difficulty': '난이도',
                'creator_name': '작성자',
                'created_at': '생성일시'
            })
            st.dataframe(problem_display_df)
        else:
            st.warning("등록된 문제가 없습니다.")

    with tab3:
        st.subheader("사용자 풀이 기록")
        if not solution_df.empty:
            solution_display_df = solution_df.copy()
            if not problem_df.empty:
                problem_titles = problem_df[['id', 'title']]
                solution_display_df = pd.merge(
                    solution_display_df,
                    problem_titles,
                    left_on='problem_id',
                    right_on='id',
                    how='left'
                )
                solution_display_df['title'] = solution_display_df['title'].fillna('삭제된 문제')
            else:
                solution_display_df['title'] = '알 수 없음'

            solution_display_df = solution_display_df[['user_name', 'title', 'solved_at']].rename(columns={
                'user_name': '사용자',
                'title': '문제 제목',
                'solved_at': '풀이 일시'
            })
            st.dataframe(solution_display_df.sort_values(by='풀이 일시', ascending=False).reset_index(drop=True))
        else:
            st.warning("풀이 기록이 없습니다.")

# --- 앱 실행 로직 ---
def run_app(supabase, user_info):
    """로그인 후 실행되는 메인 애플리케이션 로직"""
    # 1. 데이터 로드
    problem_df = load_data_from_db(supabase, "problems")
    solution_df = load_data_from_db(supabase, "solutions")

    # 2. 사이드바 렌더링
    render_sidebar(user_info, supabase)

    # 3. 페이지 상태에 따라 다른 UI 렌더링
    page = st.session_state.get("page", "목록")

    if page == "목록":
        render_problem_list(problem_df)
    elif page == "상세":
        problem_id = st.session_state.get("selected_problem_id")
        if problem_id and not problem_df.empty:
            # ID가 문자열(UUID)이므로 문자열로 직접 비교합니다.
            selected_problem_series = problem_df[problem_df['id'] == problem_id]
            if not selected_problem_series.empty:
                selected_problem = selected_problem_series.iloc[0].to_dict()
                render_problem_detail(selected_problem, supabase, user_info)
            else:
                st.warning("문제를 찾을 수 없습니다. 목록으로 돌아갑니다.")
                st.session_state.page = "목록"
                st.rerun()
        else:
            st.warning("문제를 찾을 수 없거나 선택되지 않았습니다. 목록으로 돌아갑니다.")
            st.session_state.page = "목록"
            st.rerun()
    elif page == "만들기":
        render_creation_form(supabase, user_info)
    elif page == "수정":
        problem_to_edit = st.session_state.get("problem_to_edit")
        if problem_to_edit:
            render_edit_form(supabase, problem_to_edit)
        else:
            st.warning("수정할 문제가 선택되지 않았습니다. 목록으로 돌아갑니다.")
            st.session_state.page = "목록"
            st.rerun()
    elif page == "대시보드" and is_admin(supabase, user_info['email']):
        render_dashboard(problem_df, solution_df)
    else:
        st.session_state.page = "목록"
        st.rerun()


def main():
    st.set_page_config(page_title="study-inside", layout="wide")
    st.title("📝 스터디인사이드")
    apply_custom_css()

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
