import streamlit as st
import pandas as pd
import numpy as np
import altair as alt

# ==========================================
# ページ設定 & カスタムCSS (高級感の演出)
# ==========================================
st.set_page_config(page_title="RWGS Analyzer", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
    <style>
    /* 全体のフォントと背景色 */
    html, body, [class*="css"] {
        font-family: 'Helvetica Neue', Helvetica, Arial, 'Hiragino Sans', sans-serif;
    }
    /* ヘッダー周りのクリーン化 */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    /* ボタンのスタイリング */
    .stButton>button {
        border-radius: 8px;
        background-color: #2b3a4a;
        color: white;
        font-weight: 600;
        transition: all 0.3s;
        border: none;
        padding: 0.5rem 1rem;
    }
    .stButton>button:hover {
        background-color: #1a252f;
        box-shadow: 0 4px 12px rgba(0,0,0,0.15);
    }
    /* Expanderのスタイリング */
    .streamlit-expanderHeader {
        font-weight: 600;
        color: #2b3a4a;
    }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# 分析ロジック群
# ==========================================
def get_dynamic_stage(elapsed_min, step_time, exclude_time):
    if pd.isna(elapsed_min) or elapsed_min < 0: return np.nan, "待機"
    
    temps = [100, 200, 300, 350, 400, 450, 500, 550, 600]
    stage_idx = int(elapsed_min // step_time)
    
    if stage_idx >= len(temps): return np.nan, "終了"
    
    t_in_stage = elapsed_min % step_time
    current_temp = temps[stage_idx]
    
    # スライダーで設定した「除外時間」を使って動的に判定
    if t_in_stage < exclude_time:
        return np.nan, f"昇温/安定化 (→{current_temp}℃)"
    else:
        return current_temp, f"維持 ({current_temp}℃)"

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
st.markdown("<p style='color: #666; font-size: 1.1rem; margin-top: -10px;'>Micro GC Data Automated Processing Pipeline</p>", unsafe_allow_html=True)

# --- サイドバー (設定エリア) ---
with st.sidebar:
    st.markdown("### 🎛️ Analysis Parameters")
    
    with st.expander("⏱️ タイムライン設計", expanded=True):
        offset_min = st.number_input("測定開始の遅延 (分)", value=0.0, step=1.0)
        gc_interval = st.number_input("GC測定間隔 (分/回)", value=2.45, step=0.05)
        st.markdown("---")
        step_time = st.slider("1温度あたりの保持時間 (分)", 10, 40, 20)
        exclude_time = st.slider("昇温除外時間 (分)", 2, 15, 6, help="温度切り替え直後の不安定な時間を切り捨てます")

    with st.expander("⚙️ 検量線キャリブレーション", expanded=False):
        DEFAULT_CALIB = pd.DataFrame({
            'Gas': ['CO2', 'CO', 'CH4'],
            'Slope': [6.473e-07, 1.843e-06, 1.000e-06],
            'Intercept': [-0.0387, 0.1918, 0.0]
        })
        if 'calib_df' not in st.session_state: st.session_state['calib_df'] = DEFAULT_CALIB.copy()
        
        uploaded_calib = st.file_uploader("設定読込", type=['csv'], label_visibility="collapsed")
        if uploaded_calib:
            try: st.session_state['calib_df'] = pd.read_csv(uploaded_calib)
            except: pass
        
        edited_calib_df = st.data_editor(st.session_state['calib_df'], num_rows="dynamic", hide_index=True, use_container_width=True)
        calib_dict = edited_calib_df.set_index('Gas').to_dict(orient='index')

# --- メインエリア (ファイルアップロード) ---
st.markdown("#### 📂 1. Upload Raw Data")
col1, col2 = st.columns(2)
with col1: file_ch1 = st.file_uploader("Channel 1 (.Area)", type=['Area', 'txt', 'csv'])
with col2: file_ch2 = st.file_uploader("Channel 2 (.Area)", type=['Area', 'txt', 'csv'])

if file_ch1 and file_ch2:
    st.markdown("#### 🚀 2. Execute Pipeline")
    if st.button("Process Data", type="primary", use_container_width=True):
        with st.spinner("Analyzing data and identifying steady states..."):
            
            # データ処理
            df_ch1 = pd.read_csv(file_ch1, sep='\t', skiprows=2, encoding='shift_jis')
            df_ch2 = pd.read_csv(file_ch2, sep='\t', skiprows=2, encoding='shift_jis')
            df_ch1.columns, df_ch2.columns = df_ch1.columns.str.strip(), df_ch2.columns.str.strip()
            
            cols_to_drop = [c for c in df_ch2.columns if c in df_ch1.columns]
            df = pd.concat([df_ch1.reset_index(drop=True), df_ch2.drop(columns=cols_to_drop).reset_index(drop=True)], axis=1)
            for col in ['CO2', 'CO', 'CH4', 'N2']:
                if col in df.columns: df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

            fallback_time = pd.Series(range(len(df))) * gc_interval
            df['Datetime'] = pd.to_datetime(df['Date'].astype(str).str.strip() + ' ' + df['Time'].astype(str).str.strip(), errors='coerce')
            if df['Datetime'].isna().sum() < len(df) * 0.5:
                start_time = df['Datetime'].dropna().iloc[0]
                df['Elapsed_min'] = (df['Datetime'] - start_time).dt.total_seconds() / 60.0
                df['Elapsed_min'] = df['Elapsed_min'].fillna(fallback_time)
            else:
                df['Elapsed_min'] = fallback_time

            df['Furnace_Time_min'] = df['Elapsed_min'] - offset_min
            
            # 動的ステージ割り当て
            stage_info = df['Furnace_Time_min'].apply(lambda x: get_dynamic_stage(x, step_time, exclude_time)).tolist()
            df['Temperature'] = [x[0] for x in stage_info]
            df['Stage'] = [x[1] for x in stage_info]

            # 安定性評価
            df['CO_Slope'] = df['CO'].diff().abs().fillna(0)
            noise_floor = df['CO'].max() * 0.02 if df['CO'].max() > 0 else 1000 
            
            def evaluate_stability(row):
                if "昇温" in row['Stage']: return "Exclude: Ramp-up (昇温中)"
                elif row['CO_Slope'] > (row['CO'] * 0.05 + noise_floor): return "Exclude: Transition (過渡期)"
                else: return "Include: Steady State (プラトー)"
                    
            df['Status'] = df.apply(evaluate_stability, axis=1)

            # 濃度・メトリクス計算
            df['CO2_Conc'] = (df['CO2'] * calib_dict['CO2']['Slope'] + calib_dict['CO2']['Intercept']).clip(lower=0)
            df['CO_Conc'] = (df['CO'] * calib_dict['CO']['Slope'] + calib_dict['CO']['Intercept']).clip(lower=0)
            df['CH4_Conc'] = (df['CH4'] * calib_dict['CH4']['Slope'] + calib_dict['CH4']['Intercept']).clip(lower=0)
            df = pd.concat([df, df.apply(calc_metrics, axis=1)], axis=1)

            # プラトー抽出
            def get_steady_state(group):
                stable_points = group[group['Status'] == 'Include: Steady State (プラトー)']
                if len(stable_points) >= 1: return stable_points.tail(3).mean(numeric_only=True)
                else: return group.tail(3).mean(numeric_only=True)

            result_df = df.dropna(subset=['Temperature']).groupby('Temperature').apply(get_steady_state).reset_index()
            output_columns = ['Temperature', 'Furnace_Time_min', 'Total_Carbon(%)', 'CO2_Conc', 'CO_Conc', 'CH4_Conc', 'CO2_Conversion(%)', 'CO_Selectivity(%)', 'CH4_Selectivity(%)']
            final_result = result_df[output_columns]

        # ==========================================
        # 結果表示 (ダッシュボードスタイル)
        # ==========================================
        st.markdown("---")
        
        # KPI メトリクス表示
        st.markdown("#### 📊 Key Insights")
        if not final_result.empty:
            max_temp = final_result['Temperature'].max()
            max_conv = final_result.loc[final_result['Temperature'] == max_temp, 'CO2_Conversion(%)'].values[0]
            max_sel = final_result.loc[final_result['Temperature'] == max_temp, 'CO_Selectivity(%)'].values[0]
            avg_carbon = final_result['Total_Carbon(%)'].mean()
            
            m1, m2, m3 = st.columns(3)
            m1.metric(label=f"Max Conversion (@{int(max_temp)}℃)", value=f"{max_conv:.2f} %")
            m2.metric(label="CO Selectivity (Max Temp)", value=f"{max_sel:.1f} %")
            m3.metric(label="Avg. Carbon Balance", value=f"{avg_carbon:.2f} %", delta=f"{avg_carbon - 10.0:.2f} % from Ideal", delta_color="inverse")

        # タブ表示
        tab1, tab2 = st.tabs(["📈 Visual Analytics", "📋 Data Export"])
        
        with tab1:
            st.markdown("<br>", unsafe_allow_html=True)
            # 高級感のあるAltairチャート設定
            chart = alt.Chart(df).mark_circle(size=70, opacity=0.8).encode(
                x=alt.X('Furnace_Time_min:Q', title='Elapsed Time (min)', axis=alt.Axis(grid=False)),
                y=alt.Y('CO_Conc:Q', title='CO Concentration (%)', axis=alt.Axis(gridColor='#f0f0f0')),
                color=alt.Color('Status:N', 
                                title='Data Status',
                                scale=alt.Scale(
                                    domain=['Include: Steady State (プラトー)', 'Exclude: Transition (過渡期)', 'Exclude: Ramp-up (昇温中)'],
                                    range=['#10b981', '#f59e0b', '#ef4444'] # モダンなTailwindカラー
                                )),
                tooltip=['Furnace_Time_min', 'Temperature', 'CO_Conc', 'CO2_Conversion(%)', 'Status']
            ).properties(
                height=400
            ).configure_view(
                strokeWidth=0
            ).configure_axis(
                labelFontSize=12,
                titleFontSize=14,
                titleColor='#4b5563',
                labelColor='#6b7280'
            ).configure_legend(
                titleFontSize=13,
                labelFontSize=12,
                orient='bottom'
            ).interactive()
            
            st.altair_chart(chart, use_container_width=True)

        with tab2:
            st.markdown("<br>", unsafe_allow_html=True)
            st.dataframe(final_result.style.format("{:.3f}").background_gradient(cmap='Blues', subset=['CO2_Conversion(%)']), use_container_width=True)
            
            csv = final_result.to_csv(index=False).encode('shift_jis')
            st.download_button(label="📥 Download Result (CSV)", data=csv, file_name='RWGS_Result_Premium.csv', mime='text/csv')
