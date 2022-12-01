# SPDX-License-Identifier: GPL-3.0-or-later

bl_info = {}

import bpy

from . import github_updater


class HelloWorldOperator(bpy.types.Operator):
    bl_idname = "myaddon.helloworld"

    def execute(self, context):
        self.report({"INFO"}, "Hello World!")
        return {"SUCCESS"}


class MyAddonPreferences(bpy.types.AddonPreferences, github_updater.AddonPreferences):
    bl_idname = __package__

    def draw(self):
        github_updater.AddonPreferences.draw(self)


def register():
    bpy.utils.register_class(HelloWorldOperator)
    bpy.utils.register_class(MyAddonPreferences)
    github_updater.register(__package__, __file__)


def unregister():
    bpy.utils.unregister_class(HelloWorldOperator)
    bpy.utils.unregister_class(MyAddonPreferences)
    github_updater.unregister()
