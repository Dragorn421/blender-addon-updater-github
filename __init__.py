# SPDX-License-Identifier: GPL-3.0-or-later

bl_info = {
    "name": "(blender-addon-updater-github demo) Hello World",
    "blender": (3, 3, 0),
    "category": "Development",
}

import bpy

from . import github_updater


class HelloWorldOperator(bpy.types.Operator):
    bl_idname = "myaddon.helloworld"
    bl_label = "(blender-addon-updater-github demo) Hello World"

    def execute(self, context):
        self.report({"INFO"}, "Hello World!")
        return {"SUCCESS"}


class MyAddonPreferences(bpy.types.AddonPreferences, github_updater.AddonPreferences):
    bl_idname = __package__

    def draw(self, context):
        github_updater.AddonPreferences.draw(self, context)


def register():
    github_updater.register(__package__, __file__)
    bpy.utils.register_class(HelloWorldOperator)
    bpy.utils.register_class(MyAddonPreferences)


def unregister():
    bpy.utils.unregister_class(HelloWorldOperator)
    bpy.utils.unregister_class(MyAddonPreferences)
    github_updater.unregister()
