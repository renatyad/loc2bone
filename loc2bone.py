# -*- coding: utf-8 -*-
"""loc2bone: locator hierarchy to FK joint/control rig builder for Maya 2022-2026."""

import importlib
import math
import os
import re
import shutil
import sys
import tempfile

import maya.cmds as cmds
import maya.mel as mel
import maya.api.OpenMaya as om


PLUGIN_CMD = "loc2bone"
VERSION = "0.0.1"
COPYRIGHT = "© Renat Yad, 2026"
WINDOW = "loc2boneWindow"
MENU = "loc2boneMenu"
SHELF_MARKER = "loc2bone: Open FK Rig Builder"
SHELF_ICON = "kinJoint.png"
DEFAULT_PREFIX = "L2B"
LOCATOR_TYPES = ("trunk", "brunch", "leaf")
LOCATOR_LAYOUTS = ("FK Chain", "Independent")
MAX_GENERATED_LOCATORS = 1000
CONTROL_SHAPES = ("Circle", "Triangle", "Square", "Star")
DEFAULT_CONTROL_SHAPE = "Circle"
CONTROL_COLORS = {
    "Red": (1.0, 0.08, 0.08),
    "Yellow": (1.0, 0.85, 0.05),
    "Purple": (0.55, 0.18, 1.0),
    "Orange": (1.0, 0.35, 0.04),
    "Blue": (0.08, 0.35, 1.0),
    "Cyan": (0.0, 0.85, 1.0),
    "Green": (0.1, 0.75, 0.25),
    "Lime": (0.6, 1.0, 0.05),
    "Pink": (1.0, 0.25, 0.6),
    "Magenta": (1.0, 0.0, 0.85),
    "Teal": (0.0, 0.6, 0.55),
    "Slate": (0.38, 0.48, 0.62),
}
DEFAULT_CONTROL_COLOR = "Red"
NAME_RE = re.compile(
    r"^(?P<prefix>[A-Za-z_][A-Za-z0-9_]*)_"
    r"(?P<kind>trunk|brunch|leaf)_(?P<number>\d+)$"
)


def _interactive_ui_available():
    return not cmds.about(batch=True)


def _require_interactive_ui():
    if not _interactive_ui_available():
        raise RuntimeError(
            "loc2bone opens an interactive UI and cannot run in Maya batch mode."
        )


def _canonical_path(path):
    return os.path.normcase(os.path.abspath(path))


def _prioritize_script_directory(script_dir):
    normalized_dir = _canonical_path(script_dir)
    sys.path[:] = [
        path
        for path in sys.path
        if _canonical_path(path) != normalized_dir
    ]
    sys.path.insert(0, normalized_dir)
    return normalized_dir


def _short_name(node):
    return node.rsplit("|", 1)[-1]


def _is_locator(node):
    return bool(
        cmds.listRelatives(
            node,
            shapes=True,
            noIntermediate=True,
            type="locator",
        )
    )


def _valid_prefix(prefix):
    return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", prefix or ""))


def _validate_source_inputs(prefix, kind):
    if not _valid_prefix(prefix):
        raise RuntimeError("Prefix must match [A-Za-z_][A-Za-z0-9_]*.")
    if kind not in LOCATOR_TYPES:
        raise RuntimeError("Invalid locator type '{}'.".format(kind))


def _reserve_source_names(prefix, kind, count):
    """Return available exact source locator names without scene mutation."""
    _validate_source_inputs(prefix, kind)
    names = []
    number = 1

    while len(names) < count:
        candidate = "{}_{}_{:03d}".format(prefix, kind, number)
        if not cmds.objExists(candidate):
            names.append(candidate)
        number += 1

    return names


def _locator_nodes(prefix):
    result = []
    for node in cmds.ls("{}_*".format(prefix), long=True, type="transform") or []:
        match = NAME_RE.match(_short_name(node))
        if _is_locator(node) and match and match.group("prefix") == prefix:
            result.append(node)
    return result


def _source_parent(node, node_set):
    parent = (cmds.listRelatives(node, parent=True, fullPath=True) or [None])[0]
    while parent:
        if parent in node_set:
            return parent
        if _is_locator(parent):
            raise RuntimeError(
                "Unlabeled locator '{}' interrupts the hierarchy.".format(
                    _short_name(parent)
                )
            )
        parent = (cmds.listRelatives(parent, parent=True, fullPath=True) or [None])[0]
    return None


def _source_data(prefix):
    nodes = _locator_nodes(prefix)
    if not nodes:
        raise RuntimeError("No labeled locators found for prefix '{}'.".format(prefix))

    node_set = set(nodes)
    token_set = set()
    data = {}

    for node in nodes:
        name = _short_name(node)
        if name in token_set:
            raise RuntimeError("Duplicate locator token '{}'.".format(name))
        token_set.add(name)

        match = NAME_RE.match(name)
        parent_node = _source_parent(node, node_set)

        data[node] = {
            "name": name,
            "kind": match.group("kind"),
            "parent": parent_node,
            "matrix": om.MMatrix(
                cmds.xform(node, query=True, matrix=True, worldSpace=True)
            ),
        }

    return data


def _matrix_translation(matrix):
    return om.MTransformationMatrix(matrix).translation(om.MSpace.kWorld)


def _reset_transform(node):
    for axis in "XYZ":
        cmds.setAttr("{}.translate{}".format(node, axis), 0.0)
        cmds.setAttr("{}.rotate{}".format(node, axis), 0.0)
        cmds.setAttr("{}.scale{}".format(node, axis), 1.0)


def _control_radius(source_node, source_data):
    children = [
        item
        for item in source_data.values()
        if item["parent"] == source_node
    ]
    if not children:
        return 1.0

    origin = _matrix_translation(source_data[source_node]["matrix"])
    distances = [
        (_matrix_translation(item["matrix"]) - origin).length()
        for item in children
    ]
    return max(min(distances) * 0.25, 0.1)


def _closed_curve(name, points):
    return cmds.curve(name=name, degree=1, point=points)


def _create_control_curve(name, shape, radius):
    if shape == "Circle":
        return cmds.circle(
            name=name,
            normal=(1.0, 0.0, 0.0),
            radius=radius,
            constructionHistory=False,
        )[0]

    if shape == "Triangle":
        points = [
            (0.0, radius * math.cos(angle), radius * math.sin(angle))
            for angle in (math.pi * 0.5, math.pi * 7.0 / 6.0, math.pi * 11.0 / 6.0)
        ]
    elif shape == "Square":
        half = radius / math.sqrt(2.0)
        points = [
            (0.0, half, half),
            (0.0, -half, half),
            (0.0, -half, -half),
            (0.0, half, -half),
        ]
    elif shape == "Star":
        points = []
        for index in range(10):
            angle = math.pi * 0.5 + index * math.pi / 5.0
            point_radius = radius if index % 2 == 0 else radius * 0.45
            points.append(
                (0.0, point_radius * math.cos(angle), point_radius * math.sin(angle))
            )
    else:
        raise RuntimeError("Invalid control shape '{}'.".format(shape))

    return _closed_curve(name, points + [points[0]])


def _set_control_local_rotation_axes(control, enabled):
    if not isinstance(enabled, bool):
        raise RuntimeError("Local rotation axis setting must be a boolean.")
    if not cmds.objExists(control):
        raise RuntimeError("Control '{}' no longer exists.".format(control))
    if not cmds.attributeQuery("displayLocalAxis", node=control, exists=True):
        raise RuntimeError(
            "Control '{}' has no displayLocalAxis attribute.".format(control)
        )
    cmds.setAttr("{}.displayLocalAxis".format(control), enabled)


def _make_control(name, parent, matrix, radius, color, shape, show_local_rotation_axes=False):
    control = _create_control_curve(name, shape, radius)
    offset = cmds.createNode("transform", name="{}_gr".format(name))
    cmds.xform(offset, matrix=list(matrix), worldSpace=True)
    cmds.parent(control, offset)
    _reset_transform(control)

    if parent:
        cmds.parent(offset, parent)
        cmds.xform(offset, matrix=list(matrix), worldSpace=True)

    cmds.setAttr("{}.overrideEnabled".format(control), 1)
    cmds.setAttr("{}.overrideRGBColors".format(control), 1)
    cmds.setAttr("{}.overrideColorRGB".format(control), *color)
    _set_control_local_rotation_axes(control, show_local_rotation_axes)
    return offset, control


def _make_joint(name, parent, matrix):
    offset = cmds.createNode("transform", name="{}_gr".format(name))
    cmds.xform(offset, matrix=list(matrix), worldSpace=True)
    joint = cmds.createNode("joint", name=name)
    cmds.parent(joint, offset)
    for axis in "XYZ":
        cmds.setAttr("{}.jointOrient{}".format(joint, axis), 0.0)
    _reset_transform(joint)

    if parent:
        cmds.parent(offset, parent)
        cmds.xform(offset, matrix=list(matrix), worldSpace=True)

    return offset, joint


def label_selected(prefix, kind):
    _validate_source_inputs(prefix, kind)
    selection = cmds.ls(selection=True, long=True, type="transform") or []
    if not selection:
        raise RuntimeError("Select one or more locator transforms.")

    invalid = [_short_name(node) for node in selection if not _is_locator(node)]
    if invalid:
        raise RuntimeError(
            "Selection contains non-locators: {}".format(", ".join(invalid))
        )

    names = _reserve_source_names(prefix, kind, len(selection))
    ordered = sorted(selection, key=lambda node: node.count("|"), reverse=True)
    renamed = []

    with _undo_chunk():
        for node, name in zip(ordered, names):
            result = cmds.rename(node, name)
            if result != name:
                raise RuntimeError("Could not assign exact name '{}'.".format(name))
            renamed.append(result)

    cmds.select(renamed, replace=True)
    return renamed


def _validate_generator_inputs(prefix, kind, count, spacing, layout):
    _validate_source_inputs(prefix, kind)
    if isinstance(count, bool) or not isinstance(count, int):
        raise RuntimeError("Count must be an integer.")
    if not 1 <= count <= MAX_GENERATED_LOCATORS:
        raise RuntimeError(
            "Count must be between 1 and {}.".format(MAX_GENERATED_LOCATORS)
        )
    if not isinstance(spacing, (int, float)) or not math.isfinite(spacing):
        raise RuntimeError("Spacing must be a finite number.")
    if spacing <= 0.0:
        raise RuntimeError("Spacing must be greater than zero.")
    if layout not in LOCATOR_LAYOUTS:
        raise RuntimeError("Invalid locator layout '{}'.".format(layout))


def generate_locators(prefix, kind, count, spacing, layout):
    _validate_generator_inputs(prefix, kind, count, spacing, layout)
    names = _reserve_source_names(prefix, kind, count)
    created = []

    with _undo_chunk():
        for index, name in enumerate(names):
            locator = cmds.spaceLocator(name=name)[0]
            if locator != name:
                raise RuntimeError("Could not create exact name '{}'.".format(name))
            cmds.xform(locator, worldSpace=True, translation=(index * spacing, 0.0, 0.0))

            if layout == "FK Chain" and created:
                world_matrix = cmds.xform(locator, query=True, matrix=True, worldSpace=True)
                cmds.parent(locator, created[-1])
                cmds.xform(locator, matrix=world_matrix, worldSpace=True)

            created.append(locator)

    cmds.select(created, replace=True)
    return created


def build_rig(
    prefix,
    control_color=DEFAULT_CONTROL_COLOR,
    control_shape=DEFAULT_CONTROL_SHAPE,
    show_local_rotation_axes=False,
):
    if not _valid_prefix(prefix):
        raise RuntimeError("Prefix must match [A-Za-z_][A-Za-z0-9_]*.")
    if control_color not in CONTROL_COLORS:
        raise RuntimeError("Invalid control color '{}'.".format(control_color))
    if control_shape not in CONTROL_SHAPES:
        raise RuntimeError("Invalid control shape '{}'.".format(control_shape))
    if not isinstance(show_local_rotation_axes, bool):
        raise RuntimeError("Local rotation axis setting must be a boolean.")

    data = _source_data(prefix)
    rig_group = "{}_rig_gr".format(prefix)
    if cmds.objExists(rig_group):
        raise RuntimeError("Generated group '{}' already exists.".format(rig_group))

    joint_map = {}
    control_map = {}

    with _undo_chunk():
        rig_group = cmds.createNode("transform", name=rig_group)
        joint_root = cmds.createNode(
            "transform",
            name="{}_jnt_gr".format(prefix),
            parent=rig_group,
        )
        control_root = cmds.createNode(
            "transform",
            name="{}_ctrl_gr".format(prefix),
            parent=rig_group,
        )

        pending = list(data.keys())
        while pending:
            progress = False
            for source in pending[:]:
                parent_source = data[source]["parent"]
                if parent_source is not None and parent_source not in joint_map:
                    continue

                joint_parent = (
                    joint_map[parent_source]["joint"]
                    if parent_source else joint_root
                )
                control_parent = (
                    control_map[parent_source]["control"]
                    if parent_source else control_root
                )
                token = data[source]["name"]
                joint_name = "{}_jnt".format(token)
                control_name = "{}_ctrl".format(token)

                joint_off, joint = _make_joint(
                    joint_name,
                    joint_parent,
                    data[source]["matrix"],
                )
                control_off, control = _make_control(
                    control_name,
                    control_parent,
                    data[source]["matrix"],
                    _control_radius(source, data),
                    CONTROL_COLORS[control_color],
                    control_shape,
                    show_local_rotation_axes,
                )
                cmds.parentConstraint(
                    control,
                    joint,
                    maintainOffset=False,
                    weight=1.0,
                    name="{}_PARENT_CONSTRAINT".format(token),
                )
                cmds.scaleConstraint(
                    control,
                    joint,
                    maintainOffset=False,
                    weight=1.0,
                    name="{}_SCALE_CONSTRAINT".format(token),
                )
                joint_map[source] = {"offset": joint_off, "joint": joint}
                control_map[source] = {"offset": control_off, "control": control}
                pending.remove(source)
                progress = True

            if not progress:
                raise RuntimeError(
                    "Locator hierarchy contains a cycle or unresolved parent."
                )

        cmds.select(rig_group, replace=True)

    return rig_group


class _undo_chunk(object):
    def __enter__(self):
        cmds.undoInfo(openChunk=True, chunkName=PLUGIN_CMD)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        cmds.undoInfo(closeChunk=True)
        if exc_type:
            try:
                cmds.undo()
            except RuntimeError:
                pass
        return False


def _ui_error(error):
    cmds.warning("loc2bone: {}".format(error))


def _field_value(control):
    return cmds.textFieldGrp(control, query=True, text=True).strip()


def _kind_value(control):
    return cmds.optionMenuGrp(control, query=True, value=True)


def _shape_value(control):
    return cmds.optionMenuGrp(control, query=True, value=True)


def _count_value(control):
    return cmds.intFieldGrp(control, query=True, value1=True)


def _spacing_value(control):
    return cmds.floatFieldGrp(control, query=True, value1=True)


def _layout_value(control):
    index = cmds.radioButtonGrp(control, query=True, select=True)
    return LOCATOR_LAYOUTS[index - 1]


def _local_axes_value(control):
    return bool(cmds.checkBox(control, query=True, value=True))


def _select_color(color_name, color_state, selected_label):
    color_state["name"] = color_name
    cmds.text(
        selected_label,
        edit=True,
        label="Color: {}".format(color_name),
    )


def show_ui(*_):
    _require_interactive_ui()
    if cmds.window(WINDOW, exists=True):
        cmds.showWindow(WINDOW)
        return

    color_state = {"name": DEFAULT_CONTROL_COLOR}
    window = cmds.window(
        WINDOW,
        title="loc2bone | FK Rig Builder",
        sizeable=False,
        menuBar=True,
        widthHeight=(430, 690),
    )
    help_menu = cmds.menu(label="Help", parent=window, tearOff=False)
    cmds.menuItem(
        label="User Guide",
        parent=help_menu,
        command=lambda *_: _show_user_guide(),
    )
    cmds.menuItem(divider=True, parent=help_menu)
    cmds.menuItem(
        label="About...",
        parent=help_menu,
        command=lambda *_: _show_about(),
    )
    cmds.columnLayout(
        adjustableColumn=True,
        rowSpacing=6,
        columnAttach=("both", 10),
    )
    cmds.columnLayout(adjustableColumn=True, rowSpacing=2)
    cmds.text(label="loc2bone", align="left", font="boldLabelFont")
    cmds.text(
        label="Build FK joints and controls from labeled locators",
        align="left",
    )
    cmds.setParent("..")

    cmds.frameLayout(
        label="Source Setup",
        collapsable=False,
        marginWidth=8,
        marginHeight=8,
    )
    prefix_field = cmds.textFieldGrp(
        label="Prefix",
        text=DEFAULT_PREFIX,
        columnWidth=(1, 96),
    )
    kind_menu = cmds.optionMenuGrp(
        label="Locator Type",
        columnWidth=(1, 96),
    )
    for kind in LOCATOR_TYPES:
        cmds.menuItem(label=kind)
    cmds.button(
        label="Label Selection",
        height=28,
        command=lambda *_: _run_label(prefix_field, kind_menu),
    )
    cmds.separator(style="in", height=10)
    cmds.text(label="Locator Chain Generator", align="left", font="smallBoldLabelFont")
    count_field = cmds.intFieldGrp(
        label="Count",
        value1=5,
        columnWidth=(1, 96),
    )
    spacing_field = cmds.floatFieldGrp(
        label="Spacing",
        value1=5.0,
        precision=3,
        columnWidth=(1, 96),
    )
    layout_buttons = cmds.radioButtonGrp(
        label="Layout",
        numberOfRadioButtons=2,
        labelArray2=LOCATOR_LAYOUTS,
        select=1,
        columnWidth3=(96, 145, 145),
    )
    cmds.button(
        label="Create Locators",
        height=30,
        command=lambda *_: _run_generate(
            prefix_field,
            kind_menu,
            count_field,
            spacing_field,
            layout_buttons,
        ),
    )
    cmds.setParent("..")

    cmds.frameLayout(
        label="Control Appearance",
        collapsable=False,
        marginWidth=8,
        marginHeight=8,
    )
    shape_menu = cmds.optionMenuGrp(
        label="Control Shape",
        columnWidth=(1, 96),
    )
    for shape in CONTROL_SHAPES:
        cmds.menuItem(label=shape)
    selected_label = cmds.text(
        label="Color: {}".format(DEFAULT_CONTROL_COLOR),
        align="left",
    )
    cmds.gridLayout(
        numberOfColumns=4,
        cellWidthHeight=(98, 28),
    )
    for color_name, rgb in CONTROL_COLORS.items():
        cmds.button(
            label=color_name,
            backgroundColor=rgb,
            command=lambda *_args, name=color_name: _select_color(
                name,
                color_state,
                selected_label,
            ),
        )
    cmds.setParent("..")
    cmds.setParent("..")

    cmds.frameLayout(
        label="Rig Actions",
        collapsable=False,
        marginWidth=8,
        marginHeight=8,
    )
    local_axes_checkbox = cmds.checkBox(
        label="Display Local Rotation Axes",
        value=False,
    )
    cmds.button(
        label="Build Rig",
        height=38,
        command=lambda *_: _run_build(
            prefix_field,
            color_state,
            shape_menu,
            local_axes_checkbox,
        ),
    )
    cmds.button(
        label="Validate",
        height=28,
        command=lambda *_: _run_validate(prefix_field),
    )
    cmds.setParent("..")

    cmds.separator(style="in", height=8)
    cmds.button(
        label="Close",
        height=28,
        command=lambda *_: cmds.deleteUI(WINDOW),
    )
    cmds.showWindow(window)


def _run_label(prefix_field, kind_menu):
    try:
        renamed = label_selected(
            _field_value(prefix_field),
            _kind_value(kind_menu),
        )
        cmds.inViewMessage(
            amg="loc2bone: labeled {} locator(s)".format(len(renamed)),
            pos="topCenter",
            fade=True,
        )
    except RuntimeError as error:
        _ui_error(error)


def _run_generate(
    prefix_field,
    kind_menu,
    count_field,
    spacing_field,
    layout_buttons,
):
    try:
        created = generate_locators(
            _field_value(prefix_field),
            _kind_value(kind_menu),
            _count_value(count_field),
            _spacing_value(spacing_field),
            _layout_value(layout_buttons),
        )
        cmds.inViewMessage(
            amg="loc2bone: created {} locator(s)".format(len(created)),
            pos="topCenter",
            fade=True,
        )
    except RuntimeError as error:
        _ui_error(error)


def _run_build(prefix_field, color_state, shape_menu, local_axes_checkbox):
    try:
        group = build_rig(
            _field_value(prefix_field),
            color_state["name"],
            _shape_value(shape_menu),
            _local_axes_value(local_axes_checkbox),
        )
        cmds.inViewMessage(
            amg="loc2bone: built {}".format(group),
            pos="topCenter",
            fade=True,
        )
    except RuntimeError as error:
        _ui_error(error)


def _run_validate(prefix_field):
    try:
        data = _source_data(_field_value(prefix_field))
        roots = sum(1 for item in data.values() if item["parent"] is None)
        cmds.confirmDialog(
            title="loc2bone Validation",
            message="{} labeled locators, {} root(s).".format(len(data), roots),
            button="OK",
        )
    except RuntimeError as error:
        _ui_error(error)


def _close_layout_dialog(*_):
    cmds.layoutDialog(dismiss="Close")


def _centered_close_button():
    button_width = 100
    button_height = 28
    layout = cmds.formLayout(
        height=button_height,
        numberOfDivisions=100,
    )
    button = cmds.button(
        label="Close",
        width=button_width,
        height=button_height,
        command=_close_layout_dialog,
    )
    cmds.formLayout(
        layout,
        edit=True,
        attachForm=[
            (button, "top", 0),
            (button, "bottom", 0),
        ],
        attachPosition=[
            (button, "left", 50, -(button_width // 2)),
            (button, "right", 50, button_width // 2),
        ],
    )
    cmds.setParent("..")


def _modal_dialog_layout(lines, title_line=None):
    layout = cmds.columnLayout(
        adjustableColumn=True,
        rowSpacing=8,
        columnAttach=("both", 18),
    )
    if title_line:
        cmds.text(label=title_line, align="left", font="boldLabelFont")
        cmds.separator(style="in")
    for line in lines:
        cmds.text(label=line, align="left")
    cmds.separator(style="none", height=20)
    _centered_close_button()
    return layout


def _show_user_guide(*_):
    lines = (
        "Maya 2022-2026 compatible.",
        "Prefix: [A-Za-z_][A-Za-z0-9_]*.",
        "Locator Type: source label category.",
        "Label Selection: labels selected locators as PREFIX_TYPE_###.",
        "Count / Spacing: create locators along world X.",
        "FK Chain: each locator is parented to the previous locator.",
        "Independent: each locator remains a world root.",
        "Create Locators: skips occupied numeric labels.",
        "Control Shape / Color: apply to newly built controls.",
        "Display Local Rotation Axes: affects new _ctrl nodes only.",
        "Build Rig: creates <prefix>_rig_gr with _jnt and _ctrl hierarchies.",
        "Validate: checks labels and locator hierarchy only.",
        "Generation and rig build are each one undo operation.",
    )
    cmds.layoutDialog(
        title="loc2bone User Guide",
        ui=lambda: _modal_dialog_layout(lines, "loc2bone User Guide"),
    )


def _show_about(*_):
    cmds.layoutDialog(
        title="About loc2bone",
        ui=lambda: _modal_dialog_layout(
            ("Version {}".format(VERSION), COPYRIGHT),
            "loc2bone",
        ),
    )


class Loc2BoneCommand(om.MPxCommand):
    @staticmethod
    def creator():
        return Loc2BoneCommand()

    def doIt(self, args):
        _require_interactive_ui()
        show_ui()


def _create_menu():
    if cmds.menu(MENU, exists=True):
        cmds.deleteUI(MENU)
    cmds.menu(MENU, label="loc2bone", parent="MayaWindow", tearOff=True)
    cmds.menuItem(label="Open loc2bone", parent=MENU, command=show_ui)
    cmds.menuItem(divider=True, parent=MENU)
    cmds.menuItem(
        label="User Guide",
        parent=MENU,
        command=lambda *_: _show_user_guide(),
    )
    cmds.menuItem(
        label="About...",
        parent=MENU,
        command=lambda *_: _show_about(),
    )


def _delete_menu():
    if cmds.menu(MENU, exists=True):
        cmds.deleteUI(MENU)


def initializePlugin(plugin):
    plugin_fn = om.MFnPlugin(plugin, "loc2bone", VERSION, "Any")
    plugin_fn.registerCommand(PLUGIN_CMD, Loc2BoneCommand.creator)
    if _interactive_ui_available():
        _create_menu()


def uninitializePlugin(plugin):
    if _interactive_ui_available():
        _delete_menu()
        if cmds.window(WINDOW, exists=True):
            cmds.deleteUI(WINDOW)
    plugin_fn = om.MFnPlugin(plugin)
    plugin_fn.deregisterCommand(PLUGIN_CMD)


def run():
    """Open a fresh loc2bone window from the Script Editor or shelf."""
    _require_interactive_ui()
    _create_menu()
    if cmds.window(WINDOW, exists=True):
        cmds.deleteUI(WINDOW)
    show_ui()


def _script_files_match(source_path, target_path):
    if not os.path.isfile(target_path):
        return False
    with open(source_path, "rb") as source_file:
        with open(target_path, "rb") as target_file:
            return source_file.read() == target_file.read()


def _install_script_to_user_directory():
    source_path = _canonical_path(__file__)
    script_dir = _canonical_path(cmds.internalVar(userScriptDir=True))
    target_path = _canonical_path(os.path.join(script_dir, "loc2bone.py"))
    temp_path = ""

    try:
        if source_path == target_path or _script_files_match(source_path, target_path):
            return script_dir, target_path

        file_descriptor, temp_path = tempfile.mkstemp(
            prefix=".loc2bone_",
            suffix=".tmp",
            dir=script_dir,
        )
        os.close(file_descriptor)
        shutil.copy2(source_path, temp_path)
        os.replace(temp_path, target_path)
    except (IOError, OSError) as error:
        raise RuntimeError(
            "Could not install loc2bone to '{}': {}".format(script_dir, error)
        )
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass

    return script_dir, target_path


def _active_shelf():
    try:
        shelf = cmds.tabLayout("ShelfLayout", query=True, selectTab=True)
    except RuntimeError:
        shelf = None
    if not shelf or not cmds.shelfLayout(shelf, exists=True):
        raise RuntimeError("Select an active shelf before installing loc2bone.")
    return shelf


def _shelf_command(script_dir):
    return "\n".join((
        "import importlib",
        "import os",
        "import sys",
        "_loc2bone_dir = os.path.normcase(os.path.abspath({!r}))".format(script_dir),
        "sys.path[:] = [path for path in sys.path if os.path.normcase(os.path.abspath(path)) != _loc2bone_dir]",
        "sys.path.insert(0, _loc2bone_dir)",
        "_loc2bone_path = os.path.normcase(os.path.abspath(os.path.join(_loc2bone_dir, 'loc2bone.py')))",
        "_loc2bone_module = sys.modules.get('loc2bone')",
        "if _loc2bone_module:",
        "    _loaded_path = os.path.normcase(os.path.abspath(getattr(_loc2bone_module, '__file__', '')))",
        "    if _loaded_path != _loc2bone_path:",
        "        del sys.modules['loc2bone']",
        "import loc2bone",
        "importlib.reload(loc2bone)",
        "loc2bone.run()",
    ))


def _update_shelf_button(shelf, script_dir):
    command = _shelf_command(script_dir)
    owned_buttons = []

    for child in cmds.shelfLayout(shelf, query=True, childArray=True) or []:
        try:
            if cmds.shelfButton(child, query=True, annotation=True) == SHELF_MARKER:
                owned_buttons.append(child)
        except RuntimeError:
            pass

    button_kwargs = {
        "label": "loc2bone",
        "annotation": SHELF_MARKER,
        "image1": SHELF_ICON,
        "imageOverlayLabel": "L2B",
        "command": command,
        "sourceType": "python",
    }

    if owned_buttons:
        button = owned_buttons[0]
        cmds.shelfButton(button, edit=True, **button_kwargs)
        for duplicate in owned_buttons[1:]:
            cmds.deleteUI(duplicate)
    else:
        button = cmds.shelfButton(parent=shelf, **button_kwargs)

    try:
        mel.eval("saveAllShelves $gShelfTopLevel;")
    except RuntimeError:
        cmds.warning("loc2bone: shelf button added but could not save shelves.")

    return button


def _load_installed_module(script_dir, target_path):
    script_dir = _prioritize_script_directory(script_dir)
    target_path = _canonical_path(target_path)
    module = sys.modules.get("loc2bone")
    if module:
        loaded_path = _canonical_path(getattr(module, "__file__", ""))
        if loaded_path != target_path:
            del sys.modules["loc2bone"]

    importlib.invalidate_caches()
    module = importlib.import_module("loc2bone")
    return importlib.reload(module)


def onMayaDroppedPythonFile(*_):
    """Install this file and add or update the active-shelf launcher."""
    try:
        script_dir, target_path = _install_script_to_user_directory()
        _update_shelf_button(_active_shelf(), script_dir)
        installed_module = _load_installed_module(script_dir, target_path)
        cmds.inViewMessage(
            amg="loc2bone installed to the active shelf.",
            pos="topCenter",
            fade=True,
        )
        installed_module.run()
    except (RuntimeError, ImportError, OSError) as error:
        cmds.confirmDialog(
            title="loc2bone Installation",
            message=str(error),
            button="Close",
        )


if __name__ == "__main__":
    run()
