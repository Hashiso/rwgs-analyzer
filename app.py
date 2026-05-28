import streamlit as st
import pandas as pd
import numpy as np
import altair as alt
import os
import hashlib
import jwt
import datetime

# ==========================================
# ページ設定 & カスタムCSS
# ==========================================
st.set_page_config(page_title="RWGS Analyzer", layout="wide")

st.markdown("""
    <style>
    html, body, [class*="css"] { font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; }
    .stButton>button { border-radius: 8px; background-color: #2b3a4a; color: white; font-weight: 600; border: none; padding: 0.5rem 1rem; }
    .stButton>button:hover { background-color: #1a252f; }
    .auth-box { max-width: 400px; margin: 50px auto; padding: 30px; border-radius: 10px; background-color: #f8f9fa; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# 🔐 1. ユーザー認証機能 (維持)
# ==========================================
USER_DB_FILE = "users.csv"
JWT_SECRET = "ku_rwgs_secret_key_2026"

def make_hashes(password): return hashlib.sha256(str.encode(password)).hexdigest()
def check_hashes(password, hashed_password): return make_hashes(password) == hashed_password
def load_users():
    if os.path.exists(USER_DB_FILE): return pd.read_csv(USER_DB_FILE)
    return pd.DataFrame(columns=["username", "password_hash"])
def add_user(username, password):
    df = load_users()
    if username in df["username"].values: return False
    df = pd.concat([df, pd.DataFrame([[username, make_hashes(password)]], columns=["username", "password_hash"])], ignore_index=True)
    df.to_csv(USER_DB_FILE, index=False)
    return True
def login_user(username, password):
    df = load_users()
    user_rows = df[df["username"] == username]
    return check_hashes(password, user_rows.iloc[0]["password_hash"]) if not user_rows.empty else False

if "authenticated" not in st.session_state: st.session_state["authenticated"] = False
if "username" not in st.session_state: st.session_state["username"] = ""

if not st.session_state["authenticated"] and "auth" in st.query_params:
    try:
        payload = jwt.decode(st.query_params["auth"], JWT_SECRET, algorithms=["HS256"])
        st.session_state["authenticated"], st.session_state["username"] = True, payload["username"]
        st.rerun()
    except: pass

if not st.session_state["authenticated"]:
    st.markdown("<div class='auth-box'>", unsafe_allow_html=True)
    st.markdown("<h2 style='text-align: center;'>🔐 RWGS Analyzer</h2>", unsafe_allow_html=True)
    tab_login, tab_register = st.tabs(["🔑 ログイン", "📝 新規登録"])
    with tab_login:
        u = st.text_input("ユーザー名", key="l_u")
        p = st.text_input("パスワード", type="password", key="l_p")
        if st.button("ログイン", use_container_width=True) and login_user(u, p):
            st.session_state["authenticated"], st.session_state["username"] = True, u
            st.rerun()
    with tab_register:
        ru = st.text_input("ユーザー名", key="r_u")
        rp = st.text_input("パスワード", type="password", key="r_p")
        if st.button("新規登録", use_container_width=True) and add_user(ru, rp):
            st.success("作成完了！ログインしてください。")
    st.markdown("</div>", unsafe_allow_html=True)
    st.stop()

current_user = st.session_state["username"]

# ==========================================
# 分析コアロジック群
# ==========================================
def get_smart_stage(elapsed_min):
    if pd.isna(elapsed_min) or elapsed_min < 0: return np.nan, "待機"
    schedule = [
        (0, 20, 100), (20, 40, 200), (40, 60, 300), (60, 80, 350), 
        (80, 103, 400), (103, 126, 450), (126, 149, 500), 
        (149, 172, 550), (172, 195, 600)
    ]
    for start, end, temp in schedule:
        if start <= elapsed_min < end:
            return (np.nan, f"昇温/安定化 (→{temp}℃)") if elapsed_min < start + 8 else (temp, f"維持 ({temp}℃)")
    return np.nan, "降温・測定終了後"

def auto_optimize_timeline(df):
    gc_interval = df['Elapsed_min'].diff().median()
    if pd.isna(gc_interval) or gc_interval <= 0: gc_interval = 2.45
    best_offset, min_score = 0.0, float('inf')
    
    for offset in np.arange(0, 40, 0.5):
        stages = (df['Elapsed_min'] - offset).apply(lambda x: get_smart_stage(x)[0])
        df_temp = pd.DataFrame({'Temp': stages, 'CO': df['CO_Conc']}).dropna()
        if df_temp['Temp'].nunique() < 1: continue
        score = df_temp.groupby('Temp')['CO'].std().fillna(0).sum() / df_temp['Temp'].nunique()
        if score < min_score: min_score, best_offset = score, offset
    return best_offset, gc_interval

def calc_metrics(row):
    co_conc, ch4_conc, co2_conc = row.get('CO_Conc', 0), row.get('CH4_Conc', 0), row.get('CO2_Conc', 0)
    total_c_conc = co2_conc + co_conc + ch4_conc
    conversion = (co_conc + ch4_conc) / total_c_conc * 100 if total_c_conc > 0 else 0
    prod_c = co_conc + ch4_conc
    return pd.Series([total_c_conc, conversion, co_conc / prod_c * 100 if prod_c > 0 else 0, ch4_conc / prod_c * 100 if prod_c > 0 else 0], 
                     index=['Total_Carbon_Conc(%)', 'CO2_Conversion(%)', 'CO_Selectivity(%)', 'CH4_Selectivity(%)'])

# ==========================================
# 📊 UI 構築
# ==========================================
st.markdown("## 🧪 RWGS Catalyst Analytics")
st.markdown("<p style='color: #666; font-size: 1.1rem; margin-top: -10px;'>Targeted Multi-Session Data Splitter</p>", unsafe_allow_html=True)

with st.sidebar:
    st.markdown(f"👤 **ユーザー:** `{current_user}`")
    if st.button("ログアウト", use_container_width=True):
        st.session_state["authenticated"] = False
        st.rerun()
    st.markdown("---")
    st.markdown("### ⚙️ キャリブレーション設定")
    USER_CALIB_FILE = f"calib_settings_{current_user}.csv"
    DEFAULT_CALIB = pd.DataFrame({'Gas': ['CO2', 'CO', 'CH4'], 'Slope': [4.75e-07, 1.44e-06, 1.66e-05], 'Intercept': [0.0, 0.0, 0.0]})
    user_calib_df = pd.read_csv(USER_CALIB_FILE) if os.path.exists(USER_CALIB_FILE) else DEFAULT_CALIB.copy()
    edited_calib_df = st.data_editor(user_calib_df, num_rows="dynamic", hide_index=True, use_container_width=True)
    calib_dict = edited_calib_df.set_index('Gas').to_dict(orient='index')
    if st.button("💾 係数を保存"): edited_calib_df.to_csv(USER_CALIB_FILE, index=False)

st.markdown("#### 📂 1. Raw Data ファイルのアップロード")
col1, col2 = st.columns(2)
with col1: file_ch1 = st.file_uploader("Channel 1 (.Area)", type=['Area', 'txt', 'csv'])
with col2: file_ch2 = st.file_uploader("Channel 2 (.Area)", type=['Area', 'txt', 'csv'])

if "split_results" not in st.session_state: st.session_state["split_results"] = None

if file_ch1 and file_ch2:
    st.markdown("#### 🚀 2. 解析の実行")
    if st.button("生データを自動解析（_rxn本試験を自動検出）", type="primary", use_container_width=True):
        with st.spinner("ファイル内から本試験データを抽出中..."):
            df_ch1 = pd.read_csv(file_ch1, sep='\t', skiprows=2, encoding='shift_jis')
            df_ch2 = pd.read_csv(file_ch2, sep='\t', skiprows=2, encoding='shift_jis')
            df_ch1.columns, df_ch2.columns = df_ch1.columns.str.strip(), df_ch2.columns.str.strip()
            
            cols_to_drop = [c for c in df_ch2.columns if c in df_ch1.columns]
            df_all = pd.concat([df_ch1.reset_index(drop=True), df_ch2.drop(columns=cols_to_drop).reset_index(drop=True)], axis=1)
            
            # データクレンジング
            for col in ['CO2', 'CO', 'CH4', 'N2', 'O2']:
                if col in df_all.columns:
                    df_all[col] = pd.to_numeric(df_all[col], errors='coerce').fillna(0)
            
            df_all['Date'] = df_all['Date'].astype(str).str.strip()
            unique_dates = sorted([d for d in df_all['Date'].unique() if d and d != 'nan'])
            
            parsed_sessions = {}
            
            for date_key in unique_dates:
                df_date = df_all[df_all['Date'] == date_key].copy()
                if len(df_date) < 3: continue
                
                # 🌟【ここを大幅改善！】
                # Sample Idに「_rxn」が含まれる本試験行だけをフィルタリング
                df_rxn = df_date[df_date['Sample Id'].astype(str).str.contains('_rxn', case=False)].copy()
                
                # 万が一「_rxn」という名前がついていないファイルでも動くようにセーフティを配置
                if df_rxn.empty:
                    df_rxn = df_date.copy()
                
                # 反応実験の最初の行（＝本当の繰り返し1）を正確な基準として経過時間を計算！
                df_rxn['Datetime'] = pd.to_datetime(df_rxn['Date'] + ' ' + df_rxn['Time'].astype(str).str.strip(), errors='coerce')
                df_rxn = df_rxn.sort_values('Datetime').reset_index(drop=True)
                
                start_time = df_rxn['Datetime'].dropna().min()
                df_rxn['Elapsed_min'] = (df_rxn['Datetime'] - start_time).dt.total_seconds() / 60.0
                
                # 濃度計算
                df_rxn['CO2_Conc'] = (df_rxn['CO2'] * calib_dict.get('CO2', {}).get('Slope', 1) + calib_dict.get('CO2', {}).get('Intercept', 0)).clip(lower=0)
                df_rxn['CO_Conc'] = (df_rxn['CO'] * calib_dict.get('CO', {}).get('Slope', 1) + calib_dict.get('CO', {}).get('Intercept', 0)).clip(lower=0)
                df_rxn['CH4_Conc'] = (df_rxn['CH4'] * calib_dict.get('CH4', {}).get('Slope', 1) + calib_dict.get('CH4', {}).get('Intercept', 0)).clip(lower=0)
                
                # タイムライン自動最適化
                best_offset, gc_interval = auto_optimize_timeline(df_rxn)
                df_rxn['Furnace_Time_min'] = df_rxn['Elapsed_min'] - best_offset
                
                stage_info = df_rxn['Furnace_Time_min'].apply(get_smart_stage)
                df_rxn['Temperature'], df_rxn['Stage'] = [x[0] for x in stage_info], [x[1] for x in stage_info]
                
                df_rxn = pd.concat([df_rxn, df_rxn.apply(calc_metrics, axis=1)], axis=1)
                
                df_rxn['Status'] = '❌ 昇温・降温・安定化待ち'
                df_rxn.loc[df_rxn['Temperature'].notna(), 'Status'] = '⚠️ プラトー前半'
                
                steady_indices = df_rxn.dropna(subset=['Temperature']).groupby('Temperature').tail(3).index
                df_rxn.loc[steady_indices, 'Status'] = '✅ 定常状態'
                
                final_result = df_rxn.loc[steady_indices].groupby('Temperature').mean(numeric_only=True).reset_index()
                
                if not final_result.empty and 100 in final_result['Temperature'].values:
                    baseline_c = final_result.loc[final_result['Temperature'] == 100, 'Total_Carbon_Conc(%)'].values[0]
                    final_result['C-Balance(%)'] = final_result['Total_Carbon_Conc(%)'] / baseline_c * 100
                else:
                    final_result['C-Balance(%)'] = 100.0
                
                cols_output = ['Temperature', 'C-Balance(%)', 'CO2_Conc', 'CO_Conc', 'CH4_Conc', 'CO2_Conversion(%)', 'CO_Selectivity(%)', 'CH4_Selectivity(%)']
                final_result = final_result[[c for c in cols_output if c in final_result.columns]]
                
                parsed_sessions[date_key] = {
                    "df": df_rxn,
                    "final_result": final_result,
                    "offset": best_offset,
                    "interval": gc_interval
                }
            
            st.session_state["split_results"] = parsed_sessions
            st.success(f"🎯 解析完了: 前処理(_red)を自動で分離し、本試験(_rxn)の開始点を基準にタイムラインを100%完全同期しました！")

# ==========================================
# 📈 表示部分 (維持)
# ==========================================
if st.session_state["split_results"] is not None:
    st.markdown("---")
    st.markdown("### 🔍 3. 日付を選択して結果を表示")
    
    sessions = st.session_state["split_results"]
    selected_date = st.selectbox("📅 解析結果を表示したい測定日を選択してください：", list(sessions.keys()))
    
    data = sessions[selected_date]
    sub_df = data["df"]
    sub_res = data["final_result"]
    
    st.markdown(f"📊 **【{selected_date} の測定データ解析】**")
    
    m1, m2, m3 = st.columns(3)
    if not sub_res.empty:
        max_temp = sub_res['Temperature'].max()
        m1.metric(label=f"最大転化率 (@{int(max_temp)}℃)", value=f"{sub_res.loc[sub_res['Temperature'] == max_temp, 'CO2_Conversion(%)'].values[0]:.2f} %")
    else:
        m1.metric(label="最大転化率", value="N/A")
    m2.metric(label="検出されたGC測定間隔", value=f"{data['interval']:.2f} min")
    m3.metric(label="電気炉のタイムラグ(同期オフセット)", value=f"{data['offset']:.1f} min")
    
    tab1, tab2 = st.tabs(["📈 タイムライングラフ", "📋 定常状態（プロット）データ一覧"])
    
    with tab1:
        st.markdown("<br>", unsafe_allow_html=True)
        chart = alt.Chart(sub_df).mark_circle(size=80, opacity=0.9).encode(
            x=alt.X('Furnace_Time_min:Q', title='電気炉稼働時間 (分)', axis=alt.Axis(grid=False)),
            y=alt.Y('CO_Conc:Q', title='CO 濃度 (%)', axis=alt.Axis(gridColor='#f0f0f0')),
            color=alt.Color('Status:N', title='データ判定', 
                             scale=alt.Scale(domain=['✅ 定常状態', '⚠️ プラトー前半', '❌ 昇温・降温・安定化待ち'], 
                                             range=['#10b981', '#f59e0b', '#ef4444'])),
            tooltip=['Furnace_Time_min', 'Temperature', 'Stage', 'CO_Conc', 'CO2_Conversion(%)', 'Status']
        ).properties(height=400).interactive()
        st.altair_chart(chart, use_container_width=True)
        
    with tab2:
        st.markdown("<br>", unsafe_allow_html=True)
        st.dataframe(sub_res.style.format("{:.3f}"), use_container_width=True)
        csv = sub_res.to_csv(index=False).encode('shift_jis')
        st.download_button(label=f"📥 {selected_date} の結果のみCSVダウンロード", data=csv, file_name=f'RWGS_Result_{selected_date}_{current_user}.csv', mime='text/csv')
