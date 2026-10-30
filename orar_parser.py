import io
import re
import datetime
import pandas as pd
import openpyxl

DAYS = ['LUNI', 'MARTI', 'MIERCURI', 'JOI', 'VINERI']
HOUR_RE = re.compile(r'^\d{1,2}-\d{1,2}$')
ROOM_RE = re.compile(r'^[ABCDE]\d{2,3}[a-z]?$', re.I)   # tiparul unei săli: A03, B124, B206b

def clean_content(text):
    # scoate sălile din text
    tokens = [t for t in str(text).split()
              if not ROOM_RE.match(t.strip('/,;'))]
    # scoate "/", "-" sau "--" rămase la început sau la sfârșit
    while tokens and tokens[-1].strip('/') in ('', '-', '--'):
        tokens.pop()
    while tokens and tokens[0].strip('/') in ('', '-', '--'):
        tokens.pop(0)
    return ' '.join(tokens)


def is_hour(v):
    return isinstance(v, (datetime.datetime, pd.Timestamp)) or bool(HOUR_RE.match(str(v).strip()))

def time_label(v):
    if isinstance(v, (datetime.datetime, pd.Timestamp)):
        return f'{v.month}-{v.day}'
    return str(v).strip()

def span_label(start, end):
    if start == end:
        return start
    return f"{start.split('-')[0]}-{end.split('-')[1]}"

def split_week(text):
    t = re.sub(r'\s*\n\s*', ' ', text).strip()
    m = re.search(r'\s/\s', t)
    if not m:
        return [('ambele', t)]
    left = t[:m.start()].strip()
    right = t[m.end():].strip().lstrip('/').strip()
    out = []
    if left and left != '--':
        out.append(('impar', left))
    if right and right != '--':
        out.append(('par', right))
    return out

def series_first_col(df, letter):
    for c in df.columns:
        v = str(df.at[0, c]).strip()
        if re.search(rf'SERIA\s+{letter}\b', v, re.I):
            return c
    return None

def merged_ends(ws):
    """(rând_0, col_0) -> ultimul rând_0 al oricărei celule îmbinate (vertical sau orizontal) care începe acolo."""
    ends = {}
    for rng in ws.merged_cells.ranges:
        if rng.max_row > rng.min_row:
            for col in range(rng.min_col, rng.max_col + 1):
                ends[(rng.min_row - 1, col - 1)] = rng.max_row - 1
    return ends

def parse_group(source, group):
    """source: cale sau fișier încărcat. group ex. '422Eb' sau '433Aa'."""
    raw = source.read() if hasattr(source, 'read') else open(source, 'rb').read()
    xls = pd.ExcelFile(io.BytesIO(raw))
    wb = openpyxl.load_workbook(io.BytesIO(raw), data_only=True)

    group = group.strip().lower()
    base = group[:-1] if group[-1] in 'ab' else group       # 422eb -> 422e
    own = group
    other = base + ('a' if group.endswith('b') else 'b') if group[-1] in 'ab' else None
    letter = base[3].upper()
    year = base[1]
    seen = {}
    lectures = []

    for sheet in xls.sheet_names:
        if 'anunturi' in sheet.lower():
            continue
        df = pd.read_excel(xls, sheet_name=sheet, header=None)
        ends = merged_ends(wb[sheet])
        cols = {str(df.at[1, c]).strip().lower(): c for c in df.columns if c >= 2}
        lecture_col = (series_first_col(df, letter)
                       if sheet.strip().endswith(year) else None)
        if own not in cols and lecture_col is None:
            continue

        is_other = df[0].notna() & ~df[0].astype(str).str.upper().str.strip().isin(DAYS)
        end = df.index[is_other & (df.index > 2)]
        end = end[0] if len(end) else len(df)
        block = df.iloc[:end].copy()
        block[0] = block[0].ffill()

        for r in block.index:
            if r < 2:
                continue
            day = str(block.at[r, 0]).strip().upper()
            if day not in DAYS or not is_hour(block.at[r, 1]):
                continue
            t_start = time_label(block.at[r, 1])

            for label in {own, other} & set(cols):
                c = cols[label]
                v = block.at[r, c]
                if pd.notna(v) and str(v).strip():
                    r_end = ends.get((r, c), r)
                    hour = span_label(t_start, time_label(block.at[r_end, 1]))
                    for saptamana, continut in split_week(str(v)):
                        continut=clean_content(continut)
                        if continut in ('', '-', '--'):
                            continue
                        seen.setdefault((sheet, day, hour, saptamana, continut), set()).add(label)

            if lecture_col is not None:
                v = block.at[r, lecture_col]
                if pd.notna(v) and re.search(r'\((curs|c)\)', str(v)):
                    r_end = ends.get((r, lecture_col), r)
                    hour = span_label(t_start, time_label(block.at[r_end, 1]))
                    lectures.append({'Year': sheet, 'Day': day, 'Hour': hour,
                                     'Week': 'ambele',
                                     'Content': clean_content(re.sub(r'\s*\n\s*', ' ', str(v)))})

    rows = []
    for (sheet, day, hour, saptamana, continut), labels in seen.items():
        if own not in labels:
            continue
        rows.append({'Year': sheet, 'Day': day, 'Hour': hour,
                     'Week': saptamana, 'Content': continut})
    return pd.DataFrame(rows + lectures)