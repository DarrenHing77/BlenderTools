# Copyright Epic Games, Inc. All Rights Reserved.

import re
import os
import bpy
from . import utilities, formatting, extension
from ..dependencies.unreal import UnrealRemoteCalls
from ..constants import BlenderTypes, PathModes, ToolInfo, Extensions, ExtensionTasks, RegexPresets

class ValidationManager:
    """
    This class validates the data before running the send2ue operation.
    """
    def run(self):
        return self.run_pre_validations()

    def __init__(self, properties):
        """
        Initializes the validation manager.

        :param object properties: The property group that contains variables that maintain the addon's correct state.
        """
        self.properties = properties
        self.mesh_objects = utilities.get_from_collection(BlenderTypes.MESH)
        self.rig_objects = utilities.get_from_collection(BlenderTypes.SKELETON)
        self.hair_objects = utilities.get_hair_objects(properties)

    def run_pre_validations(self):
        """
        Runs the validations in the correct order.
        """
        validations = [
            self.validate_scene_scale,
            self.validate_frame_rate,
            self.validate_armature_transforms,
            self.validate_export_objects_exist,
            self.validate_lod_groups,
            self.validate_project_settings,
            self.validate_disk_folders,
            self.validate_unreal_folders,
            self.validate_unreal_asset_paths,
            self.validate_materials,
            self.validate_lod_names,
            self.validate_texture_references,
            self.validate_object_names,
            self.validate_meshes_for_vertex_groups
        ]

        for validation in validations:
            if not validation():
                return False
        return True

    # ... rest of validation methods stay the same

    def validate_scene_scale(self):
        """
        Checks the scene scale to make sure it is set to 1.0.
        """
        if self.properties.validate_scene_scale:
            if bpy.context.scene.unit_settings.scale_length != 1.0:
                utilities.report_error('Scene scale is not 1! Please set it to 1.')
                return False
        return True

    def validate_frame_rate(self):
        """
        Checks the scene frame rate to make sure it is set to 30 fps.
        """
        if self.properties.validate_time_units != 'off':
            if float(bpy.context.scene.render.fps) != float(self.properties.validate_time_units):
                utilities.report_error(
                    f'Current scene FPS is "{bpy.context.scene.render.fps}". '
                    f'Please change to '
                    f'"{self.properties.validate_time_units}" in your render settings before continuing, '
                    f'or disable this validation.'
                )
                return False
        return True

    def validate_disk_folders(self):
        """
        Checks each of the entered disk folder paths to see if they are
        correct.
        """
        if self.properties.validate_paths:
            if self.properties.path_mode in [
                PathModes.SEND_TO_DISK.value,
                PathModes.SEND_TO_DISK_THEN_PROJECT.value
            ]:
                property_names = [
                    'disk_mesh_folder_path',
                    'disk_animation_folder_path'
                ]
                for property_name in property_names:
                    error_message = formatting.auto_format_disk_folder_path(property_name, self.properties)
                    if error_message:
                        utilities.report_error(error_message)
                        return False
        return True

    def validate_unreal_folders(self):
        """
        Checks each of the unreal folder paths to see if they are correct.
        """
        if self.properties.validate_paths:
            if self.properties.path_mode in [
                PathModes.SEND_TO_PROJECT.value,
                PathModes.SEND_TO_DISK_THEN_PROJECT.value
            ]:
                property_names = [
                    'unreal_mesh_folder_path',
                    'unreal_animation_folder_path'
                ]
                for property_name in property_names:
                    error_message = formatting.auto_format_unreal_folder_path(property_name, self.properties)
                    if error_message:
                        utilities.report_error(error_message)
                        return False
        return True

    def validate_unreal_asset_paths(self):
        """
        Checks each of the entered unreal asset paths to see if they are
        correct.
        """
        if self.properties.validate_paths:
            if self.properties.path_mode in [
                PathModes.SEND_TO_PROJECT.value,
                PathModes.SEND_TO_DISK_THEN_PROJECT.value
            ]:
                property_names = [
                    'unreal_skeleton_asset_path',
                    'unreal_physics_asset_path',
                    'unreal_skeletal_mesh_lod_settings_path',
                ]
                for property_name in property_names:
                    error_message = formatting.auto_format_unreal_asset_path(property_name, self.properties)
                    if error_message:
                        utilities.report_error(error_message)
                        return False
        return True

    def validate_materials(self):
        """
        Checks to see if the mesh has any unused materials.
        """
        if self.properties.validate_materials:
            for mesh_object in self.mesh_objects:
                material_slots = [material_slots.name for material_slots in mesh_object.material_slots]

                if len(mesh_object.material_slots) > 0:
                    # for each polygon check for its material index
                    for polygon in mesh_object.data.polygons:
                        if polygon.material_index >= len(mesh_object.material_slots):
                            utilities.report_error('Material index out of bounds!', f'Object "{mesh_object.name}" at polygon #{polygon.index} references invalid material index #{polygon.material_index}.')
                            return False

                        material = mesh_object.material_slots[polygon.material_index].name
                        # remove used material names from the list of unused material names
                        if material in material_slots:
                            material_slots.remove(material)

                    # iterate over unused materials and report about them
                    if material_slots:
                        for material_slot in material_slots:
                            utilities.report_error(f'Mesh "{mesh_object.name}" has a unused material "{material_slot}"')
                            return False
        return True

    def validate_lod_names(self):
        """
        Checks each object to see if the name of the object matches the supplied regex expression.
        """
        if self.properties.import_lods:
            # Extract flags and clean pattern from lod_regex
            lod_regex, flags = utilities._extract_regex_flags(self.properties.lod_regex)
            
            for mesh_object in self.mesh_objects:
                try:
                    result = re.search(f"({lod_regex})", mesh_object.name, flags)
                    if not result:
                        utilities.report_error(
                            f'Object "{mesh_object.name}" does not follow the correct lod naming convention defined in the '
                            f'import setting by the lod regex.'
                        )
                        return False
                except re.error:
                    utilities.report_error(
                        f'Invalid lod_regex pattern: "{self.properties.lod_regex}". Please check your regex syntax.'
                    )
                    return False
        return True

    def validate_texture_references(self):
        """
        Checks to see if the mesh has any materials with textures that have
        invalid references.
        """
        if self.properties.validate_textures:
            for mesh_object in self.mesh_objects:
                for material_slot in mesh_object.material_slots:
                    if material_slot.material:
                        for node in material_slot.material.node_tree.nodes:
                            if node.type == 'TEX_IMAGE':
                                if node.image:
                                    if not node.image.packed_file:
                                        if not node.image.filepath:
                                            utilities.report_error(
                                                f'Texture node "{node.name}" on material "{material_slot.material.name}" does not '
                                                f'have a valid file path.'
                                            )
                                            return False
        return True

    def validate_export_objects_exist(self):
        """
        Checks to see if there are objects to export.
        """
        # if there are objects to export, then continue with the validations
        if self.mesh_objects or self.rig_objects or self.hair_objects:
            return True

        # otherwise report an error
        utilities.report_error(
            f'No objects found in the "{utilities.ToolInfo.EXPORT_COLLECTION.value}" collection! '
            f'Create and populate the "{utilities.ToolInfo.EXPORT_COLLECTION.value}" collection, '
            f'or use the operator "Create Pre-defined Collections" under the utilities menu.'
        )
        return False

    def validate_project_settings(self):
        """
        Checks the unreal project settings to make sure they are correct.
        """
        if self.properties.validate_project_settings:
            if self.properties.path_mode in [
                PathModes.SEND_TO_PROJECT.value,
                PathModes.SEND_TO_DISK_THEN_PROJECT.value
            ]:
                # ensure unreal editor is open
                if not utilities.is_unreal_connected():
                    return False

                try:
                    result = UnrealRemoteCalls.get_project_settings()
                    if not result:
                        utilities.report_error(
                            f'Could not get the project settings from the open unreal project'
                        )
                        return False
                except:
                    utilities.report_error(
                        f'Could not get the project settings from the open unreal project'
                    )
                    return False

                # if the import type is ue5
                if bpy.context.window_manager.send2ue.source_application == 'ue5':
                    # check that the editor startup map is set to None
                    editor_startup_map = result.get('EditorStartupMap')
                    if editor_startup_map and editor_startup_map != 'None':
                        utilities.report_error(
                            f'Project setting "Editor Startup Map" must be set to "None" for UE5 imports'
                        )
                        return False

                    # check that the game default map is set to None
                    game_default_map = result.get('GameDefaultMap')
                    if game_default_map and game_default_map != 'None':
                        utilities.report_error(
                            f'Project setting "Game Default Map" must be set to "None" for UE5 imports'
                        )
                        return False

        return True

    def validate_armature_transforms(self):
        """
        Checks to see if there are any armatures that have un-applied transforms.
        """
        if self.properties.validate_armature_transforms:
            for rig_object in self.rig_objects:
                transform_matrix = rig_object.matrix_local

                # check if the rotation is identity matrix
                if not transform_matrix.to_3x3().normalized().is_identity:
                    utilities.report_error(f'Armature "{rig_object.name}" has un-applied transforms.')
                    return False

                # check if the scale is uniform and equal to 1
                scale = transform_matrix.to_scale()
                for component in scale:
                    if abs(component - 1.0) > 0.001:
                        utilities.report_error(f'Armature "{rig_object.name}" has un-applied transforms.')
                        return False

                # check if the location is zero
                location = transform_matrix.to_translation()
                for component in location:
                    if abs(component) > 0.001:
                        utilities.report_error(f'Armature "{rig_object.name}" has un-applied transforms.')
                        return False

        return True

    def validate_lod_groups(self):
        """
        Checks to see if groom lods are being imported.
        """
        if self.properties.import_lods and self.properties.import_grooms:
            utilities.report_error(
                'Groom LODs are currently unsupported at this time. Please disable either import LODs or import groom.'
            )
            return False
        return True

    def validate_object_names(self):
        """
        Checks that blender object names do not contain any special characters
        that unreal does not accept.
        """
        if self.properties.validate_object_names:
            export_objects = []
            if self.properties.import_grooms:
                export_objects.extend(self.hair_objects)
            if self.properties.import_meshes:
                export_objects.extend(self.mesh_objects)
                export_objects.extend(self.rig_objects)

            invalid_object_names = []
            for blender_object in export_objects:
                if blender_object.name.lower() in ['none']:
                    utilities.report_error(
                        f'Object "{blender_object.name}" has an invalid name. Please rename it.'
                    )
                    return False

                match = re.search(RegexPresets.INVALID_NAME_CHARACTERS, blender_object.name)
                if match:
                    invalid_object_names.append(f'\"{blender_object.name}\"')

            if invalid_object_names:
                utilities.report_error(
                    "The following blender object(s) contain special characters or "
                    "a white space in the name(s):\n{report}\nNote: the only valid special characters "
                    "are \"+\", \"-\" and \"_\".".format(
                        report=",".join(invalid_object_names)
                    )
                )
                return False
        return True

    def validate_meshes_for_vertex_groups(self):
        """
        Checks that meshes with armature modifiers actually have vertex groups.
        """
        missing_vertex_groups = []
        if self.properties.validate_meshes_for_vertex_groups:
            for mesh_object in self.mesh_objects:
                for modifier in mesh_object.modifiers:
                    if modifier.type == 'ARMATURE':
                        if modifier.use_vertex_groups and not mesh_object.vertex_groups:
                            missing_vertex_groups.append(mesh_object.name)

        if missing_vertex_groups:
            mesh_names = ''.join([f'"{mesh_name}"' for mesh_name in missing_vertex_groups])

            utilities.report_error(
                f"The following blender object(s) {mesh_names} have an armature modifier that "
                f"that should be assigned to vertex groups, yet no vertex groups were found. To fix this, assign "
                f"the vertices on your rig's mesh to vertex groups that match the armature's bone names. "
                f"Otherwise disable this validation."
            )
            return False
        return True