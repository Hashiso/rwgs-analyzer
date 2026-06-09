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
# 🔐 1. ユーザー認証機能
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

def update_password(username, old_password, new_password):
    df = load_users()
    user_idx = df.index[df['username'] == username].tolist()
    if not user_idx: return False, "ユーザーが存在しません。"
    idx = user_idx[0]
    if check_hashes(old_password, df.at[idx, 'password_hash']):
        df.at[idx, 'password_hash'] = make_hashes(new_password)
        df.to_csv(USER_DB_FILE, index=False)
        return True, "パスワードを更新しました。"
    return False, "現在のパスワードが間違っています。"

if "authenticated" not in st.session_state: st.session_state["authenticated"] = False
if "username" not in st.session_state: st.session_state["username"] = ""

if not st.session_state["authenticated"]:
    st.markdown("<div class='auth-box'>", unsafe_allow_html=True)
    st.markdown("<h2 style='text-align: center;'>🔐 RWGS Analyzer</h2>", unsafe_allow_html=True)
    tab_login, tab_register, tab_reset = st.tabs(["🔑 ログイン", "📝 新規登録", "🔄 パスワード変更"])
    with tab_login:
        u = st.text_input("ユーザー名", key="l_u")
        p = st.text_input("パスワード", type="password", key="l_p")
        if st.button("ログイン", use_container_width=True):
            if login_user(u, p):
                st.session_state["authenticated"], st.session_state["username"] = True, u
                st.rerun()
            else: st.error("ユーザー名またはパスワードが違います。")
    with tab_register:
        ru = st.text_input("希望ユーザー名", key="r_u")
        rp = st.text_input("パスワード", type="password", key="r_p")
        if st.button("新規登録", use_container_width=True) and add_user(ru, rp): st.success("作成完了！ログインしてください。")
    with tab_reset:
        reset_u = st.text_input("ユーザー名", key="res_u")
        reset_op = st.text_input("現在のパスワード", type="password", key="res_op")
        reset_np = st.text_input("新しいパスワード", type="password", key="res_np")
        if st.button("パスワード変更", use_container_width=True):
            ok, msg = update_password(reset_u, reset_op, reset_np)
            if ok: st.success(msg)
            else: st.error(msg)
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

# ==========================================
# 📊 UI 構築
# ==========================================
st.markdown("## 🧪 RWGS Catalyst Analytics")
st.markdown("<p style='color: #666; font-size: 1.1rem; margin-top: -10px;'>Single Session Pipeline (Robust Version)</p>", unsafe_allow_html=True)

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
    edited_calib_df = st.data_editor(
        user_calib_df, num_rows="dynamic", hide_index=True, use_container_width=True,
        column_config={"Slope": st.column_config.NumberColumn("Slope", format="%.3e"), "Intercept": st.column_config.NumberColumn("Intercept", format="%.4f")}
    )
    calib_dict = edited_calib_df.set_index('Gas').to_dict(orient='index')
    if st.button("💾 係数を保存"): edited_calib_df.to_csv(USER_CALIB_FILE, index=False)

st.markdown("#### 📂 1. Raw Data ファイルのアップロード")
col1, col2 = st.columns(2)
with col1: file_ch1 = st.file_uploader("Channel 1 (.Area)", type=['Area', 'txt', 'csv'])
with col2: file_ch2 = st.file_uploader("Channel 2 (.Area)", type=['Area', 'txt', 'csv'])

if file_ch1 and file_ch2:
    st.markdown("#### 🚀 2. 解析の実行")
    if st.button("全自動で解析を実行", type="primary", use_container_width=True):
        with st.spinner("データを処理しています..."):
            
            df_ch1 = pd.read_csv(file_ch1, sep='\t', skiprows=2, encoding='shift_jis')
            df_ch2 = pd.read_csv(file_ch2, sep='\t', skiprows=2, encoding='shift_jis')
            df_ch1.columns, df_ch2.columns = df_ch1.columns.str.strip(), df_ch2.columns.str.strip()
            
            cols_to_drop = [c for c in df_ch2.columns if c in df_ch1.columns]
            df = pd.concat([df_ch1.reset_index(drop=True), df_ch2.drop(columns=cols_to_drop).reset_index(drop=True)], axis=1)
            
            df = df.dropna(subset=['Date', 'Time'])
            
            for col in ['CO2', 'CO', 'CH4', 'N2', 'O2']:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
                    if col in ['O2', 'N2']:
                        df.loc[df[col] > 50000000, col] = 0

            df['Datetime'] = pd.to_datetime(df['Date'].astype(str).str.strip() + ' ' + df['Time'].astype(str).str.strip(), errors='coerce')
            df = df.dropna(subset=['Datetime']).sort_values('Datetime').reset_index(drop=True)
            
            if not df.empty:
                start_time = df['Datetime'].min()
                df['Elapsed_min'] = (df['Datetime'] - start_time).dt.total_seconds() / 60.0
            else:
                st.error("有効なデータが見つかりませんでした。")
                st.stop()

            # 濃度計算
            df['CO2_Conc'] = (df['CO2'] * calib_dict.get('CO2', {}).get('Slope', 1) + calib_dict.get('CO2', {}).get('Intercept', 0)).clip(lower=0)
            df['CO_Conc'] = (df['CO'] * calib_dict.get('CO', {}).get('Slope', 1) + calib_dict.get('CO', {}).get('Intercept', 0)).clip(lower=0)
            df['CH4_Conc'] = (df['CH4'] * calib_dict.get('CH4', {}).get('Slope', 1) + calib_dict.get('CH4', {}).get('Intercept', 0)).clip(lower=0)
            df['Total_Carbon_Conc(%)'] = df['CO2_Conc'] + df['CO_Conc'] + df['CH4_Conc']
            
            # 🌟【重要：バグ対策】全データの中で「一番ガスがしっかり流れているポイント」の全炭素量を基準にする
            max_carbon_baseline = df['Total_Carbon_Conc(%)'].max()
            if max_carbon_baseline <= 0: max_carbon_baseline = 10.0 # 安全弁
            
            # 転化率・選択率の再計算（基準をmax_carbon_baselineに統一）
            df['CO2_Conversion(%)'] = (df['CO_Conc'] + df['CH4_Conc']) / max_carbon_baseline * 100
            
            prod_c = df['CO_Conc'] + df['CH4_Conc']
            df['CO_Selectivity(%)'] = np.where(
