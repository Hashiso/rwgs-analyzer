import streamlit as st
import pandas as pd
import numpy as np

# --- 転化率・選択率の計算 ---
def calc_metrics(row, co2_in):
    co_conc = row.get('CO_Conc', 0)
    ch4_conc = row.get('CH4_Conc', 0)
    conversion = (co_conc + ch4_conc) / co2_in * 100 if co2_in > 0 else 0
    prod_c = co_conc + ch4_conc
    sel_co = co_conc / prod_c * 100 if prod_c > 0 else 0
    sel_ch4 = ch4_conc / prod_c * 100 if prod_c > 0 else 0
    return pd.Series([conversion, sel_co, sel_ch4], index=['CO2_Conversion(%)', 'CO_Selectivity(%)', 'CH4_Selectivity(%)'])

# --- 時間ベースの推測（あくまでAIの「初期推測」用） ---
def guess_temperature(elapsed_min):
    if elapsed_min <= 20: return 100
    elif elapsed_min <= 40: return 200
    elif elapsed_min <= 60: return 300
    elif elapsed_min <= 80: return 350
    elif elapsed_min <= 103: return 400
    elif elapsed_min <= 126: return 450
    elif elapsed_min <= 149: return 500
    elif elapsed_min <= 172: return 550
    elif elapsed_min <= 195: return 600
    else: return None

st.set_page_config(page_title="RWGS 活性自動評価アプリ", layout="wide")
st.title("RWGS 触媒活性 自動評価ツール 🧪 (AIアシスト版)")

# --- サイドバー設定 ---
st.sidebar.header("⚙️ 計算設定")
co2_in_conc = st.sidebar.number_input("原料の初期CO2濃度 (%)", value=10.0, step=1.0)
gc_interval = st.sidebar.number_input("GCの平均測定間隔 (分/回)", value=2.45, step=0.05)

DEFAULT_CALIB = pd.DataFrame({
    'Gas': ['CO2', 'CO', 'CH4'],
    'Slope': [6.473e-07, 1.843e-06, 1.000e-06],
    'Intercept': [-0.0387, 0.1918, 0.0]
})

if 'calib_df' not in st.session_state:
    st.session_state['calib_df'] = DEFAULT_CALIB.copy()

uploaded_calib = st.sidebar.file_uploader("📂 保存した係数を読込", type=['csv'])
if uploaded_calib:
    try: st.session_state['calib_df'] = pd.read_csv(uploaded_calib)
    except: pass

edited_calib_df = st.sidebar.data_editor(st.session_state['calib_df'], num_rows="dynamic", hide_index=True, use_container_width=True)
calib_dict = edited_calib_df.set_index('Gas').to_dict(orient='index')
st.sidebar.download_button("💾 現在の係数を保存", data=edited_calib_df.to_csv(index=False).encode('utf-8'), file_name="calib_settings.csv", mime="text/csv")

# --- メイン処理 ---
col1, col2 = st.columns(2)
with col1: file_ch1 = st.file_uploader("Channel 1 (.Area)", type=['Area', 'txt', 'csv'])
with col2: file_ch2 = st.file_uploader("Channel 2 (.Area)", type=['Area', 'txt', 'csv'])

if file_ch1 and file_ch2:
    # データの読み込みと安全な結合
    df_ch1 = pd.read_csv(file_ch1, sep='\t', skiprows=2, encoding='shift_jis')
    df_ch2 = pd.read_csv(file_ch2, sep='\t', skiprows=2, encoding='shift_jis')
    df_ch1.columns = df_ch1.columns.str.strip()
    df_ch2.columns = df_ch2.columns.str.strip()
    
    cols_to_drop = [c for c in df_ch2.columns if c in df_ch1.columns]
    df = pd.concat([df_ch1.reset_index(drop=True), df_ch2.drop(columns=cols_to_drop).reset_index(drop=True)], axis=1)
    
    for col in ['CO2', 'CO', 'CH4', 'N2']:
        if col in df.columns: df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

    # 経過時間の安全な計算
    fallback_time = pd.Series(range(len(df))) * gc_interval
    df['Datetime'] = pd.to_datetime(df['Date'].astype(str).str.strip() + ' ' + df['Time'].astype(str).str.strip(), errors='coerce')
    if df['Datetime'].isna().sum() < len(df) * 0.5:
        start_time = df['Datetime'].dropna().iloc[0]
        df['Elapsed_min'] = (df['Datetime'] - start_time).dt.total_seconds() / 60.0
        df['Elapsed_min'] = df['Elapsed_min'].fillna(fallback_time)
    else:
        df['Elapsed_min'] = fallback_time

    # AI(プログラム)による温度の初期推測
    df['Temp_Guess'] = df['Elapsed_min'].apply(guess_temperature)
    
    # 濃度計算（全データ）
    df['CO2_Conc'] = (df['CO2'] * calib_dict['CO2']['Slope'] + calib_dict['CO2']['Intercept']).clip(lower=0)
    df['CO_Conc'] = (df['CO'] * calib_dict['CO']['Slope'] + calib_dict['CO']['Intercept']).clip(lower=0)
    df['CH4_Conc'] = (df['CH4'] * calib_dict['CH4']['Slope'] + calib_dict['CH4']['Intercept']).clip(lower=0)

    st.markdown("---")
    st.subheader("👀 Step 1: CO Areaの推移グラフ")
    st.write("各温度ステップごとのCO生成量（プラトー）を視覚的に確認できます。")
    st.line_chart(df['CO'])

    st.subheader("🧠 Step 2: 定常状態の自動抽出と手動補正")
    st.write("プログラムが時間を元に各行の温度を推測しました。**昇温前の室温データや、温度移行中の不安定なデータは、一番右の「割り当て温度」を消して空欄にしてください。**")
    
    # ユーザーが編集できる表（ここでAIの推測を人間が正す）
    edit_df = df[['Time', 'Elapsed_min', 'CO', 'CH4', 'CO2', 'Temp_Guess']].copy()
    edit_df.columns = ['測定時刻', '経過時間(分)', 'CO Area', 'CH4 Area', 'CO2 Area', '割り当て温度(℃)']
    
    edited_df = st.data_editor(edit_df, num_rows="dynamic", use_container_width=True, height=400)

    # 編集結果をもとに計算するボタン
    if st.button("✨ 上記の割り当てで定常状態を計算する", type="primary"):
        # ユーザーが編集した温度を本データに反映
        df['Assigned_Temp'] = pd.to_numeric(edited_df['割り当て温度(℃)'], errors='coerce')
        
        # 温度が割り当てられていない行（室温や移行期間）は除外
        df_calc = df.dropna(subset=['Assigned_Temp']).copy()
        
        if df_calc.empty:
            st.error("温度が割り当てられたデータがありません。表を確認してください。")
        else:
            # 転化率・選択率の計算
            metrics = df_calc.apply(lambda row: calc_metrics(row, co2_in_conc), axis=1)
            df_calc = pd.concat([df_calc, metrics], axis=1)
            
            # 各温度ごとにグループ化し、「割り当てられたデータの最後の3行」の平均を取る！
            result_df = df_calc.groupby('Assigned_Temp').apply(lambda x: x.tail(3).mean(numeric_only=True)).reset_index()
            
            # 出力列の整理
            output_columns = ['Assigned_Temp', 'Elapsed_min', 'CO2_Conc', 'CO_Conc', 'CH4_Conc', 'CO2_Conversion(%)', 'CO_Selectivity(%)', 'CH4_Selectivity(%)']
            final_result = result_df[output_columns].rename(columns={'Assigned_Temp': 'Temperature'})
            
            st.success("✅ 計算完了！不要なデータは完全に除外され、指定した温度のプラトー（後方3点）だけが抽出されました。")
            
            st.dataframe(final_result.style.format("{:.3f}"))
            
            csv = final_result.to_csv(index=False).encode('shift_jis')
            st.download_button(label="📥 定常状態の結果をCSVでダウンロード", data=csv, file_name='RWGS_Result_Smart.csv', mime='text/csv')
