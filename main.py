import streamlit as st
import pandas as pd
import io

# --- 1. 유틸리티 함수 ---
def get_leak_time(detect_type, control_type):
    """API 581 기반 누출시간 산정"""
    try:
        d = str(detect_type).strip().upper()
        c = str(control_type).strip().upper()
        if d == "A" and c == "A": return 180
        if d == "B" and c == "B": return 600
    except:
        pass
    return 2400 # 기본값

def find_column(columns, keywords):
    """스마트 컬럼 찾기"""
    normalized_cols = {c: str(c).replace('\n', '').replace(' ', '').replace('•', '') for c in columns}
    for col_name, norm_name in normalized_cols.items():
        if all(k in norm_name for k in keywords):
            return col_name
    return None

def determine_limit_val(state_val, hazard_val, defaults):
    """
    단일 물질상태(state_val)와 유해성(hazard_val)을 받아 규정수량을 반환
    """
    s_val = str(state_val).strip()
    h_val = str(hazard_val).strip()
    
    # 1. 기체 (Gas)
    if '기' in s_val or '가스' in s_val:
        # 독성 여부 체크
        is_toxic = any(x in h_val for x in ['구분1', '구분2', '독성'])
        return defaults['toxic_gas'] if is_toxic else defaults['gas']
        
    # 2. 고체 (Solid)
    if '고' in s_val or '고상' in s_val or 'Solid' in s_val:
        return defaults['solid']
        
    # 3. 액체 (Liquid) - 기본값
    return defaults['liquid']

# --- 2. 메인 UI ---
st.set_page_config(page_title="CS Safety - 시나리오 툴 v12", layout="wide")
st.title("🛡️ 사고시나리오 대상설비 자동 선정 (v12.0 Final)")
st.markdown("##### [기능] '액상/기상' 혼합 시 자동으로 줄을 나누어 각각 판단합니다.")

# 사이드바
with st.sidebar:
    st.header("⚙️ 기준 수량 설정")
    defaults = {
        'toxic_gas': st.number_input("독성 가스", value=5),
        'gas': st.number_input("일반 가스", value=100),
        'liquid': st.number_input("액체", value=400),
        'solid': st.number_input("고체", value=2000)
    }
    st.divider()
    default_detect = st.selectbox("기본 검출 등급", ["C", "B", "A"], index=0)
    default_control = st.selectbox("기본 차단 등급", ["C", "B", "A"], index=0)

uploaded_file = st.file_uploader("[별지 9] 장치설비 목록 엑셀 업로드", type=['csv', 'xlsx'])

if uploaded_file:
    # 1. 파일 읽기
    try:
        if uploaded_file.name.endswith('.csv'):
            df_raw = pd.read_csv(uploaded_file, encoding='cp949')
        else:
            df_raw = pd.read_excel(uploaded_file)
    except:
        try:
            df_raw = pd.read_csv(uploaded_file, encoding='utf-8')
        except Exception as e:
            st.error(f"파일 오류: {e}")
            st.stop()
            
    df = df_raw.copy()

    # 2. 컬럼 매핑
    cols = df.columns
    col_map = {
        "설비명": find_column(cols, ["설비명"]),
        "공정": find_column(cols, ["공정"]) or find_column(cols, ["비고"]),
        "구분기호": find_column(cols, ["구분", "기호"]),
        "취급물질": find_column(cols, ["취급물질"]),
        "CAS": find_column(cols, ["CAS"]),
        "함량": find_column(cols, ["함량"]),
        "물질상태": find_column(cols, ["물질상태"]),
        "압력": find_column(cols, ["압력"]),
        "온도": find_column(cols, ["온도"]),
        "설계용량": find_column(cols, ["설계용량"]),
        "비중": find_column(cols, ["비중"]),
        "저장량_ton": find_column(cols, ["저장량", "ton"]),
        "유해성분류": find_column(cols, ["유해성"]), 
        "연결구": find_column(cols, ["연결구"]) or find_column(cols, ["누출공"]),
        "검출": find_column(cols, ["검출"]),
        "차단": find_column(cols, ["차단"]),
        "비고": find_column(cols, ["비고"])
    }

    if not col_map["설비명"]:
        st.error("❌ '설비명' 컬럼을 찾을 수 없습니다.")
        st.stop()

    # 3. 데이터 계산 (저장량)
    if col_map["저장량_ton"]:
        df['저장량_cal'] = pd.to_numeric(df[col_map["저장량_ton"]], errors='coerce').fillna(0) * 1000
    elif col_map["설계용량"]:
        sg = pd.to_numeric(df[col_map["비중"]], errors='coerce').fillna(1.0) if col_map["비중"] else 1.0
        vol = pd.to_numeric(df[col_map["설계용량"]], errors='coerce').fillna(0)
        df['저장량_cal'] = vol * sg * 1000
    else:
        df['저장량_cal'] = 0

    # 4. 리스트 생성 (분리 로직 적용)
    edit_list = []
    output_idx = 1
    
    for i, row in df.iterrows():
        # 원본 데이터 추출
        state_raw = str(row.get(col_map["물질상태"], '')).strip()
        hazard_raw = str(row.get(col_map["유해성분류"], '')).strip() if col_map["유해성분류"] else ""
        storage = row.get('저장량_cal', 0)
        
        # --- 분리 로직 (Split Logic) ---
        target_states = []
        # '액'과 '기'가 모두 포함된 경우 -> 분리
        if ('액' in state_raw and '기' in state_raw):
            target_states = ["기상", "액상"]
        else:
            # 하나만 있는 경우 그대로 사용
            target_states = [state_raw]
            
        # 각 상태별로 행 생성
        for idx, current_state in enumerate(target_states):
            # 규정수량 산정
            reg_amt = determine_limit_val(current_state, hazard_raw, defaults)
            is_target = "대상" if storage >= reg_amt else "비대상"
            
            # 누출공 및 기타 정보
            conn_raw = row.get(col_map["연결구"], 80) if col_map["연결구"] else 80
            try: conn_size = float(conn_raw)
            except: conn_size = 80.0
            
            det_val = str(row.get(col_map["검출"], default_detect)).strip() if col_map["검출"] else default_detect
            con_val = str(row.get(col_map["차단"], default_control)).strip() if col_map["차단"] else default_control
            
            # 원본 비고에 분리 여부 표시
            note = str(row.get(col_map["비고"], '')).strip()
            if len(target_states) > 1:
                note += f" ({current_state} 기준 판정)"

            edit_list.append({
                "번호": output_idx, # 순번 증가
                "공정": row.get(col_map["공정"], ''),
                "구분기호": row.get(col_map["구분기호"], ''),
                "장치•설비명": row.get(col_map["설비명"], ''),
                "취급물질": row.get(col_map["취급물질"], ''),
                "Cas No.": row.get(col_map["CAS"], ''),
                "함량(%)": row.get(col_map["함량"], ''),
                "물질상태": current_state, # 분리된 상태값 입력
                "운전압력": row.get(col_map["압력"], ''),
                "운전온도": row.get(col_map["온도"], ''),
                "설계용량": row.get(col_map["설계용량"], ''),
                "비중": row.get(col_map["비중"], ''),
                "저장량(ton)": row.get(col_map["저장량_ton"], 0),
                "저장량(kg)": storage,
                "규정수량": reg_amt,
                "대상여부": is_target,
                "설비연결누출공": conn_size,
                "대안누출공": conn_size,
                "검출시스템": det_val,
                "차단시스템": con_val,
                "비고": note
            })
            output_idx += 1 # 다음 번호
    
    edit_df = pd.DataFrame(edit_list)

    # 5. 현황판
    st.write("---")
    target_count = len(edit_df[edit_df['대상여부']=='대상'])
    c1, c2, c3 = st.columns(3)
    c1.metric("분석된 설비 수", f"{len(edit_df)}개", help="기상/액상 분리 포함")
    c2.metric("대상 설비", f"{target_count}개")
    c3.metric("대상 비율", f"{target_count/len(edit_df)*100:.1f}%")

    # 6. 데이터 에디터
    st.write("### 📋 설비별 상세 설정")
    edited_df = st.data_editor(
        edit_df,
        column_config={
            "검출시스템": st.column_config.SelectboxColumn(options=["A", "B", "C"], required=True),
            "차단시스템": st.column_config.SelectboxColumn(options=["A", "B", "C"], required=True),
            "저장량(kg)": st.column_config.NumberColumn(format="%.1f"),
            "대상여부": st.column_config.TextColumn(disabled=True),
        },
        disabled=["번호", "장치•설비명", "취급물질", "저장량(kg)", "물질상태"],
        hide_index=True,
    )

    # 7. 엑셀 다운로드 (색상 적용)
    if st.button("📥 엑셀 보고서 다운로드"):
        edited_df['누출시간(sec)'] = edited_df.apply(lambda x: get_leak_time(x['검출시스템'], x['차단시스템']), axis=1)
        
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            workbook = writer.book
            worksheet = workbook.add_worksheet('대상설비선정')
            
            # 스타일
            header_fmt = workbook.add_format({'bold': True, 'align': 'center', 'valign': 'vcenter', 'bg_color': '#D9D9D9', 'border': 1, 'text_wrap': True})
            yellow_fmt = workbook.add_format({'bg_color': '#FFFF00', 'border': 1, 'align': 'left'})
            white_fmt = workbook.add_format({'bg_color': '#FFFFFF', 'border': 1, 'align': 'left'})
            
            # 헤더 그리기
            headers_1 = [
                ('번호',0,0), ('공정',1,1), ('구분\n기호',2,2), ('장치•\n설비명',3,3), ('취급물질',4,4), 
                ('Cas No.',5,5), ('함량(%)',6,6), ('물질상태',7,7), ('운전 압력\n(MPa)',8,8), 
                ('운전 온도\n(℃)',9,9), ('설계용량\n(m3)',10,10), ('비중',11,11), ('저장량\n(ton)',12,12), 
                ('저장량\n(kg)',13,13), ('사고시나리오\n규정수량(kg)',14,14), ('대상여부',15,15)
            ]
            for txt, c1, c2 in headers_1:
                worksheet.merge_range(0, c1, 1, c2, txt, header_fmt)
            
            worksheet.merge_range(0, 16, 0, 17, "누출공 크기(mm)", header_fmt)
            worksheet.write(1, 16, "설비연결", header_fmt)
            worksheet.write(1, 17, "대안", header_fmt)
            
            worksheet.merge_range(0, 18, 0, 20, "API 581 누출시간", header_fmt)
            worksheet.write(1, 18, "검출", header_fmt)
            worksheet.write(1, 19, "차단", header_fmt)
            worksheet.write(1, 20, "시간(sec)", header_fmt)
            
            worksheet.merge_range(0, 21, 1, 21, "비고", header_fmt)
            
            # 데이터 쓰기 (색상 적용)
            output_cols = [
                '번호', '공정', '구분기호', '장치•설비명', '취급물질', 'Cas No.', '함량(%)', '물질상태',
                '운전압력', '운전온도', '설계용량', '비중', '저장량(ton)', '저장량(kg)', '규정수량', '대상여부',
                '설비연결누출공', '대안누출공', '검출시스템', '차단시스템', '누출시간(sec)', '비고'
            ]
            
            start_row = 2
            for r_idx, row in edited_df.iterrows():
                is_target = str(row['대상여부']).strip() == '대상'
                cell_fmt = yellow_fmt if is_target else white_fmt
                
                for c_idx, col in enumerate(output_cols):
                    val = row[col]
                    if pd.isna(val): val = ""
                    worksheet.write(start_row+r_idx, c_idx, val, cell_fmt)

            worksheet.set_column('D:D', 25) 
            worksheet.set_column('V:V', 30)

        st.success("✅ 분리 로직 적용 완료! 다운로드하세요.")
        st.download_button(
            label="📥 결과 보고서 다운로드",
            data=output.getvalue(),
            file_name="3-가-1_대상설비_선정_결과.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )