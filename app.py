import streamlit as st
import pandas as pd
import numpy as np
import altair as alt

# ==========================================
# 1. タイムライン推論（スケジュールに基づく自動判定）
# ==========================================
def get_stage_info(elapsed_min):
    if pd.isna(elapsed_min) or elapsed_min < 0: return np.nan, "待機"
    elif elapsed_min < 2: return np.nan, "昇温 (→100℃)"
    elif elapsed_min <= 20: return 100, "維持 (100℃)"
    elif elapsed_min < 22: return np.nan, "昇温 (→200℃)"
    elif elapsed_min <= 40: return 200, "維持 (200℃)"
    elif elapsed_min < 42: return np.nan, "昇温 (→300℃)"
    elif elapsed_min <= 60: return 300, "維持 (300℃)"
    elif elapsed_min < 62: return np.nan, "昇温 (→350℃)"
    elif elapsed_min <= 80: return 350, "維持 (350℃)"
    elif elapsed_min < 85: return np.nan, "昇温 (→400℃)"
    elif elapsed_min <= 103: return 400, "維持 (400℃)"
    elif elapsed_min < 108: return np.nan, "昇温 (→450℃)"
    elif elapsed_min <= 126: return 450, "維持 (450℃)"
    elif elapsed_min < 131: return np.nan, "昇温 (→500℃)"
    elif elapsed_min <= 149: return 500, "維持 (500℃)"
    elif elapsed_min < 154: return np.nan, "昇温 (→550℃)"
    elif elapsed_min <= 172: return 550, "維持 (550℃)"
    elif elapsed_min < 177: return np.nan, "昇温 (→600℃)"
    elif elapsed_min <= 195: return 600, "維持 (600℃)"
    else: return np.nan, "終了"

# ==========================================
# 2. 転化率・選択率の計算（実測カーボンバランス基準に修正！）
# ==========================================
def calc_metrics(row):
    co_conc = row.get('CO_Conc', 0)
    ch4_conc = row.get('CH4_Conc', 0)
    co2_conc = row.get('CO2_Conc', 0)
    
    # 🌟修正: 実際の全炭素量(CO2+CO+CH4)を分母にして転化率を計算
    total_c = co2_conc + co_conc + ch4_conc
    conversion = (co_conc + ch4_conc) / total_c * 100 if total_c > 0 else 0
    
    prod_c = co_conc + ch4_conc
    sel_co = co_conc / prod_c * 100 if prod_c > 0 else 0
    sel_ch4 = ch4_conc / prod_c * 100 if prod_c > 0 else 0
    
    return pd.Series([total_c, conversion, sel_co, sel_ch4], index=['Total_Carbon(%)', 'CO2_Conversion(%)', 'CO_Selectivity(%)', 'CH4_Selectivity(%)'])

# ==========================================
# 3. WebアプリUI
# ==========================================
st.set_page_config(page_title="RWGS 活性自動評価アプリ", layout="wide")
st.title("RWGS 触媒活性 自動評価ツール 🧪 (AI自動判定版)")

# --- サイドバー設定 ---
st.sidebar.header("⏱️ タイムライン補正")
offset_min = st.sidebar.number_input("昇温開始の遅れ (分)", value=0.0, step=1.0, help="室温での待機時間")
gc_interval = st.sidebar.number_input("GC平均測定間隔 (分/回)", value=2.45, step=0.05)

st.sidebar.markdown("---")
st.sidebar.header("⚙️ 検量線の係数")

DEFAULT_CALIB = pd.DataFrame({
    'Gas': ['CO2', 'CO', 'CH4'],
    'Slope': [6.473e-07, 1.843e-06, 1.000e-06],
    'Intercept': [-0.0387, 0.1918, 0.0]
})

if 'calib_df' not in st.session_state: st.session_state['calib_df'] = DEFAULT_CALIB.copy()

uploaded_calib = st.sidebar.file_uploader("📂 保存した係数を読込", type=['csv'])
if uploaded_calib:
    try: st.session_state['calib_df'] = pd.read_csv(uploaded_calib)
    except: pass

edited_calib_df = st.sidebar.data_editor(st.session_state['calib_df'], num_rows="dynamic", hide_index=True)
calib_dict = edited_calib_df.set_index('Gas').to_dict(orient='index')
st.sidebar.download_button("💾 係数を保存", data=edited_calib_df.to_csv(index=False).encode('utf-8'), file_name="calib_settings.csv", mime="text/csv")

# --- メイン処理 ---
st.write("Micro GCの `.Area` ファイル（Channel 1 と Channel 2）をアップロードしてください。")
col1, col2 = st.columns(2)
with col1: file_ch1 = st.file_uploader("Channel 1 (.Area)", type=['Area', 'txt', 'csv'])
with col2: file_ch2 = st.file_uploader("Channel 2 (.Area)", type=['Area', 'txt', 'csv'])

if file_ch1 and file_ch2:
    if st.button("🚀 全自動データ処理を開始", type="primary"):
        # データ読み込みと結合
        df_ch1 = pd.read_csv(file_ch1, sep='\t', skiprows=2, encoding='shift_jis')
        df_ch2 = pd.read_csv(file_ch2, sep='\t', skiprows=2, encoding='shift_jis')
        df_ch1.columns, df_ch2.columns = df_ch1.columns.str.strip(), df_ch2.columns.str.strip()
        
        cols_to_drop = [c for c in df_ch2.columns if c in df_ch1.columns]
        df = pd.concat([df_ch1.reset_index(drop=True), df_ch2.drop(columns=cols_to_drop).reset_index(drop=True)], axis=1)
        
        for col in ['CO2', 'CO', 'CH4', 'N2']:
            if col in df.columns: df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

        # 時間計算
        fallback_time = pd.Series(range(len(df))) * gc_interval
        df['Datetime'] = pd.to_datetime(df['Date'].astype(str).str.strip() + ' ' + df['Time'].astype(str).str.strip(), errors='coerce')
        if df['Datetime'].isna().sum() < len(df) * 0.5:
            start_time = df['Datetime'].dropna().iloc[0]
            df['Elapsed_min'] = (df['Datetime'] - start_time).dt.total_seconds() / 60.0
            df['Elapsed_min'] = df['Elapsed_min'].fillna(fallback_time)
        else:
            df['Elapsed_min'] = fallback_time

        df['Furnace_Time_min'] = df['Elapsed_min'] - offset_min

        # ステージの自動割り当て
        stage_info = df['Furnace_Time_min'].apply(get_stage_info).tolist()
        df['Temperature'] = [x[0] for x in stage_info]
        df['Stage'] = [x[1] for x in stage_info]

        # CO AREAの変動率（微分）を計算し、不安定なデータを自動検出
        df['CO_Slope'] = df['CO'].diff().abs().fillna(0)
        noise_floor = df['CO'].max() * 0.02 if df['CO'].max() > 0 else 1000 
        
        def evaluate_stability(row):
            if "昇温" in row['Stage']:
                return "❌ 昇温中 (自動除外)"
            elif row['CO_Slope'] > (row['CO'] * 0.05 + noise_floor):
                return "⚠️ 過渡期 (自動除外)"
            else:
                return "✅ プラトー (安定)"
                
        df['Status'] = df.apply(evaluate_stability, axis=1)

        # 濃度計算
        df['CO2_Conc'] = (df['CO2'] * calib_dict['CO2']['Slope'] + calib_dict['CO2']['Intercept']).clip(lower=0)
        df['CO_Conc'] = (df['CO'] * calib_dict['CO']['Slope'] + calib_dict['CO']['Intercept']).clip(lower=0)
        df['CH4_Conc'] = (df['CH4'] * calib_dict['CH4']['Slope'] + calib_dict['CH4']['Intercept']).clip(lower=0)
        
        # 🌟修正: 実測カーボン基準での転化率計算を実行
        metrics_all = df.apply(calc_metrics, axis=1)
        df = pd.concat([df, metrics_all], axis=1)

        # 最終抽出: 各温度で「✅プラトー」と判定されたもののうち、最新の3点を自動取得
        def get_steady_state(group):
            stable_points = group[group['Status'] == '✅ プラトー (安定)']
            if len(stable_points) >= 1: return stable_points.tail(3).mean(numeric_only=True)
            else: return group.tail(3).mean(numeric_only=True)

        result_df = df.dropna(subset=['Temperature']).groupby('Temperature').apply(get_steady_state).reset_index()
        
        output_columns = ['Temperature', 'Furnace_Time_min', 'Total_Carbon(%)', 'CO2_Conc', 'CO_Conc', 'CH4_Conc', 'CO2_Conversion(%)', 'CO_Selectivity(%)', 'CH4_Selectivity(%)']
        final_result = result_df[output_columns]

        # --- 結果表示 ---
        st.success("✅ アルゴリズムが昇温中と過渡期のデータを全自動で除外しました！")
        
        # 🌟【新規追加】判定結果の可視化グラフ (Altairを使用)
        st.subheader("📈 アルゴリズムの判定結果グラフ")
        st.write("どのデータがノイズとして切り捨てられ、どれがプラトーとして採用候補になったか確認できます。")
        
        chart = alt.Chart(df).mark_circle(size=80).encode(
            x=alt.X('Furnace_Time_min', title='電気炉稼働時間 (分)'),
            y=alt.Y('CO_Conc', title='CO 濃度 (%)'),
            color=alt.Color('Status', title='判定ステータス', 
                            scale=alt.Scale(
                                domain=['✅ プラトー (安定)', '⚠️ 過渡期 (自動除外)', '❌ 昇温中 (自動除外)'],
                                range=['#28a745', '#ffc107', '#dc3545']
                            )),
            tooltip=['Furnace_Time_min', 'Temperature', 'CO_Conc', 'Status']
        ).interactive()
        
        st.altair_chart(chart, use_container_width=True)

        st.markdown("---")
        st.subheader("📊 抽出された定常状態 (Igor用)")
        st.dataframe(final_result.style.format("{:.3f}"))
        
        csv = final_result.to_csv(index=False).encode('shift_jis')
        st.download_button(label="📥 結果をCSVでダウンロード", data=csv, file_name='RWGS_Result_AutoFiltered.csv', mime='text/csv')
