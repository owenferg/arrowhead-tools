<-- ARROWHEAD TOOLS -->

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

To see changes for either Python script reflected in ArcGIS Pro,
go to the layer's Symbology and go to the "Vary symbol by attribute" tab.
Pick the rotation field and set the rotation to Geographic.

Any questions? Reach out to Owen Ferguson:
owen@owenferg.com
owenf@uoregon.edu

Visit the Github repository for more information:
https://github.com/owenferg/arrowhead-tools

Arrowhead Tools v1.3
