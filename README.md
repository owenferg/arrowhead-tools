# Arrowhead Tools

An ArcGIS Pro Python toolbox that creates and rotates arrowhead points for arrow lines, and merges new line, point, or polygon data into the existing GIUM layers as dated, Mapbox-ready releases. It was created to assist the data pipeline of CMS's Global Atlas of Ungulate Migration interactive web map, via the InfoGraphics Lab at the University of Oregon. Created by Owen Ferguson.

## Download

[Download the latest ArcGIS Pro toolbox](https://github.com/owenferg/arrowhead-tools/releases/latest/)

Extract the ZIP, then add `arrow_tools.pyt` to the ArcGIS Pro Catalog pane.
Keep all of the extracted files in the same folder.

## Requirements

- ArcGIS Pro 3.3 or newer with ArcPy (Basic license or higher)
- Arrowhead points and lines with defined spatial references
- An editable arrowhead point layer with an Object ID field

No third-party Python packages are required.

## Install

Keep the files in `toolbox/` together:

- `arrow_tools.pyt`
- `arcpy_common.py`
- `arrow_creation_arcpy.py`
- `arrow_rotation_arcpy.py`
- `arrow_rotation_core.py`
- `gium_integration_arcpy.py`
- `gium_integration_core.py`
- `arrowhead_symbology.lyrx`

In ArcGIS Pro, add `arrow_tools.pyt` to the Catalog pane. The toolbox includes three tools:

- **Create Arrowheads from Line Endpoints** creates a new arrowhead point layer when you only have lines. The new layer is added to the map with its arrowhead symbol and rotation already set up.
- **Update Existing Arrowhead Rotations** updates arrowhead points that already exist.
- **Integrate Data into Existing GIUM Layers** creates safe, dated copies of existing GIUM production layers from new line, point, or polygon data, along with a QA report that flags anything worth checking before uploading to Mapbox.

