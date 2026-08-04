"""Surface engine: (byte range, dtype, params, w, h) -> 2-D raster.

Six of the eight reference views are the same operation behind different
parameters. One protocol, one cache, one frontend canvas component.
"""

from .base import Raster, Surface, SurfaceRequest, get_surface, SURFACES

__all__ = ["Raster", "Surface", "SurfaceRequest", "get_surface", "SURFACES"]
