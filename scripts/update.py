#!/usr/bin/env python3
"""Laedt die fuenf CSV-Dateien des ESMA Interim MiCA Registers und pflegt eine SQLite-Datenbank.

Pro Register gibt es eine Tabelle mit allen CSV-Spalten plus:
  _key          Hash der Zeile (identische Zeile = identischer Schluessel)
  _first_seen   Datum, an dem die Zeile erstmals in der CSV gesehen wurde
  _removed_at   Datum, an dem die Zeile aus der CSV verschwunden ist (NULL = aktuell enthalten)
Datumsspalten im Format TT/MM/JJJJ bekommen zusaetzlich eine ISO-Spalte <name>_iso (JJJJ-MM-TT).

Aenderungen an einer Zeile erscheinen als neue Zeile (neuer _key); die alte Zeile bekommt _removed_at.
Mit MICA_SOURCE_DIR=<ordner> werden die CSVs lokal gelesen (zum Testen).
"""
import csv
import datetime as dt
import hashlib
import io
import json
import os
import re
import sqlite3
import sys
import urllib.request

BASE = "https://www.esma.europa.eu/sites/default/files/2024-12/"
REGISTERS = {
    "casps": "CASPS.csv",
    "ncasp": "NCASP.csv",
    "emtwp": "EMTWP.csv",
    "artzz": "ARTZZ.csv",
    "other": "OTHER.csv",
}
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "docs", "mica.db")
STATUS_PATH = os.path.join(ROOT, "docs", "status.json")
RAW_DIR = os.path.join(ROOT, "data", "raw")
DATE_RE = re.compile(r"^\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*$")


def fetch(name):
    src = os.environ.get("MICA_SOURCE_DIR")
    if src:
        with open(os.path.join(src, name), "rb") as f:
            return f.read()
    req = urllib.request.Request(
        BASE + name, headers={"User-Agent": "mica-register-mirror (GitHub Actions)"}
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def decode(raw):
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def parse(text):
    sample = text[:5000]
    try:
        delim = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        delim = ","
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        raise ValueError("leere CSV")
    header = [h.strip() for h in rows[0]]
    keep = [i for i, h in enumerate(header) if h]  # leere Spalten (Komma am Zeilenende) ignorieren
    seen, cols = {}, []
    for i in keep:
        h = re.sub(r"\W+", "_", header[i]).strip("_") or "col"
        seen[h] = seen.get(h, 0) + 1
        cols.append(h if seen[h] == 1 else f"{h}_{seen[h]}")
    out = []
    for r in rows[1:]:
        r = r + [""] * (len(header) - len(r))
        out.append({c: r[i].strip() for c, i in zip(cols, keep)})
    return cols, out


def iso(v):
    m = DATE_RE.match(v or "")
    if not m:
        return None
    d, mo, y = map(int, m.groups())
    try:
        return dt.date(y, mo, d).isoformat()
    except ValueError:
        return None


def row_key(row):
    payload = json.dumps(sorted(row.items()), ensure_ascii=False)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def ensure_table(con, table, cols):
    q = lambda c: '"' + c + '"'
    existing = {r[1] for r in con.execute(f'PRAGMA table_info("{table}")')}
    if not existing:
        base = ["_key TEXT PRIMARY KEY", "_first_seen TEXT", "_removed_at TEXT"]
        con.execute(f'CREATE TABLE "{table}" ({", ".join(base)})')
        existing = {"_key", "_first_seen", "_removed_at"}
    for c in cols:
        if c not in existing:
            con.execute(f'ALTER TABLE "{table}" ADD COLUMN {q(c)} TEXT')
            existing.add(c)
    return existing


def update_register(con, table, raw, today):
    cols, rows = parse(decode(raw))
    date_cols = [c for c in cols if any(r.get(c) and DATE_RE.match(r[c]) for r in rows)]
    iso_cols = [c + "_iso" for c in date_cols]
    ensure_table(con, table, cols + iso_cols)
    q = lambda c: '"' + c + '"'

    current = {}
    for r in rows:
        for c in date_cols:
            r[c + "_iso"] = iso(r.get(c))
        current[row_key({k: v for k, v in r.items() if not k.endswith("_iso")})] = r

    known = {k: rem for k, rem in con.execute(f'SELECT _key, _removed_at FROM "{table}"')}
    added = removed = reactivated = 0
    all_cols = cols + iso_cols
    for k, r in current.items():
        if k not in known:
            con.execute(
                f'INSERT INTO "{table}" (_key,_first_seen,_removed_at,{",".join(map(q, all_cols))}) '
                f'VALUES (?,?,NULL,{",".join("?" * len(all_cols))})',
                [k, today] + [r.get(c) for c in all_cols],
            )
            added += 1
        elif known[k] is not None:
            con.execute(f'UPDATE "{table}" SET _removed_at=NULL WHERE _key=?', (k,))
            reactivated += 1
    for k, rem in known.items():
        if k not in current and rem is None:
            con.execute(f'UPDATE "{table}" SET _removed_at=? WHERE _key=?', (today, k))
            removed += 1
    return {"rows": len(current), "added": added, "removed": removed, "reactivated": reactivated}


def create_views(con):
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    con.execute("DROP VIEW IF EXISTS casps_entities")
    if "casps" in tables:
        cols = {r[1] for r in con.execute('PRAGMA table_info("casps")')}
        if {"ae_lei", "ae_lei_name"} <= cols:
            con.execute(
                """CREATE VIEW casps_entities AS
                   SELECT ae_lei, MAX(ae_lei_name) AS name, MAX(ae_homeMemberState) AS land,
                          MAX(ae_competentAuthority) AS behoerde,
                          MIN(_first_seen) AS erstmals_gesehen,
                          SUM(_removed_at IS NULL) AS aktive_zeilen
                   FROM casps GROUP BY ae_lei"""
            )


def main():
    today = dt.date.today().isoformat()
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    os.makedirs(RAW_DIR, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    summary, errors = {}, {}
    for table, name in REGISTERS.items():
        try:
            raw = fetch(name)
            with open(os.path.join(RAW_DIR, name), "wb") as f:
                f.write(raw)
            summary[table] = update_register(con, table, raw, today)
        except Exception as e:  # ein defektes Register soll die anderen nicht blockieren
            errors[table] = f"{type(e).__name__}: {e}"
    create_views(con)
    con.commit()
    con.execute("VACUUM")
    con.close()
    status = {
        "last_checked": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "registers": summary,
        "errors": errors,
    }
    with open(STATUS_PATH, "w", encoding="utf-8") as f:
        json.dump(status, f, indent=2, ensure_ascii=False)
    print(json.dumps(status, indent=2, ensure_ascii=False))
    if errors and not summary:
        sys.exit(1)


if __name__ == "__main__":
    main()
