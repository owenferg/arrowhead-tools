# Arrowhead Tools

An ArcGIS Pro Python toolbox that creates arrowhead points from line endpoints or rotates existing arrowhead points to match nearby lines. It was created to assist the data pipeline of CMS's Global Atlas of Ungulate Migration interactive web map, via the InfoGraphics Lab at the University of Oregon. Created by Owen Ferguson.

## Download

[Download the latest ArcGIS Pro toolbox](https://github.com/owenferg/arrowhead-tools/releases/latest/)

Extract the ZIP, then add `arrow_tools.pyt` to the ArcGIS Pro Catalog pane.
Keep all four extracted Python files in the same folder.

## Requirements

- ArcGIS Pro with ArcPy
- Arrowhead points and lines with defined spatial references
- An editable arrowhead point layer with an Object ID field

No third-party Python packages are required.

## Install

Keep the four Python files in `toolbox/` together:

- `arrow_tools.pyt`
- `arrow_creation_arcpy.py`
- `arrow_rotation_arcpy.py`
- `arrow_rotation_core.py`

In ArcGIS Pro, add `arrow_tools.pyt` to the Catalog pane. The toolbox includes two tools:

- **Create Arrowheads from Line Endpoints** creates a new arrowhead point layer when you only have lines.
- **Update Existing Arrowhead Rotations** updates arrowhead points that already exist.

## Create arrowheads when you only have lines

Open **Create Arrowheads from Line Endpoints** and provide the line layer. The tool accepts:

- **Lines:** the lines used to create the new arrowheads. Selections and definition queries are honored.
- **Arrowhead placement:** creates points at the end of each line by default. You can instead use the start of each line or both ends.
- **Rotation field name:** the numeric field that receives the calculated rotation. The default is `Rotation`.
- **Rotation buffer (degrees):** an offset added to each calculated rotation. The default is `+3` degrees clockwise; negative values rotate counterclockwise.
- **Output arrowheads:** the new point feature class. The default name is the line layer name followed by `_Arrowheads`.

Each output point includes the source line's editable attributes, its source Object ID and part number, whether it came from the start or end of the line, and its rotation. Multipart lines create the selected arrowheads for each open part. Closed and unusable parts are skipped.

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

## Test

Run the tests that do not require ArcGIS Pro:

```shell
python -m unittest discover -s tests -p "test_*.py"
```

Run the ArcGIS smoke test from the ArcGIS Pro Python window or an authorized Python Command Prompt:

```python
exec(open(r"C:\path\to\arrowhead-tools\tests\arcgis_pro_smoke_test.py").read())
```

The smoke test creates a temporary geodatabase, runs the toolbox logic, checks the buffered rotations and audit statuses, and removes the temporary data.

## License

Released under the [MIT License](LICENSE).
