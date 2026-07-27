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

Create arrowheads when you only have lines:

Open "Create Arrowheads from Line Endpoints"

- Lines: select the layer that contains the arrow lines. Any selection or definition
query on the layer will be honored.
- Arrowhead placement: creates arrowheads at the end of each line by default. This
can be changed to the start of each line or both the start and end.
- Rotation field name: the field in the new layer that will contain rotation values.
- Rotation buffer (degrees): a buffer that adjusts each arrow by a certain amount of
degrees. I found the default of 3 to produce best results, but can be modified at
your discretion.
- Output arrowheads: location and name for the new arrowhead point layer. By default,
the name is your arrow line layer followed by _Arrowheads.

The new layer keeps the editable fields from the lines and adds fields for the source
line, multipart part, endpoint type, and arrowhead rotation. The script creates
rotation-ready data, but does not choose your arrowhead symbol. Set up your preferred
marker in the layer's symbology and use the rotation field to rotate it.

Update rotations when you already have arrowhead points:

Open "Update Existing Arrowhead Rotations"

- Arrowhead points: select your layer that contains the arrowhead points
- Lines: select your layer that contains the arrow's lines
- Maximum endpoint distance: determines how far the script searches for an endpoint 
of a line from the arrowhead point. I recommend not changing this.
- Rotation field: The field from your arrowhead points layer that determines rotation 
values. Choose from the dropdown if the default "Rotation" isn't accurate.
- Rotation buffer (degrees): A buffer that adjusts each arrow by a certain amount of 
degrees. I found the default of 3 to produce best results, but can be modified at 
your discretion.
- Audit output table: Optional; creates a table that contains information about the
results of executing the script.

When parameters are set to your preference, press "Run". 

To see changes for either Python script,
go to the layer's Symbology and go to the "Vary symbol by attribute" tab.
Pick the rotation field and set the rotation to Geographic.

Any questions? Reach out to Owen Ferguson:
owenf@uoregon.edu
ferguson.owen555@gmail.com

Visit the Github repository for more information:
https://github.com/owenferg/arrowhead-tools
