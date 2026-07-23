import streamlit as st
import subprocess
import time
import tempfile
import os

st.set_page_config(page_title="Anypoint RPA", layout="centered")

# 커스텀 스타일 삽입
st.markdown("""
    <style>
    body {
        font-family: 'Segoe UI', sans-serif;
    }

    .stApp {
    }

    /* 파일명 리스트 및 실행 로그 박스 */
    .log-box {
        background-color: #000;
        border: 1px solid #dee2e6;
        border-radius: 10px;
        padding: 1rem;
        margin-top: 1rem;
        max-height: 500px;
        overflow-y: auto;
        font-family: monospace;
        font-size: 0.9rem;
        white-space: pre-wrap;
    }

    /* 버튼 스타일 */
    .stButton>button {
        background-color: #2c7be5;
        color: white;
        border-radius: 8px;
        padding: 0.5rem 1rem;
        font-weight: bold;
    }
    .stButton>button:hover{
        transition:all ease-in-out .3s;
        background-color: #fff;
        color: #2c7be5;
        border:1px solid #2c7be5;
        }
            
    .stAlert{
            margin-top: 25px;
            }
    </style>
""", unsafe_allow_html=True)

st.title("Anypointmedia RPA")
st.markdown("실행할 파일을 업로드하고, 선택한 순서대로 실행할 수 있습니다.")

uploaded_files = st.file_uploader("실행 파일 업로드하세요", type=["py"], accept_multiple_files=True)

if uploaded_files:
    st.subheader("업로드된 파일")
    filenames = [file.name for file in uploaded_files]
    selected_files = st.multiselect("실행할 파일을 선택하세요 (순서 중요)", filenames, default=filenames)

    if st.button("실행 (순차적으로)") and selected_files:
        st.subheader("실행 로그")

        with tempfile.TemporaryDirectory() as temp_dir:
            file_map = {}

            for file in uploaded_files:
                save_path = os.path.join(temp_dir, file.name)
                with open(save_path, "wb") as f:
                    f.write(file.getvalue())
                file_map[file.name] = save_path

            log_file_path = os.path.join(temp_dir, "execution_log.txt")
            log_contents = ""

            start_time = time.time()

            for filename in selected_files:
                filepath = file_map[filename]
                st.write(f"`{filename}` 실행")

                try:
                    result = subprocess.run(["python", filepath], cwd=temp_dir, capture_output=True, text=True)
                    log_contents += f"\n===== {filename} =====\n"
                    log_contents += result.stdout
                    log_contents += result.stderr

                    if result.stderr:
                        st.error(result.stderr)

                except Exception as e:
                    st.error(f"오류 발생: {e}")
                    log_contents += f"오류 발생: {e}\n"

            end_time = time.time()
            execution_time = end_time - start_time
            st.write(f"⏱실행 시간: {execution_time:.2f}초")

            # 로그 출력
            st.markdown(f"<div class='log-box'>{log_contents}</div>", unsafe_allow_html=True)

        st.success("모든 파일 실행 완료")

else:
    st.info("파일을 업로드하세요.")
