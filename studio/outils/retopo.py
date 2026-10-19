"""Version « jeu / animation » d'un GLB TRELLIS, dans Blender sans écran :
retopologie en quads (voxel remesh + QuadriFlow), UV, bake couleur + normal map depuis la version HD.

blender -b -P retopo.py -- <entree.glb> <sortie.glb> [faces=15000] [texture=2048]
"""

import math
import sys

import bpy

argv = sys.argv[sys.argv.index("--") + 1:]
SRC, DST = argv[0], argv[1]
FACES = int(argv[2]) if len(argv) > 2 else 15000
TEX = int(argv[3]) if len(argv) > 3 else 2048

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=SRC)

# Version HD : un seul objet, transformations appliquées
meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
bpy.ops.object.select_all(action="DESELECT")
for o in meshes:
    o.select_set(True)
bpy.context.view_layer.objects.active = meshes[0]
if len(meshes) > 1:
    bpy.ops.object.join()
hi = bpy.context.view_layer.objects.active
for o in list(bpy.context.scene.objects):
    if o is not hi and o.type != "MESH":
        o.select_set(False)
bpy.ops.object.parent_clear(type="CLEAR_KEEP_TRANSFORM")
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
taille = max(hi.dimensions)
print(f"HD : {len(hi.data.polygons)} faces, taille {taille:.3f}", flush=True)

# Version légère : copie sans matériaux → maillage étanche (voxels) → quads propres
lo = hi.copy()
lo.data = hi.data.copy()
lo.name = "jeu"
bpy.context.collection.objects.link(lo)
lo.data.materials.clear()
bpy.ops.object.select_all(action="DESELECT")
lo.select_set(True)
bpy.context.view_layer.objects.active = lo
# (Blender d'Ubuntu sans OpenVDB : remesh « octree » au lieu du remesh voxel)
m = lo.modifiers.new("etanche", "REMESH")
m.mode = "SMOOTH"
m.octree_depth = 8
m.use_remove_disconnected = False
bpy.ops.object.modifier_apply(modifier=m.name)
print(f"voxels : {len(lo.data.polygons)} faces", flush=True)
try:
    r = bpy.ops.object.quadriflow_remesh(target_faces=FACES, use_preserve_sharp=True, use_preserve_boundary=True,
                                          smooth_normals=False, seed=0)
    print("QuadriFlow :", r, len(lo.data.polygons), "faces", flush=True)
except RuntimeError as e:
    print("QuadriFlow impossible, décimation à la place :", e, flush=True)
if len(lo.data.polygons) > FACES * 1.5:  # QuadriFlow a échoué : simple décimation
    m = lo.modifiers.new("dec", "DECIMATE")
    m.ratio = FACES / len(lo.data.polygons)
    bpy.ops.object.modifier_apply(modifier=m.name)
    print("décimé :", len(lo.data.polygons), "faces", flush=True)
for p in lo.data.polygons:
    p.use_smooth = True

# UV
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=0.004)
bpy.ops.object.mode_set(mode="OBJECT")

# Matériau de la version légère : couleur + normal map, remplies par le bake
couleur = bpy.data.images.new("couleur", TEX, TEX)
normale = bpy.data.images.new("normale", TEX, TEX, float_buffer=False)
normale.colorspace_settings.name = "Non-Color"
mat = bpy.data.materials.new("jeu")
mat.use_nodes = True
nt = mat.node_tree
bsdf = nt.nodes["Principled BSDF"]
bsdf.inputs["Roughness"].default_value = 0.6
n_col = nt.nodes.new("ShaderNodeTexImage")
n_col.image = couleur
n_nor = nt.nodes.new("ShaderNodeTexImage")
n_nor.image = normale
n_map = nt.nodes.new("ShaderNodeNormalMap")
lo.data.materials.append(mat)

sc = bpy.context.scene
sc.render.engine = "CYCLES"
sc.cycles.device = "CPU"
sc.cycles.samples = 4
bk = sc.render.bake
bk.use_selected_to_active = True
bk.cage_extrusion = taille * 0.01
bk.max_ray_distance = taille * 0.04
bk.margin = 8
bpy.ops.object.select_all(action="DESELECT")
hi.select_set(True)
lo.select_set(True)
bpy.context.view_layer.objects.active = lo


def bake(noeud, type_, **kw):
    for n in nt.nodes:
        n.select = False
    noeud.select = True
    nt.nodes.active = noeud
    bpy.ops.object.bake(type=type_, **kw)


bake(n_col, "DIFFUSE", pass_filter={"COLOR"})
print("couleur cuite", flush=True)
bake(n_nor, "NORMAL")
print("normal map cuite", flush=True)

nt.links.new(n_col.outputs["Color"], bsdf.inputs["Base Color"])
nt.links.new(n_nor.outputs["Color"], n_map.inputs["Color"])
nt.links.new(n_map.outputs["Normal"], bsdf.inputs["Normal"])
couleur.pack()
normale.pack()

bpy.data.objects.remove(hi, do_unlink=True)
bpy.ops.object.select_all(action="DESELECT")
lo.select_set(True)
bpy.ops.export_scene.gltf(filepath=DST, use_selection=True, export_format="GLB", export_image_format="JPEG")
print(f"OK {len(lo.data.polygons)} faces → {DST}", flush=True)
