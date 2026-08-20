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
- **Integrate Data into Existing GIUM Layers** creates safe, dated copies of existing GIUM production layers from new line, point, or polygon data.

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
