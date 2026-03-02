import streamlit as st
import pandas as pd
import requests
import calendar
import time
import datetime
import plotly.express as px
import os

# --- 1. 설정 및 API 정보 ---
SERVICE_KEY = st.secrets["data_go_kr_key"]
BASE_URL = "http://apis.data.go.kr/1230000/as/ScsbidInfoService"
MASTER_FILE = "HIST_BID_MASTER_5Y.csv"
PRICE_FILE = "PREP_PRICE_DETAIL.csv"

st.set_page_config(page_title="조달청 스마트 분석 시스템", layout="wide")

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

# --- 2. 업데이트 로직 ---
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
    
    if new_master_rows:
        new_master_df = pd.DataFrame(new_master_rows)
        final_master = pd.concat([existing_master, new_master_df]).drop_duplicates(subset=['bidNtceNo', 'bidNtceOrd'])
        final_master.to_csv(MASTER_FILE, index=False, encoding="utf-8-sig")
        st.success(f"✅ 낙찰 마스터 업데이트 완료")

    # B. 예비가격 상세 업데이트
    updated_master = pd.read_csv(MASTER_FILE) if os.path.exists(MASTER_FILE) else pd.DataFrame()
    if not updated_master.empty:
        st.info("🚀 2단계: 누락된 예비가격 상세 정보를 가져옵니다...")
        existing_price = pd.read_csv(PRICE_FILE) if os.path.exists(PRICE_FILE) else pd.DataFrame()
        collected_bids = existing_price['bidNtceNo'].unique().astype(str).tolist() if not existing_price.empty else []
        target_bids = [b for b in updated_master['bidNtceNo'].unique().astype(str) if b not in collected_bids]

        new_price_rows = []
        if target_bids:
            progress = st.progress(0)
            for i, bid_no in enumerate(target_bids):
                data = get_api_data("/getOpengResultListInfoCnstwkPreparPcDetail", {"inqryDiv": "2", "bidNtceNo": bid_no})
                if data and "response" in data:
                    items_root = data["response"].get("body", {}).get("items", "")
                    if items_root:
                        items = items_root if isinstance(items_root, list) else items_root.get("item", [])
                        if isinstance(items, dict): items = [items]
                        new_price_rows.extend(items)
                progress.progress((i + 1) / len(target_bids))
                time.sleep(0.05)
            
            if new_price_rows:
                new_price_df = pd.DataFrame(new_price_rows)
                final_price = pd.concat([existing_price, new_price_df]).drop_duplicates()
                final_price.to_csv(PRICE_FILE, index=False, encoding="utf-8-sig")
                st.success("🎉 예비가격 수집 완료!")

# --- 3. 메인 화면 UI ---
st.sidebar.title("🔍 분석 메뉴")
menu = st.sidebar.radio("원하는 기능을 선택하세요", ["🏠 낙찰 현황 대시보드", "🎯 예가 상세 분석"])

if st.sidebar.button("🔄 전체 데이터 업데이트"):
    run_integrated_update()
    st.rerun()

if menu == "🏠 낙찰 현황 대시보드":
    st.header("🏗️ 대전 지역 상하수도설비 낙찰 현황")
    if os.path.exists(MASTER_FILE):
        df = pd.read_csv(MASTER_FILE)
        st.metric("총 수집 공고", f"{len(df):,} 건")
        st.dataframe(df.sort_values('rlOpengDt', ascending=False), width=1500, hide_index=True)
    else:
        st.warning("데이터가 없습니다. 업데이트를 먼저 진행해주세요.")

elif menu == "🎯 예가 상세 분석":
    st.header("🎯 예비가격 상세 분석 (기초예가 분포)")
    if os.path.exists(PRICE_FILE) and os.path.exists(MASTER_FILE):
        price_df = pd.read_csv(PRICE_FILE)
        master_df = pd.read_csv(MASTER_FILE)
        
        if not price_df.empty and not master_df.empty:
            # 데이터 정제: 공고번호 문자열 통일 및 날짜 병합
            price_df['bidNtceNo'] = price_df['bidNtceNo'].astype(str)
            master_df['bidNtceNo'] = master_df['bidNtceNo'].astype(str)
            
            # 마스터에서 필요한 정보(공고명, 개찰일)만 추출하여 중복 제거 후 결합
            meta_df = master_df[['bidNtceNo', 'bidNtceNm', 'rlOpengDt']].drop_duplicates('bidNtceNo')
            meta_df['rlOpengDt'] = pd.to_datetime(meta_df['rlOpengDt'])
            
            # 최신 날짜순으로 정렬된 목록 생성
            sorted_meta = meta_df.sort_values('rlOpengDt', ascending=False)
            
            # [공고번호] 공고명 형식의 리스트 생성 (분석 대상이 있는 것만)
            available_bids = price_df['bidNtceNo'].unique()
            display_list = []
            bid_map = {} # 사용자가 선택한 문구에서 공고번호를 역추적하기 위한 딕셔너리
            
            for idx, row in sorted_meta.iterrows():
                if row['bidNtceNo'] in available_bids:
                    label = f"[{row['bidNtceNo']}] {row['bidNtceNm']}"
                    display_list.append(label)
                    bid_map[label] = row['bidNtceNo']
            
            # 선택 상자 구성
            selected_label = st.selectbox("분석할 공고를 선택하세요 (최신순)", display_list)
            
            if selected_label:
                target_bid = bid_map[selected_label]
                detail = price_df[price_df['bidNtceNo'] == target_bid].copy()
                
                detail['bsisPlnprc'] = pd.to_numeric(detail['bsisPlnprc'], errors='coerce')
                detail['drwtNum'] = pd.to_numeric(detail['drwtNum'], errors='coerce')

                if not detail['bsisPlnprc'].isnull().all():
                    st.subheader(f"📍 {selected_label}")
                    
                    fig = px.bar(detail.sort_values('bsisPlnprc'), 
                                 x='bsisPlnprc', y='drwtNum', color='drwtYn',
                                 color_discrete_map={'Y': '#EF553B', 'N': '#636EFA'},
                                 title=f"예비가격 분포 (Y: 추첨됨)",
                                 labels={'bsisPlnprc':'기초예비가격', 'drwtNum':'추첨횟수', 'drwtYn':'추첨여부'})
                    
                    st.plotly_chart(fig, use_container_width=True)
                    st.dataframe(detail[['bsisPlnprc', 'drwtNum', 'drwtYn']].sort_values('bsisPlnprc'), 
                                 width=1500, hide_index=True)
        else:
            st.warning("분석할 데이터가 부족합니다.")
    else:
        st.error("데이터 파일이 없습니다. 전체 업데이트를 실행해 주세요.")
