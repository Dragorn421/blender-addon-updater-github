# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

from pathlib import Path
import urllib.request
import json
from typing import Optional
import io
import traceback


def log(*args):
    print(__name__, *args)


class Settings:
    package: str
    root_dir: Path
    commit_sha: Optional[str]
    simulate: bool


SETTINGS = Settings()

ERRORS: dict[str, str] = dict()


class AddonPreferences:
    auto_check_updates: bpy.props.BoolProperty(name="Auto-check updates", default=True)

    def draw(self):
        layout: bpy.types.UILayout = self.layout
        layout.prop(self, "auto_check_updates")

        for key, val in ERRORS.items():
            box = layout.box()
            box.alert = True
            layout.label(icon="ERROR", text=key)
            box.separator()
            for line in val.splitlines():
                layout.label(text=line)


def handler_load_post_impl():
    prefs = bpy.context.preferences.addons[SETTINGS.package].preferences
    if prefs is None:
        log("No prefs")
        return
    assert isinstance(
        prefs, AddonPreferences
    ), f"Addon preferences of {SETTINGS.package} should use the github_updater.AddonPreferences mixin"
    if prefs.auto_check_updates:
        with urllib.request.urlopen(
            "https://api.github.com/repos/Dragorn421/blender-addon-updater-github/commits/main"
        ) as f:
            data = json.load(f)
        sha = data["sha"]
        log("latest sha =", sha)


@bpy.app.handlers.persistent
def handler_load_post(*_args, **_kwargs):
    try:
        handler_load_post_impl()
    except:
        string_io = io.StringIO()
        traceback.print_exc(file=string_io)
        ERRORS["handler_load_post_impl"] = string_io.getvalue()
        raise
    finally:
        bpy.app.handlers.load_post.remove(handler_load_post)


def register(root_package: str, root_init_file: str):
    SETTINGS.package = root_package

    SETTINGS.root_dir = Path(root_init_file).parent

    commit_txt_path = SETTINGS.root_dir / "commit.txt"
    if commit_txt_path.is_file():
        commit = commit_txt_path.read_text().strip()
        if commit == "main":
            SETTINGS.commit_sha = None
        else:
            SETTINGS.commit_sha = commit
    else:
        SETTINGS.commit_sha = None

    if (SETTINGS.root_dir / ".git").exists():
        SETTINGS.simulate = True
    else:
        SETTINGS.simulate = False

    bpy.app.handlers.load_post.append(handler_load_post)


def unregister():
    try:
        bpy.app.handlers.load_post.remove(handler_load_post)
    except:
        pass
