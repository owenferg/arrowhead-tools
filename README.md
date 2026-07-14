# Arrowhead Tools

An ArcGIS Pro Python toolbox that rotates arrowhead point features to match nearby line endpoints. It was created to assist the data pipeline of CMS's Global Atlas of Ungulate Migration interactive web map, via the InfoGraphics Lab at the University of Oregon. Created by Owen Ferguson.

## Download

[Download the latest ArcGIS Pro toolbox](https://github.com/owenferg/arrowhead-tools/releases/latest/download/arrowhead-tools-toolbox.zip)

Extract the ZIP, then add `arrowTools.pyt` to the ArcGIS Pro Catalog pane.
Keep all three extracted files in the same folder.

## Requirements

- ArcGIS Pro with ArcPy
- Arrowhead points and lines with defined spatial references
- An editable arrowhead point layer with an Object ID field

No third-party Python packages are required.

## Install

Keep the three files in `toolbox/` together:

- `arrowTools.pyt`
- `arrow_tool.py`
- `arrow_rotation_core.py`

In ArcGIS Pro, add `arrowTools.pyt` to the Catalog pane and open **Calculate Arrowhead Rotations**.

## Use

The tool accepts:

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
