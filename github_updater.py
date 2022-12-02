# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

from dataclasses import dataclass
from pathlib import Path
import datetime
import urllib.request
import urllib.error
import json
from typing import Optional
import io
import traceback
import re


def log(*args):
    print(__name__, *args)


@dataclass
class Remote:
    owner: str  # "Dragorn421"
    repo: str  # "blender-addon-updater-github"
    branch: str  # "main"


class Settings:
    package: str
    root_dir: Path
    commit_sha: Optional[str]
    simulate: bool
    builtin_remotes: list[Remote]

    def get_cafile(self):
        """Get a path to a certificate to trust to authenticate github api https requests.

        Python bundled with Blender doesn't seem to like verifying the api.github.com certificate:

            [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate (_ssl.c:997)

        urllib can be told to simply skip verifying certificates, but that's probably a bad idea
        (for reference: `ssl._create_unverified_context()`).
        Instead, the workaround here is to provide a certificate to trust.

        On 2022-12-01, the certificate for api.github.com was certified by the certificate "DigiCert Global Root CA".
        This certificate could/can be found at https://www.digicert.com/kb/digicert-root-certificates.htm .
        Direct link was/is: https://cacerts.digicert.com/DigiCertGlobalRootCA.crt.pem .

        Note it will expire in November 2031, but may be phased out or revoked before then.
        The GitHub certificate may also not rely on this certificate in the future.
        Overall, this is quite fragile, but there doesn't seem to be a better painless alternative.
        """
        return self.root_dir / "DigiCertGlobalRootCA.crt.pem"


SETTINGS = Settings()

ERRORS: dict[str, str] = dict()


class CustomRemotePropertyGroup(bpy.types.PropertyGroup, Remote):
    owner: bpy.props.StringProperty()
    repo: bpy.props.StringProperty()
    branch: bpy.props.StringProperty()

    def is_set(self):
        return self.owner or self.repo or self.branch

    def github_tree_url_get(self):
        if self.is_set():
            return f"https://github.com/{self.owner}/{self.repo}/tree/{self.branch}"
        else:
            return ""

    def github_tree_url_set(self, url: str):
        m = re.fullmatch(
            r"\s*https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/tree/(?P<branch>[^/]+)\s*"
        )
        if m is not None:
            self.owner = m.group("owner")
            self.repo = m.group("repo")
            self.branch = m.group("branch")

    github_tree_url: bpy.props.StringProperty(
        get=github_tree_url_get,
        set=github_tree_url_set,
    )


class AddonPreferences:
    auto_check_updates: bpy.props.BoolProperty(name="Auto-check updates", default=True)

    custom_remotes: bpy.props.CollectionProperty(type=CustomRemotePropertyGroup)

    # last_update_check_datetime

    last_update_check_datetime_isoformat: bpy.props.StringProperty()

    @property
    def last_update_check_datetime(self):
        try:
            if self.last_update_check_datetime_isoformat == "":
                return None
            else:
                return datetime.datetime.fromisoformat(
                    self.last_update_check_datetime_isoformat
                )
        except:
            string_io = io.StringIO()
            traceback.print_exc(file=string_io)
            print(string_io.getvalue())
            ERRORS["last_update_check_datetime fromisoformat"] = string_io.getvalue()
            return None

    @last_update_check_datetime.setter
    def last_update_check_datetime(self, v: datetime.datetime):
        self.last_update_check_datetime_isoformat = v.isoformat()

    # update_check_period_timedelta

    update_check_period_days: bpy.props.IntProperty(
        name="Update check period days",
        min=0,
        max=1000,
        soft_max=30,
        default=1,
    )

    update_check_period_hours: bpy.props.IntProperty(
        name="Update check period hours",
        min=0,
        max=1000,
        soft_max=30,
        default=12,
    )

    @property
    def update_check_period_timedelta(self):
        try:
            return datetime.timedelta(
                days=self.update_check_period_days,
                hours=self.update_check_period_hours,
            )
        except:
            string_io = io.StringIO()
            traceback.print_exc(file=string_io)
            print(string_io.getvalue())
            ERRORS["update_check_period_timedelta"] = string_io.getvalue()
            return datetime.timedelta(0)

    # draw

    def draw(self, context):
        layout: bpy.types.UILayout = self.layout

        layout.prop(self, "auto_check_updates")
        layout.prop(self, "update_check_period_days")
        layout.prop(self, "update_check_period_hours")

        box = layout.box()
        box.label(text="Custom remotes")
        for custom_remote in self.custom_remotes.values():
            custom_remote: CustomRemotePropertyGroup
            box2 = box.box()
            box2.prop(custom_remote, "owner")
            box2.prop(custom_remote, "repo")
            box2.prop(custom_remote, "branch")
            box2.prop(custom_remote, "github_tree_url")

        dbg_box = layout.box()
        dbg_box.prop(self, "last_update_check_datetime_isoformat")

        for key, val in ERRORS.items():
            box = layout.box()
            box.alert = True
            box.label(icon="ERROR", text=key)
            box.separator()
            for line in val.splitlines():
                box.label(text=line)


def process_api_compare_data_result(data):
    # https://docs.github.com/en/rest/commits/commits#compare-two-commits
    assert isinstance(data, dict), data
    ahead_by = data["ahead_by"]
    behind_by = data["behind_by"]
    total_commits = data["total_commits"]
    commits = data["commits"]
    assert isinstance(commits, list)
    if len(commits) == total_commits:
        log("latest sha =", commits[-1]["sha"])
    else:
        log("you're so behind the latest upstream commit isn't listed (by default)")


def handler_load_post_impl():
    prefs = bpy.context.preferences.addons[SETTINGS.package].preferences
    if prefs is None:
        log("No prefs")
        return
    assert isinstance(
        prefs, AddonPreferences
    ), f"Addon preferences of {SETTINGS.package} should use the github_updater.AddonPreferences mixin"
    if SETTINGS.commit_sha is not None and prefs.auto_check_updates:
        last_update_check_datetime = prefs.last_update_check_datetime
        now = datetime.datetime.now()
        if (
            last_update_check_datetime is None
            or last_update_check_datetime + prefs.update_check_period_timedelta < now
        ):
            try:
                with urllib.request.urlopen(
                    f"https://api.github.com/repos/Dragorn421/blender-addon-updater-github/compare/{SETTINGS.commit_sha}...main",
                    cafile=SETTINGS.get_cafile(),  # TODO this is deprecated, use context instead
                ) as http_response:
                    http_response_status = http_response.status
                    assert http_response_status in {200, 404}, (
                        http_response.url,
                        http_response_status,
                        http_response.reason,
                    )
                    data = json.load(http_response)
            except urllib.error.HTTPError as e:
                http_response_status = e.code
                assert http_response_status in {200, 404}, (
                    e.url,
                    http_response_status,
                    e.reason,
                )
                data = json.load(e)
                e.close()

            if http_response_status == 404:
                ERRORS["Commit not found"] = f"{SETTINGS.commit_sha}\n\n{data}"
            else:
                assert http_response_status == 200
                process_api_compare_data_result(data)
                # prefs.last_update_check_datetime = now


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
    bpy.utils.register_class(CustomRemotePropertyGroup)

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
    bpy.utils.unregister_class(CustomRemotePropertyGroup)
    try:
        bpy.app.handlers.load_post.remove(handler_load_post)
    except:
        pass
