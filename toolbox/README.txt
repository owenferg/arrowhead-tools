Keep these python files together when using the script.

How to use:

1. Open your ArcGIS Pro project
2. In the Catalog pane, right click Toolboxes and select Add Toolbox
3. Navigate to the toolbox folder and select arrow_tools.pyt, hit OK
4. The toolbox should now appear under Toolboxes. Expand arrow_tools.pyt and open 
Calculate Arrowhead Rotation

Parameters:

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

When parameters are set to your preference, press "Run". Changes should be reflected 
automatically visually and in the attribute table of your arrowheads layer.

Any questions? Reach out to Owen Ferguson:
owenf@uoregon.edu
ferguson.owen555@gmail.com

Visit the Github repository for more information:
https://github.com/owenferg/arrowhead-tools