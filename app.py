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
ADMIN_EMAIL = "jwj1013kor@gmail.com"
SUPABASE_BUCKET_NAME = "images"

CLIENT_ID = st.secrets.get("oauth_credentials", {}).get("CLIENT_ID") or os.getenv("CLIENT_ID")
CLIENT_SECRET = st.secrets.get("oauth_credentials", {}).get("CLIENT_SECRET") or os.getenv("CLIENT_SECRET")
REDIRECT_URI = st.secrets.get("oauth_credentials", {}).get("REDIRECT_URI", "https://study-inside.onrender.com")
AUTHORIZE_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"
SUPABASE_URL = st.secrets.get("SUPABASE_URL") or os.getenv("SUPABASE_URL")
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY") or os.getenv("SUPABASE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- 상태 관리 ---
def initialize_app_state():
    # 🔑 예전에 쓰던 오래된 키 정리
    old_keys = ["sidebar_btn_list_first", "sidebar_btn_list", "btn_list"]
    for k in old_keys:
        if k in st.session_state:
            del st.session_state[k]

    if 'page' not in st.session_state: 
        st.session_state.page = "목록"
    if 'selected_problem_id' not in st.session_state: 
        st.session_state.selected_problem_id = None
    if 'token' not in st.session_state: 
        st.session_state.token = None
    if 'user_info' not in st.session_state: 
        st.session_state.user_info = None

# --- Supabase 파일 처리 ---
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
        st.warning(f"Storage 파일 삭제 오류: {e}")

# --- Supabase DB 처리 ---
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

# --- UI ---
def render_sidebar(user_info):
    with st.sidebar:
        st.header(f"👋 {user_info['name']}님")
        st.write(f"_{user_info['email']}_")
        st.divider()

        if user_info["email"] == ADMIN_EMAIL:
            if st.button("📊 관리자 대시보드", key="sidebar_btn_dashboard", use_container_width=True):
                st.session_state.page = "대시보드"; st.rerun()

        if st.button("📝 문제 목록", key="sidebar_btn_list", use_container_width=True):
            st.session_state.page = "목록"; st.rerun()

        if st.button("✍️ 새로운 문제 만들기", key="sidebar_btn_create", use_container_width=True):
            st.session_state.page = "만들기"; st.rerun()

        if st.button("🚪 로그아웃", key="sidebar_btn_logout", use_container_width=True):
            st.session_state.clear(); st.rerun()

def render_problem_list(problem_df):
    st.header("🔎 전체 문제 목록")
    search_query = st.text_input("🔎 문제 검색", placeholder="제목 또는 내용으로 검색하세요.")

    categories = ["전체"]
    if 'category' in problem_df.columns:
        categories += sorted(problem_df["category"].unique().tolist())
    selected_category = st.selectbox("📚 분류별로 보기:", categories)

    df = problem_df
    if search_query:
        df = df[df['title'].str.contains(search_query, na=False) | df['question'].str.contains(search_query, na=False)]
    if selected_category != "전체":
        df = df[df["category"] == selected_category]

    st.divider()
    if df.empty:
        st.info("표시할 문제가 없습니다.")
    else:
        for _, row in df.iterrows():
            if st.button(f"[{row['category']}] | {row['title']} - {row['creator_name']}", key=f"view_{row['id']}"):
                st.session_state.selected_problem_id = row['id']
                st.session_state.page = "상세"; st.rerun()

def render_problem_detail(problem, supabase, user_info):
    problem_id = problem['id']
    problem_type = '주관식' if problem.get('question_type') == '주관식' else '객관식'

    st.header(f"{problem['title']}")
    st.caption(f"출제자: {problem['creator_name']} | 분류: {problem['category']} | 유형: {problem_type}")
    st.markdown(f"**문제 내용:**\n\n{problem['question']}")
    if problem.get('question_image_url'):
        st.image(problem['question_image_url'])

    options = [problem.get(f"option{i}") for i in range(1, 5) if problem.get(f"option{i}")]
    user_answer = st.radio("정답:", options, index=None, key=f"answer_radio_{problem_id}") if problem_type == '객관식' else st.text_input("정답 입력")

    if st.button("정답 확인", key=f"check_answer_{problem_id}"):
        is_correct = str(user_answer).strip() == str(problem["answer"]).strip()
        if is_correct:
            st.success("정답입니다! 👍")
            st.session_state[f"show_explanation_{problem_id}"] = True
            solution_data = {"problem_id": problem_id, "user_email": user_info['email'], "user_name": user_info['name']}
            save_solution_to_db(supabase, solution_data)
        else:
            st.error("틀렸습니다. 다시 시도해보세요. 👎")
            st.session_state[f"show_explanation_{problem_id}"] = False

    if st.session_state.get(f"show_explanation_{problem_id}") and problem.get('explanation'):
        st.info(f"**해설:**\n\n{problem['explanation']}")
        if problem.get('explanation_image_url'):
            st.image(problem['explanation_image_url'])

    if user_info['email'] == problem.get('creator_email') or user_info['email'] == ADMIN_EMAIL:
        st.divider(); st.subheader("🔒 문제 관리")
        if st.button("🗑️ 문제 삭제하기", key=f"delete_problem_{problem_id}", type="secondary"):
            delete_problem_from_db(supabase, problem)
            st.success("문제가 삭제되었습니다."); st.session_state.page = "목록"; st.rerun()

def render_creation_form(supabase, user_info):
    st.header("✍️ 새로운 문제 만들기")
    question_type = st.radio("📋 문제 유형", ('객관식', '주관식'), key="question_type_radio")

    with st.form("creation_form"):
        title = st.text_input("📝 문제 제목")
        category = st.selectbox("📚 분류", ["수학2", "확률과 통계", "독서", "영어", "물리학1", "화학1", "생명과학1", "지구과학1", "사회문화", "윤리와사상", "기타"], index=None)
        question = st.text_area("❓ 문제 내용")
        question_image = st.file_uploader("🖼️ 문제 이미지 추가", type=['png', 'jpg', 'jpeg'])
        explanation = st.text_area("📝 문제 풀이/해설")
        explanation_image = st.file_uploader("🖼️ 해설 이미지 추가", type=['png', 'jpg', 'jpeg'])

        options = ["", "", "", ""]
        answer_payload = None

        if question_type == '객관식':
            st.subheader("📝 선택지 입력")
            options = [st.text_input(f"선택지 {i+1}") for i in range(4)]
            answer_payload = st.radio("✅ 정답 선택", [f"선택지 {i+1}" for i in range(4)], index=None, key="answer_radio")
        else:
            answer_payload = st.text_input("✅ 정답 입력")

        submitted = st.form_submit_button("문제 제출하기", type="primary")

    if submitted:
        final_answer = ""
        if question_type == '객관식':
            if answer_payload:
                selected_idx = int(answer_payload.split(" ")[1]) - 1
                if options[selected_idx]: final_answer = options[selected_idx]
        else:
            final_answer = answer_payload

        is_valid = all([title, category, question, final_answer]) and (all(options) if question_type == '객관식' else True)
        if not is_valid:
            st.warning("이미지를 제외한 모든 필수 필드를 채워주세요!")
        else:
            with st.spinner('처리 중...'):
                q_img_url, err1 = upload_image_to_storage(supabase, SUPABASE_BUCKET_NAME, question_image)
                if err1: st.error(err1); return
                e_img_url, err2 = upload_image_to_storage(supabase, SUPABASE_BUCKET_NAME, explanation_image)
                if err2: st.error(err2); return

                new_problem = {
                    "title": title, "category": category, "question": question,
                    "option1": options[0], "option2": options[1], "option3": options[2], "option4": options[3],
                    "answer": final_answer, "creator_name": user_info['name'], "creator_email": user_info['email'],
                    "explanation": explanation, "question_type": question_type,
                    "question_image_url": q_img_url, "explanation_image_url": e_img_url,
                }
                save_problem_to_db(supabase, new_problem)
                st.success("🎉 문제가 성공적으로 만들어졌습니다!"); st.session_state.page = "목록"; st.rerun()

def render_dashboard(problem_df, solution_df):
    st.header("📊 관리자 대시보드")
    st.write("사용자 활동 및 문제 통계를 확인합니다.")

    if problem_df.empty and solution_df.empty: 
        st.info("아직 데이터가 없습니다."); return

    tab1, tab2, tab3 = st.tabs(["사용자별 통계", "문제별 통계", "전체 데이터"])

    with tab1:
        st.subheader("👤 사용자별 문제 생성 수")
        if not problem_df.empty and 'creator_name' in problem_df.columns:
            creation_stats = problem_df['creator_name'].value_counts().reset_index()
            creation_stats.columns = ['출제자', '생성한 문제 수']
            st.dataframe(creation_stats, use_container_width=True)
            st.bar_chart(creation_stats.set_index('출제자'))
        else: st.write("생성된 문제가 없습니다.")

        st.subheader("✅ 사용자별 문제 풀이 수")
        if not solution_df.empty and 'user_name' in solution_df.columns:
            solution_stats = solution_df['user_name'].value_counts().reset_index()
            solution_stats.columns = ['사용자', '해결한 문제 수']
            st.dataframe(solution_stats, use_container_width=True)
            st.bar_chart(solution_stats.set_index('사용자'))
        else: st.write("풀이 기록이 없습니다.")

    with tab2:
        st.subheader("📈 가장 많이 푼 문제 Top 5")
        if not solution_df.empty and not problem_df.empty:
            solved_counts = solution_df['problem_id'].value_counts().reset_index()
            solved_counts.columns = ['id', 'solved_count']
            problem_titles = problem_df[['id', 'title']]
            merged_stats = pd.merge(solved_counts, problem_titles, on='id', how='left')
            merged_stats = merged_stats[['title', 'solved_count']].head(5)
            merged_stats.columns = ['문제 제목', '풀이 횟수']
            st.dataframe(merged_stats, use_container_width=True)
            st.bar_chart(merged_stats.set_index('문제 제목'))
        else: st.write("풀이 기록이 없습니다.")

    with tab3:
        st.subheader("📚 전체 문제 데이터"); st.dataframe(problem_df)
        st.subheader("📝 전체 풀이 기록"); st.dataframe(solution_df)

# --- 실행 로직 ---
def run_app(supabase, user_info):
    problem_df = load_data_from_db(supabase, "problems")
    solution_df = load_data_from_db(supabase, "solutions")
    render_sidebar(user_info)
    page = st.session_state.get("page", "목록")

    if page == "목록":
        render_problem_list(problem_df)
    elif page == "상세":
        problem_id = st.session_state.get("selected_problem_id")
        if problem_id and not problem_df.empty:
            selected_problem = problem_df[problem_df['id'] == problem_id].iloc[0].to_dict()
            render_problem_detail(selected_problem, supabase, user_info)
        else:
            st.warning("문제를 찾을 수 없습니다. 목록으로 돌아갑니다.")
            st.session_state.page = "목록"; st.rerun()
    elif page == "만들기":
        render_creation_form(supabase, user_info)
    elif page == "대시보드" and user_info['email'] == ADMIN_EMAIL:
        render_dashboard(problem_df, solution_df)
    else:
        st.session_state.page = "목록"; st.rerun()

def main():
    st.set_page_config(page_title="2학년 문제 공유 게시판", layout="wide")
    st.title("📝 2학년 문제 공유 게시판")

    if not all([CLIENT_ID, CLIENT_SECRET]):
        st.error("OAuth2.0 클라이언트 ID/Secret이 누락되었습니다."); st.stop()

    oauth2 = OAuth2Component(
        CLIENT_ID, CLIENT_SECRET,
        AUTHORIZE_ENDPOINT, TOKEN_ENDPOINT, TOKEN_ENDPOINT, REVOKE_ENDPOINT
    )

    if 'token' not in st.session_state or st.session_state.token is None:
        result = oauth2.authorize_button(
            name="구글 계정으로 로그인",
            icon="https://www.google.com/favicon.ico",
            redirect_uri=REDIRECT_URI,
            scope="openid email profile",
            key="google_login",
            use_container_width=True,
        )
        if result and "token" in result:
            st.session_state.token = result.get("token"); st.rerun()
    else:
        token_details = st.session_state.get("token", {})
        user_details = {}
        if "id_token" in token_details:
            try:
                decoded = jwt.decode(token_details["id_token"], options={"verify_signature": False})
                user_details = {"name": decoded.get("name"), "email": decoded.get("email"), "picture": decoded.get("picture")}
            except Exception as e:
                st.error(f"ID Token 디코딩 실패: {e}")

        if not user_details:
            st.error("사용자 정보를 가져올 수 없습니다."); st.json(token_details)
            if st.button("로그인 페이지로 돌아가기", key="btn_back_to_login"):
                st.session_state.clear(); st.rerun()
            st.stop()

        st.session_state.user_info = user_details
        st.success(f"환영합니다, {user_details['name']}님!")
        if user_details.get("picture"): st.image(user_details["picture"], width=100)
        st.write("이메일:", user_details["email"])
        run_app(supabase, user_details)

if __name__ == "__main__":
    initialize_app_state()
    main()
