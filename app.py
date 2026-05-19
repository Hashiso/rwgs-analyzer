import streamlit as st
import pandas as pd
import numpy as np
from io import BytesIO

# ==========================================
# 1. 経過時間から温度を判定する関数
# ==========================================
def get_temperature(elapsed_min):
    if elapsed_min <= 20: return 100
    elif elapsed_min <= 40: return 200
    elif elapsed_min <= 60: return 300
    elif elapsed_min <= 80: return 350
    elif elapsed_min <= 103: return 400
    elif elapsed_min <= 126: return 450
    elif elapsed_min <= 149: return 500
    elif elapsed_min <= 172: return 550
    elif elapsed_min <= 195: return 600
    else: return np.nan

# ==========================================
# 2. 転化率・選択率の計算関数
# ==========================================
def calc_metrics(row):
    total_c = row['CO2_Conc'] + row['CO_Conc'] + row['CH4_Conc']
    conversion = (row['CO_Conc'] + row['CH4_Conc']) / total_c * 100 if total_c > 0 else 0
        
    prod_c = row['CO_Conc'] + row['CH4_Conc']
    sel_co = row['CO_Conc'] / prod_c * 100 if prod_c > 0 else 0
    sel_ch4 = row['CH4_Conc'] / prod_c * 100 if prod_c > 0 else 0
        
    return pd.Series([conversion, sel_co, sel_ch4], index=['CO2_Conversion(%)', 'CO_Selectivity(%)', 'CH4_Selectivity(%)'])

# ==========================================
# 3. WebアプリのUIと処理
# ==========================================
st.set_page_config(page_title="RWGS 活性自動評価アプリ", layout="wide")
st.title("RWGS 触媒活性 自動評価ツール 🧪")

# --- 検量線設定エリア ---
st.sidebar.header("⚙️ 検量線の係数設定")
st.sidebar.write("※ 濃度(%) = Area × Slope + Intercept")

# デフォルトの検量線データ
DEFAULT_CALIB = pd.DataFrame({
    'Gas': ['CO2', 'CO', 'CH4'],
    'Slope': [6.473e-07, 1.843e-06, 1.000e-06],
    'Intercept': [-0.0387, 0.1918, 0.0]
})

# セッションステートの初期化
if 'calib_df' not in st.session_state:
    st.session_state['calib_df'] = DEFAULT_CALIB.copy()

# 係数ファイルの読み込み（アップロード）
uploaded_calib = st.sidebar.file_uploader("📂 保存した係数(CSV)を読み込む", type=['csv'])
if uploaded_calib is not None:
    try:
        st.session_state['calib_df'] = pd.read_csv(uploaded_calib)
        st.sidebar.success("係数を読み込みました！")
    except Exception as e:
        st.sidebar.error("ファイルの読み込みに失敗しました。")

# 表形式で係数を編集可能にする
st.sidebar.write("▼ 表の数値を直接クリックして編集できます")
edited_calib_df = st.sidebar.data_editor(
    st.session_state['calib_df'], 
    num_rows="dynamic", 
    hide_index=True,
    use_container_width=True
)

# 編集した係数をCSVとしてダウンロード（保存）するボタン
calib_csv = edited_calib_df.to_csv(index=False).encode('utf-8')
st.sidebar.download_button(
    label="💾 現在の係数をCSVとして保存",
    data=calib_csv,
    file_name="calib_settings.csv",
    mime="text/csv"
)

# 辞書型に変換（計算用）
calib_dict = edited_calib_df.set_index('Gas').to_dict(orient='index')

# --- メインエリア（データ処理） ---
st.write("Micro GCの `.Area` ファイル（Channel 1 と Channel 2）をアップロードしてください。")

col1, col2 = st.columns(2)
with col1:
    file_ch1 = st.file_uploader("Channel 1 のファイル (.Area)", type=['Area', 'txt', 'csv'])
with col2:
    file_ch2 = st.file_uploader("Channel 2 のファイル (.Area)", type=['Area', 'txt', 'csv'])

if file_ch1 is not None and file_ch2 is not None:
    if st.button("データ処理開始", type="primary"):
        try:
            # ファイルの読み込み
            df_ch1 = pd.read_csv(file_ch1, sep='\t', skiprows=2, encoding='shift_jis')
            df_ch2 = pd.read_csv(file_ch2, sep='\t', skiprows=2, encoding='shift_jis')

            # データ結合
            df = pd.merge(df_ch1, df_ch2, on=['Date', 'Time'], suffixes=('_ch1', '_ch2'))
            
            # 🌟【修正箇所】文字列になってしまった空白データを数値(0)に強制変換
            for col in ['CO2', 'CO', 'CH4']:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
            
            df['Datetime'] = pd.to_datetime(df['Date'] + ' ' + df['Time'])
            start_time = df['Datetime'].iloc[0]
            df['Elapsed_min'] = (df['Datetime'] - start_time).dt.total_seconds() / 60.0

            # 温度割り当て
            df['Temperature'] = df['Elapsed_min'].apply(get_temperature)

            # 濃度計算（編集された検量線係数を使用）
            df['CO2_Conc'] = (df['CO2'] * calib_dict['CO2']['Slope'] + calib_dict['CO2']['Intercept']).clip(lower=0)
            df['CO_Conc'] = (df['CO'] * calib_dict['CO']['Slope'] + calib_dict['CO']['Intercept']).clip(lower=0)
            df['CH4_Conc'] = (df['CH4'] * calib_dict['CH4']['Slope'] + calib_dict['CH4']['Intercept']).clip(lower=0)

            # 定常状態の抽出
            result_df = df.dropna(subset=['Temperature']).groupby('Temperature').apply(lambda x: x.tail(3).mean(numeric_only=True)).reset_index()

            # 転化率・選択率計算
            metrics = result_df.apply(calc_metrics, axis=1)
            result_df = pd.concat([result_df, metrics], axis=1)

            # 出力列の整理
            output_columns = ['Temperature', 'Elapsed_min', 'CO2_Conc', 'CO_Conc', 'CH4_Conc', 'CO2_Conversion(%)', 'CO_Selectivity(%)', 'CH4_Selectivity(%)']
            final_result = result_df[output_columns]

            st.success("✅ 処理が完了しました！")
            
            # 結果のプレビュー表示
            st.dataframe(final_result.style.format("{:.2f}"))

            # CSVダウンロードボタンの作成
            csv = final_result.to_csv(index=False).encode('shift_jis')
            st.download_button(
                label="📥 評価結果をCSVでダウンロード (Igor用)",
                data=csv,
                file_name='RWGS_Result_Summary.csv',
                mime='text/csv',
            )

        except Exception as e:
            st.error(f"エラーが発生しました。ファイル形式が正しいか確認してください。\n詳細: {e}")
