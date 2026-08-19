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
three tools. The first two create or rotate arrowheads; the third creates versioned
GIUM Atlas release datasets.

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
- Rotation buffer (degrees): a buffer that adjusts each arrow by a certain amount of
degrees. I found the default of 3 to produce best results, but change to 0 if you think
the arrowheads are off (or any other integer).
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
- Maximum endpoint distance: determines how far the script searches for an endpoint 
of a line from the arrowhead point. I recommend not changing this unless you have points
that are not directly on the start or ending points of your lines.
- Rotation field: the field from your arrowhead points layer that determines rotation 
values. Choose from the dropdown if the default "Rotation" isn't accurate (ArcGIS Pro
will give you a warning if this is the case).
- Rotation buffer (degrees): a buffer that adjusts each arrow by a certain amount of
degrees. I found the default of 3 to produce best results, but change to 0 if you think
the arrowheads are off (or any other integer).
- Audit output table: Optional; creates a table that contains information about the
results of executing the script. Mostly just for debugging.

When parameters are set to your preference, press "Run". 

For integrating new arrows into GIUM Atlas production layers:

Open "Integrate Arrow Data into GIUM Atlas Layers"

- Leave both processing switches on for a normal arrow release. Turn one off only if
  you intentionally need a line-only or point-only release.
- Existing SeasonalArrows target: choose the complete latest seasonal-arrow line
  shapefile (.shp), not a geodatabase feature class. The tool ignores selections
  on this historical target so old features are not accidentally omitted.
- New seasonal arrow lines: choose the new line layer. Selections and definition
  queries are honored.
- Line geographic transformation: Optional; only needed when the new lines and the
  existing target use different datums. The tool fills in ArcGIS Pro's recommended
  transformation once both line layers are chosen, so leave it alone unless you 
  require a specific one. The dropdown stays empty when both layers already
  share a coordinate system, and any value entered then is ignored. If the datums
  differ but the dropdown is still empty, ArcGIS has no transformation that covers
  your area and this tool will stop. Run the ArcGIS Pro Project tool on the new
  lines to convert them to the target coordinate system, then choose that projected
  layer here and run again.
- Existing GIUMPointLabels target: choose the complete latest point-label
  shapefile (.shp), not a geodatabase feature class.
- New arrowhead points: choose the arrowhead output made by the first tool.
- Point geographic transformation: Optional; works exactly like the line version
  above, but for the two point layers.
- Herd name, Country, Season, Line class, and Point type fill blank values on the new
  features only. Values that are already present are preserved. Point type defaults
  to "Arrowhead".
- Release date: controls readable names such as GIUMPointLabelsMerged_June10_2026.
- Release output folder: choose a writable folder for the new shapefiles, ZIP,
  GeoJSON, and QA CSV. Existing releases are never overwritten.

The tool validates geometry, projections, GIUM fields, metadata, rotation, and row
counts before it publishes the dated outputs. It never changes the selected target
or source datasets. Review the old and new layers visually in ArcGIS Pro before
uploading anything to Mapbox. Detailed instructions and error guidance are in
docs/gium-integration-workflow.md in the full repository.

To see changes made by the first two tools reflected in ArcGIS Pro,
go to the layer's Symbology and go to the "Vary symbol by attribute" tab.
Pick the rotation field and set the rotation to Geographic.

Any questions? Reach out to Owen Ferguson:
owen@owenferg.com
owenf@uoregon.edu

Visit the Github repository for more information:
https://github.com/owenferg/arrowhead-tools

Arrowhead Tools v1.5
