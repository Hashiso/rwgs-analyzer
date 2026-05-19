import streamlit as st
import pandas as pd
import numpy as np
import altair as alt

# ==========================================
# ページ設定 & カスタムCSS
# ==========================================
st.set_page_config(page_title="RWGS Analyzer", layout="wide")

st.markdown("""
    <style>
    html, body, [class*="css"] { font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; }
    .stButton>button { border-radius: 8px; background-color: #2b3a4a; color: white; font-weight: 600; border: none; padding: 0.5rem 1rem; }
    .stButton>button:hover { background-color: #1a252f; }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# 分析ロジック群
# ==========================================
def get_smart_stage(elapsed_min):
    """スケジュール定義 (昇温時間+安定化のためのバッファを除外して判定)"""
    if pd.isna(elapsed_min) or elapsed_min < 0: return np.nan, "待機"
    
    # (開始時間, 終了時間, 温度)
    schedule = [
        (0, 20, 100), (20, 40, 200), (40, 60, 300), (60, 80, 350),
        (80, 103, 400), (103, 126, 450), (126, 149, 500), (149, 172, 550), (172, 195, 600)
    ]
    
    for start, end, temp in schedule:
        if start <= elapsed_min < end:
            # 最初の8分間は「昇温 ＋ 安定化待ち」として問答無用で除外
            if elapsed_min < start + 8:
                return np.nan, f"昇温/安定化 (→{temp}℃)"
            else:
                return temp, f"維持 ({temp}℃)"
    return np.nan, "終了"

def auto_optimize_timeline(df):
    """AI自動判定：定常状態のばらつき(分散)が最小になるオフセットを全自動探索"""
    gc_interval = df['Elapsed_min'].diff().median()
    if pd.isna(gc_interval) or gc_interval <= 0: gc_interval = 2.45
        
    best_offset = 0.0
    min_score = float('inf')
    
    # 0分〜40分までの遅れを0.5分刻みで総当たり検証
    for offset in np.arange(0, 40, 0.5):
        t_furnace = df['Elapsed_min'] - offset
        stages = t_furnace.apply(lambda x: get_smart_stage(x)[0])
        
        df_temp = pd.DataFrame({'Temp': stages, 'CO': df['CO_Conc']}).dropna()
        if len(df_temp['Temp'].unique()) < 8: continue # データが少なすぎる場合は除外
            
        # 抽出されたプラトーのばらつき（標準偏差）の合計
        var_sum = df_temp.groupby('Temp')['CO'].std().fillna(0).sum()
        
        # 600℃でCOが最大になっていない場合は不正なズレとしてペナルティ
        means = df_temp.groupby('Temp')['CO'].mean()
        penalty = 10000 if (means.empty or means.idxmax() != 600) else 0
            
        score = var_sum + penalty
        if score < min_score:
            min_score = score
            best_offset = offset
            
    return best_offset, gc_interval

def calc_metrics(row):
    co_conc = row.get('CO_Conc', 0)
    ch4_conc = row.get('CH4_Conc', 0)
    co2_conc = row.get('CO2_Conc', 0)
    
    total_c = co2_conc + co_conc + ch4_conc
    conversion = (co_conc + ch4_conc) / total_c * 100 if total_c > 0 else 0
    prod_c = co_conc + ch4_conc
    sel_co = co_conc / prod_c * 100 if prod_c > 0 else 0
    sel_ch4 = ch4_conc / prod_c * 100 if prod_c > 0 else 0
    
    return pd.Series([total_c, conversion, sel_co, sel_ch4], 
                     index=['Total_Carbon(%)', 'CO2_Conversion(%)', 'CO_Selectivity(%)', 'CH4_Selectivity(%)'])

# ==========================================
# UI 構築
# ==========================================
st.markdown("## 🧪 RWGS Catalyst Analytics")
st.markdown("<p style='color: #666; font-size: 1.1rem; margin-top: -10px;'>Fully Automated Data Pipeline</p>", unsafe_allow_html=True)

# --- サイドバー (検量線のみ) ---
with st.sidebar:
    st.markdown("### ⚙️ キャリブレーション")
    DEFAULT_CALIB = pd.DataFrame({
        'Gas': ['CO2', 'CO', 'CH4'],
        'Slope': [6.473e-07, 1.843e-06, 1.000e-06],
        'Intercept': [-0.0387, 0.1918, 0.0]
    })
    if 'calib_df' not in st.session_state: st.session_state['calib_df'] = DEFAULT_CALIB.copy()
    
    uploaded_calib = st.file_uploader("設定読込", type=['csv'])
    if uploaded_calib:
        try: st.session_state['calib_df'] = pd.read_csv(uploaded_calib)
        except: pass
    
    edited_calib_df = st.data_editor(st.session_state['calib_df'], num_rows="dynamic", hide_index=True, use_container_width=True)
    calib_dict = edited_calib_df.set_index('Gas').to_dict(orient='index')
    st.download_button("💾 係数を保存", data=edited_calib_df.to_csv(index=False).encode('utf-8'), file_name="calib_settings.csv", mime="text/csv")

# --- メインエリア ---
st.markdown("#### 📂 1. Upload Raw Data")
col1, col2 = st.columns(2)
with col1: file_ch1 = st.file_uploader("Channel 1 (.Area)", type=['Area', 'txt', 'csv'])
with col2: file_ch2 = st.file_uploader("Channel 2 (.Area)", type=['Area', 'txt', 'csv'])

if file_ch1 and file_ch2:
    st.markdown("#### 🚀 2. Execute Analysis")
    if st.button("全自動で解析を実行", type="primary", use_container_width=True):
        with st.spinner("AIが昇温タイミングを推論し、最適な定常状態を抽出しています..."):
            
            # データ読み込み・クレンジング
            df_ch1 = pd.read_csv(file_ch1, sep='\t', skiprows=2, encoding='shift_jis')
            df_ch2 = pd.read_csv(file_ch2, sep='\t', skiprows=2, encoding='shift_jis')
            df_ch1.columns, df_ch2.columns = df_ch1.columns.str.strip(), df_ch2.columns.str.strip()
            
            cols_to_drop = [c for c in df_ch2.columns if c in df_ch1.columns]
            df = pd.concat([df_ch1.reset_index(drop=True), df_ch2.drop(columns=cols_to_drop).reset_index(drop=True)], axis=1)
            for col in ['CO2', 'CO', 'CH4', 'N2']:
                if col in df.columns: df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

            # 時間計算
            df['Datetime'] = pd.to_datetime(df['Date'].astype(str).str.strip() + ' ' + df['Time'].astype(str).str.strip(), errors='coerce')
            if df['Datetime'].isna().sum() < len(df) * 0.5:
                start_time = df['Datetime'].dropna().iloc[0]
                df['Elapsed_min'] = (df['Datetime'] - start_time).dt.total_seconds() / 60.0
                df['Elapsed_min'] = df['Elapsed_min'].fillna(pd.Series(range(len(df))) * 2.45)
            else:
                df['Elapsed_min'] = pd.Series(range(len(df))) * 2.45

            # 濃度計算
            df['CO2_Conc'] = (df['CO2'] * calib_dict['CO2']['Slope'] + calib_dict['CO2']['Intercept']).clip(lower=0)
            df['CO_Conc'] = (df['CO'] * calib_dict['CO']['Slope'] + calib_dict['CO']['Intercept']).clip(lower=0)
            df['CH4_Conc'] = (df['CH4'] * calib_dict['CH4']['Slope'] + calib_dict['CH4']['Intercept']).clip(lower=0)
            
            # --- AIによるアラインメント（判断をアプリに任せる） ---
            best_offset, gc_interval = auto_optimize_timeline(df)
            
            # 決定されたタイムラインを適用
            df['Furnace_Time_min'] = df['Elapsed_min'] - best_offset
            stage_info = df['Furnace_Time_min'].apply(get_smart_stage)
            df['Temperature'] = [x[0] for x in stage_info]
            df['Stage'] = [x[1] for x in stage_info]
            
            # メトリクス計算
            df = pd.concat([df, df.apply(calc_metrics, axis=1)], axis=1)

            # ステータスのラベリング（グラフ可視化用）
            df['Status'] = '❌ 昇温中・安定化待ち (除外)'
            df.loc[df['Temperature'].notna(), 'Status'] = '⚠️ プラトー前半 (除外)'
            
            # 各温度ごとに、抽出対象となる「最後の3点」を特定
            steady_indices = df.dropna(subset=['Temperature']).groupby('Temperature').tail(3).index
            df.loc[steady_indices, 'Status'] = '✅ 定常状態 (抽出対象)'

            # 最終結果の集計
            final_result = df.loc[steady_indices].groupby('Temperature').mean(numeric_only=True).reset_index()
            output_columns = ['Temperature', 'Total_Carbon(%)', 'CO2_Conc', 'CO_Conc', 'CH4_Conc', 'CO2_Conversion(%)', 'CO_Selectivity(%)', 'CH4_Selectivity(%)']
            final_result = final_result[output_columns]

        # ==========================================
        # 結果表示
        # ==========================================
        st.markdown("---")
        st.success(f"🤖 **AI Auto-Alignment 完了:** \nデータから逆算し、**測定開始から約 {best_offset:.1f} 分の遅れ** を自動検知してスケジュールを補正しました。")

        # KPI メトリクス
        if not final_result.empty:
            max_temp = final_result['Temperature'].max()
            max_conv = final_result.loc[final_result['Temperature'] == max_temp, 'CO2_Conversion(%)'].values[0]
            avg_carbon = final_result['Total_Carbon(%)'].mean()
            
            m1, m2, m3 = st.columns(3)
            m1.metric(label=f"Max Conversion (@{int(max_temp)}℃)", value=f"{max_conv:.2f} %")
            m2.metric(label="Detected GC Interval", value=f"{gc_interval:.2f} min")
            m3.metric(label="Avg. Carbon Balance", value=f"{avg_carbon:.2f} %")

        tab1, tab2 = st.tabs(["📈 Visual Analytics", "📋 Data Export"])
        
        with tab1:
            st.markdown("<br>", unsafe_allow_html=True)
            # Altairグラフ
            chart = alt.Chart(df).mark_circle(size=80, opacity=0.9).encode(
                x=alt.X('Furnace_Time_min:Q', title='電気炉稼働時間 (分)', axis=alt.Axis(grid=False)),
                y=alt.Y('CO_Conc:Q', title='CO 濃度 (%)', axis=alt.Axis(gridColor='#f0f0f0')),
                color=alt.Color('Status:N', 
                                title='データ判定',
                                scale=alt.Scale(
                                    domain=['✅ 定常状態 (抽出対象)', '⚠️ プラトー前半 (除外)', '❌ 昇温中・安定化待ち (除外)'],
                                    range=['#10b981', '#f59e0b', '#ef4444']
                                )),
                tooltip=['Furnace_Time_min', 'Temperature', 'CO_Conc', 'CO2_Conversion(%)', 'Status']
            ).properties(height=400).interactive()
            
            st.altair_chart(chart, use_container_width=True)

        with tab2:
            st.markdown("<br>", unsafe_allow_html=True)
            # エラーの原因だった background_gradient を削除し、シンプルなフォーマットに変更
            st.dataframe(final_result.style.format("{:.3f}"), use_container_width=True)
            
            csv = final_result.to_csv(index=False).encode('shift_jis')
            st.download_button(label="📥 結果をCSVでダウンロード", data=csv, file_name='RWGS_Result_AutoAligned.csv', mime='text/csv')
