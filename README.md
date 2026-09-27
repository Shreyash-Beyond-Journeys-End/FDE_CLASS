# FDE Data Foundations coursework

**Assignment:** green taxi street hails and the Boro Zone, a monthly pipeline on NYC TLC data (Track B).
See [assignment/README.md](assignment/README.md).

**Exercises:** the FlashEats notebooks for classes 5 to 8 are in [exercises/](exercises/). Class 8 runs the
pipeline in `exercises/FlashEats_Classroom_Pack_V2/Class8_Project/`, and my Gate 2 readiness review is
`GATE2_DATA_READINESS.md` in that folder.

**Setup:** the TLC data isn't included. The first pipeline run downloads it (about 17 MB) into
`assignment/data/raw/` and builds the database in `assignment/data/processed/`:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r assignment/requirements.txt -r exercises/requirements.txt
cd assignment
python run_all.py --start 2025-07 --end 2026-06
```

The exercise notebooks don't need a download.
