# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

from dataclasses import dataclass
from pathlib import Path
import datetime
import urllib.request
import urllib.error
import json
from typing import Optional
import traceback
import re


def log(*args):
    print(__name__, *args)


@dataclass(frozen=True)
class Remote:
    owner: str  # "Dragorn421"
    repo: str  # "blender-addon-updater-github"
    branch: str  # "main"


@dataclass
class Version:
    remote: Remote
    commit: Optional[str]  # sha1


@dataclass
class Settings:
    package: str  # Value of __package__ for accessing addon preferences
    root_dir: Path  # Folder containing the top-most __init__.py of the addon
    version: Optional[Version]  # Current version
    simulate: bool  # Do not remove or write anything (for development purposes) (TODO)
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


@dataclass
class RemoteCompareInfo:
    ahead_by: int
    behind_by: int
    ahead_by_commits: list[str]


class GitHubUpdaterContext:
    settings: Settings
    errors: dict[str, str]
    remote_compare_infos: dict[Remote, RemoteCompareInfo]

    def __init__(self):
        self.settings = None
        self.errors = dict()
        self.remote_compare_infos = dict()


GHUC: GitHubUpdaterContext


class CustomRemotePropertyGroup(bpy.types.PropertyGroup):
    owner: bpy.props.StringProperty()
    repo: bpy.props.StringProperty()
    branch: bpy.props.StringProperty()

    def as_remote(self):
        assert self.is_set()
        return Remote(self.owner, self.repo, self.branch)

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
            e_str = traceback.format_exc()
            print(e_str)
            GHUC.errors["last_update_check_datetime fromisoformat"] = e_str
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
            e_str = traceback.format_exc()
            print(e_str)
            GHUC.errors["update_check_period_timedelta"] = e_str
            return datetime.timedelta(0)

    # draw

    def draw(self, context):
        layout: bpy.types.UILayout = self.layout

        layout.prop(self, "auto_check_updates")
        layout.prop(self, "update_check_period_days")
        layout.prop(self, "update_check_period_hours")

        def draw_remote(
            layout: bpy.types.UILayout,
            remote: Remote,
            prop_group: Optional[CustomRemotePropertyGroup] = None,
        ):
            box = layout.box()
            if prop_group is None:
                box.label(text=f"Owner: {remote.owner}")
                box.label(text=f"Repo: {remote.repo}")
                box.label(text=f"Branch: {remote.branch}")
            else:
                box.prop(custom_remote, "owner")
                box.prop(custom_remote, "repo")
                box.prop(custom_remote, "branch")
                box.prop(custom_remote, "github_tree_url")

            op = layout.operator(
                CheckUpdatesOperator.bl_idname,
                text="Check updates now",
                icon="FILE_REFRESH",
            )
            op.remote_owner = remote.owner
            op.remote_repo = remote.repo
            op.remote_branch = remote.branch

            remote_compare_info = GHUC.remote_compare_infos.get(remote)
            if remote_compare_info is not None:
                if remote_compare_info.behind_by == 0:
                    if remote_compare_info.ahead_by == 0:
                        layout.label(text="Your version is up to date with this remote")
                    else:
                        layout.label(
                            text="Your version is out of date with this remote"
                        )
                        # TODO "update now" button
                        layout.label(
                            text=f"The remote is {remote_compare_info.ahead_by} commits ahead:"
                        )
                        box = layout.box()
                        for ahead_by_commit in remote_compare_info.ahead_by_commits:
                            box.label(text=ahead_by_commit)

        box = layout.box()
        box.label(text="Current remote")
        box2 = box.box()
        if GHUC.settings.version is None:
            box2.label(text="None (no information)")
        else:
            draw_remote(box2, GHUC.settings.version.remote)

        box = layout.box()
        box.label(text="Built-in remotes")
        for builtin_remote in GHUC.settings.builtin_remotes:
            box2 = box.box()
            draw_remote(box2, builtin_remote)

        box = layout.box()
        box.label(text="Custom remotes")
        for custom_remote in self.custom_remotes.values():
            custom_remote: CustomRemotePropertyGroup
            box2 = box.box()
            draw_remote(box2, custom_remote.as_remote(), custom_remote)

        dbg_box = layout.box()
        dbg_box.prop(self, "last_update_check_datetime_isoformat")

        for key, val in GHUC.errors.items():
            box = layout.box()
            box.alert = True
            box.label(icon="ERROR", text=key)
            box.separator()
            for line in val.splitlines():
                box.label(text=line)


class CheckUpdatesOperator(bpy.types.Operator):
    bl_label_fmt = "Check updates for {addon_name}"
    bl_idname_fmt = "{addon_key}.github_updater_check_updates"

    remote_owner: bpy.props.StringProperty()
    remote_repo: bpy.props.StringProperty()
    remote_branch: bpy.props.StringProperty()

    @classmethod
    def poll(cls, context):
        return (
            GHUC.settings.version is not None
            and GHUC.settings.version.commit is not None
        )

    def invoke(self, context, event):
        remote = GHUC.settings.version.remote
        self.remote_owner = remote.owner
        self.remote_repo = remote.repo
        self.remote_branch = remote.branch
        return self.execute(context)

    def execute(self, context):
        check_updates(
            Remote(self.remote_owner, self.remote_repo, self.remote_branch),
            GHUC.settings.version.commit,
        )

        return {"FINISHED"}


def process_api_compare_data_result(remote, data):
    # https://docs.github.com/en/rest/commits/commits#compare-two-commits
    assert isinstance(data, dict), data

    ahead_by = data["ahead_by"]
    behind_by = data["behind_by"]
    total_commits = data["total_commits"]
    assert isinstance(ahead_by, int), ahead_by
    assert isinstance(behind_by, int), behind_by
    assert isinstance(total_commits, int), total_commits

    commits = data["commits"]
    assert isinstance(commits, list)

    ahead_by_commits = []
    for commit in commits:
        assert isinstance(commit, dict), commit
        commit_commit = commit["commit"]
        assert isinstance(commit_commit, dict), commit_commit
        message = commit_commit["message"]
        assert isinstance(message, str)
        ahead_by_commits.append(message.splitlines()[0])

    if len(commits) == total_commits:
        log("latest sha =", commits[-1]["sha"])
    else:
        # more than 250 commits behind
        log("you're so behind the latest upstream commit isn't listed (by default)")
        ahead_by_commits.append(f"... {total_commits - len(commits)} more omitted")
    GHUC.remote_compare_infos[remote] = RemoteCompareInfo(
        ahead_by, behind_by, ahead_by_commits
    )


def check_updates(remote: Remote, commit):
    try:
        with urllib.request.urlopen(
            f"https://api.github.com/repos/{remote.owner}/{remote.repo}/compare/{commit}...{remote.branch}",
            cafile=GHUC.settings.get_cafile(),  # TODO this is deprecated, use context instead
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
        GHUC.errors["Commit not found"] = f"{commit}\n\n{data}"
        return False
    else:
        assert http_response_status == 200
        process_api_compare_data_result(remote, data)
        return True


def handler_load_post_impl():
    prefs = bpy.context.preferences.addons[GHUC.settings.package].preferences
    if prefs is None:
        log("No prefs")
        return
    assert isinstance(
        prefs, AddonPreferences
    ), f"Addon preferences of {GHUC.settings.package} should use the github_updater.AddonPreferences mixin"

    if (
        prefs.auto_check_updates
        and GHUC.settings.version is not None
        and GHUC.settings.version.commit is not None
    ):
        commit = GHUC.settings.version.commit
        last_update_check_datetime = prefs.last_update_check_datetime
        now = datetime.datetime.now()
        if (
            last_update_check_datetime is None
            or last_update_check_datetime + prefs.update_check_period_timedelta < now
        ):
            if check_updates(
                GHUC.settings.version.remote,
                commit,
            ):
                prefs.last_update_check_datetime = now


@bpy.app.handlers.persistent
def handler_load_post(*_args, **_kwargs):
    try:
        handler_load_post_impl()
    except:
        e_str = traceback.format_exc()
        print(e_str)
        GHUC.errors["handler_load_post_impl"] = e_str
        raise
    finally:
        bpy.app.handlers.load_post.remove(handler_load_post)


def init_settings(
    root_package: str,
    root_init_file: str,
    builtin_remotes: Optional[list[Remote]] = None,
):
    root_dir = Path(root_init_file).parent

    version_json_path = root_dir / "version.json"
    if version_json_path.is_file():
        with version_json_path.open() as f:
            version_data = json.load(f)

        owner = version_data["owner"]
        repo = version_data["repo"]
        branch = version_data["branch"]
        commit = version_data["commit"]

        version = Version(Remote(owner, repo, branch), commit)
    else:
        version = None

    if (root_dir / ".git").exists():
        simulate = True
    else:
        simulate = False

    if builtin_remotes:
        builtin_remotes = list(builtin_remotes)
        assert all(
            isinstance(builtin_remote, Remote) for builtin_remote in builtin_remotes
        ), builtin_remotes
    else:
        builtin_remotes = []

    GHUC.settings = Settings(
        root_package,
        root_dir,
        version,
        simulate,
        builtin_remotes,
    )


classes_fmt = (CheckUpdatesOperator,)
classes = (
    CustomRemotePropertyGroup,
    CheckUpdatesOperator,
)


def register(
    root_package: str,
    root_init_file: str,
    builtin_remotes: Optional[list[Remote]] = None,
):
    global GHUC
    GHUC = GitHubUpdaterContext()

    def fmt(str: str):
        # TODO
        return str.format(
            addon_name=root_package,
            addon_key=root_package.lower(),
        )

    for class_fmt in classes_fmt:
        if hasattr(class_fmt, "bl_label_fmt"):
            class_fmt.bl_label = fmt(class_fmt.bl_label_fmt)
        if hasattr(class_fmt, "bl_idname_fmt"):
            class_fmt.bl_idname = fmt(class_fmt.bl_idname_fmt)

    for clazz in classes:
        bpy.utils.register_class(clazz)

    try:
        init_settings(root_package, root_init_file, builtin_remotes)
    except:
        e_str = traceback.format_exc()
        print(e_str)
        GHUC.errors["register init_settings"] = e_str
    else:
        bpy.app.handlers.load_post.append(handler_load_post)


def unregister():
    global GHUC
    del GHUC

    for clazz in reversed(classes):
        bpy.utils.unregister_class(clazz)

    try:
        bpy.app.handlers.load_post.remove(handler_load_post)
    except:
        pass
