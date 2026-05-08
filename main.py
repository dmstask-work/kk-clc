import os
import pandas as pd
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

from dash import Dash, dcc, html, Input, Output

# ─── Constants ────────────────────────────────────────────────────────────────
MONTH_MAP = {
    1:'JANUARI',  2:'FEBRUARI', 3:'MARET',    4:'APRIL',
    5:'MEI',      6:'JUNI',     7:'JULI',     8:'AGUSTUS',
    9:'SEPTEMBER',10:'OKTOBER',11:'NOVEMBER',12:'DESEMBER'
}
MONTH_NUM = {v: k for k, v in MONTH_MAP.items()}
MONTH_ID  = {  # title-case for display
    1:'Januari', 2:'Februari', 3:'Maret',    4:'April',
    5:'Mei',     6:'Juni',     7:'Juli',     8:'Agustus',
    9:'September',10:'Oktober',11:'November',12:'Desember'
}
INVALID        = {'nan', 'none', 'nat', ''}
SOURCE_ORGANIC = {'organik', 'ads', 'ig'}


# ─── Data helpers ─────────────────────────────────────────────────────────────
def clean_wa(series):
    return (series.astype(str)
                  .str.strip()
                  .str.replace(r'\.0$', '', regex=True)
                  .str.replace(r'[^\d]', '', regex=True))

def clean_nama(series):
    return series.fillna('').astype(str).str.strip().str.lower()


# ─── Core computation ─────────────────────────────────────────────────────────
def compute(cutoff_date: datetime) -> dict:
    selected_year  = cutoff_date.year
    sel_month_num  = cutoff_date.month
    selected_month = MONTH_MAP[sel_month_num]

    # Load leads
    source_dir = 'source'
    all_dfs = []
    for f in sorted(os.listdir(source_dir)):
        if f.endswith('.csv') and 'Data Leads Keke_2026' in f:
            mname = f.replace('Data Leads Keke_2026 - ', '').replace('.csv', '').strip()
            df = pd.read_csv(os.path.join(source_dir, f))
            df['_month']     = mname
            df['_month_num'] = MONTH_NUM.get(mname, 0)
            all_dfs.append(df)

    leads_all = pd.concat(all_dfs, ignore_index=True)
    leads_all['No WhatsApp'] = clean_wa(leads_all['No WhatsApp'])
    leads_all['_nama_norm']  = clean_nama(leads_all['Nama Lengkap'])

    # Load DO
    do_df = pd.read_excel(os.path.join('DO', 'Rekap_Invoice_Konsultasi_Cleaned.xlsx'))
    do_df['No WhatsApp']     = clean_wa(do_df['No WhatsApp'])
    do_df['_nama_norm']      = clean_nama(do_df['Nama Klien'])
    do_df['Tanggal Payment'] = pd.to_datetime(do_df['Tanggal Payment'], errors='coerce')
    do_df['Tanggal Source']  = pd.to_datetime(do_df['Tanggal Source'],  errors='coerce')
    do_df['_source_norm']    = do_df['Source'].astype(str).str.strip().str.lower()
    do_df['_pay_month_num']  = do_df['Tanggal Payment'].dt.month.fillna(0).astype(int)
    do_df['_pay_year']       = do_df['Tanggal Payment'].dt.year.fillna(0).astype(int)

    # Slices — MTD = 1st of selected month up to exact cutoff date
    month_start = datetime(selected_year, sel_month_num, 1)
    do_mtd_df = do_df[
        (do_df['Tanggal Payment'] >= month_start) &
        (do_df['Tanggal Payment'] <= cutoff_date)
    ]
    do_sel_df = do_df[
        (do_df['_pay_year'] == selected_year) &
        (do_df['_pay_month_num'] == sel_month_num) &
        (do_df['Tanggal Payment'] <= cutoff_date)
    ]
    leads_sel = leads_all[leads_all['_month'] == selected_month]
    leads_mtd = leads_all[leads_all['_month_num'] <= sel_month_num]

    # Identity sets (all-time DO)
    do_wa_set   = set(do_df['No WhatsApp']) - INVALID
    do_nama_set = set(do_df['_nama_norm'])  - INVALID

    def in_do_sets(row):
        wa, nama = str(row['No WhatsApp']).strip(), str(row['_nama_norm']).strip()
        return (wa not in INVALID and wa in do_wa_set) or \
               (nama not in INVALID and nama in do_nama_set)

    # KOTAK 1 — unique leads in selected month only
    box1_df = leads_sel.drop_duplicates(subset=['No WhatsApp'])
    BOX1    = len(box1_df)

    # KOTAK 2 — unique leads MTD not yet in DO
    leads_mtd_uniq = leads_mtd.drop_duplicates(subset=['No WhatsApp'])
    box2_df        = leads_mtd_uniq[~leads_mtd_uniq.apply(in_do_sets, axis=1)]
    BOX2           = len(box2_df)

    # KOTAK 3 raw — unique clients in DO MTD
    seen_wa, seen_nama, box3_idx = set(), set(), []
    for idx, row in do_mtd_df.iterrows():
        wa, nama = row['No WhatsApp'], row['_nama_norm']
        wa_v, na_v = wa not in INVALID, nama not in INVALID
        if not ((wa_v and wa in seen_wa) or (na_v and nama in seen_nama)):
            if wa_v: seen_wa.add(wa)
            if na_v: seen_nama.add(nama)
            box3_idx.append(idx)
    box3_raw_df = do_df.loc[box3_idx]
    BOX3_raw    = len(box3_raw_df)

    # B — clients in Kotak 3 raw, payment this month, who came from leads MTD
    leads_mtd_wa_set   = set(leads_mtd['No WhatsApp']) - INVALID
    leads_mtd_nama_set = set(leads_mtd['_nama_norm'])  - INVALID

    def in_leads_mtd(row):
        wa, nama = str(row['No WhatsApp']).strip(), str(row['_nama_norm']).strip()
        return (wa not in INVALID and wa in leads_mtd_wa_set) or \
               (nama not in INVALID and nama in leads_mtd_nama_set)

    box3_sel_month = box3_raw_df[box3_raw_df['_pay_month_num'] == sel_month_num]
    B_count        = int(box3_sel_month.apply(in_leads_mtd, axis=1).sum())

    # KOTAK 3 final
    BOX3 = BOX3_raw - B_count if B_count > 0 else BOX3_raw

    # A — DO clients in selected month where:
    #     (Source in [Organik/Ads/IG] OR Tanggal Source in selected month)
    #     AND name/WA did NOT appear in any earlier month's leads
    leads_prev    = leads_all[leads_all['_month_num'] < sel_month_num]
    prev_wa_set   = set(leads_prev['No WhatsApp']) - INVALID
    prev_nama_set = set(leads_prev['_nama_norm'])  - INVALID

    def source_ok(row):
        src = row['_source_norm']
        tgl = row['Tanggal Source']
        return src in SOURCE_ORGANIC or \
               (pd.notna(tgl) and tgl.year == selected_year and tgl.month == sel_month_num
                and tgl <= cutoff_date)

    def is_new_this_month(row):
        wa, nama = str(row['No WhatsApp']).strip(), str(row['_nama_norm']).strip()
        return not ((wa not in INVALID and wa in prev_wa_set) or
                    (nama not in INVALID and nama in prev_nama_set))

    mask_a  = (do_sel_df.apply(source_ok, axis=1) &
               do_sel_df.apply(is_new_this_month, axis=1))
    A_count = do_sel_df[mask_a].drop_duplicates(subset=['No WhatsApp']).shape[0]

    # C — clients with >1 transaction in DO MTD (repeat)
    wa_freq     = do_mtd_df['No WhatsApp'].value_counts()
    nama_freq   = do_mtd_df['_nama_norm'].value_counts()
    repeat_wa   = set(wa_freq[wa_freq > 1].index)   - INVALID
    repeat_nama = set(nama_freq[nama_freq > 1].index) - INVALID
    C_count = do_mtd_df[
        do_mtd_df['No WhatsApp'].isin(repeat_wa) |
        do_mtd_df['_nama_norm'].isin(repeat_nama)
    ]['No WhatsApp'].nunique()

    # D — referral rows in DO MTD
    D_count = int((do_mtd_df['_source_norm'] == 'referral').sum())

    return {
        'BOX1': BOX1, 'BOX2': BOX2, 'BOX3': BOX3,
        'A': A_count, 'B': B_count, 'C': C_count, 'D': D_count,
    }


# ─── Dash App ─────────────────────────────────────────────────────────────────
app    = Dash(__name__, title='CLC Dashboard')
server = app.server  # expose Flask server for gunicorn

BG     = '#F5F9FF'
ACCENT = '#1e3a5f'
HDRB   = '#ADD3FA'
GRAY   = '#94a3b8'
DARK   = '#1e293b'

today = datetime.today()

# ─── Style helpers ─────────────────────────────────────────────────────────────
def card_box(label, value, bg, tc):
    return html.Div([
        html.Div(label, style={
            'backgroundColor': bg, 'color': tc, 'fontWeight': '700',
            'fontSize': '13px', 'padding': '10px 12px',
            'borderRadius': '10px 10px 0 0', 'textAlign': 'center',
        }),
        html.Div(str(value), style={
            'backgroundColor': bg, 'color': tc, 'fontWeight': '700',
            'fontSize': '38px', 'padding': '18px 12px 20px',
            'borderRadius': '0 0 10px 10px', 'textAlign': 'center',
        }),
    ], style={
        'flex': '1', 'margin': '0 5px',
        'borderRadius': '10px',
        'boxShadow': '0 2px 10px rgba(30,41,59,0.10)',
    })


HDR_CELL = {
    'backgroundColor': HDRB, 'color': ACCENT, 'fontWeight': '700',
    'fontSize': '14px', 'padding': '11px 16px',
    'borderRadius': '8px', 'margin': '0 4px', 'textAlign': 'center',
}
VAL_CELL = {
    'backgroundColor': '#FFFFFF', 'color': DARK,
    'fontSize': '18px', 'fontWeight': '700',
    'padding': '13px 16px', 'borderRadius': '8px',
    'margin': '0 4px', 'textAlign': 'center',
    'boxShadow': '0 2px 8px rgba(30,41,59,0.08)',
}

# ─── Layout ───────────────────────────────────────────────────────────────────
app.layout = html.Div(
    style={'backgroundColor': BG, 'minHeight': '100vh',
           'fontFamily': "'Segoe UI', sans-serif", 'padding': '36px 24px'},
    children=[
        # Title
        html.H2('CLC - KEMUNING KEMBAR', style={
            'textAlign': 'center', 'color': ACCENT,
            'margin': '0 0 4px 0', 'fontWeight': '700', 'letterSpacing': '1px',
        }),
        html.P(id='subtitle', style={
            'textAlign': 'center', 'color': GRAY,
            'margin': '0 0 28px 0', 'fontSize': '14px',
        }),

        # Controls
        html.Div([
            html.Div([
                html.Label('Pilih Tanggal', style={
                    'fontWeight': '600', 'fontSize': '13px',
                    'color': DARK, 'marginBottom': '5px', 'display': 'block'
                }),
                dcc.DatePickerSingle(
                    id='date-picker',
                    date=today.strftime('%Y-%m-%d'),
                    display_format='DD/MM/YYYY',
                    first_day_of_week=1,
                    style={'fontSize': '14px'},
                ),
            ]),
        ], style={'display': 'flex', 'alignItems': 'flex-end',
                  'justifyContent': 'center', 'marginBottom': '28px'}),

        # Table
        html.Div(id='table-section',
                 style={'maxWidth': '840px', 'margin': '0 auto 20px auto'}),

        # Cards
        html.Div(id='cards-section',
                 style={'maxWidth': '840px', 'margin': '0 auto'}),
    ],
)


# ─── Callback ─────────────────────────────────────────────────────────────────
@app.callback(
    Output('subtitle',      'children'),
    Output('table-section', 'children'),
    Output('cards-section', 'children'),
    Input('date-picker', 'date'),
)
def update(date_str):
    if not date_str:
        return '', '', ''

    cutoff = datetime.strptime(date_str[:10], '%Y-%m-%d')

    try:
        r = compute(cutoff)
    except Exception as e:
        msg = html.P(f'Error: {e}', style={'color': 'red', 'textAlign': 'center'})
        return f'Error', msg, ''

    month_id = MONTH_ID[cutoff.month]
    subtitle = f'Tanggal 1 - {cutoff.day} {month_id} {cutoff.year}'

    # Table header
    header = html.Div([
        html.Div('Sales',   style={**HDR_CELL, 'flex': '1.6', 'textAlign': 'left'}),
        html.Div('KOTAK 1', style={**HDR_CELL, 'flex': '1'}),
        html.Div('KOTAK 2', style={**HDR_CELL, 'flex': '1'}),
        html.Div('KOTAK 3', style={**HDR_CELL, 'flex': '1.4'}),
    ], style={'display': 'flex', 'marginBottom': '6px'})

    # Table data row
    data_row = html.Div([
        html.Div('KEKE',            style={**VAL_CELL, 'flex': '1.6', 'textAlign': 'left',
                                           'fontSize': '14px', 'fontWeight': '400'}),
        html.Div(f"{r['BOX1']:,}",  style={**VAL_CELL, 'flex': '1'}),
        html.Div(f"{r['BOX2']:,}",  style={**VAL_CELL, 'flex': '1'}),
        html.Div(f"{r['BOX3']:,}",  style={**VAL_CELL, 'flex': '1.4'}),
    ], style={'display': 'flex'})

    table = html.Div([header, data_row])

    # Cards
    card_defs = [
        ('A  →  Closing Rate',        r['A'], '#ADD3FA', '#1e3a5f'),
        ('B  →  Closing dari Kotak 2', r['B'], '#B9EBFA', '#0c5f70'),
        ('C  →  Lanjutan',             r['C'], '#FAEFC3', '#7a5c0a'),
        ('D  →  Referral',             r['D'], '#FAD4C8', '#7a2c18'),
    ]
    cards = html.Div(
        [card_box(lbl, val, bg, tc) for lbl, val, bg, tc in card_defs],
        style={'display': 'flex'},
    )

    return subtitle, table, cards


# ─── Entry point ──────────────────────────────────────────────────────────────
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8050))
    app.run(debug=False, host='0.0.0.0', port=port)
