# MiCA-Register (ESMA) als durchsuchbare Datenbank

Täglicher, inoffizieller Spiegel des [ESMA Interim MiCA Registers](https://www.esma.europa.eu/esmas-activities/digital-finance-and-innovation/markets-crypto-assets-regulation-mica).

- `scripts/update.py` lädt die fünf CSVs (CASPS, NCASP, EMTWP, ARTZZ, OTHER) und pflegt `docs/mica.db` (SQLite) mit Verlauf (`_first_seen`, `_removed_at`).
- `.github/workflows/update.yml` führt das täglich aus und committet Änderungen. Manuell startbar unter Actions → Run workflow.
- `docs/index.html` ist die Webseite (GitHub Pages, Quelle: Branch `main`, Ordner `/docs`). Filter, Suche, SQL-Feld und CSV-Export laufen im Browser über sql.js.
- `data/raw/` enthält die Roh-CSVs; die Git-Historie ist damit auch ein Änderungsprotokoll.

Lokaler Test ohne ESMA-Zugriff: `MICA_SOURCE_DIR=./ordner_mit_csvs python scripts/update.py`

`_first_seen` ist der Tag, an dem dieser Spiegel eine Zeile erstmals gesehen hat, nicht das ESMA-Aufnahmedatum. Beim ersten Lauf tragen alle Zeilen das Startdatum.
