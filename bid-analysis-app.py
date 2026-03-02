import streamlit as st
import pandas as pd
import requests
import calendar
import time
import datetime
import plotly.express as px
import os

# --- 1. 설정 및 API 정보 ---
# Streamlit Cloud의 Secrets에 저장된 키를 바로 사용합니다.
SERVICE_KEY = st.secrets["data_go_kr_key"]

BASE_URL = "http://apis.data.go.kr/1230000/as/ScsbidInfoService"
MASTER_FILE = "HIST_BID_MASTER_5Y.csv"
PRICE_FILE = "PREP_PRICE_DETAIL.csv"

st.set_page_config(page_title="조달청 스마트 분석 시스템", layout="wide")

# API 호출 공통 함수
def get_api_data(endpoint, extra_params):
    params = {
        "serviceKey": SERVICE_KEY,
        "numOfRows": "999",
        "pageNo": "1",
        "type": "json",
    }
    params.update(extra_params)
    try:
        response = requests.get(BASE_URL + endpoint, params=params, timeout=30)
        if response.status_code == 200:
            return response.json()
    except Exception as e:
        st.error(f"📡 API 통신 에러: {e}")
    return None

# --- 2. 예비가격 수집 로직 (미수집분만 추출) ---
def update_price_details_logic(target_bid_list):
    if not target_bid_list:
        return pd.DataFrame()

    existing_price_df = pd.read_csv(PRICE_FILE) if os.path.exists(PRICE_FILE) else pd.DataFrame()
    
    if not existing_price_df.empty:
        collected_bids = existing_price_df['bidNtceNo'].unique().tolist()
        # 이미 상세 정보가 있는 공고는 제외하고 수집
        target_bid_list = [b for b in target_bid_list if b not in collected_bids]

    if not target_bid_list:
        st.info("모든 공고의 예비가격이 이미 수집되어 있습니다.")
        return existing_price_df

    new_price_rows = []
    price_progress = st.progress(0)
    status_text = st.empty()
    
    for i, bid_no in enumerate(target_bid_list):
        status_text.text(f"🎯 미수집 예가 가져오는 중: {i+1}/{len(target_bid_list)} (공고: {bid_no})")
        data = get_api_data("/getOpengResultListInfoCnstwkPreparPcDetail", {"inqryDiv": "2", "bidNtceNo": bid_no})
        
        if data and "response" in data:
            body = data["response"].get("body", {})
            items_root = body.get("items", "")
            if items_root:
                items = items_root if isinstance(items_root, list) else items_root.get("item", [])
                if isinstance(items, dict): items = [items]
                new_price_rows.extend(items)
        
        price_progress.progress((i + 1) / len(target_bid_list))
        time.sleep(0.05) # API 부하 방지용 짧은 대기

    if new_price_rows:
        new_price_df = pd.DataFrame(new_price_rows)
        final_price_df = pd.concat([existing_price_df, new_price_df]).drop_duplicates()
        final_price_df.to_csv(PRICE_FILE, index=False, encoding="utf-8-sig")
        return final_price_df
    return existing_price_df

# --- 3. 통합 업데이트 로직 ---
def run_integrated_update():
    # A. 낙찰 마스터 업데이트 (기존 로직 유지)
    existing_master = pd.read_csv(MASTER_FILE) if os.path.exists(MASTER_FILE) else pd.DataFrame()
    start_dt_str = "202201010000"
    if not existing_master.empty:
        existing_master['rlOpengDt'] = pd.to_datetime(existing_master['rlOpengDt'])
        start_dt_str = (existing_master['rlOpengDt'].max() + datetime.timedelta(minutes=1)).strftime('%Y%m%d%H%M')

    now = datetime.datetime.now()
    end_dt_str = now.strftime('%Y%m%d%H%M')
    
    new_master_rows = []
    st.info("🚜 1단계: 신규 낙찰 공고 확인 중...")
    
    # [수집 루프 - 연도/월 단위]
    for year in range(int(start_dt_str[:4]), now.year + 1):
        s_month = int(start_dt_str[4:6]) if year == int(start_dt_str[:4]) else 1
        for month in range(s_month, 13):
            last_day = calendar.monthrange(year, month)[1]
            m_start = f"{year}{month:02d}010000"
            actual_start = start_dt_str if m_start < start_dt_str else m_start
            actual_end = f"{year}{month:02d}{last_day}2359"
            if actual_start > end_dt_str: break

            page = 1
            while True:
                data = get_api_data("/getScsbidListSttusCnstwkPPSSrch", {
                    "inqryDiv": "2", "inqryBgnDt": actual_start, "inqryEndDt": actual_end,
                    "prtcptLmtRgnCd": "30", "indstrytyCd": "4996", "pageNo": str(page)
                })
                if not data or "response" not in data: break
                body = data["response"].get("body", {})
                items = body.get("items", "")
                if not items: break
                items = items if isinstance(items, list) else items.get("item", [])
                if isinstance(items, dict): items = [items]
                new_master_rows.extend(items)
                if len(items) < 999: break
                page += 1

    # 저장 및 예가 수집 연동
    if new_master_rows:
        new_master_df = pd.DataFrame(new_master_rows)
        final_master = pd.concat([existing_master, new_master_df]).drop_duplicates(subset=['bidNtceNo', 'bidNtceOrd'])
        final_master.to_csv(MASTER_FILE, index=False, encoding="utf-8-sig")
        st.success(f"✅ {len(new_master_rows)}개의 새 공고를 마스터에 추가했습니다.")
    
    # B. 예가 미수집분 통합 수집 (마스터에 있는 모든 공고 대상 체크)
    updated_master = pd.read_csv(MASTER_FILE) if os.path.exists(MASTER_FILE) else pd.DataFrame()
    if not updated_master.empty:
        st.info("🚀 2단계: 미수집된 예비가격 상세 정보를 가져옵니다...")
        all_bids = updated_master['bidNtceNo'].unique().tolist()
        update_price_details_logic(all_bids)
        st.success("✅ 모든 데이터 최신화 완료!")

# --- 4. 화면 구성 ---
st.sidebar.title("📊 메뉴 선택")
menu = st.sidebar.radio("기능 선택", ["🏠 낙찰 현황 대시보드", "🎯 예가 상세 분석"])

if st.sidebar.button("🔄 전체 데이터 최신화", help="낙찰 정보와 예비가격 미수집분을 모두 업데이트합니다."):
    run_integrated_update()
    st.rerun()

if menu == "🏠 낙찰 현황 대시보드":
    st.header("🏗️ 대전 상하수도설비공사 낙찰 현황")
    if os.path.exists(MASTER_FILE):
        df = pd.read_csv(MASTER_FILE)
        df['rlOpengDt'] = pd.to_datetime(df['rlOpengDt'])
        st.metric("총 공고 수", f"{len(df):,} 건")
        st.dataframe(df.sort_values('rlOpengDt', ascending=False), width='stretch', hide_index=True)
    else:
        st.warning("데이터 파일이 없습니다. 업데이트 버튼을 눌러주세요.")

elif menu == "🎯 예가 상세 분석":
    st.header("🎯 예비가격 상세 분석")
    if os.path.exists(PRICE_FILE):
        price_df = pd.read_csv(PRICE_FILE)
        # 최신 공고가 위로 오도록 정렬하여 선택 박스 구성
        bid_options = sorted(price_df['bidNtceNo'].unique(), reverse=True)
        target_bid = st.selectbox("분석할 공고번호 선택", bid_options)
        
        if target_bid:
            detail = price_df[price_df['bidNtceNo'] == target_bid].copy()
            detail['preparPc'] = pd.to_numeric(detail['preparPc'])
            
            st.subheader(f"📍 공고번호: {target_bid}")
            fig = px.scatter(detail, x='preparPc', y='drawCnt', size='drawCnt', color='drawCnt', 
                             title="예비가격별 선택 횟수 분포", labels={'preparPc':'예비가격', 'drawCnt':'선택횟수'})
            st.plotly_chart(fig, width='stretch')
            st.dataframe(detail[['preparPc', 'drawCnt']].sort_values('preparPc'), width='stretch', hide_index=True)
    else:
        st.error("예비가격 데이터 파일이 없습니다. 먼저 업데이트를 진행해 주세요.")
