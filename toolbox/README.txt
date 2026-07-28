Keep all four Python files together when using the toolbox:

- arrow_tools.pyt
- arrow_creation_arcpy.py
- arrow_rotation_arcpy.py
- arrow_rotation_core.py

How to use:

1. Open your ArcGIS Pro project
2. In the Catalog pane, right click Toolboxes and select Add Toolbox
3. Navigate to the toolbox folder and select arrow_tools.pyt, hit OK
4. The toolbox should now appear under Toolboxes. Expand arrow_tools.pyt. There are
two tools depending on what data you have.

For creating arrowheads when you only have lines:

Open "Create Arrowheads from Line Endpoints"

- Lines: select the layer that contains the arrow lines
- Arrowhead placement: choose the point(s) on the line where arrowhead points are drawn.
By default, this is "END" as in the end point of the line. You can also choose "START"
for the opposite end of the line, or "BOTH" to draw points at both end points. I recommend
"BOTH" if your line layer is complicated, and then delete points afterwards that you don't
need from the attribute table.
- Rotation field name: the field in the new layer that will contain rotation values. Default
is "Rotation"; update this field if you would prefer a different field name.
- Rotation buffer (degrees): a buffer that adjusts each arrow by a certain amount of
degrees. I found the default of 3 to produce best results, but change to 0 if you think
the arrowheads are off (or any other integer).
- Output arrowheads: location and name for the new arrowhead point layer. By default,
the name is your arrow line layer followed by _Arrowheads.

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

To see changes for either Python script reflected in ArcGIS Pro,
go to the layer's Symbology and go to the "Vary symbol by attribute" tab.
Pick the rotation field and set the rotation to Geographic.

Any questions? Reach out to Owen Ferguson:
ferguson.owen555@gmail.com
owenf@uoregon.edu

Visit the Github repository for more information:
https://github.com/owenferg/arrowhead-tools

Arrowhead Tools v1.3
