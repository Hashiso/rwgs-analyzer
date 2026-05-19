import streamlit as st
import pandas as pd
import numpy as np
import altair as alt
import os
import hashlib
import jwt
import datetime
import extra_streamlit_components as stx

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
# 🔐 1. 独自ユーザー認証 & クッキー自動ログイン
# ==========================================
USER_DB_FILE = "users.csv"
COOKIE_KEY = "rwgs_auth_token"
JWT_SECRET = "ku_rwgs_secret_key_2026"

# 🌟 安全なクッキーマネージャーの初期化 (エラーの原因だった st.cache_resource を廃止)
if "cookie_manager" not in st.session_state:
    st.session_state["cookie_manager"] = stx.CookieManager(key="rwgs_cookie_manager")
cookie_manager = st.session_state["cookie_manager"]

def make_hashes(password):
    return hashlib.sha256(str.encode(password)).hexdigest()

def check_hashes(password, hashed_password):
    return make_hashes(password) == hashed_password

def load_users():
    if os.path.exists(USER_DB_FILE):
        return pd.read_csv(USER_DB_FILE)
    return pd.DataFrame(columns=["username", "password_hash"])

def add_user(username, password):
    df = load_users()
    if username in df["username"].values:
        return False
    hashed_pwd = make_hashes(password)
    new_user = pd.DataFrame([[username, hashed_pwd]], columns=["username", "password_hash"])
    df = pd.concat([df, new_user], ignore_index=True)
    df.to_csv(USER_DB_FILE, index=False)
    return True

def login_user(username, password):
    df = load_users()
    user_rows = df[df["username"] == username]
    if not user_rows.empty:
        return check_hashes(password, user_rows.iloc[0]["password_hash"])
    return False

def create_token(username):
    payload = {"username": username, "exp": datetime.datetime.utcnow() + datetime.timedelta(days=30)}
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")

def verify_token(token):
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return payload["username"]
    except:
        return None

if "authenticated" not in st.session_state:
    st.session_state["authenticated"] = False
if "username" not in st.session_state:
    st.session_state["username"] = ""

# 🔄 自動ログインチェック
if not st.session_state["authenticated"]:
    token = cookie_manager.get(COOKIE_KEY)
    if token:
        saved_user = verify_token(token)
        if saved_user:
            st.session_state["authenticated"] = True
            st.session_state["username"] = saved_user

def login_screen():
    st.markdown("<div class='auth-box'>", unsafe_allow_html=True)
    st.markdown("<h2 style='text-align: center;'>🔐 RWGS Analyzer Login</h2>", unsafe_allow_html=True)
    
    tab_login, tab_register = st.tabs(["🔑 ログイン", "📝 新規アカウント作成"])
    
    with tab_login:
        login_user_input = st.text_input("ユーザー名", key="login_user")
        login_pass_input = st.text_input("パスワード", type="password", key="login_pass")
        remember_me = st.checkbox("次回から自動ログイン（状態を保持）", value=True)
        
        if st.button("ログイン", use_container_width=True):
            if login_user(login_user_input, login_pass_input):
                st.session_state["authenticated"] = True
                st.session_state["username"] = login_user_input
                
                if remember_me:
                    token = create_token(login_user_input)
                    cookie_manager.set(COOKIE_KEY, token, max_age=30*24*60*60)
                st.rerun()
            else:
                st.error("ユーザー名またはパスワードが正しくありません。")
                
    with tab_register:
        reg_user_input = st.text_input("希望するユーザー名", key="reg_user")
        reg_pass_input = st.text_input("パスワードを設定", type="password", key="reg_pass")
        reg_pass_confirm = st.text_input("パスワード（確認用）", type="password", key="reg_pass_conf")
        
        if st.button("新規アカウントを登録", use_container_width=True):
            if not reg_user_input.strip() or not reg_pass_input.strip():
                st.error("ユーザー名とパスワードを入力してください。")
            elif reg_pass_input != reg_pass_confirm:
                st.error("パスワードが一致しません。")
            else:
                if add_user(reg_user_input.strip(), reg_pass_input.strip()):
                    st.success("アカウントを作成しました！「ログイン」タブからログインしてください。")
                else:
                    st.error("このユーザー名はすでに使われています。")
    st.markdown("</div>", unsafe_allow_html=True)

def logout():
    st.session_state["authenticated"] = False
    st.session_state["username"] = ""
    cookie_manager.delete(COOKIE_KEY)
    st.rerun()

# 認証チェック
if not st.session_state["authenticated"]:
    login_screen()
    st.stop()

current_user = st.session_state["username"]

# ==========================================
# 分析ロジック群（数理統計アルゴリズム）
# ==========================================
def get_smart_stage(elapsed_min):
    if pd.isna(elapsed_min) or elapsed_min < 0: return np.nan, "待機"
    schedule = [(0, 20, 100), (20, 40, 200), (40, 60, 300), (60, 80, 350), (80, 103, 400), (103, 126, 450), (126, 149, 500), (149, 172, 550), (172, 195, 600)]
    for start, end, temp in schedule:
        if start <= elapsed_min < end:
            if elapsed_min < start + 8: return np.nan, f"昇温/安定化 (→{temp}℃)"
            else: return temp, f"維持 ({temp}℃)"
    return np.nan, "終了"

def auto_optimize_timeline(df):
    gc_interval = df['Elapsed_min'].diff().median()
    if pd.isna(gc_interval) or gc_interval <= 0: gc_interval = 2.45
    best_offset, min_score = 0.0, float('inf')
    
    for offset in np.arange(0, 40, 0.5):
        stages = (df['Elapsed_min'] - offset).apply(lambda x: get_smart_stage(x)[0])
        df_temp = pd.DataFrame({'Temp': stages, 'CO': df['CO_Conc']}).dropna()
        if len(df_temp['Temp'].unique()) < 8: continue
        score = df_temp.groupby('Temp')['CO'].std().fillna(0).sum()
        means = df_temp.groupby('Temp')['CO'].mean()
        if means.empty or means.idxmax() != 600: score += 10000
        if score < min_score: min_score, best_offset = score, offset
    return best_offset, gc_interval

def calc_metrics(row):
    co_conc, ch4_conc, co2_conc = row.get('CO_Conc', 0), row.get('CH4_Conc', 0), row.get('CO2_Conc', 0)
    total_c = co2_conc + co_conc + ch4_conc
    conversion = (co_conc + ch4_conc) / total_c * 100 if total_c > 0 else 0
    prod_c = co_conc + ch4_conc
    return pd.Series([total_c, conversion, co_conc / prod_c * 100 if prod_c > 0 else 0, ch4_conc / prod_c * 100 if prod_c > 0 else 0], 
                     index=['Total_Carbon(%)', 'CO2_Conversion(%)', 'CO_Selectivity(%)', 'CH4_Selectivity(%)'])

# ==========================================
# 📊 UI 構築
# ==========================================
st.markdown("## 🧪 RWGS Catalyst Analytics")
st.markdown("<p style='color: #666; font-size: 1.1rem; margin-top: -10px;'>Fully Automated Data Pipeline</p>", unsafe_allow_html=True)

with st.sidebar:
    st.markdown(f"👤 **ログイン中:** `{current_user}`")
    if st.button("ログアウト", use_container_width=True):
        logout()
    
    st.markdown("---")
    st.markdown("### ⚙️ あなたのキャリブレーション設定")
    
    USER_CALIB_FILE = f"calib_settings_{current_user}.csv"
    DEFAULT_CALIB = pd.DataFrame({
        'Gas': ['CO2', 'CO', 'CH4'],
        'Slope': [6.473e-07, 1.843e-06, 1.000e-06],
        'Intercept': [-0.0387, 0.1918, 0.0]
    })
    
    if os.path.exists(USER_CALIB_FILE):
        user_calib_df = pd.read_csv(USER_CALIB_FILE)
    else:
        user_calib_df = DEFAULT_CALIB.copy()
    
    edited_calib_df = st.data_editor(user_calib_df, num_rows="dynamic", hide_index=True, use_container_width=True)
    calib_dict = edited_calib_df.set_index('Gas').to_dict(orient='index')
    
    if st.button("💾 この係数を自分の設定として保存"):
        edited_calib_df.to_csv(USER_CALIB_FILE, index=False)
        st.success(f"保存しました！次回ログイン時も自動でこの係数が読み込まれます。")

st.markdown("#### 📂 1. Upload Raw Data")
col1, col2 = st.columns(2)
with col1: file_ch1 = st.file_uploader("Channel 1 (.Area)", type=['Area', 'txt', 'csv'])
with col2: file_ch2 = st.file_uploader("Channel 2 (.Area)", type=['Area', 'txt', 'csv'])

if file_ch1 and file_ch2:
    st.markdown("#### 🚀 2. Execute Analysis")
    if st.button("全自動で解析を実行", type="primary", use_container_width=True):
        with st.spinner("タイムラインの同期および定常状態の抽出を実行中..."):
            
            df_ch1 = pd.read_csv(file_ch1, sep='\t', skiprows=2, encoding='shift_jis')
            df_ch2 = pd.read_csv(file_ch2, sep='\t', skiprows=2, encoding='shift_jis')
            df_ch1.columns, df_ch2.columns = df_ch1.columns.str.strip(), df_ch2.columns.str.strip()
            
            cols_to_drop = [c for c in df_ch2.columns if c in df_ch1.columns]
            df = pd.concat([df_ch1.reset_index(drop=True), df_ch2.drop(columns=cols_to_drop).reset_index(drop=True)], axis=1)
            for col in ['CO2', 'CO', 'CH4', 'N2']:
                if col in df.columns: df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

            df['Datetime'] = pd.to_datetime(df['Date'].astype(str).str.strip() + ' ' + df['Time'].astype(str).str.strip(), errors='coerce')
            if df['Datetime'].isna().sum() < len(df) * 0.5:
                start_time = df['Datetime'].dropna().iloc[0]
                df['Elapsed_min'] = (df['Datetime'] - start_time).dt.total_seconds() / 60.0
                df['Elapsed_min'] = df['Elapsed_min'].fillna(pd.Series(range(len(df))) * 2.45)
            else:
                df['Elapsed_min'] = pd.Series(range(len(df))) * 2.45

            df['CO2_Conc'] = (df['CO2'] * calib_dict.get('CO2', {}).get('Slope', 1) + calib_dict.get('CO2', {}).get('Intercept', 0)).clip(lower=0)
            df['CO_Conc'] = (df['CO'] * calib_dict.get('CO', {}).get('Slope', 1) + calib_dict.get('CO', {}).get('Intercept', 0)).clip(lower=0)
            df['CH4_Conc'] = (df['CH4'] * calib_dict.get('CH4', {}).get('Slope', 1) + calib_dict.get('CH4', {}).get('Intercept', 0)).clip(lower=0)
            
            best_offset, gc_interval = auto_optimize_timeline(df)
            df['Furnace_Time_min'] = df['Elapsed_min'] - best_offset
            stage_info = df['Furnace_Time_min'].apply(get_smart_stage)
            df['Temperature'], df['Stage'] = [x[0] for x in stage_info], [x[1] for x in stage_info]
            
            df = pd.concat([df, df.apply(calc_metrics, axis=1)], axis=1)
            df['Status'] = '❌ 昇温中・安定化待ち (除外)'
            df.loc[df['Temperature'].notna(), 'Status'] = '⚠️ プラトー前半 (除外)'
            
            steady_indices = df.dropna(subset=['Temperature']).groupby('Temperature').tail(3).index
            df.loc[steady_indices, 'Status'] = '✅ 定常状態 (抽出対象)'

            final_result = df.loc[steady_indices].groupby('Temperature').mean(numeric_only=True).reset_index()
            final_result = final_result[['Temperature', 'Total_Carbon(%)', 'CO2_Conc', 'CO_Conc', 'CH4_Conc', 'CO2_Conversion(%)', 'CO_Selectivity(%)', 'CH4_Selectivity(%)']]

        st.markdown("---")
        st.success(f"🎯 **タイムライン自動補正 完了:** \nデータ構造を解析し、**測定開始から約 {best_offset:.1f} 分の遅れ** を自動検知してスケジュールを同期しました。")

        if not final_result.empty:
            max_temp = final_result['Temperature'].max()
            m1, m2, m3 = st.columns(3)
            m1.metric(label=f"最大転化率 (@{int(max_temp)}℃)", value=f"{final_result.loc[final_result['Temperature'] == max_temp, 'CO2_Conversion(%)'].values[0]:.2f} %")
            m2.metric(label="検出されたGC測定間隔", value=f"{gc_interval:.2f} min")
            m3.metric(label="平均炭素バランス (C-Balance)", value=f"{final_result['Total_Carbon(%)'].mean():.2f} %")

        tab1, tab2 = st.tabs(["📈 グラフ分析", "📋 定常状態データ一覧"])
        with tab1:
            st.markdown("<br>", unsafe_allow_html=True)
            chart = alt.Chart(df).mark_circle(size=80, opacity=0.9).encode(
                x=alt.X('Furnace_Time_min:Q', title='電気炉稼働時間 (分)', axis=alt.Axis(grid=False)),
                y=alt.Y('CO_Conc:Q', title='CO 濃度 (%)', axis=alt.Axis(gridColor='#f0f0f0')),
                color=alt.Color('Status:N', title='データ判定', scale=alt.Scale(domain=['✅ 定常状態 (抽出対象)', '⚠️ プラトー前半 (除外)', '❌ 昇温中・安定化待ち (除外)'], range=['#10b981', '#f59e0b', '#ef4444'])),
                tooltip=['Furnace_Time_min', 'Temperature', 'CO_Conc', 'CO2_Conversion(%)', 'Status']
            ).properties(height=400).interactive()
            st.altair_chart(chart, use_container_width=True)

        with tab2:
            st.markdown("<br>", unsafe_allow_html=True)
            st.dataframe(final_result.style.format("{:.3f}"), use_container_width=True)
            csv = final_result.to_csv(index=False).encode('shift_jis')
            st.download_button(label="📥 結果をCSVでダウンロード", data=csv, file_name=f'RWGS_Result_{current_user}.csv', mime='text/csv')
