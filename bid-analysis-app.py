import streamlit as st
import pandas as pd
import requests
import calendar
import time
import datetime
import plotly.express as px
import os

# --- 1. 설정 및 API 정보 ---
# Streamlit Cloud Secrets에서 인증키를 가져옵니다.
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

# --- 2. 예비가격 수집 로직 (XML 매핑 반영) ---
def update_price_details_logic(target_bid_list):
    if not target_bid_list:
        return pd.DataFrame()

    existing_price_df = pd.read_csv(PRICE_FILE) if os.path.exists(PRICE_FILE) else pd.DataFrame()
    
    # 이미 수집된 공고번호는 제외하여 수집 시간 단축
    if not existing_price_df.empty:
        collected_bids = existing_price_df['bidNtceNo'].unique().tolist()
        target_bid_list = [b for b in target_bid_list if b not in collected_bids]

    if not target_bid_list:
        return existing_price_df

    new_price_rows = []
    price_progress = st.progress(0)
    status_text = st.empty()
    
    for i, bid_no in enumerate(target_bid_list):
        status_text.text(f"🎯 신규 예가 수집 중: {i+1}/{len(target_bid_list)} (공고: {bid_no})")
        # 예비가격 상세 API 호출 (inqryDiv: 2는 공고번호 기준)
        data = get_api_data("/getOpengResultListInfoCnstwkPreparPcDetail", {"inqryDiv": "2", "bidNtceNo": bid_no})
        
        if data and "response" in data:
            body = data["response"].get("body", {})
            items_root = body.get("items", "")
            if items_root:
                items = items_root if isinstance(items_root, list) else items_root.get("item", [])
                if isinstance(items, dict): items = [items]
                
                for item in items:
                    # 사용자 제공 XML 구조에 맞게 필드 매핑
                    mapped_item = {
                        "bidNtceNo": item.get("bidNtceNo"),
                        "preparPc": item.get("bsisPlnprc"), # 기초예비가격
                        "drawCnt": item.get("drwtNum"),    # 추첨횟수
                        "drwtYn": item.get("drwtYn"),      # 추첨여부(Y/N)
                        "bidNtceNm": item.get("bidNtceNm")
                    }
                    new_price_rows.append(mapped_item)
        
        price_progress.progress((i + 1) / len(target_bid_list))
        time.sleep(0.05) # API 과부하 방지

    if new_price_rows:
        new_price_df = pd.DataFrame(new_price_rows)
        # 숫자형 변환
        new_price_df['preparPc'] = pd.to_numeric(new_price_df['preparPc'], errors='coerce')
        new_price_df['drawCnt'] = pd.to_numeric(new_price_df['drawCnt'], errors='coerce')
        
        # 기존 데이터와 합치고 중복 제거
        final_price_df = pd.concat([existing_price_df, new_price_df]).drop_duplicates()
        final_price_df.to_csv(PRICE_FILE, index=False, encoding="utf-8-sig")
        return final_price_df
    return existing_price_df

# --- 3. 통합 업데이트 로직 ---
def run_integrated_update():
    # A. 낙찰 마스터 업데이트
    existing_master = pd.read_csv(MASTER_FILE) if os.path.exists(MASTER_FILE) else pd.DataFrame()
    start_dt_str = "202201010000"
    if not existing_master.empty:
        existing_master['rlOpengDt'] = pd.to_datetime(existing_master['rlOpengDt'])
        start_dt_str = (existing_master['rlOpengDt'].max() + datetime.timedelta(minutes=1)).strftime('%Y%m%d%H%M')

    now = datetime.datetime.now()
    end_dt_str = now.strftime('%Y%m%d%H%M')
    
    new_master_rows = []
    st.info("🚜 1단계: 신규 낙찰 정보를 조회 중입니다...")
    
    # 연/월 단위 수집 로직
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
    
    # 마스터 저장
    if new_master_rows:
        new_master_df = pd.DataFrame(new_master_rows)
        final_master = pd.concat([existing_master, new_master_df]).drop_duplicates(subset=['bidNtceNo', 'bidNtceOrd'])
        final_master.to_csv(MASTER_FILE, index=False, encoding="utf-8-sig")
        st.success(f"✅ 낙찰 마스터 업데이트 완료 ({len(new_master_rows)}건 추가)")

    # B. 예비가격 미수집분 통합 업데이트
    updated_master = pd.read_csv(MASTER_FILE) if os.path.exists(MASTER_FILE) else pd.DataFrame()
    if not updated_master.empty:
        st.info("🚀 2단계: 누락된 예비가격 상세 정보를 가져옵니다...")
        all_bids = updated_master['bidNtceNo'].unique().tolist()
        update_price_details_logic(all_bids)
        st.success("🎉 모든 데이터 업데이트가 성공적으로 끝났습니다!")

# --- 4. 메인 화면 UI ---
st.sidebar.title("🔍 분석 메뉴")
menu = st.sidebar.radio("원하는 기능을 선택하세요", ["🏠 낙찰 현황 대시보드", "🎯 예가 상세 분석"])

if st.sidebar.button("🔄 전체 데이터 업데이트", help="새로운 낙찰 공고와 예비가격 데이터를 모두 가져옵니다."):
    run_integrated_update()
    st.rerun()

if menu == "🏠 낙찰 현황 대시보드":
    st.header("🏗️ 대전 지역 상하수도설비 낙찰 현황")
    if os.path.exists(MASTER_FILE):
        df = pd.read_csv(MASTER_FILE)
        df['rlOpengDt'] = pd.to_datetime(df['rlOpengDt'])
        st.metric("총 수집 공고", f"{len(df):,} 건")
        st.dataframe(df.sort_values('rlOpengDt', ascending=False), width=1500, hide_index=True)
    else:
        st.warning("데이터가 없습니다. 업데이트 버튼을 눌러주세요.")

elif menu == "🎯 예가 상세 분석":
    st.header("🎯 예비가격 상세 분석 (기초예가 분포)")
    if os.path.exists(PRICE_FILE):
        price_df = pd.read_csv(PRICE_FILE)
        bid_options = sorted(price_df['bidNtceNo'].unique(), reverse=True)
        target_bid = st.selectbox("분석할 공고번호를 선택하세요", bid_options)
        
        if target_bid:
            detail = price_df[price_df['bidNtceNo'] == target_bid].copy()
            
            if not detail.empty and 'preparPc' in detail.columns:
                st.subheader(f"📍 공고명: {detail['bidNtceNm'].iloc[0]}")
                
                # 시각화: 막대 그래프 (예가별 추첨 횟수)
                fig = px.bar(detail.sort_values('preparPc'), 
                             x='preparPc', y='drawCnt',
                             color='drwtYn', 
                             color_discrete_map={'Y': '#EF553B', 'N': '#636EFA'},
                             title=f"공고번호 [{target_bid}] 예비가격 분포",
                             labels={'preparPc':'기초예비가격', 'drawCnt':'추첨횟수', 'drwtYn':'추첨여부'})
                
                st.plotly_chart(fig, use_container_width=True)
                
                # 상세 표
                st.write("📋 상세 예비가격 목록")
                st.dataframe(detail[['preparPc', 'drawCnt', 'drwtYn']].sort_values('preparPc'), 
                             width=1500, hide_index=True)
            else:
                st.error("데이터 구조가 잘못되었습니다. 업데이트를 다시 수행해 주세요.")
    else:
        st.error("수집된 예가 데이터 파일이 없습니다.")
