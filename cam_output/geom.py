import trimesh, numpy as np
from shapely.geometry import Polygon, Point
from shapely.ops import unary_union
from functools import reduce
ROOT='D:/UEC/QDD/MDX-540/model/'
_meshes={}
def mesh(n):
    if n not in _meshes: _meshes[n]=trimesh.load(ROOT+f'{n}/{n}.stl')
    return _meshes[n]
def sect_orig(n,z):
    s=mesh(n).section(plane_normal=[0,0,1],plane_origin=[0,0,z])
    if s is None: return Polygon()
    polys=[Polygon(d[:,:2]).buffer(0) for d in s.discrete if len(d)>3]
    return reduce(lambda a,b:a.symmetric_difference(b),polys)
# machine (flipped) coords, centered: Zm=-(z+5), y->-y
from shapely import affinity
def sect_m(n,Zm):
    g=sect_orig(n,-Zm-5)
    return affinity.scale(g,1,-1,origin=(0,0))
def parts_m(Zm):
    return unary_union([sect_m('Inner_Core',Zm),sect_m('Outer_Core',Zm)])
