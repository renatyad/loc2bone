# User Guide

## Open the tool

Drag `loc2bone.py` into a Maya viewport to install it on the active shelf. Click **loc2bone** to open the tool.

For Script Editor use:

```python
import loc2bone
loc2bone.run()
```

## Source setup

Use one prefix per rig. A prefix can contain letters, numbers, and underscores, but cannot start with a number.

**Locator Type** is a naming category only. The available values are `trunk`, `brunch`, and `leaf`.

### Label Selection

Select locator transforms, set the prefix and type, then click **Label Selection**. The tool assigns the next free names, for example:

```text
Plant_trunk_001
Plant_trunk_002
```

## Locator Chain Generator

Set a count, spacing, and layout, then click **Create Locators**.

- **FK Chain** parents each locator under the preceding locator while preserving world-space spacing.
- **Independent** creates separate root locators.
- Generated locators are placed along world X.
- Occupied numbers are skipped rather than overwritten.

## Control appearance

Choose a Circle, Triangle, Square, or Star before building. Pick a viewport color from the palette. Both choices apply to controls created by the next build.

Enable **Display Local Rotation Axes** only when you want visible axes on the generated controls. It does not change transforms, constraints, joints, or offset groups.

## Validate and build

**Validate** checks that the prefix finds labeled locators and that no unlabeled locator interrupts their hierarchy. Regular transform groups can sit between labeled locators; the nearest labeled locator ancestor becomes the generated FK parent.

**Build Rig** creates:

```text
PREFIX_rig_gr
├── PREFIX_jnt_gr
└── PREFIX_ctrl_gr
```

Every labeled source locator produces a matching `_jnt` and `_ctrl`, each under a `_gr` offset. Controls drive joints through parent and scale constraints.

## Common messages

**No labeled locators found**
: Check the prefix and locator names.

**Unlabeled locator interrupts the hierarchy**
: Label the locator between the two source locators, or remove it from that part of the hierarchy.

**Generated group already exists**
: Delete or rename the old `PREFIX_rig_gr`, or use another prefix.

## Update

Download the newer `loc2bone.py` and drag it into Maya again. The installed script and existing shelf button are updated in place.
