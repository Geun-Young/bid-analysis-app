import streamlit as st
import pandas as pd
import requests
import calendar
import time
import datetime
import plotly.express as px
import os

# --- 1. 설정 및 API 정보 (Secrets 사용 권장) ---
# 로컬 테스트 시에는 직접 입력, 배포 시에는 st.secrets를 사용합니다.
if "data_go_kr_key" in st.secrets:
    SERVICE_KEY = st.secrets["data_go_kr_key"]
else:
    SERVICE_KEY = "iFzIGgOAmOlMUeV4lW34bEc8aM2Pq0pppKXZisyLuUxPAXA94HhjPM+XF0kdZ3UfaiFop3xbIUzCphIkCc8uZg=="

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

# --- 3. 데이터 업데이트 로직 (마스터 데이터 수집 전체 포함) ---
def update_master_data():
    existing_df = pd.read_csv(MASTER_FILE) if os.path.exists(MASTER_FILE) else pd.DataFrame()
    start_dt_str = "202201010000"
    
    if not existing_df.empty:
        existing_df['rlOpengDt'] = pd.to_datetime(existing_df['rlOpengDt'])
        start_dt_str = (existing_df['rlOpengDt'].max() + datetime.timedelta(minutes=1)).strftime('%Y%m%d%H%M')

    now = datetime.datetime.now()
    end_dt_str = now.strftime('%Y%m%d%H%M')
    start_year = int(start_dt_str[:4])
    end_year = now.year
    
    new_rows = []
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    for idx, year in enumerate(range(start_year, end_year + 1)):
        status_text.text(f"⏳ {year}년 낙찰 데이터 수집 중...")
        s_month = int(start_dt_str[4:6]) if year == start_year else 1
        
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
                items_root = body.get("items", "")
                if not items_root: break
                items = items_root if isinstance(items_root, list) else items_root.get("item", [])
                if isinstance(items, dict): items = [items]
                
                new_rows.extend(items)
                if len(items) < 999: break
                page += 1
                time.sleep(0.1)
        progress_bar.progress((idx + 1) / (end_year - start_year + 1))

    if new_rows:
        new_df = pd.DataFrame(new_rows)
        final_df = pd.concat([existing_df, new_df]).drop_duplicates(subset=['bidNtceNo', 'bidNtceOrd'])
        final_df.to_csv(MASTER_FILE, index=False, encoding="utf-8-sig")
        return final_df
    return existing_df

def update_price_details():
    if not os.path.exists(MASTER_FILE):
        st.error("먼저 '낙찰 현황' 데이터를 업데이트해야 합니다.")
        return
    master_df = pd.read_csv(MASTER_FILE)
    bid_list = master_df['bidNtceNo'].unique().tolist()
    existing_price_df = pd.read_csv(PRICE_FILE) if os.path.exists(PRICE_FILE) else pd.DataFrame()
    if not existing_price_df.empty:
        collected_bids = existing_price_df['bidNtceNo'].unique().tolist()
        bid_list = [b for b in bid_list if b not in collected_bids]

    if not bid_list:
        st.success("모든 공고의 예가 정보가 최신입니다.")
        return existing_price_df

    new_price_rows = []
    progress_bar = st.progress(0)
    for i, bid_no in enumerate(bid_list):
        data = get_api_data("/getOpengResultListInfoCnstwkPreparPcDetail", {"inqryDiv": "2", "bidNtceNo": bid_no})
        if data and "response" in data:
            body = data["response"].get("body", {})
            items_root = body.get("items", "")
            if items_root:
                items = items_root if isinstance(items_root, list) else items_root.get("item", [])
                if isinstance(items, dict): items = [items]
                new_price_rows.extend(items)
        progress_bar.progress((i + 1) / len(bid_list))
        if (i + 1) % 10 == 0: time.sleep(0.1)

    if new_price_rows:
        new_df = pd.DataFrame(new_price_rows)
        final_df = pd.concat([existing_price_df, new_df]).drop_duplicates()
        final_df.to_csv(PRICE_FILE, index=False, encoding="utf-8-sig")
        return final_df
    return existing_price_df

# --- 4. UI 구성 ---
st.sidebar.title("📊 메뉴 선택")
menu = st.sidebar.radio("기능 선택", ["🏠 낙찰 현황 대시보드", "🎯 예가 상세 분석"])

if menu == "🏠 낙찰 현황 대시보드":
    st.header("🏗️ 대전 상하수도설비공사 낙찰 현황")
    if st.sidebar.button("🔄 낙찰 데이터 업데이트"):
        with st.spinner("데이터 업데이트 중..."):
            st.session_state.df = update_master_data()
            st.rerun()

    if os.path.exists(MASTER_FILE):
        df = pd.read_csv(MASTER_FILE)
        df['rlOpengDt'] = pd.to_datetime(df['rlOpengDt'])
        st.metric("총 공고 수", f"{len(df):,} 건")
        st.dataframe(df.sort_values('rlOpengDt', ascending=False), width='stretch')
    else:
        st.warning("데이터가 없습니다. 업데이트를 눌러주세요.")

elif menu == "🎯 예가 상세 분석":
    st.header("🎯 예비가격 상세 분석")
    if st.sidebar.button("🚀 예가 정보 가져오기"):
        with st.spinner("수집 중..."):
            update_price_details()
            st.rerun()

    if os.path.exists(PRICE_FILE):
        price_df = pd.read_csv(PRICE_FILE)
        target_bid = st.selectbox("공고번호 선택", price_df['bidNtceNo'].unique())
        if target_bid:
            detail = price_df[price_df['bidNtceNo'] == target_bid]
            st.write(f"📍 공고번호: {target_bid}")
            fig = px.scatter(detail, x='preparPc', y='drawCnt', size='drawCnt', title="예가 분포")
            st.plotly_chart(fig, width='stretch')
            st.dataframe(detail[['preparPc', 'drawCnt']], width='stretch')