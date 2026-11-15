"""plan2bim: 2D floor-plan drawings (DWG or DXF) -> exact 3D building model (IFC + web viewer).

The wall mass is not re-drawn from centre lines. It is the set of closed cells
that the drawing itself encloses, so every wall face in the model lies on a
line of the drawing. See docs/plan2bim.md.
"""
__version__ = "0.2.0"
