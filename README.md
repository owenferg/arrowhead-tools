# Arrowhead Tools

An ArcGIS Pro Python toolbox that creates arrowhead points from line endpoints or rotates existing arrowhead points to match nearby lines. It was created to assist the data pipeline of CMS's Global Atlas of Ungulate Migration interactive web map, via the InfoGraphics Lab at the University of Oregon. Created by Owen Ferguson.

## Download

[Download the latest ArcGIS Pro toolbox](https://github.com/owenferg/arrowhead-tools/releases/latest/)

Extract the ZIP, then add `arrow_tools.pyt` to the ArcGIS Pro Catalog pane.
Keep all six extracted Python files in the same folder.

## Requirements

- ArcGIS Pro 3.3 or newer with ArcPy (Basic license or higher)
- Arrowhead points and lines with defined spatial references
- An editable arrowhead point layer with an Object ID field

No third-party Python packages are required.

## Install

Keep the six Python files in `toolbox/` together:

- `arrow_tools.pyt`
- `arrow_creation_arcpy.py`
- `arrow_rotation_arcpy.py`
- `arrow_rotation_core.py`
- `gium_integration_arcpy.py`
- `gium_integration_core.py`

In ArcGIS Pro, add `arrow_tools.pyt` to the Catalog pane. The toolbox includes three tools:

- **Create Arrowheads from Line Endpoints** creates a new arrowhead point layer when you only have lines.
- **Update Existing Arrowhead Rotations** updates arrowhead points that already exist.
- **Integrate Arrow Data into GIUM Atlas Layers** creates safe, dated GIUM line and point-label releases from new lines and Part 1 arrowheads.

## Create arrowheads when you only have lines

Open **Create Arrowheads from Line Endpoints** and provide the line layer. The tool accepts:

- **Lines:** the lines used to create the new arrowheads. Selections and definition queries are honored.
- **Arrowhead placement:** creates points at the end of each line by default. You can instead use the start of each line, both ends, or a custom field that controls placement for each line.
- **Custom placement field:** available when arrowhead placement is `CUSTOM`. True values create arrowheads at both ends of a line, while false values create an arrowhead at the end only. The field can contain Short, Long, or Big Integer `1`/`0` values, or Text `true`/`false` values; text matching is case-insensitive and ignores surrounding whitespace. Nulls and other values are rejected.
- **Rotation field name:** the numeric field that receives the calculated rotation. The default is `Rotation`.
- **Rotation buffer (degrees):** an offset added to each calculated rotation. The default is `+3` degrees clockwise; negative values rotate counterclockwise.
- **Output arrowheads:** the new point feature class. The default name is the line layer name followed by `_Arrowheads`.

Each output point includes the source line's editable attributes, its source Object ID and part number, whether it came from the start or end of the line, and its rotation. Multipart lines create the selected arrowheads for each open part, using the line's custom placement value for every part when applicable. Closed and unusable parts are skipped.

The output contains the data needed for field-driven marker rotation, but the tool does not choose an arrowhead symbol. In the layer's symbology properties, select your preferred marker and use the generated rotation field to control its rotation.

## Update rotations when you already have arrowhead points

See `README.txt` in the download folder for step-by-step instructions for use in ArcGIS Pro.

Open **Update Existing Arrowhead Rotations**. The tool accepts:

- **Arrowhead points:** point features that are updated in place.
- **Lines:** line features whose start or end directions control rotation.
- **Maximum endpoint match distance:** the largest allowed distance between an arrowhead and a line endpoint.
- **Rotation field name:** the numeric field that receives the calculated rotation. The tool creates it when needed.
- **Rotation buffer (degrees):** an offset added to each calculated rotation. The default is `+3` degrees clockwise; negative values rotate counterclockwise. I found that +3 generally has the best results.
- **Audit output table:** an optional table containing each point Object ID, match status, and final buffered rotation.

Rotations are measured clockwise from east and normalized to the range `0–360`. Arrowheads with no endpoint inside the match distance, or with an exact tie between endpoints, are not changed.

An existing audit table is replaced only when ArcGIS Pro's **Overwrite outputs** setting is enabled.

For layers in a geographic coordinate system, the tool performs distance and direction calculations in WGS 1984 Web Mercator Auxiliary Sphere and reports a warning.

## Integrate new arrows into the GIUM Atlas datasets

The third tool is specific to the Global Initiative on Ungulate Migration workflow. It can update either or both of the two GIUM production datasets involved in an arrow release:

- the latest complete `SeasonalArrows` line shapefile; and
- the latest complete `GIUMPointLabels` point shapefile, which stores arrowheads alongside other point labels.

The tool never edits those targets. It creates new dated shapefiles named like `SeasonalArrowsMerged_June10_2026` and `GIUMPointLabelsMerged_June10_2026`, explicitly projects selected new features to the corresponding target coordinate system, maps the known GIUM fields, fills only missing metadata, and verifies that historical and new feature counts are preserved. It also creates a zipped seasonal-arrow shapefile, a formatted WGS 84 point-label GeoJSON file, and a CSV QA report.

Both branches are enabled by default but can be run independently. New-input selections and definition queries are honored; selections on historical targets are intentionally ignored so an incomplete production release cannot be created accidentally. Existing dated releases are never overwritten.

## Test

Run the tests that do not require ArcGIS Pro:

```shell
python -m unittest discover -s tests -p "test_*.py"
```

Run the ArcGIS smoke test from the ArcGIS Pro Python window or an authorized Python Command Prompt:

```python
exec(open(r"C:\path\to\arrowhead-tools\tests\arcgis_pro_smoke_test.py").read())
```

The smoke test creates disposable geodatabase and shapefile data, exercises all three tools, and checks selections, complete-history preservation, projection coordinates, field precedence, packaging, rollback, buffered rotations, and audit outputs before removing the temporary data.