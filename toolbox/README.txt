<-- ARROWHEAD TOOLS -->

Keep all six Python files together when using the toolbox:

- arrow_tools.pyt
- arrow_creation_arcpy.py
- arrow_rotation_arcpy.py
- arrow_rotation_core.py
- gium_integration_arcpy.py
- gium_integration_core.py

How to use:

1. Open your ArcGIS Pro project
2. In the Catalog pane, right click Toolboxes and select Add Toolbox
3. Navigate to the toolbox folder and select arrow_tools.pyt, hit OK
4. The toolbox should now appear under Toolboxes. Expand arrow_tools.pyt. There are
three tools. The first two create or rotate arrowheads; the third adds new
line, point, or polygon data to existing GIUM Atlas layers.

For creating arrowheads when you only have lines:

Open "Create Arrowheads from Line Endpoints"

- Lines: select the layer that contains the arrow lines. Any selection or definition
query on the layer will be honored.
- Arrowhead placement: creates arrowheads at the end of each line by default. This
can be changed to the start of each line, both the start and end, or CUSTOM.
- Custom placement field: available when arrowhead placement is CUSTOM. Select a
Short, Long, or Big Integer field containing 1/0, or a Text field containing
true/false. True creates arrowheads at both ends of the line, while false creates an
arrowhead at the end only. Text matching is case-insensitive and ignores surrounding
spaces. Nulls and other values are rejected.
- Rotation field name: the field in the new layer that will contain rotation values.
Default is "Rotation"; update this field if you would prefer a different field name.
- Clockwise rotation buffer (degrees): a buffer that adjusts each arrow by a certain amount of
degrees. The default of 0 is recommended; change it only if your arrowheads look
consistently off, in which case any positive or negative integer works.
- Output arrowheads: location and name for the new arrowhead point layer. By default,
the name is your arrow line layer followed by _Arrowheads.

The new layer keeps the editable fields from the lines and adds fields for the source
line, multipart part, endpoint type, and arrowhead rotation. For multipart lines,
CUSTOM applies the line's placement value to every open part. The script creates
rotation-ready data, but does not choose your arrowhead symbol. Set up your preferred
marker in the layer's symbology and use the rotation field to rotate it.

For updating rotations when you already have arrowhead points:

Open "Update Existing Arrowhead Rotations"

- Arrowhead points: select your layer that contains the arrowhead points.
- Lines: select your layer that contains the arrow's lines.
- Maximum endpoint match distance: determines how far the script searches for an
endpoint of a line from the arrowhead point. The default is 5 Meters. I recommend not
changing this unless you have points that are not directly on the start or ending
points of your lines.
- Rotation field name: the field from your arrowhead points layer that determines
rotation values. Choose from the dropdown if the default "Rotation" isn't accurate
(ArcGIS Pro will give you a warning if this is the case).
- Clockwise rotation buffer (degrees): a buffer that adjusts each arrow by a certain amount of
degrees. The default of 0 is recommended; change it only if your arrowheads look
consistently off, in which case any positive or negative integer works.
- Audit output table: Optional; creates a table that contains information about the
results of executing the script. Mostly just for debugging.

When parameters are set to your preference, press "Run". 

For integrating new data into existing GIUM Atlas layers:

Open "Integrate Data into Existing GIUM Layers"

Add one row to the datasets table for each production layer you are updating.
A normal arrow release uses two rows (Seasonal arrows and GIUM point labels).
A barriers-only or protected-areas-only run uses one row. You can add as many
rows as you need for a bulk release; nothing is written unless every row
passes every check.

- Layer type: choose Seasonal arrows, GIUM point labels, Linear barriers,
  Point barriers, Polygon features, Protected areas, Line labels, or Other.
  This controls which fields are required, the output filename, and the
  default package (ZIP for most layers, GeoJSON for point labels).
- Existing production shapefile: choose the complete latest .shp file for
  that layer, not a geodatabase feature class. The tool ignores selections
  on this historical target so old features are not accidentally omitted.
- New data: choose the layer you are adding. Selections and definition
  queries are honored. The geometry type must match the layer type
  (polygons for protected areas, lines for seasonal arrows, and so on).
- Class, Type, and Season: optional; fill blanks on the new features only.
  Values already present are preserved. For Class and Type, type these to
  match existing spelling and capitalization when you can; the tool warns
  if the value is not already in the target, which usually means a typo.
  Ignore the warning when you are intentionally adding a new category.
  Season is not spell-checked because new season names are added often.
- Package as: optional. Leave blank to use the default for that layer type
  (ZIP for most layers, GeoJSON for point labels). Choose Both when you
  need a shapefile ZIP and a GeoJSON from the same row.
- Geographic transformation: optional; only needed when the new data and
  the existing target use different datums. Type a transformation name if
  you need a specific one; otherwise leave it blank and the tool picks
  ArcGIS Pro's recommended transformation. If the datums differ and ArcGIS
  has no transformation that covers your area, this tool will stop. Run
  the ArcGIS Pro Project tool on the new data to convert it to the target
  coordinate system, then choose that projected layer here and run again.
- Herd name and Country: fill blanks on every row. Values already present
  are preserved.
- Release date: controls readable names such as GIUMPointLabelsMerged_June10_2026
  and GIUMIntegration_June10_2026_QA.csv.
- Release output folder: choose a writable folder for the new shapefiles,
  ZIP or GeoJSON packages, and QA CSV. Existing releases are never overwritten.

The tool stops before copying if the new data contains multipart features.
Mapbox cannot import multipart data, so run Multipart To Singlepart first
and choose that result.

The tool validates geometry, projections, GIUM fields, metadata, rotation,
and row counts before it publishes the dated outputs. It never changes the
selected target or source datasets. Review the old and new layers visually
in ArcGIS Pro before uploading anything to Mapbox.

To see changes made by the first two tools reflected in ArcGIS Pro,
go to the layer's Symbology and go to the "Vary symbol by attribute" tab.
Pick the rotation field and set the rotation to Geographic.

Any questions? Reach out to Owen Ferguson:
owen@owenferg.com
owenf@uoregon.edu

Visit the Github repository for more information:
https://github.com/owenferg/arrowhead-tools

Arrowhead Tools v1.5
