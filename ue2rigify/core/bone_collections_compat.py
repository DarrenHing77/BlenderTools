"""
Bone collections compatibility layer for Blender 4.0+
"""
import bpy

def ensure_bone_collection(arm, name="Layer 1"):
    """
    Ensure bone collection exists for both old and new Blender versions.
    
    :param bpy.types.Armature arm: The armature data
    :param str name: Collection name for Blender 4.0+
    :return: Collection or layer index
    """
    if hasattr(arm, 'rigify_layers'):
        # Blender 3.x
        if len(arm.rigify_layers) == 0:
            arm.rigify_layers.add()
        return 0  # layer index
    else:
        # Blender 4.0+
        if not arm.collections:
            collection = arm.collections.new(name=name)
        else:
            collection = arm.collections[0]
        return collection

def add_bone_to_collection(arm, bone_name, collection_index=0):
    """
    Add bone to collection/layer for compatibility.
    
    :param bpy.types.Armature arm: The armature data
    :param str bone_name: Name of the bone
    :param int collection_index: Collection index or layer number
    """
    if hasattr(arm, 'rigify_layers'):
        # Blender 3.x - set bone layer
        bone = arm.edit_bones.get(bone_name)
        if bone:
            bone.layers[collection_index] = True
    else:
        # Blender 4.0+ - add to collection
        bone = arm.edit_bones.get(bone_name)
        if bone and arm.collections:
            if collection_index < len(arm.collections):
                collection = arm.collections[collection_index]
                collection.assign(bone)

class CompatRigifyLayers:
    """Compatibility wrapper for rigify_layers in Blender 4.0+"""
    def __init__(self, arm):
        self.arm = arm
        
    def add(self):
        """Add a new layer/collection"""
        if hasattr(self.arm, 'rigify_layers'):
            return self.arm.rigify_layers.add()
        else:
            # Create new bone collection
            collection_name = f"Layer {len(self.arm.collections) + 1}"
            return self.arm.collections.new(name=collection_name)