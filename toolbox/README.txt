<-- ARROWHEAD TOOLS -->

Keep all of these files together when using the toolbox:

- arrow_tools.pyt
- arcpy_common.py
- arrow_creation_arcpy.py
- arrow_rotation_arcpy.py
- arrow_rotation_core.py
- gium_integration_arcpy.py
- gium_integration_core.py
- arrowhead_symbology.lyrx

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
CUSTOM applies the line's placement value to every open part. When the new layer is
added to the map, it's already drawn with a gray arrowhead that is rotated by the
rotation field, so there's no need to set up the symbology yourself. If you'd like
to use a different arrowhead symbol, replace arrowhead_symbology.lyrx with your own
layer file. Just make sure its rotation is set by the Rotation field and that the
symbol points east (right) before it's rotated.

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
  default package (a ZIP).
- Existing production shapefile: choose the complete latest .shp file for
  that layer, not a geodatabase feature class. The tool ignores selections
  on this historical target so old features are not accidentally omitted.
- New data: choose the layer you are adding. Selections and definition
  queries are honored. The geometry type must match the layer type
  (polygons for protected areas, lines for seasonal arrows, and so on).
- Class, Type, and Season: optional; fill blanks on the new features only.
  Values already present are preserved. If every feature in the production
  shapefile has the same Class or Type (like "Seasonal Movement" for seasonal
  arrows), that value is filled in for you as soon as you pick the shapefile,
  and you can still change it. Otherwise, type these to match existing
  spelling and capitalization when you can; the tool warns if the value is
  not already in the target, which usually means a typo. Ignore the warning
  when you are intentionally adding a new category. Season is not
  spell-checked because new season names are added often. For seasonal
  arrows, if the new lines don't have a Season field, the season is taken
  from their Type field instead, so you only need to type a Season when
  neither field is filled in.
- Package as: optional. Leave blank for the default ZIP, which is what
  Mapbox uses for every layer, including point labels. Choose GeoJSON or
  Both only when you specifically need a GeoJSON.
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
- Split multipart features into single parts: optional, off by default.
  Mapbox cannot import multipart data, so the tool normally stops if the new
  data has any. Check this box to have the tool split them on its own copy of
  the new data instead. Your original data is not changed.
- Project without a datum transformation when ArcGIS has none: optional, off
  by default. Only check this if the tool stops because ArcGIS has no
  transformation between the two datums. Features can shift slightly (anywhere
  from a few meters up to a few hundred depending on the datums), so check
  their placement closely afterward.

The tool validates geometry, projections, GIUM fields, metadata, rotation,
and row counts before it publishes the dated outputs. It never changes the
selected target or source datasets. When it finishes, the new release
shapefiles are added to the map you have open, so you can compare the old
and new layers right away. Review them visually in ArcGIS Pro before
uploading anything to Mapbox.

The QA CSV lists every check the tool ran. Anything marked WARN didn't stop
the release but is worth a look, such as a Class or Type that isn't already in
the target, fields on the new data that weren't carried over, or geometry
problems that were already in the production layer. If a run fails, the tool
leaves a GIUMIntegration_<date>_QA_FAILED.csv in the output folder that shows
how far it got and why it stopped. It gets deleted the next time that release
is created successfully.

To see changes made by the rotation tool reflected in ArcGIS Pro,
go to the layer's Symbology and go to the "Vary symbol by attribute" tab.
Pick the rotation field and set the rotation to Geographic. (Layers created by
the first tool already have this set up.)

Any questions? Reach out to Owen Ferguson:
owen@owenferg.com
owenf@uoregon.edu

Visit the Github repository for more information:
https://github.com/owenferg/arrowhead-tools

Arrowhead Tools v1.6
