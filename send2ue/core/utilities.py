# Copyright Epic Games, Inc. All Rights Reserved.

import os
import re
import bpy
import math
import shutil
import importlib
import tempfile
import base64
from . import settings, formatting
from ..ui import header_menu
from ..dependencies import unreal
from ..constants import BlenderTypes, UnrealTypes, ToolInfo, PreFixToken, PathModes, RegexPresets
from mathutils import Vector, Quaternion


def escape_local_view():
    """
    Escapes local view if currently in local view mode.
    """
    for area in bpy.context.screen.areas:
        if area.type == 'VIEW_3D':
            for space in area.spaces:
                if space.type == 'VIEW_3D' and space.local_view:
                    with bpy.context.temp_override(area=area, space=space):
                        bpy.ops.view3d.localview(frame_selected=False)
                    break

# Alias functions for backward compatibility with extensions
def convert_blender_rotation_to_unreal_rotation(rotation):
    """
    Alias for convert_to_unreal_rotation for backward compatibility.
    """
    return convert_to_unreal_rotation(rotation)

def convert_blender_to_unreal_location(location):
    """
    Alias for convert_to_unreal_location for backward compatibility.
    """
    return convert_to_unreal_location(location)

def track_progress(message='', attribute=''):
    """
    A decorator that makes its wrapped function a queued job.

    :param str message: A the progress message.
    :param str attribute: The asset attribute to use in as the message.
    """

    def decorator(function):
        def wrapper(*args, **kwargs):
            asset_id = args[0]
            bpy.app.driver_namespace[ToolInfo.EXECUTION_QUEUE.value].put(
                (function, args, kwargs, message, asset_id, attribute)
            )

        return wrapper

    return decorator


def get_asset_id(file_path):
    """
    Gets the asset id, which is the hash from the file path.

    :return str: The asset id.
    """
    file_path_bytes = file_path.encode('utf-8')
    base64_bytes = base64.b64encode(file_path_bytes)
    return base64_bytes.decode('utf-8')


def get_asset_data_by_attribute(name, value):
    """
    Gets the first asset data block that matches the given attribute value.

    :returns: A asset data dict.
    :rtype: dict
    """
    for asset_data in bpy.context.window_manager.send2ue.asset_data.copy().values():
        if asset_data.get(name) == value:
            return asset_data
    return {}


def get_asset_name_from_file_name(file_path):
    """
    Get a asset name from a file path.

    :param str file_path: A file path.
    :return str: A asset name.
    """
    if file_path:
        return os.path.splitext(os.path.basename(file_path))[0]


def get_operator_class_by_bl_idname(bl_idname):
    """
    Gets a operator class from its bl_idname.

    :return class: The operator class.
    """
    context, name = bl_idname.split('.')
    return getattr(bpy.types, f'{context.upper()}_OT_{name}', None)


def _extract_regex_flags(pattern):
    """Extract regex flags from pattern and return clean pattern + flags"""
    flags = 0
    
    # Handle case-insensitive flag
    if pattern.startswith("(?i)"):
        flags |= re.IGNORECASE
        pattern = pattern[4:]
    
    # Remove any other flags that might cause issues
    pattern = re.sub(r'^\(\?[a-zA-Z]*\)', '', pattern)
    
    return pattern, flags


def get_lod0_name(asset_name, properties):
    """
    Gets the correct name for lod0.

    :param str asset_name: The name of the asset.
    :param PropertyData properties: A property data instance that contains all property values of the tool.
    :return str: The full name for lod0.
    """
    lod_regex, flags = _extract_regex_flags(properties.lod_regex)
    
    try:
        result = re.search(f"({lod_regex})", asset_name, flags)
        if result:
            lod = result.groups()[-1]
            return asset_name.replace(lod, f'{lod[:-1]}0')
    except (re.error, IndexError):
        pass
    return asset_name


def get_lod_index(asset_name, properties):
    """
    Gets the lod index from the given asset name.

    :param str asset_name: The name of the asset.
    :param PropertyData properties: A property data instance that contains all property values of the tool.
    :return int: The lod index
    """
    lod_regex, flags = _extract_regex_flags(properties.lod_regex)
    
    try:
        result = re.search(f"({lod_regex})", asset_name, flags)
        if result:
            lod = result.groups()[-1]
            return int(lod[-1])
    except (re.error, ValueError, IndexError):
        pass
    return 0


def get_temp_folder():
    """
    Gets the full path to the temp folder on disk.

    :return str: A folder path.
    """
    return os.path.join(
        tempfile.gettempdir(),
        'blender',
        'send2ue',
        'data'
    )


def get_export_folder_path(properties, asset_type):
    """
    Gets the path to the export folder according to path mode set in properties

    :param PropertyData properties: A property data instance that contains all property values of the tool.
    :param str asset_type: The type of data being exported.
    :return str: The path to the export folder.
    """
    export_folder = None
    # if saving in a temp location
    if properties.path_mode in [
        PathModes.SEND_TO_PROJECT.value,
        PathModes.SEND_TO_DISK_THEN_PROJECT.value
    ]:
        export_folder = os.path.join(get_temp_folder(), asset_type)

    # if saving to a specified location
    if properties.path_mode in [
        PathModes.SEND_TO_DISK.value,
        PathModes.SEND_TO_DISK_THEN_PROJECT.value
    ]:
        if asset_type in [UnrealTypes.STATIC_MESH, UnrealTypes.SKELETAL_MESH]:
            export_folder = formatting.resolve_path(properties.disk_mesh_folder_path)

        if asset_type == UnrealTypes.ANIM_SEQUENCE:
            export_folder = formatting.resolve_path(properties.disk_animation_folder_path)

        if asset_type == UnrealTypes.GROOM:
            export_folder = formatting.resolve_path(properties.disk_groom_folder_path)

    return export_folder


def get_import_path(properties, unreal_asset_type, *args, **kwargs):
    """
    Gets the unreal import path.

    :param object properties: The property group that contains variables that maintain the addon's correct state.
    :param str unreal_asset_type: The type of asset.
    :return str: The full import path for the given asset.
    """
    if unreal_asset_type == UnrealTypes.ANIM_SEQUENCE:
        game_path = properties.unreal_animation_folder_path

    elif unreal_asset_type == UnrealTypes.GROOM:
        game_path = properties.unreal_groom_folder_path

    else:
        game_path = properties.unreal_mesh_folder_path

    return game_path


def get_mesh_unreal_type(mesh_object):
    """
    Gets the unreal type of the mesh object.

    :param object mesh_object: A object of type mesh.
    :return str: The unreal mesh type.
    """
    # Check if the mesh has an armature modifier or is a child of an armature
    if get_armature_modifier_rig_object(mesh_object) or is_child_of_armature(mesh_object):
        return UnrealTypes.SKELETAL_MESH
    return UnrealTypes.STATIC_MESH


def get_armature_modifier_rig_object(mesh_object):
    """
    Gets the rig object that drives the armature modifier on a mesh object.

    :param object mesh_object: A object of type mesh.
    :return object: A object of type armature.
    """
    # get the armature modifier from the mesh object
    for modifier in mesh_object.modifiers:
        if modifier.type == 'ARMATURE':
            # get the object armature that is attached to this modifier
            if modifier.object:
                return modifier.object

    return None


def is_child_of_armature(scene_object):
    """
    Checks if a given object is a child of an armature.

    :param object scene_object: A object.
    :return bool: Whether or not the object is a child of an armature.
    """
    if scene_object.parent:
        if scene_object.parent.type == BlenderTypes.SKELETON:
            return True
    return False


def convert_to_unreal_rotation(rotation):
    """
    Converts rotation to unreal values.

    :param object rotation: A rotation object.
    :return list[float]: The euler rotation in unreal units.
    """
    radians_to_degrees = 57.2958
    return [
        rotation[0] * radians_to_degrees,
        rotation[1] * radians_to_degrees * -1,
        rotation[2] * radians_to_degrees * -1
    ]


def convert_to_unreal_scale(scale):
    """
    Converts scale to unreal values.

    :param object scale: A scale object.
    :return list[float]: The scale.
    """
    return [scale[0], scale[1], scale[2]]


def convert_to_unreal_location(location):
    """
    Converts location coordinates to unreal location coordinates.

    :param object location: A location object.
    :return list[float]: The unreal location.
    """
    x = location[0] * 100
    y = location[1] * 100
    z = location[2] * 100
    return [x, -y, z]


def convert_unreal_to_blender_location(location):
    """
    Converts unreal location coordinates to blender location coordinates.

    :return list[float]: The blender location.
    """
    x = location[0] / 100
    y = location[1] / 100
    z = location[2] / 100
    return [x, -y, z]


def convert_curve_to_particle_system(curves_object):
    """
    Converts curves objects to particle systems on the mesh they are surfaced to and returns the names of the converted
    curves in a list.

    :param object curves_object: A curves objects.
    """
    # deselect everything
    deselect_all_objects()

    # select the curves object
    curves_object.select_set(True)
    bpy.context.view_layer.objects.active = curves_object

    # convert to a particle system
    bpy.ops.curves.convert_to_particle_system()


def addon_enabled(*args):
    """
    This function is designed to be called once after the addon is activated. Since the scene context
    is not accessible from inside a addon's register function, this function can be added to the event
    timer, then make function calls that use the scene context, and then is removed.
    """
    setup_project()


def setup_project(*args):
    """
    This is run when the integration launches, and on new file load events.

    :param args: This soaks up the extra arguments for the app handler.
    """
    # remove the cached files
    remove_temp_folder()

    # create the default settings template
    settings.create_default_template()

    # if the scene properties are not available yet recall this function
    properties = getattr(bpy.context.scene, ToolInfo.NAME.value, None)
    if not properties:
        bpy.app.timers.register(setup_project, first_interval=0.1)

    # ensure the extension draws are created
    bpy.ops.send2ue.reload_extensions()

    # create the scene collections
    addon = bpy.context.preferences.addons.get(ToolInfo.NAME.value)
    if addon and addon.preferences.automatically_create_collections:
        create_collections()

    # create the header menu
    if importlib.util.find_spec('unpipe') is None:
        header_menu.add_pipeline_menu()


def draw_error_message(self, context):
    """
    This function creates the layout for the error pop up

    :param object self: This refers the the Menu class definition that this function will
    be appended to.
    :param object context: This parameter will take the current blender context by default,
    or can be passed an explicit context.
    """
    self.layout.label(text=bpy.context.window_manager.send2ue.error_message)
    if bpy.context.window_manager.send2ue.error_message_details:
        self.layout.label(text=bpy.context.window_manager.send2ue.error_message_details)


def report_error(message, details='', raise_exception=True):
    """
    This function reports a given error message to the screen.

    :param str message: The error message to display to the user.
    :param str details: The error message details to display to the user.
    :param bool raise_exception: Whether to raise an exception or report the error in the popup.
    """
    # if a warning is received, then don't raise an error
    if message == {'WARNING'}:
        print(f'{message} {details}')
        return

    if os.environ.get('SEND2UE_DEV', raise_exception):
        raise RuntimeError(message + details)
    else:
        bpy.context.window_manager.send2ue.error_message = message
        bpy.context.window_manager.send2ue.error_message_details = details
        bpy.context.window_manager.popup_menu(draw_error_message, title='Error', icon='ERROR')


def report_path_error_message(layout, send2ue_property, report_text):
    """
    This function displays an error message on a row if a property
    returns a False value.

    :param object layout: The ui layout.
    :param object send2ue_property: Registered property of the addon
    :param str report_text: The text to report in the row label
    """

    # only create the row if the value of the property is true and a string
    if send2ue_property and type(report_text) == str:
        row = layout.row()

        row.alert = True
        row.label(text=report_text)


def select_all_children(scene_object, object_type, exclude_postfix_tokens=False):
    """
    Selects all of an objects children.

    :param object scene_object: A object.
    :param str object_type: The type of object to select.
    :param bool exclude_postfix_tokens: Whether or not to exclude objects that have a postfix token.
    """
    children = scene_object.children or get_meshes_using_armature_modifier(scene_object)
    for child_object in children:
        if child_object.type == object_type:
            if exclude_postfix_tokens:
                if any(child_object.name.startswith(f'{token.value}_') for token in PreFixToken):
                    continue

            child_object.select_set(True)
            if child_object.children:
                select_all_children(child_object, object_type, exclude_postfix_tokens)


def apply_all_mesh_modifiers(scene_object):
    """
    This function applies all mesh modifiers on the given object.

    :param object scene_object: A object.
    """
    # deselect everything
    deselect_all_objects()

    # select the object
    scene_object.select_set(True)
    bpy.context.view_layer.objects.active = scene_object

    # convert the modifier stack
    bpy.ops.object.convert(target='MESH', keep_original=False)


def get_from_collection(object_type):
    """
    Gets all the objects of a specified type from export collection.

    :param str object_type: The object type you would like to get.
    :return list: A list of objects
    """
    collection_objects = []

    # get the collection with the given name
    export_collection = bpy.data.collections.get(ToolInfo.EXPORT_COLLECTION.value)
    if export_collection:
        # get all the objects in the collection
        for collection_object in export_collection.all_objects:
            # if the object is the correct type
            if collection_object.type == object_type:
                # if the object is visible
                if collection_object.visible_get():
                    # ensure the object doesn't end with one of the post fix tokens
                    if not any(collection_object.name.startswith(f'{token.value}_') for token in PreFixToken):
                        # add it to the group of objects
                        collection_objects.append(collection_object)
    return sorted(collection_objects, key=lambda obj: obj.name)


def get_meshes_using_armature_modifier(rig_object):
    """
    This function get the objects using the given rig in an armature modifier.

    :param object rig_object: An object of type armature.
    :return list: A list of objects using the given rig in an armature modifier.
    """
    mesh_objects = get_from_collection(BlenderTypes.MESH)
    child_meshes = []
    for mesh_object in mesh_objects:
        if rig_object == get_armature_modifier_rig_object(mesh_object):
            child_meshes.append(mesh_object)
    return child_meshes


def get_asset_name(asset_name, properties, lod=False):
    """
    Takes a given asset name and removes the postfix _LOD and other non-alpha numeric characters
    that unreal won't except.

    :param str asset_name: The original name of the asset to export.
    :param PropertyData properties: A property data instance that contains all property values of the tool.
    :param bool lod: Whether to use the lod post fix of not.
    :return str: The formatted name of the asset to export.
    """
    asset_name = re.sub(RegexPresets.INVALID_NAME_CHARACTERS, "_", asset_name.strip())

    if properties.import_lods:
        # Extract flags and clean pattern from lod_regex
        lod_regex, flags = _extract_regex_flags(properties.lod_regex)
        
        # remove the lod name from the asset
        try:
            result = re.search(f"({lod_regex})", asset_name, flags)
            if result and not lod:
                asset_name = asset_name.replace(result.groups()[0], '')
        except re.error:
            # If regex is malformed, skip LOD processing
            pass

    return asset_name


def get_parent_collection(scene_object, collection):
    """
    This function walks the collection tree to find the collection parent of the given object.

    :param object scene_object: A object.
    :param object collection: A collection.
    :return str: The collection name.
    """
    for child_collection in collection.children:
        for child_object in child_collection.objects:
            if child_object == scene_object:
                return child_collection.name

        parent_collection = get_parent_collection(scene_object, child_collection)
        if parent_collection:
            return parent_collection
    return None


def get_mesh_object_from_curves(curves_object):
    """
    Gets the mesh object the curves object is bound to.

    :param object curves_object: A curves object.
    :return: A mesh object.
    """
    # curves object can have modifiers like surfaces deform which attach them to meshes
    for modifier in curves_object.modifiers:
        if modifier.type == 'SURFACE_DEFORM' and modifier.target and modifier.target.type == BlenderTypes.MESH:
            return modifier.target

    # if no modifiers check if the curves object is the child of a mesh
    if curves_object.parent and curves_object.parent.type == BlenderTypes.MESH:
        return curves_object.parent

    return None


def refresh_all_areas():
    """
    Refreshes all areas in all windows.
    """
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            area.tag_redraw()


def deselect_all_objects():
    """
    Deselects all objects in the scene.
    """
    for scene_object in bpy.context.scene.objects:
        scene_object.select_set(False)


def select_object(scene_object):
    """
    Selects an objects and set it to the active object.

    :param object scene_object: A object.
    """
    deselect_all_objects()
    scene_object.select_set(True)
    bpy.context.view_layer.objects.active = scene_object


def set_object_origin_to_world_origin(scene_object):
    """
    Sets the provided object's origin to the world origin.

    :param object scene_object: A object.
    """
    # deselect everything
    deselect_all_objects()

    # select the object
    scene_object.select_set(True)
    bpy.context.view_layer.objects.active = scene_object

    # move the object's pivot to the world origin
    # save the cursor location
    saved_cursor_location = bpy.context.scene.cursor.location.copy()

    # set the cursor location to the world center
    bpy.context.scene.cursor.location = [0.0, 0.0, 0.0]

    # set the origin to the cursor
    bpy.ops.object.origin_set(type='ORIGIN_CURSOR')

    # restore the cursor location
    bpy.context.scene.cursor.location = saved_cursor_location


def get_groom_object_name(curves_object, properties):
    """
    Gets the groom object name based on which mesh the curves object is attached to.

    :param object curves_object: The curves object.
    :param PropertyData properties: A property data instance that contains all property values of the tool.
    :return str: The groom object name.
    """
    mesh_object = get_mesh_object_from_curves(curves_object)
    if mesh_object:
        return get_asset_name(mesh_object.name, properties)

    # default to the curves object name if no mesh parent is found
    return get_asset_name(curves_object.name, properties)


def unpack_textures():
    """
    Unpacks all the textures that are packed into the blend file.

    :return dict: A dictionary of previously packed file paths and their new file paths.
    """
    unpacked_files = {}
    for image in bpy.data.images:
        if image.packed_file:
            # save the reference to the old file path
            old_file_path = image.filepath

            # unpack the image to the temp folder
            image.unpack(method='USE_ORIGINAL')

            # save the reference to the new file path
            unpacked_files[old_file_path] = image.filepath_from_user()

    return unpacked_files


def remove_unpacked_files(unpacked_files):
    """
    Removes unpacked files from disk and resets the images back to their packed file paths.

    :param dict unpacked_files: A dictionary of previously packed file paths and their new file paths.
    """
    for old_file_path, new_file_path in unpacked_files.items():
        for image in bpy.data.images:
            if image.filepath_from_user() == new_file_path:
                # remove the unpacked file
                if os.path.exists(new_file_path):
                    os.remove(new_file_path)

                # reset the image path back to the packed file path
                image.filepath = old_file_path


def import_asset(file_path, send2ue_data):
    """
    This function imports the given asset and applies the import settings.

    :param str file_path: The file path to import.
    :param PropertyData send2ue_data: A property data instance that contains all property values of the tool.
    """
    file_name = get_asset_name_from_file_name(file_path)
    file_extension = os.path.splitext(file_path)[1]

    # get the import settings from the addon preferences
    addon = bpy.context.preferences.addons.get(ToolInfo.NAME.value)

    # import based on file extension
    if file_extension.lower() == f'.{FileTypes.FBX}':
        bpy.ops.import_scene.fbx(
            filepath=file_path,
            **addon.preferences.get_property_group('fbx_import').to_dict()
        )

    if file_extension.lower() == f'.{FileTypes.ABC}':
        bpy.ops.wm.alembic_import(
            filepath=file_path,
            **addon.preferences.get_property_group('abc_import').to_dict()
        )

    # reset the object names back to the pre import state
    for imported_object in bpy.context.selected_objects:
        if get_asset_name_from_file_name(imported_object.name) == file_name:
            imported_object.name = file_name


def mute_nla_tracks(rig_object, mute):
    """
    This mutes or un-mutes the nla tracks on the given rig object.

    :param object rig_object: A object of type armature with animation data.
    :param bool mute: Whether or not to mute all nla tracks

    """
    if rig_object:
        if rig_object.animation_data:
            for nla_track in rig_object.animation_data.nla_tracks:
                nla_track.mute = mute


def is_unreal_connected():
    """
    Checks if the unreal rpc server is connected, and if not attempts a bootstrap.
    """
    # skips checking for and unreal connection if in send to disk mode
    # https://github.com/EpicGamesExt/BlenderTools/issues/420
    if bpy.context.scene.send2ue.path_mode == PathModes.SEND_TO_DISK.value:
        return True

    try:
        # bootstrap the unreal rpc server if it is not already running
        unreal.bootstrap_unreal_with_rpc_server()
        return True
    except ConnectionError:
        report_error('Could not find an open Unreal Editor instance!', raise_exception=False)
        return False


def is_lod_of(asset_name, mesh_object_name, properties):
    """
    Checks if the given asset name matches the lod naming convention.

    :param str asset_name: The name of the asset to export.
    :param str mesh_object_name: The name of the lod mesh.
    :param PropertyData properties: A property data instance that contains all property values of the tool.
    """
    return asset_name == get_asset_name(mesh_object_name, properties)


def is_collision_of(asset_name, mesh_object_name, properties):
    """
    Checks if the given asset name matches the collision naming convention.

    :param str asset_name: The name of the asset to export.
    :param str mesh_object_name: The name of the collision mesh.
    :param PropertyData properties: A property data instance that contains all property values of the tool.
    """
    # note we strip whitespace out of the collision name since whitespace is already striped out of the asset name
    # https://github.com/EpicGamesExt/BlenderTools/issues/397#issuecomment-1333982590
    mesh_object_name = mesh_object_name.strip()
    
    # Check basic collision pattern
    basic_pattern = r"U(BX|CP|SP|CX)_" + re.escape(asset_name) + r"(_\d+)?"
    if re.fullmatch(basic_pattern, mesh_object_name):
        return True
    
    # Check collision pattern with LOD
    lod_regex, flags = _extract_regex_flags(properties.lod_regex)
    
    try:
        lod_pattern = r"U(BX|CP|SP|CX)_" + re.escape(asset_name) + lod_regex + r"(_\d+)?"
        return bool(re.fullmatch(lod_pattern, mesh_object_name, flags))
    except re.error:
        # Fallback to basic pattern if regex is malformed
        return bool(re.fullmatch(basic_pattern, mesh_object_name))


def has_extension_draw(location):
    """
    Checks whether the given location has any draw functions.

    :param str location: The name of the draw location i.e. export, import, validations.
    """
    for extension_name in dir(bpy.context.scene.send2ue.extensions):
        extension = getattr(bpy.context.scene.send2ue.extensions, extension_name)
        if hasattr(extension, f'draw_{location}'):
            return True
    return False


def get_hair_objects(properties):
    """
    Gets all the hair objects from the export collection.

    :param PropertyData properties: A property data instance that contains all property values of the tool.
    :return list: A list of hair objects.
    """
    if properties.import_grooms:
        return get_from_collection(BlenderTypes.CURVES)
    return []


def create_collections():
    """
    Creates the collections for the addon.
    """
    for collection_name in ToolInfo.COLLECTION_NAMES.value:
        # if the collection doesn't exist, create it
        if not bpy.data.collections.get(collection_name):
            new_collection = bpy.data.collections.new(collection_name)
            bpy.context.scene.collection.children.link(new_collection)


def select_asset_collisions(asset_name, properties):
    """
    Selects all the collision meshes for the given asset.

    :param str asset_name: The name of the asset to export.
    :param PropertyData properties: A property data instance that contains all property values of the tool.
    """
    for mesh_object in get_asset_collisions(asset_name, properties):
        mesh_object.select_set(True)


def get_asset_collisions(asset_name, properties):
    """
    Gets all the collision meshes for the given asset.

    :param str asset_name: The name of the asset to export.
    :param PropertyData properties: A property data instance that contains all property values of the tool.
    :return list: A list of mesh objects.
    """
    mesh_objects = get_from_collection(BlenderTypes.MESH)
    collision_objects = []
    for mesh_object in mesh_objects:
        if is_collision_of(asset_name, mesh_object.name, properties):
            collision_objects.append(mesh_object)
    return collision_objects


def get_asset_lods(asset_name, properties):
    """
    Gets all the lod meshes for the given asset.

    :param str asset_name: The name of the asset to export.
    :param PropertyData properties: A property data instance that contains all property values of the tool.
    :return list: A list of mesh objects.
    """
    mesh_objects = get_from_collection(BlenderTypes.MESH)
    lod_objects = []
    for mesh_object in mesh_objects:
        if is_lod_of(asset_name, mesh_object.name, properties):
            lod_objects.append(mesh_object)
    return lod_objects


def get_current_context():
    """
    Gets the current state of the scene and its objects.

    :return dict: Dictionary with scene state.
    """
    active_object = bpy.context.view_layer.objects.active
    selected_objects = bpy.context.selected_objects.copy()
    current_frame = bpy.context.scene.frame_current
    visible_objects = [obj for obj in bpy.context.scene.objects if obj.visible_get()]

    return {
        'active_object': active_object,
        'selected_objects': selected_objects,
        'current_frame': current_frame,
        'visible_objects': visible_objects
    }


def set_context(context_data):
    """
    Restores the scene state.

    :param dict context_data: Dictionary with scene state to restore.
    """
    # deselect all objects first
    deselect_all_objects()

    # restore selected objects
    for scene_object in context_data.get('selected_objects', []):
        if scene_object.name in bpy.context.scene.objects:
            scene_object.select_set(True)

    # restore active object
    active_object = context_data.get('active_object')
    if active_object and active_object.name in bpy.context.scene.objects:
        bpy.context.view_layer.objects.active = active_object

    # restore current frame
    current_frame = context_data.get('current_frame')
    if current_frame is not None:
        bpy.context.scene.frame_current = current_frame


def remove_temp_data():
    """
    Removes all temp data.
    """
    remove_temp_folder()


def remove_temp_folder():
    """
    Removes the temp folder.
    """
    temp_folder = get_temp_folder()
    if os.path.exists(temp_folder):
        try:
            shutil.rmtree(temp_folder)
        except PermissionError:
            pass