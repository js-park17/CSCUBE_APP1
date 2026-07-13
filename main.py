import streamlit as st
import pandas as pd
import io

# --- 1. 유틸리티 함수 ---


# 별지 서식은 '압력/온도'처럼 '설계·운전' 두 줄짜리 병합헤더를 쓰는 경우가 많다.
# (예: 별지9호 - 1행 '압력 (MPa)' 병합, 2행 '설계'/'운전' 하위라벨)
# 이 두 줄 헤더를 자동 인식해서 "압력 (MPa)_운전" 같은 컬럼명으로 합쳐준다.
SUBHEADER_HINTS = {"설계", "운전", "기상", "액상", "고상", "상한", "하한", "최대", "최소", "평균"}

def _is_probable_subheader(row1, total_cols):
    """row1이 실제 데이터가 아니라 '설계/운전' 같은 병합헤더 하위행인지 판별"""
    if row1 is None:
        return False
    non_null = row1.notna().sum()
    if non_null == 0:
        return False
    # 병합헤더 하위행은 일부 컬럼(그룹 하위)에만 값이 있어 대체로 성글다
    sparse = non_null <= total_cols * 0.6
    vals = [str(v).strip() for v in row1.dropna().tolist()]
    hint_hits = sum(1 for v in vals if v in SUBHEADER_HINTS)
    return sparse and hint_hits >= 2

def _merge_two_row_header(raw_df):
    """header=None으로 읽은 DataFrame에서 1~2행이 병합헤더인지 감지해 컬럼명을 합치고,
    실제 데이터 부분만 반환한다. 병합헤더가 아니면 기존처럼 1행만 헤더로 사용한다."""
    if raw_df is None or len(raw_df) == 0:
        return None

    row0 = raw_df.iloc[0]
    row1 = raw_df.iloc[1] if len(raw_df) > 1 else None

    if _is_probable_subheader(row1, len(row0)):
        row0_filled = row0.ffill()  # 병합으로 비어있는 헤더 셀에 왼쪽 그룹명 채우기
        combined = []
        for c0, c1 in zip(row0_filled, row1):
            c0s = str(c0).strip() if pd.notna(c0) else ""
            c1s = str(c1).strip() if pd.notna(c1) else ""
            combined.append(f"{c0s}_{c1s}" if (c1s and c1s != c0s) else c0s)
        data = raw_df.iloc[2:].copy()
        data.columns = combined
    else:
        data = raw_df.iloc[1:].copy()
        data.columns = [str(c).strip() if pd.notna(c) else "" for c in row0]

    return data.reset_index(drop=True)

def read_table(uploaded_file):
    """CSV(cp949/utf-8) 또는 Excel 파일을 읽어, '설계/운전' 등 2행 병합헤더를 자동 인식해 DataFrame으로 변환"""
    raw = None
    try:
        if uploaded_file.name.endswith('.csv'):
            raw = pd.read_csv(uploaded_file, encoding='cp949', header=None)
        else:
            raw = pd.read_excel(uploaded_file, header=None)
    except Exception:
        try:
            uploaded_file.seek(0)
            raw = pd.read_csv(uploaded_file, encoding='utf-8', header=None)
        except Exception as e:
            st.error(f"파일 오류: {e}")
            return None

    return _merge_two_row_header(raw)

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

def normalize_key(s):
    """CAS번호/물질명 매칭용 정규화 (공백·하이픈 제거, 대문자 통일)"""
    if s is None:
        return ""
    s = str(s).strip()
    if s == "" or s.lower() in ("nan", "none"):
        return ""
    return s.upper().replace("-", "").replace(" ", "")

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
st.set_page_config(page_title="CS Safety - 시나리오 툴 v13", layout="wide")
st.title("🛡️ 사고시나리오 대상설비 자동 선정 (v13.0)")
st.markdown("##### [기능] 별지 6호서식(물질목록) 연동으로 '물질연번'·'비중'을 자동 검증합니다. '액상/기상' 혼합 시 자동으로 줄을 나누어 각각 판단합니다.")

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

# --- 3. STEP 1: 유해화학물질 목록 업로드 (별지 제6호서식) ---
st.write("### 1️⃣ 유해화학물질 목록 업로드 (별지 제6호서식)")
st.caption("연번·유해화학물질명·CAS번호·비중이 포함된 [별지 6] 유해화학물질 목록 및 취급량 파일을 업로드하면, "
           "아래 장치·설비 목록의 '물질연번'과 '비중'을 CAS번호(우선) 또는 물질명 기준으로 자동 매칭·검증합니다. (선택사항)")
material_file = st.file_uploader("별지 6호서식 업로드", type=['csv', 'xlsx', 'xls'], key="material_uploader")

material_lookup_cas = {}
material_lookup_name = {}

if material_file:
    mat_df = read_table(material_file)
    if mat_df is not None:
        mcols = mat_df.columns
        mat_col_map = {
            "연번": find_column(mcols, ["연번"]),
            "물질명": find_column(mcols, ["유해화학물질명"]) or find_column(mcols, ["물질명"]),
            "CAS": find_column(mcols, ["CAS"]),
            "비중": find_column(mcols, ["비중"]),
        }
        missing = [k for k in ["연번", "물질명", "비중"] if not mat_col_map[k]]
        if missing:
            st.warning(f"⚠️ 물질목록에서 다음 컬럼을 찾지 못했습니다: {', '.join(missing)}. 해당 항목은 매칭에 사용되지 않습니다.")

        for _, mrow in mat_df.iterrows():
            seq_no = mrow.get(mat_col_map["연번"], '') if mat_col_map["연번"] else ''
            sg_raw = mrow.get(mat_col_map["비중"], None) if mat_col_map["비중"] else None
            name_raw = mrow.get(mat_col_map["물질명"], '') if mat_col_map["물질명"] else ''
            cas_raw = mrow.get(mat_col_map["CAS"], '') if mat_col_map["CAS"] else ''

            try:
                sg_val = float(sg_raw)
            except (TypeError, ValueError):
                sg_val = None

            entry = {"연번": seq_no, "비중": sg_val, "물질명": name_raw}

            cas_key = normalize_key(cas_raw)
            if cas_key:
                material_lookup_cas[cas_key] = entry

            name_key = normalize_key(name_raw)
            if name_key:
                material_lookup_name[name_key] = entry

        st.success(f"✅ 물질목록 {len(mat_df)}건 로드 완료 (CAS 매칭키 {len(material_lookup_cas)}개, 물질명 매칭키 {len(material_lookup_name)}개)")

has_material_list = bool(material_lookup_cas or material_lookup_name)

# --- 4. STEP 2: 장치·설비 목록 업로드 (별지 제9호서식) ---
st.write("### 2️⃣ 장치·설비 목록 업로드 (별지 제9호서식)")
uploaded_file = st.file_uploader("[별지 9] 장치설비 목록 엑셀 업로드", type=['csv', 'xlsx', 'xls'], key="equipment_uploader")

if uploaded_file:
    df = read_table(uploaded_file)
    if df is None:
        st.stop()

    # 컬럼 매핑
    cols = df.columns
    col_map = {
        "설비명": find_column(cols, ["설비명"]),
        "공정": find_column(cols, ["공정"]) or find_column(cols, ["비고"]),
        "구분기호": find_column(cols, ["구분", "기호"]),
        "취급물질": find_column(cols, ["취급물질"]),
        "CAS": find_column(cols, ["CAS"]),
        "함량": find_column(cols, ["함량"]),
        "물질상태": find_column(cols, ["물질상태"]),
        # '압력 (MPa)'가 설계/운전으로 나뉜 경우 "_운전" 하위컬럼을 우선 사용, 없으면 단일 압력 컬럼 사용
        "압력": find_column(cols, ["압력", "운전"]) or find_column(cols, ["압력"]),
        "온도": find_column(cols, ["온도", "운전"]) or find_column(cols, ["온도"]),
        "설계용량": find_column(cols, ["설계용량"]),
        "비중": find_column(cols, ["비중"]),
        "저장량_ton": find_column(cols, ["저장량", "ton"]),
        "유해성분류": find_column(cols, ["유해성"]),
        "연결구": find_column(cols, ["연결구"]) or find_column(cols, ["누출공"]),
        "검출": find_column(cols, ["검출"]),
        "차단": find_column(cols, ["차단"]),
        "이격거리": find_column(cols, ["이격거리"]),
        "저장액위": find_column(cols, ["저장액위"]),
        "방류벽": find_column(cols, ["방류벽"]) or find_column(cols, ["트렌치"]),
        "실외실내": find_column(cols, ["실외", "실내"]),
        "비고": find_column(cols, ["비고"])
    }

    if not col_map["설비명"]:
        st.error("❌ '설비명' 컬럼을 찾을 수 없습니다.")
        st.stop()

    required_warn = [k for k in ["물질상태", "취급물질", "압력", "온도"] if not col_map[k]]
    if required_warn:
        st.warning(f"⚠️ 다음 컬럼을 찾지 못해 빈 값으로 처리됩니다: {', '.join(required_warn)}. 원본 파일의 헤더명(줄바꿈·병합셀 등)을 확인하세요.")

    # 리스트 생성 (분리 로직 + 물질목록 매칭 적용)
    edit_list = []
    output_idx = 1
    unmatched_count = 0

    for i, row in df.iterrows():
        state_raw = str(row.get(col_map["물질상태"], '')).strip() if col_map["물질상태"] else ''
        hazard_raw = str(row.get(col_map["유해성분류"], '')).strip() if col_map["유해성분류"] else ""
        name_raw = str(row.get(col_map["취급물질"], '')).strip() if col_map["취급물질"] else ''
        cas_raw = str(row.get(col_map["CAS"], '')).strip() if col_map["CAS"] else ''

        # --- 물질목록(별지6) 매칭: CAS번호 우선, 없으면 물질명 ---
        match = None
        if has_material_list:
            cas_key = normalize_key(cas_raw)
            if cas_key and cas_key in material_lookup_cas:
                match = material_lookup_cas[cas_key]
            else:
                name_key = normalize_key(name_raw)
                if name_key and name_key in material_lookup_name:
                    match = material_lookup_name[name_key]

        mat_no = match["연번"] if match else ''

        # --- 비중 확인: 물질목록 값을 우선 적용, 없으면 설비목록 값, 그마저 없으면 1.0 ---
        sheet_sg_raw = row.get(col_map["비중"], None) if col_map["비중"] else None
        try:
            sheet_sg = float(sheet_sg_raw)
        except (TypeError, ValueError):
            sheet_sg = None

        sg_mismatch = False
        if match and match["비중"] is not None:
            resolved_sg = match["비중"]
            if sheet_sg is not None and abs(sheet_sg - resolved_sg) > 0.001:
                sg_mismatch = True
        elif sheet_sg is not None:
            resolved_sg = sheet_sg
        else:
            resolved_sg = 1.0

        if has_material_list and not match:
            unmatched_count += 1

        # --- 분리 로직 (Split Logic) ---
        # '액'과 '기'가 모두 포함된 경우 -> 분리
        if ('액' in state_raw and '기' in state_raw):
            target_states = ["기상", "액상"]
        else:
            target_states = [state_raw]

        # 각 상태별로 행 생성
        for current_state in target_states:
            # 저장량 계산 (물질목록으로 검증된 비중 적용)
            if col_map["저장량_ton"]:
                ton_raw = row.get(col_map["저장량_ton"], 0)
                try:
                    ton_val = float(ton_raw)
                except (TypeError, ValueError):
                    ton_val = 0.0
                storage = ton_val * 1000
                ton_display = ton_val
            elif col_map["설계용량"]:
                vol_raw = row.get(col_map["설계용량"], 0)
                try:
                    vol_val = float(vol_raw)
                except (TypeError, ValueError):
                    vol_val = 0.0
                storage = vol_val * resolved_sg * 1000
                ton_display = round(storage / 1000, 3)
            else:
                storage = 0.0
                ton_display = 0

            # 규정수량 산정
            reg_amt = determine_limit_val(current_state, hazard_raw, defaults)
            is_target = "대상" if storage >= reg_amt else "비대상"

            # 누출공 및 기타 정보
            conn_raw = row.get(col_map["연결구"], 80) if col_map["연결구"] else 80
            try: conn_size = float(conn_raw)
            except: conn_size = 80.0

            det_val = str(row.get(col_map["검출"], default_detect)).strip() if col_map["검출"] else default_detect
            con_val = str(row.get(col_map["차단"], default_control)).strip() if col_map["차단"] else default_control

            # 비고: 분리 여부 + 물질목록 매칭/비중 검증 결과 표시
            note = str(row.get(col_map["비고"], '')).strip() if col_map["비고"] else ''
            if len(target_states) > 1:
                note += f" ({current_state} 기준 판정)"
            if has_material_list and not match:
                note += " [⚠물질목록 미매칭 - 설비목록 비중 사용]"
            elif sg_mismatch:
                note += f" [⚠비중 불일치: 설비목록 {sheet_sg} → 물질목록 {resolved_sg} 적용]"

            edit_list.append({
                "번호": output_idx,               # 순번 증가
                "물질연번": mat_no,                 # 별지6호서식 연번 매칭 결과
                "공정": row.get(col_map["공정"], ''),
                "구분기호": row.get(col_map["구분기호"], ''),
                "장치•설비명": row.get(col_map["설비명"], ''),
                "취급물질": row.get(col_map["취급물질"], ''),
                "Cas No.": row.get(col_map["CAS"], ''),
                "함량(%)": row.get(col_map["함량"], ''),
                "물질상태": current_state,          # 분리된 상태값 입력
                "운전압력": row.get(col_map["압력"], ''),
                "운전온도": row.get(col_map["온도"], ''),
                "설계용량": row.get(col_map["설계용량"], ''),
                "비중": resolved_sg,                # 물질목록 검증 비중
                "저장량(ton)": ton_display,
                "저장량(kg)": storage,
                "규정수량": reg_amt,
                "대상여부": is_target,
                "설비연결누출공": conn_size,
                "대안누출공": conn_size,
                "검출시스템": det_val,
                "차단시스템": con_val,
                "이격거리(m)": row.get(col_map["이격거리"], '') if col_map["이격거리"] else '',
                "저장액위(m)": row.get(col_map["저장액위"], '') if col_map["저장액위"] else '',
                "방류벽면적(m2)": row.get(col_map["방류벽"], '') if col_map["방류벽"] else '',
                "실외/실내": row.get(col_map["실외실내"], '') if col_map["실외실내"] else '',
                "비고": note.strip()
            })
            output_idx += 1  # 다음 번호

    edit_df = pd.DataFrame(edit_list)

    if has_material_list and unmatched_count:
        st.warning(f"⚠️ 설비 {unmatched_count}건이 물질목록과 매칭되지 않았습니다 (CAS/물질명 불일치). "
                   f"이 경우 설비목록 자체 비중값이 사용되었으니 '비고'란을 확인하세요.")

    # 5. 현황판
    st.write("---")
    target_count = len(edit_df[edit_df['대상여부']=='대상']) if len(edit_df) else 0
    c1, c2, c3 = st.columns(3)
    c1.metric("분석된 설비 수", f"{len(edit_df)}개", help="기상/액상 분리 포함")
    c2.metric("대상 설비", f"{target_count}개")
    c3.metric("대상 비율", f"{target_count/len(edit_df)*100:.1f}%" if len(edit_df) else "0.0%")

    # 6. 데이터 에디터
    st.write("### 📋 설비별 상세 설정")
    edited_df = st.data_editor(
        edit_df,
        column_config={
            "검출시스템": st.column_config.SelectboxColumn(options=["A", "B", "C"], required=True),
            "차단시스템": st.column_config.SelectboxColumn(options=["A", "B", "C"], required=True),
            "저장량(kg)": st.column_config.NumberColumn(format="%.1f"),
            "비중": st.column_config.NumberColumn(format="%.3f"),
            "대상여부": st.column_config.TextColumn(disabled=True),
        },
        disabled=["번호", "물질연번", "장치•설비명", "취급물질", "저장량(kg)", "물질상태", "비중"],
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

            # 헤더 그리기 ('물질연번' 컬럼 추가로 인덱스 전체 +1)
            headers_1 = [
                ('번호',0,0), ('물질\n연번',1,1), ('공정',2,2), ('구분\n기호',3,3), ('장치•\n설비명',4,4), ('취급물질',5,5),
                ('Cas No.',6,6), ('함량(%)',7,7), ('물질상태',8,8), ('운전 압력\n(MPa)',9,9),
                ('운전 온도\n(℃)',10,10), ('설계용량\n(m3)',11,11), ('비중',12,12), ('저장량\n(ton)',13,13),
                ('저장량\n(kg)',14,14), ('사고시나리오\n규정수량(kg)',15,15), ('대상여부',16,16)
            ]
            for txt, c1, c2 in headers_1:
                worksheet.merge_range(0, c1, 1, c2, txt, header_fmt)

            worksheet.merge_range(0, 17, 0, 18, "누출공 크기(mm)", header_fmt)
            worksheet.write(1, 17, "설비연결", header_fmt)
            worksheet.write(1, 18, "대안", header_fmt)

            worksheet.merge_range(0, 19, 0, 21, "API 581 누출시간", header_fmt)
            worksheet.write(1, 19, "검출", header_fmt)
            worksheet.write(1, 20, "차단", header_fmt)
            worksheet.write(1, 21, "시간(sec)", header_fmt)

            site_headers = [
                ('지면 위\n이격거리(m)',22,22), ('저장액위\n(m)',23,23),
                ('방류벽/방류턱/\n트렌치 면적(m2)',24,24), ('실외/\n실내',25,25)
            ]
            for txt, c1, c2 in site_headers:
                worksheet.merge_range(0, c1, 1, c2, txt, header_fmt)

            worksheet.merge_range(0, 26, 1, 26, "비고", header_fmt)

            # 데이터 쓰기 (색상 적용)
            output_cols = [
                '번호', '물질연번', '공정', '구분기호', '장치•설비명', '취급물질', 'Cas No.', '함량(%)', '물질상태',
                '운전압력', '운전온도', '설계용량', '비중', '저장량(ton)', '저장량(kg)', '규정수량', '대상여부',
                '설비연결누출공', '대안누출공', '검출시스템', '차단시스템', '누출시간(sec)',
                '이격거리(m)', '저장액위(m)', '방류벽면적(m2)', '실외/실내', '비고'
            ]

            start_row = 2
            for r_idx, row in edited_df.iterrows():
                is_target = str(row['대상여부']).strip() == '대상'
                cell_fmt = yellow_fmt if is_target else white_fmt

                for c_idx, col in enumerate(output_cols):
                    val = row[col]
                    if pd.isna(val): val = ""
                    worksheet.write(start_row+r_idx, c_idx, val, cell_fmt)

            worksheet.set_column('E:E', 25)
            worksheet.set_column('AA:AA', 30)

        st.success("✅ 물질목록 연동(물질연번·비중 검증) 결과 적용 완료! 다운로드하세요.")
        st.download_button(
            label="📥 결과 보고서 다운로드",
            data=output.getvalue(),
            file_name="3-가-1_대상설비_선정_결과.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
elif has_material_list:
    st.info("👆 이제 [별지 9] 장치·설비 목록을 업로드하면 방금 올린 물질목록과 자동으로 매칭됩니다.")
