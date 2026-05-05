# USDA Web Soil Survey – Automated PDF Report Generator

Automates the generation of 3 soil report PDFs per property from the [USDA Web Soil Survey](https://websoilsurvey.nrcs.usda.gov/app/WebSoilSurvey.aspx). Takes a folder of shapefiles (one boundary polygon per property) and produces a sub-folder of PDFs for each property.

## Output files (per property)

| File | Report |
|------|--------|
| `{Stand}_SoilSurvey.pdf` | Soil Map |
| `{Stand}_forestprod.pdf` | Forestland Productivity |
| `{Stand}_ErosionHazard_Off-Road_Off-Trail.pdf` | Erosion Hazard (Off-Road, Off-Trail) |

---

## Requirements

- Python 3.9 or newer
- Internet connection (the script drives the live WSS website)

---

## One-time setup

**Mac / Linux**

```bash
chmod +x setup.sh
./setup.sh
```

**Windows**

```bat
setup.bat
```

Both scripts install the Python dependencies (`playwright`) and download the Chromium browser that Playwright uses.

---

## Input folder structure

Each property needs a shapefile group named `{Stand}_boundary.*` with at minimum `.shp`, `.shx`, and `.prj` files present. Optional extras (`.dbf`, `.cpg`) are included automatically if found.

```
1063Test/
  NKL-4_boundary.shp
  NKL-4_boundary.shx
  NKL-4_boundary.prj
  NKL-4_boundary.dbf
  NKL-4_boundary.cpg
  NKL-5_boundary.shp
  ...
```

The `Stand` name is derived from the filename prefix before `_boundary` (e.g. `NKL-4`).

---

## Usage

```bash
# Process all properties in 1063Test/, save to ./output/ (default)
python3 wss_automation.py 1063Test

# Specify a custom output folder
python3 wss_automation.py 1063Test my_reports

# Run headless (no visible browser window)
python3 wss_automation.py 1063Test my_reports --headless

# Run 2 properties in parallel (use cautiously — WSS may rate-limit)
python3 wss_automation.py 1063Test my_reports --workers 2
```

On Windows replace `python3` with `python`.

### Arguments

| Argument | Default | Description |
|----------|---------|-------------|
| `input_dir` | `1063Test` | Folder containing `*_boundary` shapefile sets |
| `output_dir` | `output` | Destination for per-property PDF sub-folders |
| `--headless` | off | Hide the browser window |
| `--workers N` | `1` | Number of properties to process simultaneously |

---

## Output folder structure

```
output/
  NKL-4/
    NKL-4_SoilSurvey.pdf
    NKL-4_forestprod.pdf
    NKL-4_ErosionHazard_Off-Road_Off-Trail.pdf
  NKL-5/
    NKL-5_SoilSurvey.pdf
    ...
```

---

## Validation and stop-on-failure

After each property finishes, the script checks that all 3 PDFs were created on disk. If any are missing, it prints a clear error and stops processing further properties. A summary table is printed at the end showing each property's result.

```
════════════════════════════════════════════════════════════
  SUMMARY
════════════════════════════════════════════════════════════
  NKL-4        ✓  3/3 PDFs
  NKL-5        ✗  2/3 PDFs  — missing: NKL-5_ErosionHazard_Off-Road_Off-Trail.pdf
  NKL-6        ⏭  skipped
════════════════════════════════════════════════════════════
```

To re-run only failed properties, point the script at a folder containing just those shapefiles.

---

## Parallel workers

With `--workers 2`, two properties run simultaneously in separate browser windows. Each has its own session and output folder — the `[Stand]` prefix in terminal output keeps them identifiable.

Recommended limit is `--workers 2`. Higher values risk WSS rate-limiting requests and causing timeouts.

---

## Example – test on a single property

```bash
python3 wss_automation.py RRG-2 test_output
```

Compare the generated PDFs in `test_output/RRG-2/` against the reference files in the project root (`RRG-2 Soil Survey.pdf`, `RRG2_forestprod.pdf`, `RRG2Erosion_Hazard_Off-Road_Off-Trail.pdf`).

---

## Troubleshooting

**Browser window opens but nothing happens**
The WSS website can be slow on first load, especially for a region that hasn't been recently requested. Default timeouts are 90 s for navigation and 180 s for map/report rendering. If the script times out, re-running usually succeeds as WSS caches recent results server-side.

**"Non-fatal errors creating printable version"**
WSS sometimes shows this warning after generating a PDF (typically an SVG rendering note). The script automatically dismisses it and the PDF is still valid.

**A property fails but others succeed**
Each property runs in its own isolated browser context. A failure on one stops subsequent properties from starting but does not affect any already in progress. Re-run with a folder containing only the failed property's shapefiles.

**Windows path issues**
Use forward slashes or quote paths with spaces:
```bat
python wss_automation.py "My Shapefiles" "My Reports"
```
