import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd
import uuid
import os
import json
import streamlit.components.v1 as components
import base64
from io import BytesIO

# --- 상수 및 기본 설정 ---
HEADERS = ["id", "title", "category", "question", "option1", "option2", "option3", "option4", "answer", "creator", "password", "explanation", "question_image", "explanation_image"]

# --- 유틸리티 함수 ---
def image_to_base64(image):
    """Streamlit UploadedFile 객체를 Base64 문자열로 변환"""
    buffered = BytesIO()
    image.save(buffered, format=image.format)
    return base64.b64encode(buffered.getvalue()).decode()

def base64_to_image(b64_string):
    """Base64 문자열을 이미지로 디코딩"""
    return base64.b64decode(b64_string)

# --- CSS 스타일 ---
def apply_custom_css():
    st.markdown(r"""
        <style>
            /* 메인 타이틀 */
            .st-emotion-cache-10trblm {
                background-color: #0d6efd;
                color: white;
                padding: 1rem;
                border-radius: 0.5rem;
                text-align: center;
            }
            h1 {
                color: white;
                font-size: 2.2rem; /* 기본 글씨 크기 조정 */
            }
            /* 페이지 부제목 */
            h2 {
                border-bottom: 2px solid #0d6efd;
                padding-bottom: 0.5rem;
                color: #0d6efd;
            }
            
            /* 모바일 화면 대응 */
            @media (max-width: 768px) {
                h1 {
                    font-size: 1.8rem; /* 모바일에서 글씨 크기 더 줄이기 */
                }
            }
        </style>
    """, unsafe_allow_html=True)

# --- 상태 관리 함수 ---
def initialize_app_state():
    """앱 세션 상태 초기화"""
    if 'page' not in st.session_state:
        st.session_state.page = "목록"
    if 'selected_problem_id' not in st.session_state:
        st.session_state.selected_problem_id = None
    if 'unlocked_problem_id' not in st.session_state:
        st.session_state.unlocked_problem_id = None
    
    keys_to_init = ['creator', 'title', 'question', 'opt1', 'opt2', 'opt3', 'opt4', 'password', 'category', 'answer', 'explanation']
    for key in keys_to_init:
        if key not in st.session_state:
            st.session_state[key] = "" if key not in ['category', 'answer'] else None

# --- 구글 시트 및 데이터 처리 함수 ---
@st.cache_resource
def connect_to_sheet():
    """다양한 환경(Cloud, Render, Local)에 맞춰 구글 시트에 연결합니다."""
    # 1. Render 또는 다른 환경 변수 기반 플랫폼
    if "GCP_CREDS_JSON" in os.environ:
        creds_json_str = os.environ["GCP_CREDS_JSON"]
        creds_json = json.loads(creds_json_str)
        creds = Credentials.from_service_account_info(creds_json, scopes=["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
        return gspread.authorize(creds)
    
    # 2. Streamlit Cloud Secrets
    if "gcp_service_account" in st.secrets:
        creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
        return gspread.authorize(creds)

    # 3. 로컬 credentials.json 파일
    script_dir = os.path.dirname(os.path.abspath(__file__))
    credentials_path = os.path.join(script_dir, "credentials.json")
    if os.path.exists(credentials_path):
        creds = Credentials.from_service_account_file(credentials_path, scopes=["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
        return gspread.authorize(creds)
    
    # 모든 방법 실패 시
    st.error("🚨 구글 시트 연결 정보를 찾을 수 없습니다. 환경에 맞는 설정이 필요합니다.")
    st.stop()


@st.cache_resource
def get_sheet(_client, sheet_name="문제 목록"):
    """워크시트 객체를 가져오고 리소스로 캐시합니다."""
    try:
        spreadsheet = _client.open("MyQuizApp")
        worksheet = spreadsheet.worksheet(sheet_name)
        # 헤더가 존재하는지 확인하고, 없으면 추가합니다.
        current_headers = worksheet.row_values(1)
        if not all(header in current_headers for header in HEADERS):
             worksheet.update('A1', [HEADERS])
        return worksheet
    except gspread.WorksheetNotFound:
        spreadsheet = _client.open("MyQuizApp")
        worksheet = spreadsheet.add_worksheet(title=sheet_name, rows="100", cols="20")
        worksheet.append_row(HEADERS)
        return worksheet

@st.cache_data(ttl=600)
def load_data(_worksheet):
    """Google Sheet에서 데이터를 로드하고 10분 동안 캐시합니다."""
    records = _worksheet.get_all_records()
    if not records:
        return pd.DataFrame(columns=HEADERS)
    
    df = pd.DataFrame(records)
    # 모든 헤더가 있는지 확인
    for col in HEADERS:
        if col not in df.columns:
            df[col] = ""
    return df

def save_problem(worksheet, data):
    """새로운 문제를 시트에 저장하고 캐시를 지웁니다."""
    worksheet.append_row([data.get(h, "") for h in HEADERS])
    load_data.clear()

def delete_problem(worksheet, problem_id):
    """시트에서 문제를 삭제하고 캐시를 지웁니다."""
    cell = worksheet.find(problem_id)
    if cell:
        worksheet.delete_rows(cell.row)
        load_data.clear()

def update_problem(worksheet, problem_id, data):
    """시트에서 문제를 업데이트하고 캐시를 지웁니다."""
    cell = worksheet.find(problem_id)
    if cell:
        worksheet.update(f'A{cell.row}', [[data.get(h, "") for h in HEADERS]])
        load_data.clear()

# --- UI 렌더링 함수 ---
def render_problem_list(problem_df):
    st.header("🔎 전체 문제 목록")
    if st.button("✍️ 새로운 문제 만들기"):
        st.session_state.page = "만들기"; st.rerun()

    search_query = st.text_input("🔎 문제 검색", placeholder="제목 또는 내용으로 검색하세요.")
    categories = ["전체"] + sorted(problem_df["category"].unique().tolist())
    selected_category = st.selectbox("📚 분류별로 보기:", categories)

    df = problem_df
    if search_query: df = df[df['title'].str.contains(search_query, na=False) | df['question'].str.contains(search_query, na=False)]
    if selected_category != "전체": df = df[df["category"] == selected_category]

    st.divider()
    if df.empty: st.info("표시할 문제가 없습니다.")
    else:
        for index, row in df.iterrows():
            if st.button(f"[{row['category']}] | {row['title']} - {row['creator']}", key=f"view_{row['id']}", use_container_width=True):
                st.session_state.selected_problem_id = row['id']
                st.session_state.page = "상세"
                st.rerun()

def render_problem_detail(problem, worksheet):
    problem_id = problem['id']
    if f"show_explanation_{problem_id}" not in st.session_state:
        st.session_state[f"show_explanation_{problem_id}"] = False

    if st.button("⬅️ 목록으로 돌아가기"):
        st.session_state.page = "목록"; st.rerun()

    st.header(f"{problem['title']}")
    st.caption(f"출제자: {problem['creator']} | 분류: {problem['category']}")
    st.markdown(f"**문제 내용:**\n\n{problem['question']}")
    if problem.get('question_image'):
        try:
            st.image(base64_to_image(problem['question_image']))
        except Exception as e:
            st.warning(f"문제 이미지를 불러오는 데 실패했습니다: {e}")

    options = [problem.get(f"option{i}") for i in range(1, 5) if problem.get(f"option{i}")]
    if options:
        user_answer = st.radio("정답을 선택하세요:", options, index=None)
        if st.button("정답 확인"):
            if user_answer == problem["answer"]:
                st.success("정답입니다! 👍")
                st.session_state[f"show_explanation_{problem_id}"] = True
            else:
                st.error("틀렸습니다. 다시 시도해보세요. 👎")
                st.session_state[f"show_explanation_{problem_id}"] = False
    
    if st.session_state[f"show_explanation_{problem_id}"] and problem.get('explanation'):
        st.info(f"**해설:**\n\n{problem['explanation']}")
        if problem.get('explanation_image'):
            try:
                st.image(base64_to_image(problem['explanation_image']))
            except Exception as e:
                st.warning(f"해설 이미지를 불러오는 데 실패했습니다: {e}")

    st.divider()
    st.subheader("🔒 문제 관리")
    
    if st.session_state.get('unlocked_problem_id') == problem_id:
        with st.expander("✏️ 문제 수정하기", expanded=True):
            edited_title = st.text_input("제목 수정", value=problem['title'])
            edited_question = st.text_area("내용 수정", value=problem['question'])
            
            if problem.get('question_image'):
                st.write("현재 문제 이미지:")
                st.image(base64_to_image(problem['question_image']))
            new_question_image = st.file_uploader("새 문제 이미지 업로드 (기존 이미지 대체)", type=['png', 'jpg', 'jpeg'])
            
            edited_explanation = st.text_area("해설 수정", value=str(problem.get('explanation', '')))
            
            if problem.get('explanation_image'):
                st.write("현재 해설 이미지:")
                st.image(base64_to_image(problem['explanation_image']))
            new_explanation_image = st.file_uploader("새 해설 이미지 업로드 (기존 이미지 대체)", type=['png', 'jpg', 'jpeg'])

            edited_options = [st.text_input(f"선택지 {i+1} 수정", value=problem.get(f'option{i+1}', '')) for i in range(4)]
            valid_edited_options = [opt for opt in edited_options if opt]
            
            current_answer_index = 0
            if problem['answer'] in valid_edited_options:
                current_answer_index = valid_edited_options.index(problem['answer'])
            
            edited_answer = st.selectbox("정답 수정", valid_edited_options, index=current_answer_index)

            if st.button("변경사항 저장", type="primary"):
                updated_data = problem.copy()
                updated_data.update({
                    "title": edited_title, "question": edited_question, "explanation": edited_explanation,
                    "answer": edited_answer, "option1": edited_options[0], "option2": edited_options[1], 
                    "option3": edited_options[2], "option4": edited_options[3]
                })
                if new_question_image:
                    from PIL import Image
                    img = Image.open(new_question_image)
                    updated_data['question_image'] = image_to_base64(img)
                if new_explanation_image:
                    from PIL import Image
                    img = Image.open(new_explanation_image)
                    updated_data['explanation_image'] = image_to_base64(img)

                update_problem(worksheet, problem_id, updated_data)
                st.success("문제가 업데이트되었습니다."); st.rerun()
        
        st.divider()

        # --- 삭제 확인 로직 ---
        if f'confirm_delete_{problem_id}' not in st.session_state:
            st.session_state[f'confirm_delete_{problem_id}'] = False

        if st.session_state[f'confirm_delete_{problem_id}']:
            st.error("정말로 이 문제를 삭제하시겠습니까? 이 작업은 되돌릴 수 없습니다.")
            col1, col2, _ = st.columns([1.5, 1, 2])
            with col1:
                if st.button("✅ 예, 삭제합니다"):
                    delete_problem(worksheet, problem_id)
                    st.session_state[f'confirm_delete_{problem_id}'] = False
                    st.success("문제가 삭제되었습니다.")
                    st.session_state.page = "목록"
                    st.rerun()
            with col2:
                if st.button("❌ 아니요, 취소합니다"):
                    st.session_state[f'confirm_delete_{problem_id}'] = False
                    st.rerun()
        else:
            if st.button("🗑️ 문제 삭제하기"):
                st.session_state[f'confirm_delete_{problem_id}'] = True
                st.rerun()
    else:
        password_input = st.text_input("문제 관리를 위해 비밀번호를 입력하세요.", type="password")
        if st.button("인증하기"):
            ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")
            if not ADMIN_PASSWORD:
                try:
                    ADMIN_PASSWORD = st.secrets.get("general", {}).get("admin_password")
                except (AttributeError, FileNotFoundError):
                    ADMIN_PASSWORD = None

            if password_input == str(problem.get('password', '')) or (ADMIN_PASSWORD and password_input == ADMIN_PASSWORD):
                st.session_state.unlocked_problem_id = problem_id; st.success("인증되었습니다."); st.rerun()
            else:
                st.error("비밀번호가 틀렸습니다.")

def render_creation_form(worksheet):
    st.header("✍️ 새로운 문제 만들기")
    if st.button("⬅️ 목록으로 돌아가기"):
        st.session_state.page = "목록"; st.rerun()

    title = st.text_input("📝 문제 제목", key="title")
    creator = st.text_input("👤 출제자 이름", key="creator")
    category = st.selectbox("📚 분류", ["수학2", "확률과 통계", "독서", "영어", "물리학1", "화학1", "생명과학1", "지구과학1", "사회문화", "윤리와사상", "기타"], index=None, key="category")
    password = st.text_input("🔒 비밀번호 설정", type="password", key="password")
    
    question = st.text_area("❓ 문제 내용", key="question")
    question_image = st.file_uploader("🖼️ 문제 이미지 추가", type=['png', 'jpg', 'jpeg'])
    
    explanation = st.text_area("📝 문제 풀이/해설", key="explanation")
    explanation_image = st.file_uploader("🖼️ 해설 이미지 추가", type=['png', 'jpg', 'jpeg'])

    options = [st.text_input(f"선택지 {i+1}", key=f"opt{i+1}") for i in range(4)]
    answer = st.selectbox("✅ 정답 선택", [opt for opt in options if opt], index=None, key="answer")

    if st.button("문제 제출하기"):
        if not all([title, creator, category, password, question, answer, explanation]) or not all(options):
            st.warning("모든 필드를 채워주세요! (해설 포함)")
        else:
            new_problem = {
                "id": str(uuid.uuid4()), "title": title, "category": category, "question": question,
                "option1": options[0], "option2": options[1], "option3": options[2], "option4": options[3],
                "answer": answer, "creator": creator, "password": password, "explanation": explanation,
                "question_image": "", "explanation_image": ""
            }
            if question_image:
                from PIL import Image
                img = Image.open(question_image)
                new_problem['question_image'] = image_to_base64(img)
            if explanation_image:
                from PIL import Image
                img = Image.open(explanation_image)
                new_problem['explanation_image'] = image_to_base64(img)

            save_problem(worksheet, new_problem)
            st.success("🎉 문제가 성공적으로 만들어졌습니다!"); st.session_state.page = "목록"; st.rerun()

# --- 메인 앱 로직 ---
st.set_page_config(page_title="2학년 문제 공유 게시판", layout="wide")
apply_custom_css()
st.title("📝 2학년 문제 공유 게시판")

initialize_app_state()
client = connect_to_sheet()
worksheet = get_sheet(client)
problem_df = load_data(worksheet)

if st.session_state.page == "목록":
    render_problem_list(problem_df)
elif st.session_state.page == "상세":
    problem_df_filtered = problem_df[problem_df['id'] == st.session_state.selected_problem_id]
    if not problem_df_filtered.empty:
        problem = problem_df_filtered.iloc[0].to_dict()
        render_problem_detail(problem, worksheet)
    else:
        st.error("선택된 문제를 찾을 수 없습니다. 목록으로 돌아갑니다.")
        st.session_state.page = "목록"
        st.rerun()
elif st.session_state.page == "만들기":
    render_creation_form(worksheet)

# --- Streamlit UI 요소 숨기기 (최종 JavaScript 방식) ---
hide_streamlit_elems = """
<script>
    const hideElements = () => {
        // 대상 요소를 찾기 위한 모든 알려진 선택자 목록
        const selectors = [
            'div[data-testid="stToolbar"]',
            'div[data-testid="stDecoration"]',
            '#MainMenu',
            'header',
            'footer',
            'a[href*="streamlit.io"]' // Streamlit 링크를 포함하는 모든 a 태그
        ];

        let elementsFound = false;
        const doc = window.parent.document;

        selectors.forEach(selector => {
            const elements = doc.querySelectorAll(selector);
            elements.forEach(el => {
                // a 태그의 경우, 부모 div를 숨겨서 전체 UI를 제거
                let targetElement = (el.tagName === 'A') ? el.closest('div') : el;
                if (targetElement && targetElement.style.display !== 'none') {
                    targetElement.style.display = 'none';
                    elementsFound = true;
                }
            });
        });
        return elementsFound;
    };

    // 100ms 간격으로 주기적으로 실행하여 UI 요소를 계속 확인하고 숨김
    const intervalId = setInterval(() => {
        hideElements();
    }, 100);
</script>
"""
components.html(hide_streamlit_elems, height=0)
