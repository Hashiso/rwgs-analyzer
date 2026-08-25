import streamlit as st
import pandas as pd
import numpy as np
import altair as alt
import os
import hashlib
import jwt
import datetime
import re  # 🌟 ファイル名からの時間抽出のために追加

# ==========================================
# ページ設定
# ==========================================
st.set_page_config(page_title="RWGS Analyzer", layout="wide")
st.markdown("""
    <style>
    .stButton>button { border-radius: 8px; background-color: #2b3a4a; color: white; border: none; }
    .stButton>button:hover { background-color: #1a252f; }
    .auth-box { max-width: 400px; margin: 50px auto; padding: 30px; border-radius: 10px; background-color: #f8f9fa; }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# 🔐 1. ユーザー認証
# ==========================================
USER_DB_FILE = "users.csv"
JWT_SECRET = "ku_rwgs_secret_key_2026"

def make_hashes(p): return hashlib.sha256(str.encode(p)).hexdigest()
def check_hashes(p, hp): return make_hashes(p) == hp

def load_users():
    if os.path.exists(USER_DB_FILE): return pd.read_csv(USER_DB_FILE)
    return pd.DataFrame(columns=["username", "password_hash"])

def add_user(u, p):
    df = load_users()
    if u in df["username"].values: return False
    df = pd.concat([df, pd.DataFrame([[u, make_hashes(p)]], columns=["username", "password_hash"])], ignore_index=True)
    df.to_csv(USER_DB_FILE, index=False)
    return True

def login_user(u, p):
    df = load_users()
    user_rows = df[df["username"] == u]
    return check_hashes(p, user_rows.iloc[0]["password_hash"]) if not user_rows.empty else False

if "authenticated" not in st.session_state: st.session_state["authenticated"] = False
if "username" not in st.session_state: st.session_state["username"] = ""

if not st.session_state["authenticated"]:
    st.markdown("<div class='auth-box'><h2 style='text-align: center;'>🔐 RWGS Analyzer</h2></div>", unsafe_allow_html=True)
    tab1, tab2 = st.tabs(["🔑 ログイン", "📝 新規登録"])
    with tab1:
        u = st.text_input("ユーザー名", key="l_u")
        p = st.text_input("パスワード", type="password", key="l_p")
        if st.button("ログイン", use_container_width=True) and login_user(u, p):
            st.session_state["authenticated"], st.session_state["username"] = True, u
            st.rerun()
    with tab2:
        ru = st.text_input("希望ユーザー名", key="r_u")
        rp = st.text_input("パスワード", type="password", key="r_p")
        if st.button("新規登録", use_container_width=True) and add_user(ru, rp): st.success("作成完了！")
    st.stop()

current_user = st.session_state["username"]

# ==========================================
# 分析コアロジック
# ==========================================
def get_smart_stage(elapsed_min):
    if pd.isna(elapsed_min) or elapsed_min < 0: return np.nan, "待機"
    schedule = [(0,20,100), (20,40,200), (40,60,300), (60,80,350), (80,103,400), (103,126,450), (126,149,500), (149,172,550), (172,195,600)]
    for start, end, temp in schedule:
        if start <= elapsed_min < end:
            return (np.nan, f"昇温/安定化 (→{temp}℃)") if elapsed_min < start + 8 else (temp, f"維持 ({temp}℃)")
    return np.nan, "降温・測定終了"

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

with st.sidebar:
    st.markdown(f"👤 **ユーザー:** `{current_user}`")
    if st.button("ログアウト", use_container_width=True):
        st.session_state["authenticated"] = False
        st.rerun()
    st.markdown("---")
    
    st.markdown("### ✂️ データトリミング設定")
    ignore_mins = st.number_input("測定開始から除外する時間 (分)", min_value=0, max_value=200, value=0, step=5, help="GCの挙動が不安定な最初の時間を切り捨てます")
    
    st.markdown("---")
    st.markdown("### ⚙️ キャリブレーション設定")
    USER_CALIB_FILE = f"calib_settings_{current_user}.csv"
    
    DEFAULT_CALIB = pd.DataFrame({'Gas': ['CO2', 'CO', 'CH4'], 'Slope': [4.75e-07, 1.44e-06, 1.29e-06], 'Intercept': [0.0, 0.0, 0.0]})
    user_calib_df = pd.read_csv(USER_CALIB_FILE) if os.path.exists(USER_CALIB_FILE) else DEFAULT_CALIB.copy()
    edited_calib_df = st.data_editor(
        user_calib_df, num_rows="dynamic", hide_index=True, use_container_width=True,
        column_config={"Slope": st.column_config.NumberColumn("Slope", format="%.3e"), "Intercept": st.column_config.NumberColumn("Intercept", format="%.4f")}
    )
    calib_dict = edited_calib_df.set_index('Gas').to_dict(orient='index')
    if st.button("💾 係数を保存"): edited_calib_df.to_csv(USER_CALIB_FILE, index=False)

st.markdown("#### 📂 Raw Data ファイルのアップロード")
col1, col2 = st.columns(2)
with col1: file_ch1 = st.file_uploader("Channel 1 (.Area)", type=['Area', 'txt', 'csv'])
with col2: file_ch2 = st.file_uploader("Channel 2 (.Area)", type=['Area', 'txt', 'csv'])

if file_ch1 and file_ch2:
    if st.button("🚀 全自動で解析を実行", type="primary", use_container_width=True):
        with st.spinner("データを処理しています..."):
            
            # データ読み込みと結合
            df_ch1 = pd.read_csv(file_ch1, sep='\t', skiprows=2, encoding='shift_jis')
            df_ch2 = pd.read_csv(file_ch2, sep='\t', skiprows=2, encoding='shift_jis')
            df_ch1.columns, df_ch2.columns = df_ch1.columns.str.strip(), df_ch2.columns.str.strip()
            cols_to_drop = [c for c in df_ch2.columns if c in df_ch1.columns]
            df = pd.concat([df_ch1.reset_index(drop=True), df_ch2.drop(columns=cols_to_drop).reset_index(drop=True)], axis=1)
            
            # 🌟 欠損値対策: 日時計算のベースとなる列の存在確認
            if 'File Name' in df.columns:
                df = df.dropna(subset=['File Name'])
            else:
                df = df.dropna(subset=['Date', 'Time'])

            for col in ['CO2', 'CO', 'CH4', 'N2', 'O2']:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
                    if col in ['O2', 'N2']: df.loc[df[col] > 50000000, col] = 0

            # 🌟 時間計算: ファイル名から本来の測定日時を抽出するロジック
            def extract_real_time(filename):
                match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}-\d{2}-\d{2})', str(filename))
                if match:
                    return pd.to_datetime(match.group(1), format='%Y-%m-%d %H-%M-%S', errors='coerce')
                return pd.NaT

            if 'File Name' in df.columns:
                df['Datetime'] = df['File Name'].apply(extract_real_time)
                # 抽出失敗時のフォールバックとして従来の時刻を使用
                fallback_dt = pd.to_datetime(df['Date'].astype(str).str.strip() + ' ' + df['Time'].astype(str).str.strip(), errors='coerce')
                df['Datetime'] = df['Datetime'].fillna(fallback_dt)
            else:
                df['Datetime'] = pd.to_datetime(df['Date'].astype(str).str.strip() + ' ' + df['Time'].astype(str).str.strip(), errors='coerce')

            df = df.dropna(subset=['Datetime']).sort_values('Datetime').reset_index(drop=True)
            
            if df.empty:
                st.error("有効なデータが見つかりませんでした。")
                st.stop()

            start_time = df['Datetime'].min()
            df['Elapsed_min'] = (df['Datetime'] - start_time).dt.total_seconds() / 60.0
            
            df = df[df['Elapsed_min'] >= ignore_mins].reset_index(drop=True)
            
            if df.empty:
                st.error("除外時間を長く設定しすぎたため、データが全て消えました。設定を見直してください。")
                st.stop()

            # 濃度計算
            df['CO2_Conc'] = (df['CO2'] * calib_dict.get('CO2', {}).get('Slope', 1) + calib_dict.get('CO2', {}).get('Intercept', 0)).clip(lower=0)
            df['CO_Conc'] = (df['CO'] * calib_dict.get('CO', {}).get('Slope', 1) + calib_dict.get('CO', {}).get('Intercept', 0)).clip(lower=0)
            df['CH4_Conc'] = (df['CH4'] * calib_dict.get('CH4', {}).get('Slope', 1) + calib_dict.get('CH4', {}).get('Intercept', 0)).clip(lower=0)
            df['Total_Carbon_Conc(%)'] = df['CO2_Conc'] + df['CO_Conc'] + df['CH4_Conc']
            
            baseline_candidates = df['Total_Carbon_Conc(%)'].dropna()
            max_carbon_baseline = np.percentile(baseline_candidates, 95) if not baseline_candidates.empty else 10.0
            if max_carbon_baseline <= 0: max_carbon_baseline = 10.0
            
            # 各種計算
            df['C-Balance(%)'] = (df['Total_Carbon_Conc(%)'] / max_carbon_baseline) * 100
            df['CO2_Conversion(%)'] = ((df['CO_Conc'] + df['CH4_Conc']) / max_carbon_baseline) * 100
            
            prod_c = df['CO_Conc'] + df['CH4_Conc']
            df['CO_Selectivity(%)'] = np.where(prod_c > 0, (df['CO_Conc'] / prod_c) * 100, 0)
            df['CH4_Selectivity(%)'] = np.where(prod_c > 0, (df['CH4_Conc'] / prod_c) * 100, 0)
            
            # タイムライン同期
            best_offset, gc_interval = auto_optimize_timeline(df)
            df['Furnace_Time_min'] = df['Elapsed_min'] - best_offset
            stage_info = df['Furnace_Time_min'].apply(get_smart_stage)
            df['Temperature'], df['Stage'] = [x[0] for x in stage_info], [x[1] for x in stage_info]
            
            # 定常状態の抽出
            df['Status'] = '❌ 昇温・降温・安定化待ち'
            df.loc[df['Temperature'].notna(), 'Status'] = '⚠️ プラトー前半'
            steady_indices = df.dropna(subset=['Temperature']).groupby('Temperature').tail(3).index
            df.loc[steady_indices, 'Status'] = '✅ 定常状態'

            # 最終結果エクスポート用データ
            final_result = df.loc[steady_indices].groupby('Temperature').mean(numeric_only=True).reset_index()
            cols_output = ['Temperature', 'C-Balance(%)', 'CO2_Conc', 'CO_Conc', 'CH4_Conc', 'CO2_Conversion(%)', 'CO_Selectivity(%)', 'CH4_Selectivity(%)']
            final_result = final_result[[c for c in cols_output if c in final_result.columns]]

        st.success(f"🎯 解析完了！ (オフセット: {best_offset:.1f}分 / 基準炭素量: {max_carbon_baseline:.2f}%)")

        if not final_result.empty:
            max_temp = final_result['Temperature'].max()
            m1, m2, m3 = st.columns(3)
            m1.metric("最大転化率", f"{final_result.loc[final_result['Temperature'] == max_temp, 'CO2_Conversion(%)'].values[0]:.2f} %")
            m2.metric("測定間隔", f"{gc_interval:.2f} min")
            m3.metric("平均C-Balance", f"{final_result['C-Balance(%)'].mean():.2f} %")

        tab1, tab2 = st.tabs(["📈 グラフ", "📋 CSV用データ一覧"])
        with tab1:
            chart = alt.Chart(df).mark_circle(size=80).encode(
                x=alt.X('Furnace_Time_min:Q', title='電気炉稼働時間 (分)'),
                y=alt.Y('CO_Conc:Q', title='CO 濃度 (%)'),
                color=alt.Color('Status:N', scale=alt.Scale(domain=['✅ 定常状態', '⚠️ プラトー前半', '❌ 昇温・降温・安定化待ち'], range=['#10b981', '#f59e0b', '#ef4444'])),
                tooltip=['Temperature', 'CO_Conc', 'CO2_Conversion(%)', 'C-Balance(%)']
            ).properties(height=400).interactive()
            st.altair_chart(chart, use_container_width=True)

        with tab2:
            st.dataframe(final_result.style.format("{:.3f}"), use_container_width=True)
            csv = final_result.to_csv(index=False).encode('shift_jis')
            st.download_button("📥 綺麗なCSVをダウンロード", data=csv, file_name=f'RWGS_Clean_Result.csv', mime='text/csv')
