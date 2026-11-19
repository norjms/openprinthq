# /// script
# requires-python = ">=3.12"
# dependencies = []
#
# [tool.orcaslicer.plugin]
# id = "openprinthq-print-queue"
# name = "OpenPrintHQ Print Queue"
# description = "Printer agent that sends OrcaSlicer prints to an OpenPrintHQ print farm through its API: Print puts the job first in line on the mapped printer, Send adds it to that printer's queue."
# author = "Norman Networks"
# version = "0.15.1"
# ///
# SPDX-License-Identifier: AGPL-3.0-or-later
"""OpenPrintHQ Print Queue: an OrcaSlicer printer-agent plugin.

What it does
------------
Registers a printer agent called "OpenPrintHQ" (agent id "openprinthq", since 0.14.0). Any
printer preset whose
Printer Agent is set to it sends its sliced file to an OpenPrintHQ farm instead of
talking to the printer directly:

  * Print (start_local_print and friends): upload the file to the library (or reuse an
    identical file already there), then queue it on the mapped printer at the "print
    now" priority (default 100), at the top of that printer's queue.
  * Send (start_send_gcode_to_sdcard): the same, at the "queue" priority (default 50),
    at the end of the queue.

Since 0.12.0 the plugin talks to OpenPrintHQ only. Legacy farm support was removed.

Which printer a preset uses
---------------------------
Set in this capability's config (Plugins > Config tab):

    {"printers": {"Bambu Lab H2D 0.4 nozzle": "H2D", "MK3S MMU3": "MK3S"}}

Keys are Orca printer preset names, values are OpenPrintHQ printer names exactly as
GET /printers/ returns them. If a preset has no mapping, the plugin falls back to the
one printer whose name appears in the preset name as a whole word; if there is not
exactly one, it refuses to send.

Connection
----------
The Config page holds the OpenPrintHQ address and an API key:
  * ophqk_... (an OpenPrintHQ integration key, made under Settings, API keys): requests
    go to <address>/api/engine/api/v1, through the control plane. Scope "control" for
    everything; "submit" is enough to print, but not for the pre-send check, the queue
    controls or nozzle changes.
  * bb_... (an engine's own API key, for an engine reached without the control plane):
    requests go to <address>/api/v1.

File format
-----------
Orca hands the agent a .gcode.3mf. Bambu printers get the 3MF as is. Every other
printer gets the plate's G-code extracted from the 3MF.

HTTPS trust
-----------
The Python Orca embeds on macOS ships without root certificates, so HTTPS
verification fails with CERTIFICATE_VERIFY_FAILED. The plugin builds its own
SSL context: the platform default store, then the first system CA bundle that
loads (OpenSSL reads it in C, outside the sandbox's Python file audit), plus
the Let's Encrypt ISRG Root X1 and X2 certificates bundled below. Verification
is never turned off.
"""

import base64
import hashlib
import io
import json
import os
import re
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile

import orca

VERSION = "0.15.1"
AGENT_ID = "openprinthq"   # printer presets store it in printer_agent (0.14.0)
AGENT_NAME = "OpenPrintHQ"
CONTROL_PLANE_API = "/api/engine/api/v1"   # an ophqk_ key, through the OpenPrintHQ control plane
ENGINE_API = "/api/v1"                     # a bb_ key, straight to an engine

# ---- Permissions declared while Orca loads the plugin (0.11.1) ------------------------------
# OrcaSlicer builds from November 2026 attribute the threads a plugin starts to that plugin
# (install_python_thread_audit_shim in PythonPluginBridge.cpp), so off_context no longer keeps
# network calls out of the sandbox, and a "Yes" to a network prompt covers only that exact URL.
# request_permissions(network=...) is reduced by Orca to scheme://host and saved, so one approval
# covers every path on the host for good. Orca accepts it only while it loads the module, so it
# runs at import time below. Answering No makes Orca skip loading the plugin. The address on the
# Config page is set at run time, after loading, so only this farm's address can be declared here;
# another farm address, and a hub address, still prompt per URL
# (docs/KNOWN_ISSUES.md).
DECLARED_NETWORK = ("https://openprinthq.com",)


def declare_permissions(module=None):
    """Ask Orca at load for the OpenPrintHQ host and thread creation.

    Returns "network" (host grant requested), "threading" (an Orca without the network
    argument; only threads requested) or None (no API, or called outside plugin loading).
    Never raises: a failed declaration only means the per-request prompts stay.
    """
    module = module or orca
    request = getattr(module, "request_permissions", None)
    if request is None:
        return None
    try:
        request(network=list(DECLARED_NETWORK), threading=True)
        return "network"
    except TypeError:
        try:
            request(threading=True)
            return "threading"
        except Exception:  # noqa: BLE001 - never block loading
            return None
    except Exception:  # noqa: BLE001 - outside discovery Orca raises ValueError
        return None


DECLARED_PERMISSIONS = declare_permissions()

# Return codes, from OrcaSlicer's bambu_networking.hpp.
SUCCESS = 0
ERR_INVALID_HANDLE = -1
ERR_FILE_NOT_EXIST = -14
ERR_CANCELED = -18
ERR_INVALID_RESULT = -19
ERR_PRINT_UPLOAD = -4020
ERR_PRINT_PUBLISH = -4030
ERR_SEND_UPLOAD = -5010
ERR_CONNECTION = -6010

# SendingPrintJobStage values.
STAGE_CREATE = 0
STAGE_UPLOAD = 1
STAGE_WAITING = 2
STAGE_SENDING = 3
STAGE_FINISHED = 6
STAGE_ERROR = 7

# ConnectStatus values.
CONNECT_OK = 0
CONNECT_FAILED = 1

PLACEHOLDER_ACCESS_CODE = "88888888"  # Orca fills this in when a preset has no API key.

DEFAULT_CONFIG = {
    "printers": {},
    "print_now_priority": 100,
    "queue_priority": 50,
    "status_poll_seconds": 5,
    # v0.12: the OpenPrintHQ address and API key (the key is a secret, kept in the OS credential store)
    "ophq_url": "",
    "ophq_key": "",
    "request_timeout_seconds": 30,
    # Orca's Print dialog refuses to send to a printer that reports it is printing.
    # OpenPrintHQ queues behind the running job, so by default a busy printer is
    # reported to Orca as idle. Live state stays visible in OpenPrintHQ.
    "report_busy_as_idle": True,
    # v0.4
    "notify_job_events": True,     # Orca notification when a job finishes, fails or is cancelled
    "show_job_window": True,       # small summary window after each Print or Send
    "skip_duplicate_uploads": True,
    "default_copies": 1,
    "ams_sync": True,
    "filament_check": True,
    "maintenance_check": True,
    "nozzle_check": True,
    # v0.11: optional hub connection. 0.13.0 removed the look settings ("look", "look_mode"): every page
    # uses openprinthq-ui's glass-dark theme, and saved values are ignored.
    "hub_url": "",
    "hub_key": "",
}

# Legacy farm settings from before 0.12.0. Nothing reads them any more. The two secrets among them
# are deleted from the OS credential store and blanked in Orca's config once (OpenPrintHQAgent._config).
LEGACY_KEYS = ("base_url", "api_key", "wt_username", "wt_password", "match_filament")
LEGACY_SECRET_KEYS = ("api_key", "wt_password")

TERMINAL_OK = {"completed", "complete", "finished", "done", "success", "succeeded"}
TERMINAL_BAD = {"failed", "error", "errored", "aborted"}
TERMINAL_CANCEL = {"cancelled", "canceled", "skipped"}
TERMINAL = TERMINAL_OK | TERMINAL_BAD | TERMINAL_CANCEL

API_KEY_RE = re.compile(r"^(ophqk_|bb_)[A-Za-z0-9_-]{8,}$")


# A managed install (0.15.0), such as OrcaSlicer in the browser on an OpenPrintHQ hub, gives the address and key in
# the environment OrcaSlicer starts with. They fill only what the Config page leaves blank, are never written to the
# plugin's settings, and never sync to other computers.
MANAGED_URL_ENV = "OPENPRINTHQ_URL"
MANAGED_KEY_ENV = "OPENPRINTHQ_API_KEY"


def managed_connection(env=None):
    """(address, key) a managed install provides in the environment; blanks for what it does not."""
    env = os.environ if env is None else env
    return normalize_ophq_url(env.get(MANAGED_URL_ENV) or ""), resolve_api_key(env.get(MANAGED_KEY_ENV) or "")


def apply_managed(cfg, env=None):
    """Fill a blank address or key in cfg from the environment. Answers which ones it filled."""
    url, key = managed_connection(env)
    filled = {"url": "", "key": False}
    if url and not normalize_ophq_url(cfg.get("ophq_url") or ""):
        cfg["ophq_url"] = url
        filled["url"] = url
    if key and not resolve_api_key(cfg.get("ophq_key")):
        cfg["ophq_key"] = key
        filled["key"] = True
    return filled


def resolve_api_key(value):
    """The OpenPrintHQ API key from the config, or "" when it does not look like one.

    ophqk_ is an OpenPrintHQ integration key (through the control plane); bb_ is an engine's own key.
    """
    value = (value or "").strip()
    return value if API_KEY_RE.match(value) else ""


# Engine printer state (Bambu style gcode_state) -> the name shown on cards.
ENGINE_STATES = {"IDLE": "Idle", "RUNNING": "Printing", "PREPARE": "Preparing", "SLICING": "Preparing",
                 "PAUSE": "Paused", "FINISH": "Finished", "FAILED": "Error"}
ENGINE_BUSY = {"RUNNING", "PREPARE", "SLICING", "PAUSE"}

# Card state -> Orca/Bambu gcode_state.
STATE_MAP = {
    "Printing": "RUNNING",
    "Resuming": "RUNNING",
    "Preparing": "PREPARE",
    "Paused": "PAUSE",
    "Finished": "FINISH",
    "Complete": "FINISH",
    "Ready": "FINISH",
    "Cancelled": "FAILED",
    "Error": "FAILED",
}
STAGE_FOR_STATE = {"RUNNING": 1, "PREPARE": 1, "PAUSE": 2, "FINISH": 3, "FAILED": 4}


# ---------------------------------------------------------------------------
# Secret storage: Windows Credential Manager, the macOS Keychain, or the Linux Secret Service
# (identical block in openprinthq_print_queue.py and orca_cloud_manager.py;
# change both together)
# ---------------------------------------------------------------------------

SECRET_SERVICE_PREFIX = "NormanNetworks.OrcaPlugins"


class SecretStore:
    """Keep a plugin's secrets in the OS credential store instead of Orca's plain-text config.

    Entries are named "NormanNetworks.OrcaPlugins/<plugin id>/<key>". Only entries this
    plugin creates are touched; Orca's own sign-in ("OrcaSlicer/Auth") is never read.

    Backends: Windows Credential Manager (advapi32 Cred*W, generic credentials, persisted
    for this Windows user on this machine), the macOS login Keychain (Security.framework
    generic passwords) and, on Linux, the Secret Service (GNOME Keyring, KWallet,
    KeePassXC) through libsecret. If the backend fails to load, available() is False and
    callers keep using the config file. If it loads but cannot reach a keyring (headless
    Linux with no Secret Service), set() fails, error explains why, and callers keep the
    config copy.

    Credential Manager needs an interactive Windows logon; it works inside Orca but not
    from an SSH session (error 1312).
    """

    def __init__(self, plugin_id, platform=None):
        self.service = "%s/%s" % (SECRET_SERVICE_PREFIX, plugin_id)
        self.platform = platform or sys.platform
        self.error = ""
        self._impl = None
        try:
            if self.platform.startswith("win"):
                self._impl = _WinCred()
            elif self.platform == "darwin":
                self._impl = _MacKeychain()
            elif self.platform.startswith("linux"):
                self._impl = _LinuxSecret()
        except Exception as exc:  # missing ctypes, missing framework, ...
            self.error = str(exc)
            self._impl = None

    @property
    def backend(self):
        if self._impl is None:
            return ""
        return self._impl.label

    def available(self):
        return self._impl is not None

    def get(self, key):
        if self._impl is None:
            return None
        try:
            return self._impl.get(self.service, key)
        except Exception as exc:
            self.error = str(exc)
            return None

    def set(self, key, value):
        """Store value; True only if it reads back identically."""
        if self._impl is None:
            return False
        try:
            self._impl.set(self.service, key, value)
            ok = self._impl.get(self.service, key) == value
            if not ok:
                self.error = "stored value did not read back"
            return ok
        except Exception as exc:
            self.error = str(exc)
            return False

    def delete(self, key):
        if self._impl is None:
            return False
        try:
            return self._impl.delete(self.service, key)
        except Exception as exc:
            self.error = str(exc)
            return False


class _WinCred:
    label = "Windows Credential Manager"
    CRED_TYPE_GENERIC = 1
    CRED_PERSIST_LOCAL_MACHINE = 2
    ERROR_NOT_FOUND = 1168

    def __init__(self):
        import ctypes
        from ctypes import wintypes as w
        self.ctypes = ctypes

        class CREDENTIAL(ctypes.Structure):
            _fields_ = [("Flags", w.DWORD), ("Type", w.DWORD), ("TargetName", w.LPWSTR),
                        ("Comment", w.LPWSTR), ("LastWritten", w.FILETIME),
                        ("CredentialBlobSize", w.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                        ("Persist", w.DWORD), ("AttributeCount", w.DWORD), ("Attributes", ctypes.c_void_p),
                        ("TargetAlias", w.LPWSTR), ("UserName", w.LPWSTR)]

        self.CREDENTIAL = CREDENTIAL
        self.PCRED = ctypes.POINTER(CREDENTIAL)
        adv = ctypes.WinDLL("advapi32", use_last_error=True)
        adv.CredWriteW.argtypes = [self.PCRED, w.DWORD]
        adv.CredWriteW.restype = w.BOOL
        adv.CredReadW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.POINTER(self.PCRED)]
        adv.CredReadW.restype = w.BOOL
        adv.CredDeleteW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD]
        adv.CredDeleteW.restype = w.BOOL
        adv.CredFree.argtypes = [ctypes.c_void_p]
        adv.CredFree.restype = None
        self.adv = adv

    def _target(self, service, key):
        return "%s/%s" % (service, key)

    def get(self, service, key):
        ct = self.ctypes
        p = self.PCRED()
        if not self.adv.CredReadW(self._target(service, key), self.CRED_TYPE_GENERIC, 0, ct.byref(p)):
            err = ct.get_last_error()
            if err == self.ERROR_NOT_FOUND:
                return None
            raise OSError("CredReadW failed (error %d)" % err)
        try:
            c = p.contents
            return ct.string_at(c.CredentialBlob, c.CredentialBlobSize).decode("utf-8")
        finally:
            self.adv.CredFree(p)

    def set(self, service, key, value):
        ct = self.ctypes
        blob = value.encode("utf-8")
        buf = (ct.c_ubyte * max(len(blob), 1)).from_buffer_copy(blob or b"\0")
        cred = self.CREDENTIAL(Type=self.CRED_TYPE_GENERIC, TargetName=self._target(service, key),
                               CredentialBlobSize=len(blob), CredentialBlob=buf,
                               Persist=self.CRED_PERSIST_LOCAL_MACHINE, UserName=key,
                               Comment="OrcaSlicer plugin secret (%s)" % service)
        if not self.adv.CredWriteW(ct.byref(cred), 0):
            raise OSError("CredWriteW failed (error %d)" % ct.get_last_error())

    def delete(self, service, key):
        ct = self.ctypes
        if self.adv.CredDeleteW(self._target(service, key), self.CRED_TYPE_GENERIC, 0):
            return True
        if ct.get_last_error() == self.ERROR_NOT_FOUND:
            return False
        raise OSError("CredDeleteW failed (error %d)" % ct.get_last_error())


class _MacKeychain:
    label = "macOS Keychain"
    ERR_NOT_FOUND = -25300

    def __init__(self):
        import ctypes
        import ctypes.util
        self.ctypes = ctypes
        path = ctypes.util.find_library("Security") or "/System/Library/Frameworks/Security.framework/Security"
        sec = ctypes.cdll.LoadLibrary(path)
        cf_path = ctypes.util.find_library("CoreFoundation") or "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        cf = ctypes.cdll.LoadLibrary(cf_path)
        vp, u32, i32 = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int32
        sec.SecKeychainFindGenericPassword.argtypes = [vp, u32, ctypes.c_char_p, u32, ctypes.c_char_p,
                                                       ctypes.POINTER(u32), ctypes.POINTER(vp), ctypes.POINTER(vp)]
        sec.SecKeychainFindGenericPassword.restype = i32
        sec.SecKeychainAddGenericPassword.argtypes = [vp, u32, ctypes.c_char_p, u32, ctypes.c_char_p,
                                                      u32, ctypes.c_char_p, ctypes.POINTER(vp)]
        sec.SecKeychainAddGenericPassword.restype = i32
        sec.SecKeychainItemModifyAttributesAndData.argtypes = [vp, vp, u32, ctypes.c_char_p]
        sec.SecKeychainItemModifyAttributesAndData.restype = i32
        sec.SecKeychainItemDelete.argtypes = [vp]
        sec.SecKeychainItemDelete.restype = i32
        sec.SecKeychainItemFreeContent.argtypes = [vp, vp]
        sec.SecKeychainItemFreeContent.restype = i32
        cf.CFRelease.argtypes = [vp]
        cf.CFRelease.restype = None
        self.sec, self.cf = sec, cf

    def _find(self, service, key, want_data):
        ct = self.ctypes
        s, a = service.encode("utf-8"), key.encode("utf-8")
        length, data, item = ct.c_uint32(0), ct.c_void_p(), ct.c_void_p()
        rc = self.sec.SecKeychainFindGenericPassword(
            None, len(s), s, len(a), a,
            ct.byref(length) if want_data else None, ct.byref(data) if want_data else None, ct.byref(item))
        if rc == self.ERR_NOT_FOUND:
            return None, None
        if rc != 0:
            raise OSError("SecKeychainFindGenericPassword failed (%d)" % rc)
        value = None
        if want_data:
            value = ct.string_at(data, length.value).decode("utf-8")
            self.sec.SecKeychainItemFreeContent(None, data)
        return value, item

    def get(self, service, key):
        value, item = self._find(service, key, True)
        if item is not None and item.value:
            self.cf.CFRelease(item)
        return value

    def set(self, service, key, value):
        ct = self.ctypes
        data = value.encode("utf-8")
        _, item = self._find(service, key, False)
        if item is not None and item.value:
            try:
                rc = self.sec.SecKeychainItemModifyAttributesAndData(item, None, len(data), data)
            finally:
                self.cf.CFRelease(item)
        else:
            s, a = service.encode("utf-8"), key.encode("utf-8")
            new_item = ct.c_void_p()
            rc = self.sec.SecKeychainAddGenericPassword(None, len(s), s, len(a), a, len(data), data, ct.byref(new_item))
            if new_item.value:
                self.cf.CFRelease(new_item)
        if rc != 0:
            raise OSError("Keychain write failed (%d)" % rc)

    def delete(self, service, key):
        _, item = self._find(service, key, False)
        if item is None or not item.value:
            return False
        try:
            rc = self.sec.SecKeychainItemDelete(item)
        finally:
            self.cf.CFRelease(item)
        if rc != 0:
            raise OSError("Keychain delete failed (%d)" % rc)
        return True


class _LinuxSecret:
    """Secret Service (GNOME Keyring, KWallet's Secret Service bridge, KeePassXC) through libsecret."""
    label = "the Secret Service keyring"
    SCHEMA_NAME = b"org.normannetworks.OrcaPluginSecret"

    def __init__(self):
        import ctypes
        import ctypes.util
        self.ctypes = ctypes
        lib = ctypes.util.find_library("secret-1") or "libsecret-1.so.0"
        glib_name = ctypes.util.find_library("glib-2.0") or "libglib-2.0.so.0"
        sec = ctypes.CDLL(lib)
        glib = ctypes.CDLL(glib_name)
        vp, cp = ctypes.c_void_p, ctypes.c_char_p

        class GError(ctypes.Structure):
            _fields_ = [("domain", ctypes.c_uint32), ("code", ctypes.c_int), ("message", cp)]

        self.GError = GError
        self.PGError = ctypes.POINTER(GError)
        glib.g_hash_table_new.argtypes = [vp, vp]
        glib.g_hash_table_new.restype = vp
        glib.g_hash_table_insert.argtypes = [vp, vp, vp]
        glib.g_hash_table_insert.restype = ctypes.c_int
        glib.g_hash_table_unref.argtypes = [vp]
        glib.g_hash_table_unref.restype = None
        glib.g_error_free.argtypes = [self.PGError]
        glib.g_error_free.restype = None
        sec.secret_schema_newv.argtypes = [cp, ctypes.c_int, vp]
        sec.secret_schema_newv.restype = vp
        sec.secret_password_storev_sync.argtypes = [vp, vp, cp, cp, cp, vp, ctypes.POINTER(self.PGError)]
        sec.secret_password_storev_sync.restype = ctypes.c_int
        sec.secret_password_lookupv_sync.argtypes = [vp, vp, vp, ctypes.POINTER(self.PGError)]
        sec.secret_password_lookupv_sync.restype = vp
        sec.secret_password_clearv_sync.argtypes = [vp, vp, vp, ctypes.POINTER(self.PGError)]
        sec.secret_password_clearv_sync.restype = ctypes.c_int
        sec.secret_password_free.argtypes = [vp]
        sec.secret_password_free.restype = None
        self.sec, self.glib = sec, glib
        self.str_hash = ctypes.cast(glib.g_str_hash, vp)
        self.str_equal = ctypes.cast(glib.g_str_equal, vp)
        types = glib.g_hash_table_new(self.str_hash, self.str_equal)
        self._keep = [b"service", b"key"]
        for name in self._keep:
            glib.g_hash_table_insert(types, name, None)  # SECRET_SCHEMA_ATTRIBUTE_STRING == 0
        self.schema = sec.secret_schema_newv(self.SCHEMA_NAME, 0, types)
        glib.g_hash_table_unref(types)
        if not self.schema:
            raise OSError("libsecret: could not create the schema")

    def _attrs(self, service, key):
        vals = [b"service", service.encode("utf-8"), b"key", key.encode("utf-8")]
        table = self.glib.g_hash_table_new(self.str_hash, self.str_equal)
        self.glib.g_hash_table_insert(table, vals[0], vals[1])
        self.glib.g_hash_table_insert(table, vals[2], vals[3])
        return table, vals  # vals keeps the bytes alive while the table is in use

    def _check(self, err, what):
        if err:
            msg = (err.contents.message or b"").decode("utf-8", "replace")
            self.glib.g_error_free(err)
            raise OSError("%s failed: %s" % (what, msg or "unknown libsecret error"))

    def get(self, service, key):
        ct = self.ctypes
        table, _vals = self._attrs(service, key)
        err = self.PGError()
        try:
            ptr = self.sec.secret_password_lookupv_sync(self.schema, table, None, ct.byref(err))
            self._check(err, "Secret Service lookup")
            if not ptr:
                return None
            try:
                return ct.string_at(ptr).decode("utf-8")
            finally:
                self.sec.secret_password_free(ptr)
        finally:
            self.glib.g_hash_table_unref(table)

    def set(self, service, key, value):
        ct = self.ctypes
        table, _vals = self._attrs(service, key)
        err = self.PGError()
        try:
            ok = self.sec.secret_password_storev_sync(self.schema, table, None,
                                                      ("OrcaSlicer plugin secret: %s/%s" % (service, key)).encode("utf-8"),
                                                      value.encode("utf-8"), None, ct.byref(err))
            self._check(err, "Secret Service store")
            if not ok:
                raise OSError("Secret Service store failed")
        finally:
            self.glib.g_hash_table_unref(table)

    def delete(self, service, key):
        ct = self.ctypes
        table, _vals = self._attrs(service, key)
        err = self.PGError()
        try:
            removed = self.sec.secret_password_clearv_sync(self.schema, table, None, ct.byref(err))
            self._check(err, "Secret Service delete")
            return bool(removed)
        finally:
            self.glib.g_hash_table_unref(table)


class MemorySecretBackend:
    """In-memory backend for tests: SecretStore(plugin_id)._impl = MemorySecretBackend()."""
    label = "test store"

    def __init__(self):
        self.items = {}

    def get(self, service, key):
        return self.items.get((service, key))

    def set(self, service, key, value):
        self.items[(service, key)] = value

    def delete(self, service, key):
        return self.items.pop((service, key), None) is not None


def migrate_secrets(cfg, store, keys, clear_key="clear_secrets"):
    """Move secrets out of a config dict into the store, then fill them back in memory.

    Returns (cfg_for_use, cfg_to_save_or_None, status). cfg_to_save is set when the
    stored config must be rewritten (a plain-text secret was moved, or a clear was
    requested). status maps each key to "store", "config" or "" (not set).

    Rules:
      * a non-empty secret in the config is a new value: write it to the store and,
        if it reads back, blank it in the config;
      * an empty secret in the config means "keep whatever the store has";
      * keys listed in cfg[clear_key] are deleted from the store and the config;
      * without a working store, secrets stay in the config as before.
    """
    use = dict(cfg)
    save = None
    status = {}
    clears = [k for k in (cfg.get(clear_key) or []) if k in keys]
    if clears:
        save = dict(cfg)
        save.pop(clear_key, None)
        for k in clears:
            if store.available():
                store.delete(k)
            save[k] = ""
            use[k] = ""
        use.pop(clear_key, None)
    for k in keys:
        plain = (save if save is not None else cfg).get(k) or ""
        if not store.available():
            use[k] = plain
            status[k] = "config" if plain else ""
            continue
        if plain:
            if store.set(k, plain):
                if save is None:
                    save = dict(cfg)
                save[k] = ""
                use[k] = plain
                status[k] = "store"
            else:
                use[k] = plain
                status[k] = "config"
        else:
            val = store.get(k) or ""
            use[k] = val
            status[k] = "store" if val else ""
    if save is not None:
        save["secret_store"] = store.backend or ""
    return use, save, status


# ---------------------------------------------------------------------------
# Settings sync through a carrier preset
# (identical block in openprinthq_print_queue.py and orca_cloud_manager.py;
# change both together)
# ---------------------------------------------------------------------------
#
# Non-secret plugin settings travel between computers inside one real, hidden
# process preset whose "notes" field holds a JSON document. Orca syncs that preset
# through its own cloud like any other user preset, so every computer signed in
# to the same Orca account receives it (at Orca start). Secrets never go in it.
#
# Document: {"schema": 1, "plugins": {<plugin id>: {"settings": {<key>:
#            {"v": value, "t": epoch ms, "m": machine}}}}}
# Merge: per key, last writer wins by "t". A plugin remembers the values and
# stamps it last agreed on (config key "_sync"), so a local edit is any value
# that differs from that base.
#
# Fast path: all plugins run in one Python interpreter inside Orca, so they share
# an in-process bus (sys.modules[SYNC_BUS_NAME]). Orca Cloud Manager, when signed
# in, pulls the carrier from the cloud every minute and pushes changes at once
# through its own session; other plugins read and publish through the bus.

# ---- Network calls off Orca's audited thread (shared block: identical in OpenPrintHQ Print
# Queue and Orca Cloud Manager) -----------------------------------------------------------
# Older OrcaSlicer nightlies audited Python events only on a thread inside a call from Orca, so
# network calls could run on a short-lived plugin thread that was not audited. OrcaSlicer 2.5.1 and
# later give a plugin's own threads its audit context (install_python_thread_audit_shim), so that
# no longer avoids anything, and a plugin meant for other people should not try. Since 0.15.0 it is
# off: every call is audited, and the hosts declared at load (declare_permissions) are asked about
# once and saved. OFF_CONTEXT = True restores the old behaviour for an old nightly.
OFF_CONTEXT = False
_OFF_CONTEXT_FLAG = "_normannetworks_off_context"


def off_context(fn, *args, **kwargs):
    """Run fn(*args, **kwargs) on a fresh plugin thread and return its result or raise its error."""
    current = threading.current_thread()
    if not OFF_CONTEXT or getattr(current, _OFF_CONTEXT_FLAG, False):
        return fn(*args, **kwargs)
    box = {}

    def run():
        try:
            box["value"] = fn(*args, **kwargs)
        except BaseException as exc:  # handed back to the caller below
            box["error"] = exc

    worker = threading.Thread(target=run, name="nn-offcontext", daemon=True)
    setattr(worker, _OFF_CONTEXT_FLAG, True)
    worker.start()
    worker.join()
    if "error" in box:
        raise box["error"]
    return box.get("value")


import uuid as _sync_uuid

SYNC_SCHEMA = 1
CARRIER_NAME = "~ Norman Networks plugin settings (do not select)"
CARRIER_VERSION = "2.4.0.9"
ORCA_UUID_NAMESPACE = _sync_uuid.UUID("f47ac10b-58cc-4372-a567-0e02b2c3d479")
SYNC_BUS_NAME = "_normannetworks_orca_plugin_bus"
SYNC_RESERVED_KEYS = ("_sync", "secret_store", "clear_secrets")
_MISSING = object()


def unescape_cstyle(text):
    """Undo Orca's escape_string_cstyle (\\n, \\r, \\\\, \\") as returned by config_value()."""
    if text is None:
        return ""
    out, i, s = [], 0, str(text)
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            n = s[i + 1]
            out.append({"n": "\n", "r": "\r"}.get(n, n))
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def carrier_setting_id(user_id):
    """The cloud id Orca itself would give the carrier (uuid5 of "<user id>/<name>")."""
    key = ("%s/%s" % (user_id, CARRIER_NAME)) if user_id else CARRIER_NAME
    return str(_sync_uuid.uuid5(ORCA_UUID_NAMESPACE, key))


def empty_carrier():
    return {"schema": SYNC_SCHEMA, "plugins": {}}


def parse_carrier(text):
    try:
        doc = json.loads(text) if text else None
    except (TypeError, ValueError):
        return empty_carrier()
    if not isinstance(doc, dict) or doc.get("schema") != SYNC_SCHEMA or not isinstance(doc.get("plugins"), dict):
        return empty_carrier()
    return doc


def carrier_text(doc):
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def carrier_rev(doc):
    """Newest stamp anywhere in the document; used to pick the freshest copy."""
    best = 0
    for section in (doc.get("plugins") or {}).values():
        for entry in ((section or {}).get("settings") or {}).values():
            try:
                best = max(best, int(entry.get("t") or 0))
            except (TypeError, ValueError, AttributeError):
                pass
    return best


def carrier_preset_json(doc):
    """The process preset file Orca loads (a root preset: no parent, hidden from every printer)."""
    return {
        "compatible_printers": [],
        "compatible_printers_condition": "false",
        "from": "User",
        "inherits": "",
        "name": CARRIER_NAME,
        "notes": carrier_text(doc),
        "print_settings_id": CARRIER_NAME,
        "version": CARRIER_VERSION,
    }


def carrier_cloud_content(doc):
    """Content for /api/v1/sync/push (flat strings, as Orca sends)."""
    return {
        "compatible_printers_condition": "false",
        "from": "User",
        "inherits": "",
        "name": CARRIER_NAME,
        "notes": carrier_text(doc),
        "print_settings_id": CARRIER_NAME,
        "type": "print",
        "version": CARRIER_VERSION,
    }


def read_carrier_from_presets(bundle):
    """The carrier as Orca loaded it at start (through the preset API; no file access)."""
    if bundle is None:
        return None
    try:
        preset = bundle.prints.find_preset(CARRIER_NAME)
    except Exception:
        return None
    if preset is None or getattr(preset, "name", CARRIER_NAME) != CARRIER_NAME:
        return None
    try:
        return parse_carrier(unescape_cstyle(preset.config_value("notes")))
    except Exception:
        return None


def user_dir_from_presets(bundle):
    """<data dir>/user/<user id> from any loaded user preset's file path, or ""."""
    if bundle is None:
        return ""
    for attr in ("printers", "filaments", "prints"):
        coll = getattr(bundle, attr, None)
        if coll is None:
            continue
        try:
            names = coll.preset_names()
        except Exception:
            continue
        for name in names:
            p = coll.find_preset(name)
            try:
                if p is None or not p.is_user():
                    continue
            except Exception:
                continue
            path = getattr(p, "file", "") or ""
            parent = os.path.dirname(os.path.dirname(path))
            if path and os.path.basename(os.path.dirname(parent)).lower() == "user":
                return parent
    return ""


def merge_settings(values, state, section, now, machine):
    """Per-key last-writer-wins merge of one plugin's settings.

    values  : this computer's current syncable settings {key: value}
    state   : {"base": {key: value}, "stamps": {key: t}} from the last merge ({} the first time)
    section : this plugin's carrier section {"settings": {key: {"v", "t", "m"}}}
    Returns (new_values, new_state, new_section, local_changed, carrier_changed).
    """
    base = dict((state or {}).get("base") or {})
    stamps = dict((state or {}).get("stamps") or {})
    remote = dict(((section or {}).get("settings")) or {})
    out_values, out_remote = dict(values), dict(remote)
    local_changed = carrier_changed = False
    first_time = not base and not stamps
    for key in sorted(set(values) | set(remote)):
        lv = values.get(key, _MISSING)
        r = remote.get(key) if isinstance(remote.get(key), dict) else None
        rt = int(r.get("t") or 0) if r else 0
        edited_here = lv is not _MISSING and key in base and lv != base[key]
        if edited_here and rt <= now:
            out_remote[key] = {"v": lv, "t": now, "m": machine}
            stamps[key] = now
            carrier_changed = True
        elif r is not None and (rt > int(stamps.get(key, 0)) or (first_time and lv != r.get("v"))):
            if lv is _MISSING or lv != r.get("v"):
                out_values[key] = r.get("v")
                local_changed = True
            stamps[key] = rt
        elif r is None and lv is not _MISSING:
            t = int(stamps.get(key) or now)
            out_remote[key] = {"v": lv, "t": t, "m": machine}
            stamps[key] = t
            carrier_changed = True
    new_state = {"base": {k: out_values[k] for k in out_values}, "stamps": stamps}
    new_section = {"settings": out_remote}
    return out_values, new_state, new_section, local_changed, carrier_changed


class _SyncBus:
    """In-process channel shared by every Norman Networks plugin in this Orca."""

    def __init__(self):
        self.lock = threading.RLock()
        self.doc = None              # freshest carrier document seen in this process
        self.rev = -1
        self.source = ""             # "presets", "cloud", or a plugin id
        self.cloud = {}              # {"setting_id", "updated_time"} of the cloud copy, when known
        self.pusher = None           # callable(doc) set by a plugin that can push to the cloud
        self.pusher_owner = ""

    def offer(self, doc, source):
        """Keep doc if it is at least as fresh as what we have. Returns True if taken."""
        rev = carrier_rev(doc)
        with self.lock:
            if self.doc is None or rev >= self.rev:
                self.doc, self.rev, self.source = doc, rev, source
                return True
        return False


def sync_bus():
    bus = sys.modules.get(SYNC_BUS_NAME)
    if bus is None or not hasattr(bus, "offer"):
        bus = _SyncBus()
        sys.modules[SYNC_BUS_NAME] = bus
    return bus


def machine_name():
    for var in ("COMPUTERNAME", "HOSTNAME"):
        if os.environ.get(var):
            return os.environ[var]
    try:
        return os.uname().nodename
    except Exception:
        return "unknown"


def write_carrier_file(user_dir, doc):
    """Write the carrier preset and mark it for upload. Returns the .json path ("" if unchanged).

    New file: .info gets sync_info = create (Orca uploads it at its next start).
    Existing file with a cloud id: sync_info = update, updated_time left alone (it is
    Orca's version check against the cloud copy).
    """
    folder = os.path.join(user_dir, "process")
    path = os.path.join(folder, CARRIER_NAME + ".json")
    info_path = path[:-5] + ".info"
    body = json.dumps(carrier_preset_json(doc), indent=4, ensure_ascii=False) + "\n"
    try:
        with open(path, encoding="utf-8") as fh:
            if fh.read() == body:
                return ""
    except OSError:
        pass
    os.makedirs(folder, exist_ok=True)
    tmp = path + ".nnsync-tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(body)
    os.replace(tmp, path)
    try:
        with open(info_path, encoding="utf-8", newline="") as fh:
            info_text = fh.read()
    except OSError:
        info_text = ""
    info = parse_info_text(info_text)
    if (info.get("setting_id") or "").strip():
        new_info = update_info_text(info_text, sync_info="update")
    else:
        new_info = ("sync_info = create\nuser_id = %s\nsetting_id = \nbase_id = \nupdated_time = 0\n"
                    % os.path.basename(user_dir))
    tmp = info_path + ".nnsync-tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(new_info)
    os.replace(tmp, info_path)
    return path


def record_cloud_carrier(user_dir, doc, setting_id, updated_time):
    """Mirror the cloud copy on disk so Orca neither re-uploads nor re-downloads it."""
    folder = os.path.join(user_dir, "process")
    path = os.path.join(folder, CARRIER_NAME + ".json")
    os.makedirs(folder, exist_ok=True)
    body = json.dumps(carrier_preset_json(doc), indent=4, ensure_ascii=False) + "\n"
    with open(path + ".nnsync-tmp", "w", encoding="utf-8", newline="\n") as fh:
        fh.write(body)
    os.replace(path + ".nnsync-tmp", path)
    info = ("sync_info = \nuser_id = %s\nsetting_id = %s\nbase_id = \nupdated_time = %d\n"
            % (os.path.basename(user_dir), setting_id, int(updated_time)))
    with open(path[:-5] + ".info.nnsync-tmp", "w", encoding="utf-8", newline="") as fh:
        fh.write(info)
    os.replace(path[:-5] + ".info.nnsync-tmp", path[:-5] + ".info")


def parse_info_text(text):
    out = {}
    for line in (text or "").splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


def update_info_text(text, **fields):
    nl = "\r\n" if "\r\n" in (text or "") else "\n"
    lines = (text or "").splitlines()
    seen = set()
    for i, line in enumerate(lines):
        k = line.partition("=")[0].strip()
        if "=" in line and k in fields:
            lines[i] = "%s = %s" % (k, fields[k])
            seen.add(k)
    for k, v in fields.items():
        if k not in seen:
            lines.append("%s = %s" % (k, v))
    return nl.join(lines) + nl


def files_writable(path):
    """False where Orca's sandbox denies Python file access (path parts with secret/cert/conf)."""
    parts = [p.lower() for p in re.split(r"[\\/]+", path or "") if p]
    return bool(parts) and not any(w in part for part in parts for w in ("secret", "cert", "conf"))


class SettingsSync:
    """Keeps one plugin's non-secret settings in step with the carrier preset."""

    def __init__(self, plugin_id, exclude=(), bundle=None, now=None, machine=None):
        self.plugin_id = plugin_id
        self.exclude = set(exclude) | set(SYNC_RESERVED_KEYS)
        self.bundle = bundle or (lambda: orca.host.preset_bundle() if orca is not None else None)
        self.now = now or (lambda: int(time.time() * 1000))
        self.machine = machine or machine_name()
        self.status = {"state": "idle", "detail": "", "machine": self.machine, "last": 0}
        self._lock = threading.Lock()

    def _doc(self):
        bus = sync_bus()
        try:
            from_presets = read_carrier_from_presets(self.bundle())
        except Exception:
            from_presets = None
        if from_presets is not None:
            bus.offer(from_presets, "presets")
        with bus.lock:
            return json.loads(json.dumps(bus.doc)) if bus.doc is not None else empty_carrier()

    def syncable(self, stored, defaults):
        keys = (set(defaults) | set(stored)) - self.exclude
        return {k: stored.get(k, defaults.get(k)) for k in sorted(keys) if not k.startswith("_")}

    def sync(self, stored, defaults):
        """Merge stored config with the carrier. Returns the config to save, or None if unchanged."""
        if not self._lock.acquire(blocking=False):
            return None
        try:
            doc = self._doc()
            values = self.syncable(stored, defaults)
            section = (doc.get("plugins") or {}).get(self.plugin_id) or {}
            new_values, new_state, new_section, local_changed, carrier_changed = merge_settings(
                values, stored.get("_sync") or {}, section, self.now(), self.machine)
            save = None
            if local_changed or new_state != (stored.get("_sync") or {}):
                save = dict(stored)
                save.update(new_values)
                save["_sync"] = new_state
            if carrier_changed:
                doc.setdefault("plugins", {})[self.plugin_id] = new_section
                doc["schema"] = SYNC_SCHEMA
                self.publish(doc)
            self.status.update(state="ok", last=self.now(),
                               detail="pulled newer settings" if local_changed else ("published local changes" if carrier_changed else "in sync"))
            return save
        except Exception as exc:
            self.status.update(state="error", detail=str(exc))
            return None
        finally:
            self._lock.release()

    def publish(self, doc):
        """Share doc in-process, push it to the cloud if a signed-in plugin can, and write the carrier file."""
        bus = sync_bus()
        bus.offer(doc, self.plugin_id)
        pusher = bus.pusher
        if pusher is not None:
            try:
                threading.Thread(target=pusher, args=(json.loads(json.dumps(doc)),),
                                 name="nnsync-push", daemon=True).start()
            except Exception:
                pass
        udir = ""
        try:
            udir = user_dir_from_presets(self.bundle())
        except Exception:
            pass
        if udir and files_writable(udir):
            try:
                write_carrier_file(udir, doc)
            except OSError as exc:
                self.status.update(state="error", detail="could not write the carrier preset: %s" % exc)
        elif pusher is None:
            self.status.update(detail="this computer cannot write Orca's preset folder; changes spread once Orca Cloud Manager is signed in here or on another computer")


SECRET_KEYS = ("ophq_key", "hub_key")
SECRETS = SecretStore("openprinthq-print-queue")   # store names follow the plugin id (0.14.0)
# The address stays on each computer (0.15.0): the same farm can have a different address from different places (a
# browser desktop beside the farm reaches it on the inside), so one computer's address must not replace another's.
LOCAL_KEYS = ("ophq_url",)
SETTINGS_SYNC = SettingsSync("openprinthq-print-queue", exclude=SECRET_KEYS + LEGACY_KEYS + LOCAL_KEYS)
SYNC_INTERVAL_S = 30


class OpenPrintHQError(Exception):
    """An API call failed (the farm or the hub). `code` is the HTTP status (0 for network errors)."""

    def __init__(self, message, code=0, error_code=""):
        super().__init__(message)
        self.code = code
        self.error_code = error_code


def _unquote(value):
    """Preset string options come back serialized; strip one layer of quotes."""
    if value is None:
        return ""
    text = str(value).strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        text = text[1:-1]
    return text


def _notify(text, level="WarningNotificationLevel"):
    """Show a plater notification; never let a UI problem break a print job."""
    try:
        levels = orca.host.ui.NotificationLevel
        orca.host.ui.push_notification(getattr(levels, level), "OpenPrintHQ: " + text)
    except Exception:
        pass
    print("[openprinthq] " + text)


def normalize_base_url(host, port="", use_ssl=False):
    """Build https://host[:port] from what the preset or Orca hands us."""
    host = (host or "").strip()
    if not host:
        return ""
    if not re.match(r"^https?://", host, re.I):
        host = ("https://" if use_ssl or not port else "http://") + host
    parts = urllib.parse.urlsplit(host)
    netloc = parts.netloc
    if port and port.isdigit() and ":" not in netloc:
        netloc += ":" + port
    return urllib.parse.urlunsplit((parts.scheme.lower(), netloc, "", "", "")).rstrip("/")


def normalize_ophq_url(url):
    """https://host[:port][/path] from what the Config page holds; keeps a path, drops query and slash."""
    url = (url or "").strip()
    if not url:
        return ""
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parts.scheme.lower(), parts.netloc, parts.path, "", "")).rstrip("/")


def engine_api_base(url, key):
    """The engine API root for an OpenPrintHQ address and key.

    An ophqk_ key goes through the control plane (<address>/api/engine/api/v1); an engine's own
    bb_ key goes straight to the engine (<address>/api/v1). An address that already ends in
    /api/v1 is used as it is.
    """
    url = normalize_ophq_url(url)
    if not url:
        return ""
    path = urllib.parse.urlsplit(url).path
    if path.endswith(ENGINE_API):
        return url
    if path.endswith("/api/engine"):
        return url + ENGINE_API
    if (key or "").strip().startswith("bb_"):
        return url + ENGINE_API
    return url + CONTROL_PLANE_API


VP_PREFIX = "openprinthq_virtual_printer"


def routing_printer(preset_key):
    """Printer named in a virtual printer routing string, or "".

    The string looks like openprinthq_virtual_printer:printer=MK3S,queue=true,priority=50
    """
    value = (preset_key or "").strip()
    if not value.lower().startswith(VP_PREFIX):
        return ""
    params = value[len(VP_PREFIX):].lstrip(":")
    for part in re.split(r"[,;&]", params):
        key, _, val = part.partition("=")
        if key.strip().lower() == "printer" and val.strip():
            return urllib.parse.unquote(val.strip())
    return ""


def resolve_printer(preset_name, mapping, printer_names, routed=""):
    """Map an Orca preset name to an OpenPrintHQ printer name.

    Order: explicit mapping, the printer in the preset's virtual printer routing
    string, then a unique whole-word match in the preset name.
    Returns (name, how) or (None, reason).
    """
    if preset_name in mapping:
        return mapping[preset_name], "mapping"
    lowered = {k.lower(): v for k, v in mapping.items()}
    if preset_name.lower() in lowered:
        return lowered[preset_name.lower()], "mapping"
    if routed:
        for n in printer_names:
            if n.lower() == routed.lower():
                return n, "routing string"
    hits = [n for n in printer_names
            if re.search(r"(?<![A-Za-z0-9])" + re.escape(n) + r"(?![A-Za-z0-9])", preset_name, re.I)]
    if len(hits) == 1:
        return hits[0], "name match"
    if not hits:
        return None, "no mapping for preset '%s' and no OpenPrintHQ printer name appears in it" % preset_name
    return None, "preset '%s' matches several OpenPrintHQ printers (%s); add a mapping" % (preset_name, ", ".join(hits))


def file_kind(data):
    """Classify a sliced file by its magic bytes."""
    if data[:4] == b"PK\x03\x04":
        return "3mf"
    if data[:4] == b"GCDE":
        return "bgcode"
    return "gcode"


def extract_plate_gcode(data, plate_index=None):
    """Pull a plate's G-code out of a .gcode.3mf. Returns bytes or None."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = [n for n in zf.namelist() if re.fullmatch(r"Metadata/plate_\d+\.gcode", n)]
        if not names:
            return None
        wanted = None
        if plate_index is not None and plate_index >= 0:
            for candidate in ("Metadata/plate_%d.gcode" % (plate_index + 1), "Metadata/plate_%d.gcode" % plate_index):
                if candidate in names:
                    wanted = candidate
                    break
        if wanted is None:
            if len(names) != 1:
                return None
            wanted = names[0]
        return zf.read(wanted)


def job_basename(params, fallback_path):
    """A readable job name without .gcode / .3mf suffixes."""
    for candidate in (getattr(params, "project_name", ""), getattr(params, "task_name", ""),
                      getattr(params, "preset_name", ""), fallback_path.replace("\\", "/").rsplit("/", 1)[-1]):
        name = (candidate or "").strip()
        if not name or name == "verify_job":
            continue
        name = re.sub(r"(\.gcode)?\.3mf$|\.b?gcode$", "", name, flags=re.I)
        name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", name).strip(" ._")
        # Orca's temp files look like ".1234.0" (pid.plate); not useful as a name.
        if name and not re.fullmatch(r"\.?\d+(\.\d+)*", name):
            return name[:120]
    return "orca-print"


def build_status_payload(printer, busy_as_idle=False, extras=None):
    """Translate a printer (printer_view) into Orca's push_status message."""
    state = printer.get("state") or "Unknown"
    online = bool(printer.get("online"))
    gcode_state = STATE_MAP.get(state, "IDLE") if online else "IDLE"
    if busy_as_idle and gcode_state in ("RUNNING", "PREPARE", "PAUSE"):
        gcode_state = "IDLE"
    p = {
        "command": "push_status",
        "msg": 0,
        "support_mqtt_alive": True,
        "gcode_state": gcode_state,
        "mc_print_stage": STAGE_FOR_STATE.get(gcode_state, 0),
        "mc_print_error_code": 0,
        "print_error": 0,
        "support_send_to_sd": True,
        "sdcard": True,
    }
    temps = printer.get("temperatures") or {}
    for orca_key, wt_key in (("nozzle", "nozzle"), ("bed", "bed")):
        pair = temps.get(wt_key) or {}
        if pair.get("current") is not None:
            p[orca_key + "_temper"] = float(pair["current"])
        if pair.get("target") is not None:
            p[orca_key + "_target_temper"] = float(pair["target"])
    if printer.get("file"):
        p["subtask_name"] = printer["file"]
        p["gcode_file"] = printer["file"]
    if printer.get("progress") is not None:
        p["mc_percent"] = int(round(float(printer["progress"])))
    if printer.get("eta_minutes") is not None:
        p["mc_remaining_time"] = int(round(float(printer["eta_minutes"])))
    layers = printer.get("layers") or {}
    if layers.get("current") is not None:
        p["layer_num"] = int(layers["current"])
    if layers.get("total") is not None:
        p["total_layer_num"] = int(layers["total"])
    if extras:
        p.update(extras)
    return {"print": p, "t_utc": int(time.time() * 1000)}


# Let's Encrypt roots, bundled for Python builds that ship with no CA store.
# ISRG Root X1, SHA-1 CA:BD:2A:79:A1:07:6A:31:F2:1D:25:36:35:CB:03:9D:43:29:A5:E8
# ISRG Root X2, SHA-1 BD:B1:B9:3C:D5:97:8D:45:C6:26:14:55:F8:DB:95:C7:5A:D1:53:AF
BUNDLED_ROOTS_PEM = """
-----BEGIN CERTIFICATE-----
MIIFazCCA1OgAwIBAgIRAIIQz7DSQONZRGPgu2OCiwAwDQYJKoZIhvcNAQELBQAw
TzELMAkGA1UEBhMCVVMxKTAnBgNVBAoTIEludGVybmV0IFNlY3VyaXR5IFJlc2Vh
cmNoIEdyb3VwMRUwEwYDVQQDEwxJU1JHIFJvb3QgWDEwHhcNMTUwNjA0MTEwNDM4
WhcNMzUwNjA0MTEwNDM4WjBPMQswCQYDVQQGEwJVUzEpMCcGA1UEChMgSW50ZXJu
ZXQgU2VjdXJpdHkgUmVzZWFyY2ggR3JvdXAxFTATBgNVBAMTDElTUkcgUm9vdCBY
MTCCAiIwDQYJKoZIhvcNAQEBBQADggIPADCCAgoCggIBAK3oJHP0FDfzm54rVygc
h77ct984kIxuPOZXoHj3dcKi/vVqbvYATyjb3miGbESTtrFj/RQSa78f0uoxmyF+
0TM8ukj13Xnfs7j/EvEhmkvBioZxaUpmZmyPfjxwv60pIgbz5MDmgK7iS4+3mX6U
A5/TR5d8mUgjU+g4rk8Kb4Mu0UlXjIB0ttov0DiNewNwIRt18jA8+o+u3dpjq+sW
T8KOEUt+zwvo/7V3LvSye0rgTBIlDHCNAymg4VMk7BPZ7hm/ELNKjD+Jo2FR3qyH
B5T0Y3HsLuJvW5iB4YlcNHlsdu87kGJ55tukmi8mxdAQ4Q7e2RCOFvu396j3x+UC
B5iPNgiV5+I3lg02dZ77DnKxHZu8A/lJBdiB3QW0KtZB6awBdpUKD9jf1b0SHzUv
KBds0pjBqAlkd25HN7rOrFleaJ1/ctaJxQZBKT5ZPt0m9STJEadao0xAH0ahmbWn
OlFuhjuefXKnEgV4We0+UXgVCwOPjdAvBbI+e0ocS3MFEvzG6uBQE3xDk3SzynTn
jh8BCNAw1FtxNrQHusEwMFxIt4I7mKZ9YIqioymCzLq9gwQbooMDQaHWBfEbwrbw
qHyGO0aoSCqI3Haadr8faqU9GY/rOPNk3sgrDQoo//fb4hVC1CLQJ13hef4Y53CI
rU7m2Ys6xt0nUW7/vGT1M0NPAgMBAAGjQjBAMA4GA1UdDwEB/wQEAwIBBjAPBgNV
HRMBAf8EBTADAQH/MB0GA1UdDgQWBBR5tFnme7bl5AFzgAiIyBpY9umbbjANBgkq
hkiG9w0BAQsFAAOCAgEAVR9YqbyyqFDQDLHYGmkgJykIrGF1XIpu+ILlaS/V9lZL
ubhzEFnTIZd+50xx+7LSYK05qAvqFyFWhfFQDlnrzuBZ6brJFe+GnY+EgPbk6ZGQ
3BebYhtF8GaV0nxvwuo77x/Py9auJ/GpsMiu/X1+mvoiBOv/2X/qkSsisRcOj/KK
NFtY2PwByVS5uCbMiogziUwthDyC3+6WVwW6LLv3xLfHTjuCvjHIInNzktHCgKQ5
ORAzI4JMPJ+GslWYHb4phowim57iaztXOoJwTdwJx4nLCgdNbOhdjsnvzqvHu7Ur
TkXWStAmzOVyyghqpZXjFaH3pO3JLF+l+/+sKAIuvtd7u+Nxe5AW0wdeRlN8NwdC
jNPElpzVmbUq4JUagEiuTDkHzsxHpFKVK7q4+63SM1N95R1NbdWhscdCb+ZAJzVc
oyi3B43njTOQ5yOf+1CceWxG1bQVs5ZufpsMljq4Ui0/1lvh+wjChP4kqKOJ2qxq
4RgqsahDYVvTH9w7jXbyLeiNdd8XM2w9U/t7y0Ff/9yi0GE44Za4rF2LN9d11TPA
mRGunUHBcnWEvgJBQl9nJEiU0Zsnvgc/ubhPgXRR4Xq37Z0j4r7g1SgEEzwxA57d
emyPxgcYxn/eR44/KJ4EBs+lVDR3veyJm+kXQ99b21/+jh5Xos1AnX5iItreGCc=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIICGzCCAaGgAwIBAgIQQdKd0XLq7qeAwSxs6S+HUjAKBggqhkjOPQQDAzBPMQsw
CQYDVQQGEwJVUzEpMCcGA1UEChMgSW50ZXJuZXQgU2VjdXJpdHkgUmVzZWFyY2gg
R3JvdXAxFTATBgNVBAMTDElTUkcgUm9vdCBYMjAeFw0yMDA5MDQwMDAwMDBaFw00
MDA5MTcxNjAwMDBaME8xCzAJBgNVBAYTAlVTMSkwJwYDVQQKEyBJbnRlcm5ldCBT
ZWN1cml0eSBSZXNlYXJjaCBHcm91cDEVMBMGA1UEAxMMSVNSRyBSb290IFgyMHYw
EAYHKoZIzj0CAQYFK4EEACIDYgAEzZvVn4CDCuwJSvMWSj5cz3es3mcFDR0HttwW
+1qLFNvicWDEukWVEYmO6gbf9yoWHKS5xcUy4APgHoIYOIvXRdgKam7mAHf7AlF9
ItgKbppbd9/w+kHsOdx1ymgHDB/qo0IwQDAOBgNVHQ8BAf8EBAMCAQYwDwYDVR0T
AQH/BAUwAwEB/zAdBgNVHQ4EFgQUfEKWrt5LSDv6kviejM9ti6lyN5UwCgYIKoZI
zj0EAwMDaAAwZQIwe3lORlCEwkSHRhtFcP9Ymd70/aTSVaYgLXTWNLxBo1BfASdW
tL4ndQavEi51mI38AjEAi/V3bNTIZargCyzuFJ0nN6T5U6VR5CmD1/iQMVtCnwr1
/q4AaOeMSQ+2b1tbFfLn
-----END CERTIFICATE-----
"""

SYSTEM_CA_FILES = (
    "/etc/ssl/cert.pem",                                  # macOS, BSD
    "/etc/ssl/certs/ca-certificates.crt",                 # Debian, Ubuntu, Arch
    "/etc/pki/tls/certs/ca-bundle.crt",                   # Fedora, RHEL
    "/etc/ssl/ca-bundle.pem",                             # openSUSE
    "/opt/homebrew/etc/ca-certificates/cert.pem",         # Homebrew (Apple silicon)
    "/usr/local/etc/ca-certificates/cert.pem",            # Homebrew (Intel)
)

_SSL_CONTEXT = None
_SSL_LOCK = threading.Lock()


def _ca_count(ctx):
    try:
        return int(ctx.cert_store_stats().get("x509_ca", 0))
    except Exception:
        return 0


def ssl_context():
    """Verifying SSL context that also works where Python has no CA store."""
    global _SSL_CONTEXT
    with _SSL_LOCK:
        if _SSL_CONTEXT is not None:
            return _SSL_CONTEXT
        ctx = ssl.create_default_context()
        if _ca_count(ctx) < 20:
            for path in SYSTEM_CA_FILES:
                try:
                    ctx.load_verify_locations(cafile=path)
                except Exception:
                    continue
                if _ca_count(ctx) >= 20:
                    break
        try:
            ctx.load_verify_locations(cadata=BUNDLED_ROOTS_PEM)
        except Exception:
            pass
        _SSL_CONTEXT = ctx
        return ctx


def _error_text(raw, status, reason=""):
    """(message, code) from an OpenPrintHQ error body: {"detail": str | {"code", "message"} | [...]} or {"error"}."""
    try:
        doc = json.loads((raw or b"").decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        doc = None
    message, code = "", ""
    if isinstance(doc, dict):
        detail = doc.get("detail")
        if isinstance(detail, dict):
            message, code = str(detail.get("message") or ""), str(detail.get("code") or "")
        elif isinstance(detail, list):
            message = "; ".join(str(d.get("msg", d)) if isinstance(d, dict) else str(d) for d in detail)
        elif detail:
            message = str(detail)
        message = message or str(doc.get("error") or doc.get("message") or "")
    message = message or reason or "HTTP %d" % status
    if status == 401:
        message += " (check the OpenPrintHQ API key on the plugin's Config page)"
    return message, code


def scope_hint(exc, scope):
    """A hint for a 403 the server did not already explain in terms of the key's scope."""
    if getattr(exc, "code", 0) != 403 or "scope" in str(exc):
        return ""
    return " (the API key needs the %s scope)" % scope


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def printer_view(row, status):
    """One printer as the agent, the tab and the job windows read it: an engine printer row plus its live status."""
    st = status if isinstance(status, dict) else {}
    online = bool(st.get("connected"))
    raw = str(st.get("state") or "").upper()
    busy = online and raw in ENGINE_BUSY
    state = (ENGINE_STATES.get(raw) or (raw.title() if raw else "Online")) if online else "Offline"
    temps = st.get("temperatures") or {}

    def pair(key):
        cur, tgt = _num(temps.get(key)), _num(temps.get(key + "_target"))
        if cur is None and tgt is None:
            return None
        return {"current": cur, "target": tgt}

    total = st.get("total_layers")
    return {
        "id": row.get("id"), "name": row.get("name") or "", "model": row.get("model") or "",
        "family": row.get("connection_type") or "bambu", "online": online, "state": state, "engine_state": raw,
        "busy": busy, "ready": (not st.get("awaiting_plate_clear")) if online else None,
        "file": (st.get("subtask_name") or st.get("gcode_file") or st.get("current_print")) if busy else None,
        "progress": _num(st.get("progress")) if busy else None,
        "eta_minutes": _num(st.get("remaining_time")) if busy else None,
        "layers": {"current": st.get("layer_num") or 0, "total": total} if busy and total else None,
        "temperatures": {k: v for k, v in (("nozzle", pair("nozzle")), ("bed", pair("bed")),
                                           ("chamber", pair("chamber"))) if v},
        "controls": row.get("controls") or {},
        "out_of_service": bool(row.get("out_of_service")),
        "out_of_service_reason": row.get("out_of_service_reason") or "",
    }


def item_name(item):
    name = (item or {}).get("library_file_name") or (item or {}).get("archive_name") or "job %s" % (item or {}).get("id")
    return re.sub(r"(\.gcode)?\.3mf$|\.b?gcode$", "", str(name), flags=re.I)


def item_status(item):
    """A queue item's status in the words the tab uses: printing, completed, failed, cancelled, or for a
    pending item waiting_filament, held (manual start), assigned (pinned to a printer) or queued."""
    st = str((item or {}).get("status") or "pending").lower()
    if st != "pending":
        return "cancelled" if st == "skipped" else st
    why = str(item.get("waiting_reason") or "").lower()
    if "filament" in why or item.get("filament_short"):
        return "waiting_filament"
    if item.get("manual_start"):
        return "held"
    return "assigned" if item.get("printer_id") else "queued"


def item_materials(item):
    """Distinct material types a queue item needs: ["PLA"], ["PLA", "PETG"]."""
    raw = (item or {}).get("required_filament_types") or []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = raw.split(",")
    out = []
    for t in list(raw if isinstance(raw, list) else []) + str((item or {}).get("filament_type") or "").split(","):
        t = str(t).strip().upper()
        if t and t not in out:
            out.append(t)
    return out


def queue_row(item):
    """A queue item as a row of the tab's farm queue."""
    st = item_status(item)
    pinned = item.get("printer_name") or ""
    target = item.get("target_model") or item.get("target_group_name") or ""
    secs = _num(item.get("print_time_seconds"))
    return {"id": item.get("id"), "name": item_name(item), "printer": pinned or (("any " + target) if target else ""),
            "printer_id": item.get("printer_id"), "status": st, "state": job_state(st),
            "priority": item.get("priority"), "position": item.get("position") or 0, "progress": None,
            "hours": round(secs / 3600.0, 2) if secs else None, "soft": False, "user": item.get("created_by_username") or "",
            "created": item.get("created_at"), "materials": item_materials(item), "why": item.get("waiting_reason") or ""}


def assignment_rows(rows):
    """Engine spool assignments (GET /inventory/assignments) as slot rows for ams_payload_from_assignments."""
    out = []
    for a in rows or []:
        sp = a.get("spool") or {}
        try:
            ams_id, tray = int(a.get("ams_id") or 0), int(a.get("tray_id") or 0)
        except (TypeError, ValueError):
            continue
        left = None
        weight, used = _num(sp.get("label_weight")), _num(sp.get("weight_used")) or 0.0
        if weight:
            left = max(0, min(100, int(round((weight - used) * 100 / weight))))
        out.append({"slot_type": "external" if ams_id >= 254 else "ams", "system_index": ams_id, "slot_index": tray,
                    "material_type": str(sp.get("material") or "").upper(), "color_hex": str(sp.get("rgba") or "")[:6],
                    "manufacturer": sp.get("brand") or "", "temperature_min": sp.get("nozzle_temp_min"),
                    "temperature_max": sp.get("nozzle_temp_max"), "remaining_percentage": left,
                    "profile_id": sp.get("slicer_filament") or ""})
    return out


class OpenPrintHQClient:
    """OpenPrintHQ engine API client (stdlib only).

    Talks to the engine's versioned API (docs/slicer-api.md in openprinthq-engine) with one API key: through the
    control plane for an ophqk_ key, or straight to an engine for its own bb_ key. Answers are shaped for the
    agent, the tab and the job windows (printer_view, queue_row, job()).
    """

    def __init__(self, base_url, api_key, timeout=30):
        self.base_url = normalize_ophq_url(base_url)
        self.api_key = (api_key or "").strip()
        self.api = engine_api_base(self.base_url, self.api_key)
        self.timeout = timeout
        self._lock = threading.Lock()
        self._index = {}          # printer name -> engine printer row
        self._index_at = 0.0
        self._status = {}         # printer name -> (time, raw engine status)

    # ---- transport -------------------------------------------------------
    def _open(self, method, url, body=None, content_type=None, timeout=None):
        headers = {"Authorization": "Bearer " + self.api_key, "Accept": "application/json",
                   "User-Agent": "OrcaSlicer-OpenPrintHQ/" + VERSION}
        if content_type:
            headers["Content-Type"] = content_type
        req = urllib.request.Request(url, data=body, headers=headers, method=method)

        def fetch():
            ctx = ssl_context() if url.lower().startswith("https:") else None
            with urllib.request.urlopen(req, timeout=timeout or self.timeout, context=ctx) as resp:
                return resp.read(), resp.status, resp.headers.get("Content-Type", "")

        try:
            raw, status, ctype = off_context(fetch)
        except urllib.error.HTTPError as exc:
            message, code = _error_text(exc.read() or b"", exc.code, exc.reason)
            raise OpenPrintHQError(message, exc.code, code) from None
        except (urllib.error.URLError, OSError) as exc:
            raise OpenPrintHQError("cannot reach %s: %s" % (self.base_url, getattr(exc, "reason", exc))) from None
        if not raw.strip():
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            hint = "; a sign-in page answered, so check the address and that the key starts with ophqk_" \
                if "html" in (ctype or "").lower() else ""
            raise OpenPrintHQError("not an OpenPrintHQ API answer from %s (HTTP %d)%s" % (self.base_url, status, hint),
                                   status) from None

    def request(self, method, path, payload=None, timeout=None, query=None):
        url = self.api + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        return self._open(method, url, body, "application/json" if body is not None else None, timeout)

    # ---- identity and printers --------------------------------------------
    def me(self, timeout=None):
        """{"email", "apiKey": {"id", "scope"}} from the control plane; None for an engine's own key."""
        if not self.api.endswith(CONTROL_PLANE_API):
            return None
        return self._open("GET", self.api[:-len(CONTROL_PLANE_API)] + "/api/me", timeout=timeout)

    def ping(self, timeout=None):
        return self.printer_rows(timeout=timeout)

    def printer_rows(self, timeout=None):
        rows = self.request("GET", "/printers/", timeout=timeout) or []
        index = {r.get("name"): r for r in rows if isinstance(r, dict) and r.get("is_active", True)}
        with self._lock:
            self._index, self._index_at = index, time.time()
        return list(index.values())

    def printer_row(self, name, timeout=None):
        with self._lock:
            row, fresh = self._index.get(name), time.time() - self._index_at < 60
        if row is None or not fresh:
            self.printer_rows(timeout=timeout)
            with self._lock:
                row = self._index.get(name)
        if row is None:
            raise OpenPrintHQError("OpenPrintHQ has no printer named %s" % name, 404)
        return row

    def printer_id(self, name):
        return int(self.printer_row(name)["id"])

    def raw_status(self, name, timeout=None, max_age=0.0):
        with self._lock:
            hit = self._status.get(name)
        if hit and time.time() - hit[0] < max_age:
            return hit[1]
        st = self.request("GET", "/printers/%d/status" % self.printer_id(name), timeout=timeout) or {}
        with self._lock:
            self._status[name] = (time.time(), st)
        return st

    def printers(self, timeout=None):
        out = []
        for row in self.printer_rows(timeout=timeout):
            try:
                st = self.request("GET", "/printers/%d/status" % int(row["id"]), timeout=timeout) or {}
                with self._lock:
                    self._status[row.get("name")] = (time.time(), st)
            except OpenPrintHQError as exc:
                if exc.code in (401, 403):
                    raise
                st = {}
            out.append(printer_view(row, st))
        return out

    def printer(self, name, timeout=None):
        return printer_view(self.printer_row(name, timeout=timeout), self.raw_status(name, timeout=timeout))

    # ---- files and jobs -------------------------------------------------------
    def upload(self, filename, data, reuse=True):
        """POST /library/files. With reuse, a file the library already has comes back with reused: true."""
        boundary = "----orca-openprinthq-" + uuid.uuid4().hex
        head = ('--%s\r\nContent-Disposition: form-data; name="file"; filename="%s"\r\n'
                "Content-Type: application/octet-stream\r\n\r\n" % (boundary, filename.replace('"', "_"))).encode("utf-8")
        body = head + data + ("\r\n--%s--\r\n" % boundary).encode("ascii")
        url = self.api + "/library/files" + ("?reuse_duplicate=true" if reuse else "")
        return self._open("POST", url, body, "multipart/form-data; boundary=" + boundary)

    def file_by_hash(self, sha256, timeout=None):
        """The library file with this SHA-256, or None."""
        try:
            return self.request("GET", "/library/files/by-hash/" + sha256.lower(), timeout=timeout)
        except OpenPrintHQError as exc:
            if exc.code == 404:
                return None
            raise

    def submit(self, file_id, priority, printer_name, quantity=1, at_top=False):
        """Queue a library file on one printer. Returns {"job_ids": [...], "item": first item}."""
        quantity = max(1, int(quantity))
        body = {"library_file_id": int(file_id), "printer_id": self.printer_id(printer_name),
                "priority": max(0, min(100, int(priority))), "quantity": quantity}
        if at_top:
            body["insert_at_top"] = True
        item = self.request("POST", "/queue/", body) or {}
        ids = [item["id"]] if item.get("id") else []
        if quantity > 1 and item.get("batch_id"):
            batch = sorted(r["id"] for r in self.queue_items() if r.get("batch_id") == item["batch_id"])
            ids = batch or ids
        return {"job_ids": ids, "item": item}

    def queue_items(self, timeout=None):
        items = []
        for st in ("printing", "pending"):
            items += self.request("GET", "/queue/", query={"status": st}, timeout=timeout) or []
        return items

    def queue(self, timeout=None):
        """The active farm queue as tab rows: printing first, then dispatch order (priority, then position)."""
        items = sorted(self.queue_items(timeout), key=lambda i: (0 if i.get("status") == "printing" else 1,
                                                                 -(i.get("priority") or 0), i.get("position") or 0,
                                                                 i.get("id") or 0))
        return [queue_row(i) for i in items]

    def job(self, job_id, timeout=None):
        """One queue item: {"id", "status", "progress", "assigned_printer", "error_message", "name"}."""
        item = self.request("GET", "/queue/%d" % int(job_id), timeout=timeout) or {}
        out = {"id": item.get("id"), "status": item_status(item), "assigned_printer": item.get("printer_name") or "",
               "printer_id": item.get("printer_id"), "progress": None, "name": item_name(item),
               "error_message": item.get("error_message") or item.get("cancel_reason") or ""}
        if out["status"] == "printing" and item.get("printer_id"):
            try:
                st = self.request("GET", "/printers/%d/status" % int(item["printer_id"]), timeout=timeout) or {}
                out["progress"] = _num(st.get("progress"))
                if str(st.get("state") or "").upper() == "PAUSE":
                    out["status"] = "paused"
            except OpenPrintHQError:
                pass
        return out

    def job_action(self, job_id, action):
        """pause / resume the printer a job is printing on; cancel a waiting job or stop a printing one."""
        item = self.request("GET", "/queue/%d" % int(job_id)) or {}
        printing = item.get("status") == "printing"
        if action in ("pause", "resume"):
            if not printing or not item.get("printer_id"):
                raise OpenPrintHQError("job #%d is not printing" % int(job_id), 409)
            return self.request("POST", "/printers/%d/print/%s" % (int(item["printer_id"]), action))
        if action == "cancel":
            if printing:
                return self.request("POST", "/queue/%d/stop" % int(job_id))
            return self.request("POST", "/queue/%d/cancel" % int(job_id),
                                {"reason": "Cancelled from OrcaSlicer", "by": "OrcaSlicer"})
        raise OpenPrintHQError("unknown job action: %s" % action)

    def set_priority(self, job_id, priority):
        return self.request("PATCH", "/queue/%d" % int(job_id), {"priority": int(priority)})

    def reorder(self, job_ids):
        """Positions 1..n in the given order (POST /queue/reorder). Dispatch still goes by priority first."""
        return self.request("POST", "/queue/reorder",
                            {"items": [{"id": int(j), "position": i + 1} for i, j in enumerate(job_ids)]})

    def print_now(self, job_id, printer_name, clear_plate=False):
        """Pin a waiting job to a printer at priority 100, first in that printer's queue; with clear_plate, then
        tell the engine the bed is clear so it can start. Returns "" or, when the engine would not mark the bed
        clear (400: it was not waiting for that, or the printer is offline), its reason; the job stays pinned."""
        jid, pid = int(job_id), self.printer_id(printer_name)
        self.request("PATCH", "/queue/%d" % jid, {"printer_id": pid, "target_model": None, "priority": 100})
        rows = self.request("GET", "/queue/", query={"status": "pending", "printer_id": pid}) or []
        others = [r["id"] for r in sorted(rows, key=lambda r: (r.get("position") or 0, r.get("id") or 0))
                  if r.get("id") != jid and r.get("printer_id") == pid]
        self.reorder([jid] + others)
        if clear_plate:
            try:
                self.request("POST", "/printers/%d/clear-plate" % pid)
            except OpenPrintHQError as exc:
                if exc.code != 400:
                    raise
                return str(exc)
        return ""

    def remove(self, job_id):
        return self.request("DELETE", "/queue/%d" % int(job_id))

    def clear_plate(self, printer_name):
        """Tell the engine the bed is clear (its "ready" latch); the next job may start at once."""
        return self.request("POST", "/printers/%d/clear-plate" % self.printer_id(printer_name))

    # ---- hardware and filament -----------------------------------------------
    def nozzle_view(self, printer_id):
        return self.request("GET", "/printers/%d/nozzles" % int(printer_id)) or {}

    def nozzles(self):
        """{printer name: [diameter per extruder, in extruder order]}."""
        out = {}
        for row in self.printer_rows():
            try:
                view = self.nozzle_view(row["id"])
            except OpenPrintHQError as exc:
                if exc.code in (401, 403):
                    raise
                continue
            sizes = [n.get("diameter_mm") for n in sorted(view.get("nozzles") or [],
                                                           key=lambda n: n.get("extruder_index") or 0)]
            if sizes:
                out[row.get("name")] = sizes
        return out

    def set_nozzles(self, name, sizes):
        """Set each extruder's nozzle size (PUT /printers/{id}/nozzles/{index}), keeping its type and flow."""
        pid = self.printer_id(name)
        current = sorted(self.nozzle_view(pid).get("nozzles") or [], key=lambda n: n.get("extruder_index") or 0)
        sizes = [float(x) for x in sizes]
        if len(sizes) != max(1, len(current)):
            raise OpenPrintHQError("%s has %d nozzle(s); got %d size(s)" % (name, max(1, len(current)), len(sizes)))
        for x in sizes:
            if not 0.1 <= x <= 2.0:
                raise OpenPrintHQError("nozzle size %s mm is out of range" % x)
        changed = 0
        for idx, size in enumerate(sizes):
            cur = current[idx] if idx < len(current) else {}
            if cur and abs((_num(cur.get("diameter_mm")) or 0) - size) < 1e-6:
                continue
            self.request("PUT", "/printers/%d/nozzles/%d" % (pid, idx),
                         {"diameter_mm": size, "nozzle_type": cur.get("nozzle_type") or "undefine",
                          "volume_type": cur.get("volume_type") or "Standard"})
            changed += 1
        return changed

    def assignments(self, name):
        """Spools assigned to a printer's slots, as slot rows (assignment_rows)."""
        rows = self.request("GET", "/inventory/assignments", query={"printer_id": self.printer_id(name)}) or []
        return assignment_rows(rows)

    def out_of_service(self):
        return self.request("GET", "/printers/out-of-service") or []

    def low_stock(self):
        """Active spools below their low-stock threshold (the spool's own, else the engine's setting)."""
        spools = self.request("GET", "/inventory/spools") or []
        try:
            threshold = _num((self.request("GET", "/settings/") or {}).get("low_stock_threshold")) or 20.0
        except OpenPrintHQError:
            threshold = 20.0
        out = []
        for s in spools:
            weight, used = _num(s.get("label_weight")), _num(s.get("weight_used")) or 0.0
            if s.get("archived_at") or not weight:
                continue
            left = max(0.0, weight - used)
            if left * 100.0 / weight < (_num(s.get("low_stock_threshold_pct")) or threshold):
                out.append({"manufacturer": s.get("brand") or "", "material_type": str(s.get("material") or "").upper(),
                            "color_name": s.get("color_name") or "", "remaining_weight": left})
        return out

    def preflight(self, filament_types=None, nozzle=None, skip_filament=False):
        """POST /queue/preflight for every active printer: who could take a job with these needs, and why not."""
        body = {"filament_types": [str(t).upper() for t in filament_types or [] if t],
                "skip_filament_check": bool(skip_filament)}
        if nozzle:
            body["nozzle_diameter"] = float(nozzle)
        return self.request("POST", "/queue/preflight", body) or {}


# ---------------------------------------------------------------- The hub (optional, 0.11.0)
#
# The hub, the rest of OpenPrintHQ around the farm, adds: plate checks after every print, staggered
# start, a farm timeline with order due dates, maintenance, real filament use, a Spoolman spool sync
# and reports. With a hub address and a hub API key (ahk_...) on the Config page, the OpenPrintHQ tab
# shows those next to the farm's own views. Without them
# the plugin works exactly as before.

HUB_KEY_RE = re.compile(r"^ahk_[0-9a-f]{8}_[A-Za-z0-9_-]{20,}$")
THEME = "glass-dark"   # openprinthq-ui's only theme; the 0.11 skins and light mode were removed in 0.13.0


class HubClient:
    """Minimal hub client (stdlib only): one summary call plus the farm actions."""

    def __init__(self, base_url, api_key, timeout=20):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _request(self, method, path, payload=None, raw=False, timeout=None):
        url = self.base_url + path
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Authorization": "Bearer " + self.api_key, "Accept": "application/json",
                   "User-Agent": "OrcaSlicer-OpenPrintHQ/" + VERSION}
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=body, headers=headers, method=method)

        def fetch():
            ctx = ssl_context() if url.lower().startswith("https:") else None
            with urllib.request.urlopen(req, timeout=timeout or self.timeout, context=ctx) as resp:
                return resp.read(), resp.status

        try:
            data, status = off_context(fetch)
        except urllib.error.HTTPError as exc:
            text = exc.read() or b""
            try:
                detail = json.loads(text.decode("utf-8")).get("detail")
            except (ValueError, AttributeError):
                detail = None
            if isinstance(detail, list):
                detail = "; ".join(str(d.get("msg", d)) for d in detail)
            if exc.code == 401:
                detail = "the hub refused the API key (check it on the Config page)"
            raise OpenPrintHQError("Hub: %s" % (detail or exc.reason or "HTTP %d" % exc.code), exc.code) from None
        except (urllib.error.URLError, OSError) as exc:
            raise OpenPrintHQError("cannot reach the hub at %s: %s" % (self.base_url, getattr(exc, "reason", exc))) from None
        if raw:
            return data
        try:
            return json.loads(data.decode("utf-8")) if data else {}
        except ValueError:
            raise OpenPrintHQError("The hub sent a non-JSON answer for %s (HTTP %d)" % (path, status), status) from None

    def farm(self, timeout=None):
        return self._request("GET", "/api/plugin/farm", timeout=timeout)

    def photo(self, check_id):
        return self._request("GET", "/api/plate-checks/%d/photo" % int(check_id), raw=True)

    def check_pass(self, check_id, set_ready=True, note=None):
        return self._request("POST", "/api/plate-checks/%d/pass" % int(check_id), {"set_ready": bool(set_ready), "note": note})

    def check_fail(self, check_id, reason, note=None, reprint=False, set_ready=True):
        return self._request("POST", "/api/plate-checks/%d/fail" % int(check_id),
                             {"reason": reason, "note": note, "reprint": bool(reprint), "set_ready": bool(set_ready)})

    def check_skip(self, check_id):
        return self._request("POST", "/api/plate-checks/%d/skip" % int(check_id), {})

    def check_retry_ready(self, check_id):
        return self._request("POST", "/api/plate-checks/%d/retry-ready" % int(check_id), {})

    def maint_done(self, task_id, notes=None):
        return self._request("POST", "/api/maintenance/tasks/%d/done" % int(task_id), {"notes": notes})

    def prioritise(self, order_id, priority=75):
        return self._request("POST", "/api/orders/%d/prioritise" % int(order_id), {"priority": int(priority)})

    def measure(self, days=3):
        return self._request("POST", "/api/farm/filament/measure", {"days": int(days)})

    def spool_sync(self):
        return self._request("POST", "/api/spools/sync", {"dry_run": False})

    def farm_sync(self):
        return self._request("POST", "/api/farm/sync", {})


def hub_connection(cfg):
    """(hub URL, hub key) from the config, or ("", "") when the hub is not set up."""
    url = (cfg.get("hub_url") or "").strip().rstrip("/")
    key = (cfg.get("hub_key") or "").strip()
    if not url or not key:
        return "", ""
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    return url, key


def resolve_look(cfg=None, hub_look=None):
    """The look of the plugin's pages. Since 0.13.0 there is one: openprinthq-ui's glass-dark theme, the same as every
    other OpenPrintHQ app. The 0.11 settings (look, look_mode) and the skin chosen in the hub are ignored."""
    return {"theme": THEME}


# Orca's vendor model_id for each printer model the farm uses. The Sync filaments
# button only works when the device reports the model_id of a matching system preset.
MODEL_IDS = {
    "bambu lab h2d": "O1D", "bambu lab h2d pro": "O1E", "bambu lab h2c": "O1C2", "bambu lab h2s": "O1S",
    "bambu lab x1 carbon": "BL-P001", "bambu lab x1": "BL-P002", "bambu lab x1e": "C13",
    "bambu lab p1s": "C12", "bambu lab p1p": "C11", "bambu lab a1": "N2S", "bambu lab a1 mini": "N1",
    "prusa mk4s": "MK4S", "prusa mk4s hf": "MK4S HF", "prusa mk3s": "MK3S", "prusa mk4": "MK4",
    "prusa core one": "COREONE",
}

# Generic Orca filament ids by material, for slots the farm knows only by type
# (manually assigned spools on the Prusas). Orca falls back to type matching anyway.
GENERIC_FILAMENT_IDS = {
    "PLA": "OFDSrzZ8", "PETG": "OFYPdQJh", "ABS": "OFY9muEs", "ASA": "OFLPAxz3", "TPU": "OFgbpcy9",
    "PA": "OFg8ndtj", "PC": "OFLakOUI", "PVA": "OFDvXujf", "HIPS": "OFsFon5l", "PCTG": "OFu1evlr",
    "PLA-CF": "OFWbdGsC", "PETG-CF": "OFoYSJKi", "PA-CF": "OFQLcbps",
}


def model_id_for(printer_model):
    return MODEL_IDS.get((printer_model or "").strip().lower(), "")


def _hex_color(value, alpha="FF"):
    v = (value or "").strip().lstrip("#").upper()
    if re.fullmatch(r"[0-9A-F]{6}", v):
        return v + alpha
    if re.fullmatch(r"[0-9A-F]{8}", v):
        return v
    return ""


NOZZLE_CHOICES = (0.2, 0.25, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0)


def ams_payload_from_status(status):
    """Orca 'ams' and 'vt_tray' fields from a Bambu printer's live status (GET /printers/{id}/status)."""
    if not isinstance(status, dict):
        return {}
    units = []
    ams_bits = tray_bits = 0
    for unit in status.get("ams") or []:
        try:
            uid = int(unit.get("id"))
        except (TypeError, ValueError):
            continue
        trays = []
        for tray in unit.get("tray") or []:
            t = {k: v for k, v in tray.items() if v is not None and k not in ("tag_uid", "tray_uuid", "xcam_info")}
            if t.get("tray_type") and not t.get("tray_info_idx"):
                t["tray_info_idx"] = GENERIC_FILAMENT_IDS.get(t["tray_type"].upper(), t["tray_type"])
            if t.get("tray_type"):
                try:
                    if uid >= 128:      # AMS HT: one tray per unit, bits from 16 (Orca DevFilaSystem)
                        tray_bits |= 1 << (16 + uid - 128)
                    elif uid < 4:
                        tray_bits |= 1 << (uid * 4 + int(t.get("id", 0)))
                except (TypeError, ValueError):
                    pass
            if "id" in t:
                t["id"] = str(t["id"])
            trays.append(t)
        u = {k: (str(v) if k in ("id", "humidity", "temp") else v) for k, v in unit.items()
             if k in ("id", "info", "humidity", "humidity_raw", "temp") and v is not None}
        u["tray"] = trays
        units.append(u)
        if uid >= 128:
            ams_bits |= 1 << (4 + uid - 128)
        elif uid < 4:
            ams_bits |= 1 << uid
    out = {}
    if units:
        out["ams"] = {"ams": units, "ams_exist_bits": "%x" % ams_bits, "tray_exist_bits": "%x" % tray_bits,
                      "version": 1}
    vir = status.get("vt_tray") or (status.get("vir_slot") or [None])[0]
    if isinstance(vir, list):   # the engine reports external spools as a list
        vir = next((v for v in vir if isinstance(v, dict) and v.get("tray_type")), None)
    if isinstance(vir, dict) and vir.get("tray_type"):
        out["vt_tray"] = {k: v for k, v in vir.items() if v is not None}
    return out


def ams_payload_from_assignments(rows):
    """Orca 'ams' and 'vt_tray' built from the spools assigned to a printer's slots (assignment_rows).

    Used for printers without live AMS telemetry: the Prusas (one manually
    assigned tool slot) and Bambu printers that are offline.
    """
    units, ext = {}, None
    for r in rows or []:
        kind = (r.get("slot_type") or "").lower()
        mat = (r.get("material_type") or "").upper()
        color = _hex_color(r.get("color_hex"))
        tray = {"id": str(r.get("slot_index") or 0)}
        if mat:
            tray.update({"tray_type": mat, "tray_info_idx": r.get("profile_id") or GENERIC_FILAMENT_IDS.get(mat, mat),
                         "tray_color": color or "FFFFFFFF", "cols": [color or "FFFFFFFF"],
                         "tray_sub_brands": r.get("manufacturer") or ""})
            if r.get("temperature_min"):
                tray["nozzle_temp_min"] = str(r["temperature_min"])
            if r.get("temperature_max"):
                tray["nozzle_temp_max"] = str(r["temperature_max"])
            if r.get("remaining_percentage") is not None:
                tray["remain"] = int(r["remaining_percentage"])
        if kind == "ams":
            units.setdefault(int(r.get("system_index") or 0), []).append(tray)
        elif kind in ("tool", "external") and mat and ext is None:
            ext = dict(tray, id="254")
    out = {}
    if units:
        out.update(ams_payload_from_status({"ams": [{"id": str(k), "tray": sorted(v, key=lambda t: int(t["id"]))}
                                                    for k, v in sorted(units.items())]}))
    if ext:
        out["vt_tray"] = ext
    return out


def plate_filaments(data, plate_index=None):
    """Filaments a sliced .gcode.3mf plate uses: [{"id", "type", "color", "used_g"}]."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            if "Metadata/slice_info.config" not in zf.namelist():
                return []
            text = zf.read("Metadata/slice_info.config").decode("utf-8", "replace")
    except (zipfile.BadZipFile, KeyError):
        return []
    plates = re.findall(r"<plate>(.*?)</plate>", text, re.S) or [text]
    chosen = plates[0]
    if plate_index:
        for block in plates:
            m = re.search(r'<metadata key="index" value="(\d+)"', block)
            if m and int(m.group(1)) == int(plate_index):
                chosen = block
                break
    out = []
    for tag in re.findall(r"<filament\b[^>]*/?>", chosen):
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', tag))
        if not attrs.get("type"):
            continue
        try:
            used = float(attrs.get("used_g") or 0)
        except ValueError:
            used = 0.0
        if used <= 0 and attrs.get("used_m") in (None, "", "0", "0.00"):
            continue
        out.append({"id": attrs.get("id", str(len(out) + 1)), "type": attrs["type"].upper(),
                    "color": "#" + _hex_color(attrs.get("color"))[:6] if _hex_color(attrs.get("color")) else "",
                    "used_g": round(used, 2)})
    return out


def plate_nozzles(data, plate_index=None):
    """Nozzle diameters a sliced .gcode.3mf was made for, e.g. [0.4] or [0.4, 0.4]."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
            if "Metadata/slice_info.config" in names:
                text = zf.read("Metadata/slice_info.config").decode("utf-8", "replace")
                plates = re.findall(r"<plate>(.*?)</plate>", text, re.S) or [text]
                chosen = plates[0]
                for block in plates:
                    m = re.search(r'<metadata key="index" value="(\d+)"', block)
                    if plate_index and m and int(m.group(1)) == int(plate_index):
                        chosen = block
                        break
                m = re.search(r'<metadata key="nozzle_diameters?" value="([^"]+)"', chosen)
                if m:
                    return [float(x) for x in re.split(r"[,;\s]+", m.group(1)) if x]
            if "Metadata/project_settings.config" in names:
                cfg = json.loads(zf.read("Metadata/project_settings.config").decode("utf-8", "replace"))
                value = cfg.get("nozzle_diameter")
                if isinstance(value, str):
                    value = re.split(r"[,;]", value)
                return [float(x) for x in value or [] if str(x).strip()]
    except (zipfile.BadZipFile, KeyError, ValueError, TypeError):
        pass
    return []


def ordinal(n):
    n = int(n)
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return "%d%s" % (n, suffix)


def queue_position(rows, printer, job_id=None):
    """(position, total) of a job among the jobs waiting for a printer, in dispatch order (priority, then position)."""
    waiting = [j for j in rows or [] if j.get("printer") == printer and j.get("state") == "queued"]
    waiting.sort(key=lambda j: (-(j.get("priority") or 0), j.get("position") or 0, j.get("id") or 0))
    ids = [j.get("id") for j in waiting]
    if job_id in ids:
        return ids.index(job_id) + 1, len(ids)
    return None, len(ids)


def preflight_blocks(reason, cfg=None):
    """Whether a preflight reason should stop a send. Busy, offline and plate not cleared only mean the job waits."""
    cfg = cfg or {}
    r = (reason or "").strip().lower()
    if r in ("busy", "offline", "plate not cleared"):
        return False
    if r.startswith("out of service"):
        return bool(cfg.get("maintenance_check", True))
    if r.startswith("missing filament"):
        return bool(cfg.get("filament_check", True))
    if re.match(r"needs an? [\d.]+ ?mm nozzle", r):
        return bool(cfg.get("nozzle_check", True))
    return True


def find_connection(cfg):
    """(OpenPrintHQ address, API key) from the plugin config; blanks when either is missing or malformed."""
    return normalize_ophq_url(cfg.get("ophq_url") or ""), resolve_api_key(cfg.get("ophq_key"))


def job_state(status):
    """Group a job status into queued, active, done, failed or cancelled."""
    st = (status or "").lower()
    if st in TERMINAL_OK:
        return "done"
    if st in TERMINAL_BAD:
        return "failed"
    if st in TERMINAL_CANCEL:
        return "cancelled"
    if st in ("printing", "running", "paused", "starting", "preparing", "dispatched", "uploading"):
        return "active"
    return "queued"


def job_materials(job):
    """Distinct material types a queued job needs, in tool order: ["PLA"], ["PLA", "PETG"]."""
    out = []
    for f in (job or {}).get("filaments") or []:
        t = (f.get("type") or "").strip().upper()
        if t and t not in out:
            out.append(t)
    if not out and (job or {}).get("material_type"):
        out.append(str(job["material_type"]).strip().upper())
    return out


def minutes_text(minutes):
    try:
        m = int(round(float(minutes)))
    except (TypeError, ValueError):
        return ""
    if m < 1:
        return "under a minute"
    if m < 60:
        return "%d min" % m
    return "%d h %02d min" % (m // 60, m % 60)


def queue_note(printer, status, ahead=0):
    """One line about when a job on this printer is likely to start."""
    if not status:
        return ""
    if not status.get("online", True) or (status.get("state") or "").lower() == "offline":
        return "%s is offline; the job waits until it is back." % printer
    parts = []
    if status.get("busy"):
        left = minutes_text(status.get("eta_minutes"))
        current = status.get("file") or "a job"
        parts.append("%s is printing %s%s" % (printer, current, (", about %s left" % left) if left else ""))
    if ahead:
        parts.append("%d earlier job%s from this OrcaSlicer %s ahead" % (ahead, "" if ahead == 1 else "s",
                                                                          "is" if ahead == 1 else "are"))
    if not parts:
        return "%s is free, so the job should start shortly." % printer
    return "; ".join(parts) + "."


class Hub:
    """State shared by the printer agent, the OpenPrintHQ tab and the job windows.

    Tracks jobs sent from this OrcaSlicer session, polls them in the background
    and raises notifications when they finish, fail or are cancelled.
    """

    def __init__(self):
        self.lock = threading.RLock()
        self.agent = None
        self.page = None
        self.who = ""                # e-mail of the API key's owner, from the Config page check
        self.windows = {}            # window id -> handle
        self.jobs = {}               # job id -> dict
        self.uploads = {}            # sha256 -> {"name", "size", "ref"}
        self.printers = []
        self.printers_at = 0.0
        self.error = ""
        self._session = None
        self._session_key = None
        self._cache = {}
        self.session_error = ""
        self._watch = None
        self._watch_stop = threading.Event()
        self._session = None
        self._session_key = None
        self._cache = {}
        self.session_error = ""
        self.hub_data = None         # the hub's summary (0.11.0)
        self.hub_error = ""
        self.hub_at = 0.0

    # ---- connection ------------------------------------------------
    def config(self):
        agent = self.agent
        if agent is not None:
            try:
                return agent._config()
            except Exception:
                pass
        return dict(DEFAULT_CONFIG)

    def client(self):
        """An OpenPrintHQClient for the Config page's address and key (one per address and key), else None."""
        cfg = self.config()
        base_url, api_key = find_connection(cfg)
        if not base_url or not api_key:
            return None
        key = (base_url, api_key)
        with self.lock:
            if getattr(self, "_client_key", None) != key or getattr(self, "_client", None) is None:
                self._client = OpenPrintHQClient(base_url, api_key, timeout=cfg.get("request_timeout_seconds", 30))
                self._client_key = key
                self._cache = {}
            return self._client

    # ---- jobs ---------------------------------------------------------
    def track(self, job_ids, name, printer, requested, priority, file_ref, mode, note=""):
        now = time.time()
        with self.lock:
            for jid in job_ids:
                self.jobs[int(jid)] = {"id": int(jid), "name": name, "printer": printer, "requested": requested,
                                       "priority": priority, "file_ref": file_ref, "mode": mode,
                                       "status": "queued", "state": "queued", "progress": 0,
                                       "created": now, "note": note, "notified": False}
        self.ensure_watch()

    def ahead_on(self, printer, before_id=None):
        """Jobs from this session still waiting or running on a printer."""
        with self.lock:
            return sum(1 for j in self.jobs.values()
                       if j["state"] in ("queued", "active") and (j.get("printer") == printer)
                       and (before_id is None or j["id"] < before_id))

    def update_job(self, jid, data):
        if not data:
            return
        with self.lock:
            job = self.jobs.get(int(jid))
            if job is None:
                return
            old = job["state"]
            job["status"] = data.get("status") or job["status"]
            job["state"] = job_state(job["status"])
            job["progress"] = data.get("progress") if data.get("progress") is not None else job["progress"]
            if data.get("assigned_printer"):
                job["printer"] = data["assigned_printer"]
            job["error"] = data.get("error_message") or ""
            changed = old != job["state"]
            finished = job["state"] in ("done", "failed", "cancelled") and not job["notified"]
            if finished:
                job["notified"] = True
            snapshot = dict(job)
        if finished and self.config().get("notify_job_events", True):
            self._announce(snapshot)
        return changed

    def _announce(self, job):
        where = job.get("printer") or job.get("requested") or "OpenPrintHQ"
        moved = job.get("requested") and job.get("printer") and job["printer"] != job["requested"]
        tail = " (sent to %s)" % job["requested"] if moved else ""
        if job["state"] == "done":
            _notify("job #%d '%s' finished on %s%s." % (job["id"], job["name"], where, tail), "PrintInfoNotificationLevel")
        elif job["state"] == "failed":
            why = (": " + job["error"]) if job.get("error") else ""
            _notify("job #%d '%s' failed on %s%s%s" % (job["id"], job["name"], where, tail, why or "."),
                    "ErrorNotificationLevel")
        else:
            _notify("job #%d '%s' was cancelled on %s%s." % (job["id"], job["name"], where, tail))

    def ensure_watch(self):
        with self.lock:
            if self._watch is not None and self._watch.is_alive():
                return
            stop = threading.Event()
            self._watch_stop = stop
            self._watch = threading.Thread(target=self._watch_loop, args=(stop,), name="openprinthq-jobs", daemon=True)
            self._watch.start()

    def _watch_loop(self, stop):
        idle_rounds = 0
        while not stop.wait(8):
            with self.lock:
                active = [j["id"] for j in self.jobs.values() if j["state"] in ("queued", "active")]
            if not active:
                idle_rounds += 1
                if idle_rounds > 3:
                    return
                continue
            idle_rounds = 0
            client = self.client()
            if client is None:
                continue
            for jid in active:
                if stop.is_set():
                    return
                try:
                    self.update_job(jid, client.job(jid, timeout=10))
                except OpenPrintHQError as exc:
                    if exc.code == 404:
                        self.update_job(jid, {"status": "cancelled", "error_message": "job no longer exists"})
                    elif exc.code == 429:
                        stop.wait(3)
                except Exception:
                    pass
            self.broadcast()

    # ---- snapshot for the tab and windows ------------------------------
    def refresh_printers(self, max_age=3.0):
        if time.time() - self.printers_at < max_age:
            return
        client = self.client()
        if client is None:
            with self.lock:
                self.error = "Not set up yet: add the OpenPrintHQ address and an API key on the plugin's Config page."
                self.printers_at = time.time()
            return
        try:
            printers = client.printers(timeout=10)
            with self.lock:
                self.printers, self.error = printers, ""
        except OpenPrintHQError as exc:
            with self.lock:
                self.error = str(exc)
        with self.lock:
            self.printers_at = time.time()

    def snapshot(self, job_ids=None):
        with self.lock:
            jobs = sorted(self.jobs.values(), key=lambda j: -j["id"])
            if job_ids is not None:
                wanted = {int(j) for j in job_ids}
                jobs = [j for j in jobs if j["id"] in wanted]
            by_name = {p.get("name"): p for p in self.printers}
            out_jobs = []
            for j in jobs:
                item = {k: j.get(k) for k in ("id", "name", "printer", "requested", "priority", "status",
                                              "state", "progress", "error", "mode")}
                if j["state"] == "queued":
                    item["note"] = queue_note(j["printer"], by_name.get(j["printer"]),
                                              self.ahead_on(j["printer"], j["id"]))
                out_jobs.append(item)
            snap = {"type": "state", "version": VERSION, "error": self.error, "printers": list(self.printers),
                    "jobs": out_jobs, "time": time.time()}
            if job_ids is None:
                snap.update(self._farm_view())
                snap["hub"] = self._hub_view()
            snap["look"] = self.look()
            return snap

    def _farm_view(self):
        """Farm queue, alerts and connection state for the tab, from cached reads (no network here)."""
        base_url, api_key = find_connection(self.config())
        cache = getattr(self, "_cache", {}) or {}

        def val(name):
            hit = cache.get(name)
            return hit[1] if hit else None
        view = {"signin": {"configured": bool(base_url and api_key), "ok": False, "user": "", "error": ""}}
        if not (base_url and api_key):
            return view
        queue = val("queue")
        view["signin"].update({"ok": queue is not None, "user": getattr(self, "who", "") or "",
                               "error": getattr(self, "session_error", "") if queue is None else ""})
        view["queue"] = queue
        view["nozzles"] = val("nozzles") or {}
        down = val("down") or []
        low = val("lowstock") or []
        stats = {}
        for row in queue or []:
            k = "printing" if row.get("state") == "active" else ("assigned" if row.get("status") == "assigned" else "queued")
            stats[k] = stats.get(k, 0) + 1
        view["alerts"] = {
            "down": [(d.get("name") or "") + (": " + d["reason"] if d.get("reason") else "") for d in down],
            "low": [" ".join(str(x) for x in (i.get("manufacturer"), i.get("material_type"), i.get("color_name")) if x)
                    for i in low],
            "stats": stats}
        return view

    def broadcast(self):
        page = self.page
        if page is not None:
            try:
                page.post_message(self.snapshot())
            except Exception:
                pass
        with self.lock:
            windows = list(self.windows.items())
        for wid, (handle, job_ids) in windows:
            try:
                if not handle.is_open():
                    with self.lock:
                        self.windows.pop(wid, None)
                    continue
                handle.post(self.snapshot(job_ids))
            except Exception:
                pass

    # ---- actions from the tab and windows -------------------------------
    def act(self, msg, reply):
        """Run a UI request off the UI thread, then reply(snapshot or error)."""
        def run():
            result = {"type": "ack", "cmd": msg.get("cmd"), "ok": True, "text": ""}
            try:
                result["text"] = self._do(msg) or ""
            except OpenPrintHQError as exc:
                result["ok"] = False
                hint = scope_hint(exc, "control")
                result["text"] = "%s%s" % (exc, hint)
            except Exception as exc:
                result["ok"] = False
                result["text"] = str(exc)
            try:
                reply(result)
            except Exception:
                pass
            self.broadcast()
        threading.Thread(target=run, name="openprinthq-action", daemon=True).start()

    def _requeue(self, client):
        with self.lock:
            self._cache.pop("queue", None)
        self.cached("queue", 0, client.queue)

    def _do(self, msg):
        cmd = msg.get("cmd")
        if str(cmd or "").startswith("hub_"):
            return self._do_hub(cmd, msg)
        if cmd == "refresh":
            self.refresh_printers(max_age=2.0)
            self.refresh_hub(max_age=20.0)
            c = self.client()
            if c is not None:
                self.cached("nozzles", 30, c.nozzles)
                self.cached("queue", 4, c.queue)
                if self.config().get("maintenance_check", True):
                    self.cached("down", 60, c.out_of_service)
                    self.cached("lowstock", 60, c.low_stock)
            return ""
        client = self.client()
        if client is None:
            raise OpenPrintHQError("no OpenPrintHQ connection; add the address and an API key on the plugin's Config page")
        if cmd == "reorder":
            order = [int(j) for j in msg.get("order") or []]
            if len(order) < 2 or len(set(order)) != len(order):
                raise OpenPrintHQError("nothing to reorder")
            client.reorder(order)
            self._requeue(client)
            return "Queue order saved."
        if cmd == "remove":
            jid = int(msg["id"])
            rows = self.cached("queue", 4, client.queue) or []
            row = next((j for j in rows if j.get("id") == jid), None)
            if row is not None and row.get("state") == "active":
                raise OpenPrintHQError("job #%d is printing; use Cancel to stop it" % jid)
            client.remove(jid)
            self._requeue(client)
            return "Job #%d removed from the queue." % jid
        if cmd in ("print_now", "clear_print"):
            jid = int(msg["id"])
            target = (msg.get("printer") or "").strip()
            if not target:
                raise OpenPrintHQError("pick a printer")
            note = client.print_now(jid, target, clear_plate=(cmd == "clear_print"))
            self._requeue(client)
            self.refresh_printers(max_age=0)
            if cmd == "clear_print" and note:
                return "Job #%d is first in line on %s; the bed was not marked clear: %s." % (jid, target, note)
            if cmd == "clear_print":
                return "Bed marked clear on %s; job #%d is first in line there." % (target, jid)
            return "Job #%d is first in line on %s (priority 100)." % (jid, target)
        if cmd == "priority":
            jid = int(msg["id"])
            pr = int(msg.get("priority"))
            if pr < 0 or pr > 100:
                raise OpenPrintHQError("priority must be 0 to 100")
            client.set_priority(jid, pr)
            self._requeue(client)
            return "Job #%d priority set to %d." % (jid, pr)
        if cmd == "set_nozzle":
            name = str(msg.get("printer") or "").strip()
            sizes = msg.get("sizes")
            if not name or not isinstance(sizes, list) or not sizes:
                raise OpenPrintHQError("no printer or nozzle size given")
            self.refresh_printers(max_age=0)
            with self.lock:
                p = next((x for x in self.printers if x.get("name") == name), None)
            if p is not None and p.get("busy"):
                raise OpenPrintHQError("%s is printing; change its nozzle when it is idle" % name)
            client.set_nozzles(name, [float(x) for x in sizes])
            with self.lock:
                getattr(self, "_cache", {}).pop("nozzles", None)
            self.cached("nozzles", 30, client.nozzles)
            label = " / ".join("%g" % float(x) for x in sizes)
            return "%s nozzle set to %s mm in OpenPrintHQ." % (name, label)
        if cmd == "set_ready":
            name = str(msg.get("printer") or "").strip()
            if not name:
                raise OpenPrintHQError("no printer given")
            try:
                client.clear_plate(name)
            except OpenPrintHQError as exc:
                if exc.code == 403:
                    raise OpenPrintHQError("OpenPrintHQ refused: this API key cannot control printers (it needs the "
                                           "control scope). Mark %s clear in OpenPrintHQ instead." % name, 403) from None
                raise
            self.refresh_printers(max_age=0)
            return "%s marked ready (bed clear). OpenPrintHQ can start its next job there." % name
        if cmd in ("pause", "resume", "cancel"):
            jid = int(msg["id"])
            client.job_action(jid, cmd)
            with self.lock:
                getattr(self, "_cache", {}).pop("queue", None)
            try:
                self.update_job(jid, client.job(jid, timeout=10))
            except OpenPrintHQError:
                pass
            return "Job #%d: %s sent." % (jid, cmd)
        if cmd == "copies":
            jid = int(msg["id"])
            n = int(msg.get("n") or 1)
            if n < 1 or n > 50:
                raise OpenPrintHQError("copies must be between 1 and 50")
            with self.lock:
                job = dict(self.jobs.get(jid) or {})
            if not job.get("file_ref"):
                raise OpenPrintHQError("job #%d was not sent from this OrcaSlicer session" % jid)
            target = job.get("requested") or job.get("printer")
            ids = (client.submit(job["file_ref"], job["priority"], target, quantity=n) or {}).get("job_ids") or []
            if not ids:
                raise OpenPrintHQError("OpenPrintHQ created no job")
            self.track(ids, job["name"], target, target, job["priority"], job["file_ref"], job["mode"])
            for wid, (handle, job_ids) in list(self.windows.items()):
                if jid in job_ids:
                    job_ids.extend(int(i) for i in ids)
            return "Added %d cop%s on %s (job%s %s)." % (n, "y" if n == 1 else "ies", target,
                                                         "" if len(ids) == 1 else "s",
                                                         ", ".join("#%s" % i for i in ids))
        raise OpenPrintHQError("unknown request: %s" % cmd)

    # ---- The hub (0.11.0) --------------------------------------------------
    def hub_client(self):
        cfg = self.config()
        url, key = hub_connection(cfg)
        if not url:
            return None
        return HubClient(url, key, timeout=min(20, cfg.get("request_timeout_seconds", 30)))

    def refresh_hub(self, max_age=20.0):
        """The hub's farm summary, at most every max_age seconds; None when the hub is not set up."""
        client = self.hub_client()
        if client is None:
            with self.lock:
                self.hub_data, self.hub_error, self.hub_at = None, "", 0.0
            return None
        with self.lock:
            if time.time() - (self.hub_at or 0.0) < max_age:
                return self.hub_data
        try:
            data, err = client.farm(timeout=15), ""
        except OpenPrintHQError as exc:
            data, err = None, str(exc)
        except Exception as exc:
            data, err = None, str(exc)
        with self.lock:
            if data is not None:
                self.hub_data = data
            self.hub_error, self.hub_at = err, time.time()
            return self.hub_data

    def _hub_view(self):
        url, _ = hub_connection(self.config())
        with self.lock:
            data, err = self.hub_data, self.hub_error
        view = {"configured": bool(url), "url": url, "ok": bool(url and data is not None and not err), "error": err}
        if url and data is not None:
            view["data"] = data
        return view

    def look(self):
        with self.lock:
            hub_look = (self.hub_data or {}).get("look") if self.hub_data else None
        return resolve_look(self.config(), hub_look)

    def photo_async(self, check_id, post):
        """Fetch a plate check's camera photo and post it to the page as a data URI."""
        def run():
            out = {"type": "photo", "id": int(check_id), "data": ""}
            try:
                client = self.hub_client()
                if client is None:
                    raise OpenPrintHQError("The hub is not connected")
                raw = client.photo(check_id)
                out["data"] = "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii")
            except Exception as exc:
                out["error"] = str(exc)
            try:
                post(out)
            except Exception:
                pass
        threading.Thread(target=run, name="hub-photo", daemon=True).start()

    def _do_hub(self, cmd, msg):
        client = self.hub_client()
        if client is None:
            raise OpenPrintHQError("The hub is not connected: add its address and an API key on the plugin's Config page")

        def fresh(text):
            with self.lock:
                self.hub_at = 0.0
            self.refresh_hub(max_age=0)
            return text

        if cmd == "hub_pass":
            cid = int(msg["id"])
            c = client.check_pass(cid, set_ready=msg.get("set_ready", True), note=msg.get("note") or None) or {}
            ready = " Ready is due at %s (staggered start)." % c["ready_due"][11:16] if c.get("ready_due") and not c.get("ready_set") \
                else (" %s set Ready." % c.get("printer") if c.get("ready_set") else "")
            if c.get("ready_error"):
                ready = " Setting Ready failed: %s" % c["ready_error"]
            return fresh("Plate on %s passed.%s" % (c.get("printer") or "the printer", ready))
        if cmd == "hub_fail":
            cid = int(msg["id"])
            reason = str(msg.get("reason") or "").strip()
            if not reason:
                raise OpenPrintHQError("pick why the print failed")
            c = client.check_fail(cid, reason, note=msg.get("note") or None, reprint=bool(msg.get("reprint")),
                                  set_ready=msg.get("set_ready", True)) or {}
            extra = " Reprint queued as job #%s." % c["reprint_job_id"] if c.get("reprint_job_id") else ""
            return fresh("Plate on %s recorded as failed (%s).%s" % (c.get("printer") or "the printer",
                                                                   c.get("reason_label") or reason, extra))
        if cmd == "hub_skip":
            client.check_skip(int(msg["id"]))
            return fresh("Plate check skipped.")
        if cmd == "hub_retry_ready":
            c = client.check_retry_ready(int(msg["id"])) or {}
            return fresh("Asked %s to be set Ready again." % (c.get("printer") or "the printer"))
        if cmd == "hub_maint_done":
            t = client.maint_done(int(msg["id"]), notes=msg.get("notes") or None) or {}
            return fresh("%s on %s marked done." % (t.get("title") or "Task", t.get("printer") or "the printer"))
        if cmd == "hub_prioritise":
            pr = int(msg.get("priority") or 75)
            if pr not in (25, 50, 75, 100):
                raise OpenPrintHQError("priority is 25, 50, 75 or 100")
            out = client.prioritise(int(msg["id"]), pr) or {}
            raised = out.get("raised")
            errs = out.get("errors") or []
            return fresh("Raised %s of the order's jobs to priority %d.%s" % (raised if raised is not None else "the",
                         pr, (" Not changed: " + "; ".join(str(e) for e in errs[:3])) if errs else ""))
        if cmd == "hub_measure":
            out = client.measure(int(msg.get("days") or 7)) or {}
            return fresh("Measured %s finished prints: %s from the printer, %s weighed, %s estimates." % (
                out.get("jobs", 0), out.get("printer", 0), out.get("weighed", 0), out.get("estimate", 0) + out.get("progress", 0)))
        if cmd == "hub_spool_sync":
            out = client.spool_sync() or {}
            moved = sum(len(v) for k, v in out.items() if isinstance(v, list) and k != "skipped")
            return fresh("Spool sync done: %d change%s." % (moved, "" if moved == 1 else "s"))
        if cmd == "hub_farm_sync":
            client.farm_sync()
            return fresh("The hub re-read the farm queue.")
        raise OpenPrintHQError("unknown hub request: %s" % cmd)

    # ---- signed-in features (v0.5) -------------------------------------------
    def session(self):
        """The OpenPrintHQ client: one API key covers everything the Legacy farm sign-in used to (0.12.0)."""
        return self.client()

    def reset_session(self):
        with self.lock:
            self._client, self._client_key, self._cache = None, None, {}

    def cached(self, name, max_age, fn):
        """Memoize a signed-in read for max_age seconds; None (and the error kept) on failure."""
        with self.lock:
            cache = getattr(self, "_cache", None)
            if cache is None:
                cache = self._cache = {}
            hit = cache.get(name)
            if hit and time.time() - hit[0] < max_age:
                return hit[1]
        try:
            value = fn()
            err = ""
        except OpenPrintHQError as exc:
            value, err = None, str(exc)
        except Exception as exc:
            value, err = None, str(exc)
        with self.lock:
            self._cache[name] = (time.time(), value)
            self.session_error = err
        return value

    def printer_extras(self, printer, printer_model=""):
        """Status fields that light up Orca's own nozzle check and Sync filaments button."""
        extras = {}
        mid = model_id_for(printer_model)
        if mid:
            extras["printer_type"] = mid
        client = self.client()
        if client is None:
            return extras
        cfg = self.config()
        sizes = (self.cached("nozzles", 60, client.nozzles) or {}).get(printer) or []
        if sizes and sizes[0] is not None:
            try:
                extras["nozzle_diameter"] = "%g" % float(sizes[0])
            except (TypeError, ValueError):
                pass
        if cfg.get("ams_sync", True):
            ams = {}
            try:
                live = client.raw_status(printer, timeout=10, max_age=20)
            except OpenPrintHQError:
                live = None
            if live:
                ams = ams_payload_from_status(live)
            if not ams.get("ams") and not ams.get("vt_tray"):
                rows = self.cached("assignments:" + printer, 20, lambda: client.assignments(printer)) or []
                ams = ams_payload_from_assignments(rows)
            extras.update(ams)
        return extras

    def preflight(self, printer, filaments, sliced_nozzles=None):
        """Problems worth stopping for before a job is sent, plus printers of the same model that could take it.

        One POST /queue/preflight answers for every printer. Busy, offline and plate-not-cleared are not
        problems here (the job waits in the queue); out of service, missing filament, nozzle size, a hardened
        nozzle for abrasive filament, the build plate and a file sliced for another model are.
        """
        client = self.client()
        empty = {"issues": [], "warnings": [], "alternatives": []}
        if client is None:
            return empty
        cfg = self.config()
        issues, warnings, alternatives = [], [], []
        types = sorted({(f.get("type") or "").upper() for f in filaments or [] if f.get("type")})
        nozzle = None
        if cfg.get("nozzle_check", True) and sliced_nozzles:
            nozzle = _num(sliced_nozzles[0])
        want_fil = bool(cfg.get("filament_check", True) and types)
        want_mnt = bool(cfg.get("maintenance_check", True))
        if nozzle or want_fil or want_mnt:
            try:
                answer = client.preflight(types if want_fil else [], nozzle, skip_filament=not want_fil)
            except OpenPrintHQError as exc:
                hint = scope_hint(exc, "control")
                answer = None
                warnings.append("Pre-send check unavailable: %s%s." % (exc, hint))
            rows = (answer or {}).get("printers") or []

            def hard(row):
                return [r for r in row.get("reasons") or [] if preflight_blocks(r, cfg)]
            mine = next((r for r in rows if r.get("name") == printer), None)
            problems = hard(mine) if mine is not None else []
            if problems:
                issues.append("%s: %s." % (printer, "; ".join(problems)))
                for r in rows:
                    if r.get("name") == printer or hard(r) or "offline" in (r.get("reasons") or []):
                        continue
                    if mine.get("model") and r.get("model") != mine.get("model"):
                        continue  # the file was sliced for a different printer model
                    alternatives.append(r.get("name"))
        if want_mnt and types:
            for item in self.cached("lowstock", 60, client.low_stock) or []:
                if (item.get("material_type") or "").upper() in types:
                    name = " ".join(str(x) for x in (item.get("manufacturer"), item.get("material_type"),
                                                     item.get("color_name")) if x)
                    left = _num(item.get("remaining_weight"))
                    warnings.append("Low stock: %s%s." % (name, (", %d g left" % int(left)) if left else ""))
        return {"issues": issues, "warnings": warnings, "alternatives": alternatives}

    def queue_jobs(self):
        client = self.client()
        if client is None:
            return None
        return self.cached("queue", 4, client.queue)

    def ask(self, html_title, issues, warnings, alternatives, printer, cancel_fn=None, timeout=300):
        """Modal pre-send prompt. Returns ("send", printer) or ("cancel", None)."""
        try:
            ui = orca.host.ui
        except Exception:
            return ("send", printer)
        done, box = threading.Event(), {}
        payload = {"title": html_title, "issues": issues, "warnings": warnings, "alternatives": alternatives,
                   "printer": printer}
        html = themed(PREFLIGHT_HTML.replace("__WT_INIT__", json.dumps(payload).replace("</", "<\\/")),
                      self.look(), hub_connection(self.config())[0])

        def on_submit(data):
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except ValueError:
                    data = {}
            box["r"] = data or {}
            done.set()

        def on_close():
            done.set()
        try:
            style = getattr(ui, "WINDOW_MODAL", 1)
            win = ui.create_window(html, title="OpenPrintHQ check", width=520, height=380, on_submit=on_submit,
                                   on_close=on_close, style=style)
        except Exception as exc:
            print("[openprinthq] could not show the pre-send check: %s" % exc)
            return ("send", printer)
        start = time.time()
        while not done.wait(0.25):
            if (cancel_fn and cancel_fn()) or time.time() - start > timeout:
                try:
                    win.close()
                except Exception:
                    pass
                return ("cancel", None)
        r = box.get("r") or {}
        if r.get("action") == "send":
            return ("send", r.get("printer") or printer)
        return ("cancel", None)

    # ---- duplicate uploads ----------------------------------------------
    def find_upload(self, client, upload_name, data):
        """(sha256, library file id or None, why) for identical bytes OpenPrintHQ already has."""
        digest = hashlib.sha256(data).hexdigest()
        with self.lock:
            hit = self.uploads.get(digest)
        if hit:
            return digest, hit["ref"], "already uploaded from this OrcaSlicer session"
        try:
            found = client.file_by_hash(digest)
        except OpenPrintHQError:
            found = None
        if found and found.get("id"):
            self.remember_upload(digest, upload_name, len(data), int(found["id"]))
            return digest, int(found["id"]), "identical file already in the OpenPrintHQ library"
        return digest, None, ""

    def remember_upload(self, digest, upload_name, size, ref):
        with self.lock:
            self.uploads[digest] = {"name": upload_name, "size": size, "ref": ref}

    def forget_upload(self, digest):
        with self.lock:
            self.uploads.pop(digest, None)

    # ---- job window -------------------------------------------------------
    def open_job_window(self, job_ids, title_line):
        try:
            ui = orca.host.ui
            html = themed(JOB_WINDOW_HTML, self.look(), hub_connection(self.config())[0]).replace("__WT_INIT__", json.dumps(
                {"title": title_line, "snapshot": self.snapshot(job_ids)}).replace("</", "<\\/"))
            holder = {}

            def on_message(msg):
                if isinstance(msg, str):
                    try:
                        msg = json.loads(msg)
                    except ValueError:
                        return
                handle = holder.get("h")
                if handle is None:
                    return
                if msg.get("cmd") == "refresh":
                    self.act(msg, lambda r: handle.post(self.snapshot(holder["ids"])))
                else:
                    self.act(msg, lambda r: handle.post(r))

            def on_close():
                with self.lock:
                    self.windows.pop(holder.get("id"), None)

            ids = [int(j) for j in job_ids]
            holder["ids"] = ids
            handle = ui.create_window(html, title="OpenPrintHQ", width=520, height=360,
                                      on_message=on_message, on_close=on_close)
            holder["h"] = handle
            holder["id"] = getattr(handle, "id", id(handle))
            with self.lock:
                self.windows[holder["id"]] = (handle, ids)
        except Exception as exc:
            print("[openprinthq] could not open the job window: %s" % exc)


HUB = Hub()


class OpenPrintHQAgent(orca.printer_agent.PrinterAgentBase):
    def __init__(self):
        super().__init__()
        self._lock = threading.RLock()
        self._callbacks = {}
        self._selected = ""
        self._dev_id = ""
        self._client = None
        self._printer = ""          # OpenPrintHQ printer name for the active preset
        self._family = None         # its engine connection_type (bambu, klipper, prusalink, ...)
        self._last_status = None
        self._busy_as_idle = True
        self._poll_stop = threading.Event()
        self._poll_thread = None
        self._extras = {}
        self._extras_at = 0.0
        self._printer_model = ""
        HUB.agent = self

    # ---- identity and config -------------------------------------------

    def get_name(self):
        return "OpenPrintHQ Print Queue"

    def get_filament_sync_mode(self):
        """Subscription lets Orca's Sync filaments button use the AMS data in our status pushes."""
        modes = getattr(orca.printer_agent, "FilamentSyncMode", None)
        if modes is None:
            return None
        cfg = self._config()
        base_url, api_key = find_connection(cfg)
        if cfg.get("ams_sync", True) and base_url and api_key:
            return getattr(modes, "Subscription", None)
        return getattr(modes, "None_", None)

    def get_default_config(self):
        return DEFAULT_CONFIG

    def get_agent_info(self):
        return orca.printer_agent.AgentInfo(AGENT_ID, AGENT_NAME, VERSION,
                                            "Send prints to an OpenPrintHQ print farm queue")

    def _drop_legacy_secrets(self, stored, readable):
        """Once per Orca session: delete the Legacy farm API key and password (0.11 and older) from the OS
        credential store and blank them in Orca's config. Returns the config to save, or None."""
        if getattr(self, "_legacy_checked", False):
            return None
        self._legacy_checked = True
        found = False
        for k in LEGACY_SECRET_KEYS:
            try:
                if SECRETS.available() and SECRETS.get(k):
                    SECRETS.delete(k)
                    found = True
            except Exception as exc:  # noqa: BLE001 - a store problem must not block the config
                print("[openprinthq] could not remove the old Legacy farm secret %s: %s" % (k, exc))
        save = None
        if readable and any(stored.get(k) for k in LEGACY_SECRET_KEYS):
            save = dict(stored)
            for k in LEGACY_SECRET_KEYS:
                save[k] = ""
            found = True
        if found or any(stored.get(k) for k in ("base_url", "wt_username")):
            self._legacy_found = True
        return save

    def _config(self):
        """Merged config with secrets filled in from the OS credential store.

        Plain-text secrets found in Orca's config (a new value typed on the Config page,
        or a config from before 0.7.0) are moved into the store and blanked in the file.
        Legacy farm settings from 0.11 and older are ignored; their secrets are deleted once.
        """
        cfg = dict(DEFAULT_CONFIG)
        stored = {}
        readable = False
        try:
            loaded = json.loads(self.get_config())
            if isinstance(loaded, dict):
                stored = loaded
                readable = True
        except Exception:
            # Orca refuses get_config() until the capability is loaded (RuntimeError).
            # An unreadable config is unknown, not empty: never write it back or sync it,
            # or defaults would be published over the real settings.
            pass
        cleaned = self._drop_legacy_secrets(stored, readable)
        if cleaned is not None:
            try:
                self.save_config(json.dumps(cleaned))
                stored = cleaned
            except Exception as exc:
                print("[openprinthq] could not blank the old Legacy farm secrets: %s" % exc)
        use, save, status = migrate_secrets(stored, SECRETS, SECRET_KEYS)
        if save is not None and readable:
            try:
                self.save_config(json.dumps(save))
            except Exception as exc:
                print("[openprinthq] could not rewrite config after moving secrets: %s" % exc)
        self._secret_status = status
        # Settings sync (non-secret keys only) through the carrier preset, at most every SYNC_INTERVAL_S.
        now = time.time()
        if readable and now - getattr(self, "_last_sync", 0) >= SYNC_INTERVAL_S:
            self._last_sync = now
            base = save if save is not None else stored
            synced = SETTINGS_SYNC.sync(base, DEFAULT_CONFIG)
            if synced is not None:
                try:
                    self.save_config(json.dumps(synced))
                except Exception as exc:
                    print("[openprinthq] could not save synced settings: %s" % exc)
                for k, v in synced.items():
                    if k not in SECRET_KEYS:
                        use[k] = v
        cfg.update(use)
        self._managed = apply_managed(cfg)
        cfg.pop("_sync", None)
        for k in LEGACY_KEYS:
            cfg.pop(k, None)
        if not isinstance(cfg.get("printers"), dict):
            cfg["printers"] = {}
        return cfg

    # ---- config page ---------------------------------------------------

    def has_config_ui(self):
        return True

    def get_config_ui(self):
        try:
            data = self._config_page_data()
        except Exception as exc:  # the page must still open, with whatever we know
            data = {"presets": [], "ophq": {"ok": False, "error": "could not gather setup details: %s" % exc,
                                            "base_url": "", "printers": []}}
        data["defaults"] = DEFAULT_CONFIG
        data["version"] = VERSION
        data["sync"] = dict(SETTINGS_SYNC.status)
        data["secrets"] = {"backend": SECRETS.backend, "status": getattr(self, "_secret_status", {}),
                           "error": SECRETS.error if not SECRETS.available() else ""}
        data["agent_id"] = AGENT_ID
        data["managed"] = getattr(self, "_managed", None) or {"url": "", "key": False}
        blob = json.dumps(data).replace("</", "<\\/")
        page = CONFIG_PAGE_HTML.replace("__WT_DATA__", blob).replace("<style>", "<style>" + SHARED_CSS, 1)
        return themed(page, HUB.look(), hub_connection(self._config())[0])


    def _config_page_data(self):
        data = self._config_page_data_wt()
        data["hub"] = self._hub_page_status()
        return data

    def _hub_page_status(self):
        """A live hub check for the Config page (the page itself cannot reach the hub)."""
        cfg = self._config()
        url, key = hub_connection(cfg)
        status = {"configured": bool(url), "url": url, "ok": False, "error": "", "team": "", "look": None,
                  "has_key": bool((cfg.get("hub_key") or "").strip())}
        if not url:
            return status
        try:
            data = HubClient(url, key, timeout=6).farm(timeout=6) or {}
            status.update({"ok": True, "team": data.get("team") or "", "look": data.get("look"),
                           "can_act": bool(data.get("can_act"))})
            with HUB.lock:
                HUB.hub_data, HUB.hub_error, HUB.hub_at = data, "", time.time()
        except OpenPrintHQError as exc:
            status["error"] = str(exc)
        return status

    def _config_page_data_wt(self):
        """Printer presets and a live OpenPrintHQ check, embedded into the config page.

        The page itself is sandboxed and cannot reach OpenPrintHQ, so this runs here,
        when Orca opens the Config tab.
        """
        cfg = self._config()
        presets = []
        try:
            collection = orca.host.preset_bundle().printers
            for name in collection.preset_names():
                preset = collection.find_preset(name)
                if preset is None:
                    continue
                agent = _unquote(preset.config_value("printer_agent"))
                visible = bool(getattr(preset, "is_visible", True))
                if not (visible or agent == AGENT_ID or name in cfg["printers"]):
                    continue
                presets.append({
                    "name": name,
                    "agent": agent,
                    "host": _unquote(preset.config_value("print_host")),
                    "route": routing_printer(_unquote(preset.config_value("printhost_apikey"))),
                    "user": bool(preset.is_user()) if hasattr(preset, "is_user") else False,
                })
        except Exception as exc:
            print("[openprinthq] could not list printer presets: %s" % exc)

        base_url, api_key = find_connection(cfg)
        status = {"ok": False, "error": "", "base_url": base_url, "api": "", "printers": [], "has_key": bool(api_key),
                  "user": "", "scope": "", "legacy": bool(getattr(self, "_legacy_found", False))}
        if not base_url:
            status["error"] = "no OpenPrintHQ address yet: enter it below"
            return {"presets": presets, "ophq": status}
        if not api_key:
            status["error"] = "no OpenPrintHQ API key yet: enter one (ophqk_...) below"
            return {"presets": presets, "ophq": status}
        client = OpenPrintHQClient(base_url, api_key, timeout=6)
        status["api"] = client.api
        try:
            me = client.me(timeout=6) or {}
            status["user"] = me.get("email") or ""
            status["scope"] = (me.get("apiKey") or {}).get("scope") or ("engine key" if api_key.startswith("bb_") else "")
            status["printers"] = [{"name": p.get("name", ""), "family": p.get("family"), "model": p.get("model"),
                                   "state": p.get("state"), "online": bool(p.get("online"))}
                                  for p in client.printers(timeout=6)]
            status["ok"] = True
            HUB.who = status["user"]
        except OpenPrintHQError as exc:
            status["error"] = str(exc)
        return {"presets": presets, "ophq": status}

    # ---- callbacks -----------------------------------------------------

    def _set_cb(self, name, fn):
        with self._lock:
            self._callbacks[name] = fn
        return SUCCESS

    def set_server_callback(self, fn): return self._set_cb("server_err", fn)
    def set_on_ssdp_msg_fn(self, fn): return self._set_cb("ssdp", fn)
    def set_on_printer_connected_fn(self, fn): return self._set_cb("printer_connected", fn)
    def set_on_subscribe_failure_fn(self, fn): return self._set_cb("subscribe_failure", fn)
    def set_on_message_fn(self, fn): return self._set_cb("message", fn)
    def set_on_user_message_fn(self, fn): return self._set_cb("user_message", fn)
    def set_on_local_connect_fn(self, fn): return self._set_cb("local_connect", fn)
    def set_on_local_message_fn(self, fn): return self._set_cb("local_message", fn)
    def set_queue_on_main_fn(self, fn): return self._set_cb("queue_on_main", fn)

    def _on_main(self, fn):
        queue = self._callbacks.get("queue_on_main")
        if queue:
            queue(fn)
        else:
            fn()

    def _dispatch_message(self, dev_id, payload):
        local = self._callbacks.get("local_message")
        cloud = self._callbacks.get("message")
        target = local or cloud
        if not target:
            return
        text = json.dumps(payload)
        self._on_main(lambda: target(dev_id, text))

    def _dispatch_connected(self, dev_id):
        local = self._callbacks.get("local_connect")
        connected = self._callbacks.get("printer_connected")
        if local:
            self._on_main(lambda: local(CONNECT_OK, dev_id, "0"))
        if connected:
            self._on_main(lambda: connected(dev_id))

    # ---- connection ----------------------------------------------------

    def _active_preset(self):
        """(name, print_host, api_key) of the edited printer preset, or blanks."""
        try:
            preset = orca.host.preset_bundle().current_printer_preset()
            return (preset.name, _unquote(preset.config_value("print_host")),
                    _unquote(preset.config_value("printhost_apikey")))
        except Exception as exc:
            print("[openprinthq] could not read the printer preset: %s" % exc)
            return ("", "", "")

    def connect_printer(self, params):
        cfg = self._config()
        preset_name, print_host, preset_key = self._active_preset()
        base_url, api_key = find_connection(cfg)
        if not base_url or not api_key:
            legacy = " This version talks to OpenPrintHQ; Legacy farm support was removed in 0.12.0." \
                if getattr(self, "_legacy_found", False) else ""
            _notify("not set up: enter the OpenPrintHQ address and an API key (ophqk_...) on the plugin's Config page "
                    "under Plugins, OpenPrintHQ Print Queue." + legacy, "ErrorNotificationLevel")
            return ERR_INVALID_HANDLE

        client = HUB.client() or OpenPrintHQClient(base_url, api_key, timeout=cfg["request_timeout_seconds"])
        try:
            # Synchronous on purpose: this runs on the UI thread, so the one-time
            # network approval prompt (if any) appears there.
            names = [p.get("name", "") for p in client.printer_rows(timeout=8)]
        except OpenPrintHQError as exc:
            _notify("connection to %s failed: %s" % (base_url, exc), "ErrorNotificationLevel")
            return ERR_CONNECTION

        printer, how = resolve_printer(preset_name, cfg["printers"], names, routing_printer(preset_key))
        if not printer:
            _notify(how + ". OpenPrintHQ printers: " + (", ".join(names) or "none visible to this key"),
                    "ErrorNotificationLevel")
            return ERR_CONNECTION
        if printer not in names:
            _notify("mapped printer '%s' is not in OpenPrintHQ. Printers: %s" % (printer, ", ".join(names)),
                    "ErrorNotificationLevel")
            return ERR_CONNECTION

        try:
            status = client.printer(printer, timeout=8)  # first audited call to the URL the poller uses
        except OpenPrintHQError as exc:
            status = None
            print("[openprinthq] initial status for %s failed: %s" % (printer, exc))

        self._stop_poller()
        with self._lock:
            self._client = client
            self._printer = printer
            self._family = (status or {}).get("family") or (client.printer_row(printer).get("connection_type") or "bambu")
            self._dev_id = params.dev_id
            self._last_status = status
        try:
            self._printer_model = _unquote(orca.host.preset_bundle().current_printer_preset()
                                           .config_value("printer_model"))
        except Exception:
            self._printer_model = ""
        self._extras = {"printer_type": model_id_for(self._printer_model)} if model_id_for(self._printer_model) else {}
        self._extras_at = 0.0
        print("[openprinthq] %s -> %s (%s) at %s" % (preset_name, printer, how, base_url))
        self._busy_as_idle = bool(cfg.get("report_busy_as_idle", True))

        dev_id = params.dev_id
        self._send_version(dev_id)
        self._dispatch_connected(dev_id)
        if status:
            self._dispatch_message(dev_id, build_status_payload(status, self._busy_as_idle, self._extras))
        self._start_poller(dev_id, cfg)
        return SUCCESS

    def disconnect_printer(self):
        self._stop_poller()
        with self._lock:
            self._client = None
            self._printer = ""
            self._family = None
        return SUCCESS

    def _start_poller(self, dev_id, cfg):
        interval = max(2.0, float(cfg.get("status_poll_seconds") or 5))
        stop = threading.Event()
        self._poll_stop = stop

        def run():
            while not stop.wait(interval):
                with self._lock:
                    client, printer = self._client, self._printer
                if not client or not printer:
                    return
                try:
                    status = client.printer(printer, timeout=10)
                except OpenPrintHQError as exc:
                    if exc.code == 429:
                        stop.wait(2)
                    continue
                except Exception:
                    continue
                if stop.is_set():
                    return
                with self._lock:
                    self._last_status = status
                    self._family = status.get("family") or self._family
                self._refresh_extras(printer)
                self._dispatch_message(dev_id, build_status_payload(status, self._busy_as_idle, self._extras))

        thread = threading.Thread(target=run, name="openprinthq-status", daemon=True)
        self._poll_thread = thread
        thread.start()

    def _refresh_extras(self, printer, force=False):
        """Nozzle size and AMS slots, at most every 30 s."""
        if not force and time.time() - self._extras_at < 30:
            return
        self._extras_at = time.time()
        try:
            extras = HUB.printer_extras(printer, self._printer_model)
        except Exception as exc:
            print("[openprinthq] could not read printer details: %s" % exc)
            return
        with self._lock:
            self._extras = extras

    def _stop_poller(self):
        self._poll_stop.set()
        thread = self._poll_thread
        self._poll_thread = None
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2)

    def _send_version(self, dev_id):
        self._dispatch_message(dev_id, {"info": {"command": "get_version", "result": "success", "module": [
            {"name": "ota", "sw_ver": VERSION, "product_name": "OpenPrintHQ"}]}})

    # ---- discovery, binding, selection --------------------------------

    def start_discovery(self, start, sending):
        return True

    def bind_detect(self, dev_ip, sec_link, detect):
        detect.dev_id = self._dev_id or dev_ip
        detect.dev_name = self._printer or dev_ip
        detect.connect_type = "lan"
        detect.bind_state = "free"
        return SUCCESS

    def get_user_selected_machine(self):
        return self._selected

    def set_user_selected_machine(self, dev_id):
        self._selected = dev_id
        return SUCCESS

    def check_cert(self): return SUCCESS
    def ping_bind(self, ping_code): return SUCCESS
    def bind(self, dev_ip, dev_id, dev_model, sec_link, timezone, improved, update_fn): return SUCCESS
    def unbind(self, dev_id): return SUCCESS

    # ---- messages from Orca's device UI -------------------------------

    def send_message(self, dev_id, json_str, qos, flag):
        return self._handle_request(dev_id, json_str)

    def send_message_to_printer(self, dev_id, json_str, qos, flag):
        return self._handle_request(dev_id, json_str)

    def _handle_request(self, dev_id, json_str):
        try:
            req = json.loads(json_str)
        except ValueError:
            return ERR_INVALID_RESULT
        if req.get("info", {}).get("command") == "get_version":
            self._send_version(dev_id)
            return SUCCESS
        if req.get("system", {}).get("command") == "get_access_code":
            self._dispatch_message(dev_id, {"system": {"command": "get_access_code",
                                                       "access_code": PLACEHOLDER_ACCESS_CODE}})
            return SUCCESS
        command = req.get("print", {}).get("command")
        if command == "pushall":
            with self._lock:
                status = self._last_status
            if status:
                self._dispatch_message(dev_id, build_status_payload(status, self._busy_as_idle, self._extras))
            return SUCCESS
        if command in ("pause", "resume", "stop"):
            return self._job_control(command)
        # Temperatures, jog, home and the like stay unsupported here: they belong in
        # OpenPrintHQ, which checks what each printer type can do.
        return SUCCESS

    def _job_control(self, action):
        """Orca's pause, resume and stop buttons act on the printer (POST /printers/{id}/print/...)."""
        with self._lock:
            client, printer, status = self._client, self._printer, self._last_status
        if not client or not printer or not (status or {}).get("busy"):
            _notify("nothing is printing on %s, so there is nothing to %s." % (printer or "this printer", action))
            return ERR_INVALID_RESULT
        try:
            client.request("POST", "/printers/%d/print/%s" % (client.printer_id(printer), action))
        except OpenPrintHQError as exc:
            _notify("%s on %s failed: %s%s" % (action, printer, exc, scope_hint(exc, "control")), "ErrorNotificationLevel")
            return ERR_PRINT_PUBLISH
        return SUCCESS

    # ---- printing ------------------------------------------------------

    def start_local_print(self, params, update_fn, cancel_fn):
        return self._submit(params, update_fn, cancel_fn, mode="now")

    def start_print(self, params, update_fn, cancel_fn, wait_fn):
        return self._submit(params, update_fn, cancel_fn, mode="now")

    def start_local_print_with_record(self, params, update_fn, cancel_fn, wait_fn):
        return self._submit(params, update_fn, cancel_fn, mode="now")

    def start_send_gcode_to_sdcard(self, params, update_fn, cancel_fn, wait_fn):
        if params.project_name == "verify_job":
            # Orca's LAN pre-flight check; never turn it into a job.
            with self._lock:
                client = self._client
            if not client:
                return ERR_CONNECTION
            try:
                client.ping(timeout=8)
            except OpenPrintHQError:
                return ERR_CONNECTION
            return SUCCESS
        return self._submit(params, update_fn, cancel_fn, mode="queue")

    def start_sdcard_print(self, params, update_fn, cancel_fn):
        _notify("printing a file already on the printer is not supported; use Print or Send.")
        return ERR_INVALID_RESULT

    def _submit(self, params, update_fn, cancel_fn, mode):
        def update(stage, code, info):
            if update_fn:
                try:
                    update_fn(stage, code, info)
                except Exception:
                    pass

        def cancelled():
            try:
                return bool(cancel_fn and cancel_fn())
            except Exception:
                return False

        upload_err = ERR_PRINT_UPLOAD if mode == "now" else ERR_SEND_UPLOAD
        with self._lock:
            client, printer, family = self._client, self._printer, self._family
        if not client or not printer:
            _notify("not connected. Select the printer again so the plugin can reach OpenPrintHQ.",
                    "ErrorNotificationLevel")
            return ERR_CONNECTION
        cfg = self._config()
        priority = cfg["print_now_priority"] if mode == "now" else cfg["queue_priority"]

        update(STAGE_CREATE, 0, "Preparing...")
        if not family:
            # The format decision below depends on how OpenPrintHQ talks to the printer.
            try:
                family = client.printer_row(printer).get("connection_type") or "bambu"
            except OpenPrintHQError as exc:
                _notify("cannot read %s from OpenPrintHQ: %s" % (printer, exc), "ErrorNotificationLevel")
                return ERR_CONNECTION
            with self._lock:
                self._family = family
        path = params.dst_file if getattr(params, "dst_file", "") else params.filename
        if not path:
            return ERR_FILE_NOT_EXIST
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except FileNotFoundError:
            _notify("sliced file not found: %s" % path, "ErrorNotificationLevel")
            return ERR_FILE_NOT_EXIST
        except PermissionError:
            _notify("OrcaSlicer did not allow the plugin to read the sliced file.", "ErrorNotificationLevel")
            return ERR_FILE_NOT_EXIST
        if cancelled():
            return ERR_CANCELED

        name = job_basename(params, path)
        kind = file_kind(data)
        raw_3mf = data if kind == "3mf" else None
        if kind == "3mf" and family != "bambu":
            gcode = extract_plate_gcode(data, getattr(params, "plate_index", None))
            if gcode is None:
                _notify("could not find the plate G-code inside the sliced 3MF.", "ErrorNotificationLevel")
                return ERR_INVALID_RESULT
            data, kind = gcode, "gcode"
        if family == "bambu" and kind != "3mf":
            _notify("%s is a Bambu printer and needs a .3mf; enable 'Use 3MF instead of G-code' "
                    "in the printer preset." % printer, "ErrorNotificationLevel")
            return ERR_INVALID_RESULT
        upload_name = name + {"3mf": ".gcode.3mf", "bgcode": ".bgcode", "gcode": ".gcode"}[kind]

        if cfg.get("filament_check", True) or cfg.get("maintenance_check", True) or cfg.get("nozzle_check", True):
            update(STAGE_CREATE, 0, "Checking filament and printer status")
            source = raw_3mf if raw_3mf is not None else b""
            plate = getattr(params, "plate_index", None)
            needs = plate_filaments(source, plate) if source else []
            sliced = plate_nozzles(source, plate) if source else []
            try:
                check = HUB.preflight(printer, needs, sliced)
            except Exception as exc:
                check = {"issues": [], "warnings": ["Pre-send check failed: %s." % exc], "alternatives": []}
            if check["issues"]:
                action, target = HUB.ask("Before sending to %s" % printer, check["issues"], check["warnings"],
                                         check["alternatives"], printer, cancelled)
                if action != "send":
                    _notify("not sent: you stopped it at the pre-send check.")
                    return ERR_CANCELED
                if target and target != printer:
                    printer = target
            elif check["warnings"]:
                _notify(" ".join(check["warnings"]))

        copies = cfg.get("default_copies") or 1
        try:
            copies = max(1, min(50, int(copies)))
        except (TypeError, ValueError):
            copies = 1

        dedupe = cfg.get("skip_duplicate_uploads", True)
        digest, file_id, reused_why = hashlib.sha256(data).hexdigest(), None, ""
        if dedupe:
            update(STAGE_UPLOAD, 0, "Checking the OpenPrintHQ library")
            digest, file_id, reused_why = HUB.find_upload(client, upload_name, data)

        def do_upload():
            update(STAGE_UPLOAD, 0, "Uploading to OpenPrintHQ")
            uploaded = client.upload(upload_name, data, reuse=dedupe) or {}
            if not uploaded.get("id"):
                raise OpenPrintHQError("upload succeeded but OpenPrintHQ returned no file id")
            HUB.remember_upload(digest, upload_name, len(data), int(uploaded["id"]))
            why = "identical file already in the OpenPrintHQ library" if uploaded.get("reused") else ""
            return int(uploaded["id"]), why

        if not file_id:
            try:
                file_id, reused_why = do_upload()
            except OpenPrintHQError as exc:
                _notify("upload failed: %s%s" % (exc, scope_hint(exc, "submit")), "ErrorNotificationLevel")
                return upload_err
        update(STAGE_UPLOAD, 100, "Uploaded" if not reused_why else "Already in OpenPrintHQ, not uploaded again")
        if cancelled():
            _notify("cancelled after upload; %s is in the OpenPrintHQ library but was not queued." % upload_name)
            return ERR_CANCELED

        update(STAGE_SENDING, 0, "Adding to the queue")
        result = None
        for attempt in (1, 2):
            try:
                result = client.submit(file_id, priority, printer, quantity=copies, at_top=(mode == "now")) or {}
                break
            except OpenPrintHQError as exc:
                if attempt == 1 and reused_why and exc.code in (400, 404, 422):
                    # The library copy we pointed at is gone (deleted or in the trash): upload after all.
                    HUB.forget_upload(digest)
                    reused_why = ""
                    try:
                        file_id, reused_why = do_upload()
                    except OpenPrintHQError as up_exc:
                        _notify("upload failed: %s" % up_exc, "ErrorNotificationLevel")
                        return upload_err
                    continue
                _notify("adding the job to the queue failed: %s%s" % (exc, scope_hint(exc, "submit")),
                        "ErrorNotificationLevel")
                return ERR_PRINT_PUBLISH
        job_ids = (result or {}).get("job_ids") or []
        if not job_ids:
            _notify("OpenPrintHQ accepted the request but created no job.", "ErrorNotificationLevel")
            return ERR_PRINT_PUBLISH

        job_id = job_ids[0]
        with self._lock:
            status = (self._last_status or {}) if printer == self._printer else {}
        ahead = HUB.ahead_on(printer)
        HUB.track(job_ids, name, printer, printer, priority, file_id, mode)
        try:
            HUB.update_job(job_id, client.job(job_id, timeout=8))
        except OpenPrintHQError:
            pass
        what = "Print now" if mode == "now" else "Queued"
        ids_text = ", ".join("#%s" % j for j in job_ids)
        count = (" x%d" % len(job_ids)) if len(job_ids) > 1 else ""
        note = queue_note(printer, status, ahead)
        with HUB.lock:
            HUB._cache.pop("queue", None)
        jobs_now = HUB.queue_jobs()
        if jobs_now:
            pos, total = queue_position(jobs_now, printer, job_id)
            if pos:
                note = ("%s; " % note.rstrip(".") if note else "") + "%s in line for %s%s." % (
                    ordinal(pos), printer, (" (of %d waiting)" % total) if total > 1 else "")
        extra = (" Not uploaded again: %s." % reused_why) if reused_why else ""
        _notify("%s: job %s '%s'%s on %s, priority %s. %s%s" % (what, ids_text, name, count, printer, priority,
                                                               note, extra), "PrintInfoNotificationLevel")
        if cfg.get("show_job_window", True):
            HUB.open_job_window(job_ids, "%s on %s" % ("Printing next" if mode == "now" else "Queued", printer))
        update(STAGE_FINISHED, 100, "3")
        return SUCCESS

CONFIG_PAGE_HTML = r"""<!doctype html>
<html><head><meta charset="utf-8">
<style>

body{padding:22px max(24px, calc((100vw - 1180px) / 2)) 26px}
p{margin:0 0 8px}
section{padding:16px 18px;margin:0 0 14px}
details>summary{cursor:pointer;font-weight:600;list-style:none}
details>summary::-webkit-details-marker{display:none}
details>summary::before{content:"\25B8";display:inline-block;width:14px;transition:transform .15s}
details[open]>summary::before{transform:rotate(90deg)}
ol{margin:8px 0 4px 18px;padding:0}
li{margin:0 0 6px}
code,kbd{font:12px var(--ah-font-mono);background:var(--ah-panel);border:1px solid var(--ah-panel-line);padding:0 5px;border-radius:6px}
.status{display:flex;gap:10px;align-items:flex-start}
.dot{width:10px;height:10px;border-radius:50%;margin-top:5px;flex:none}
.dot.ok{background:var(--ah-success)}.dot.bad{background:var(--ah-failure)}.dot.warn{background:var(--ah-attention)}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.chip{display:inline-flex;align-items:center;border:1px solid var(--ah-field-line);border-radius:999px;padding:2px 9px;font-size:12px;line-height:18px;
background:linear-gradient(var(--ah-chip-neutral-bg),var(--ah-chip-neutral-bg)),var(--ah-chip-underlay)}
.chip.off{color:var(--ah-text-3)}
table{width:100%;border-collapse:collapse;margin-top:6px}
th{text-align:left;font-weight:600;color:var(--ah-text-2);font-size:12px;padding:7px 8px;border-bottom:1px solid var(--ah-row-line)}
td{padding:8px;border-bottom:1px solid var(--ah-row-line);vertical-align:top}
tr:last-child td{border-bottom:none}
.pname{font-weight:600;word-break:break-word}
.note.warn{color:var(--ah-chip-warn-text)}.note.bad{color:var(--ah-chip-bad-text)}.note.ok{color:var(--ah-chip-ok-text)}
select{min-width:170px;max-width:100%;height:38px}
input{height:38px}
input[type=number]{width:96px}
input[type=text],input[type=password]{width:100%}
input:invalid{border-color:var(--ah-failure)}
.grid{display:grid;grid-template-columns:auto 1fr;gap:10px 16px;align-items:center}
.grid>label{color:var(--ah-text-2)}
.grid .help{grid-column:2;font-size:12px;color:var(--ah-text-3);margin-top:-6px}
button{height:38px;padding:0 16px;border-radius:var(--ah-radius-btn);font-size:14px}
button.link{border:none;background:none;color:var(--ah-link);padding:0 4px;height:auto;font-weight:600}
.bar{display:flex;gap:8px;align-items:center;justify-content:flex-end;padding-top:4px}
.bar .msg{margin-right:auto;font-size:12px}
.addrow{display:flex;gap:8px;align-items:center;margin-top:10px;flex-wrap:wrap}
</style></head>
<body>
<section>
  <h2>OpenPrintHQ Print Queue</h2>
  <p class="muted">Sends sliced plates from OrcaSlicer to an OpenPrintHQ print farm. <b>Print</b> puts the job first in line on
  the mapped printer; Orca's <i>Send to printer</i> adds it to the end of that printer's queue. Both upload the file to your
  OpenPrintHQ library (or reuse an identical file already there) and queue it there.</p>
  <div id="legacy" class="note warn" style="display:none">This version talks to OpenPrintHQ. Legacy farm support was removed in
  0.12.0, and the saved Legacy farm API key and password were deleted from this computer. Enter your OpenPrintHQ address and
  API key below.</div>
  <details id="howto">
    <summary>Setup directions</summary>
    <ol>
      <li><b>Make an API key in OpenPrintHQ</b>: Settings, <i>API keys</i>, scope <b>control</b> (the pre-send check, the
        queue controls, Set ready and nozzle changes need it; <b>submit</b> is enough to print). Copy the
        <code>ophqk_...</code> key; it is shown once.</li>
      <li><b>Enter the OpenPrintHQ address and the key</b> below, for example <code>https://openprinthq.com</code>, and
        click <b>Save</b>.</li>
      <li><b>Point the printer preset at this plugin.</b> Preferences, <i>Developer</i>: check <i>Use printer agents
        instead of print hosts</i>. Printer settings, <i>Basic information, Advanced</i>: set <i>Printer Agent</i> to
        <b>OpenPrintHQ</b>, give the preset any print host (OrcaSlicer only uses a printer agent when the host is not
        empty; the plugin uses the address on this page), and save the preset. Do the same for each nozzle size you use.</li>
      <li><b>Map the preset to an OpenPrintHQ printer</b> in the table below. Presets left on <i>Auto</i> use the one
        printer whose name appears in the preset name.</li>
      <li><b>Print.</b> Slice, then press <b>Print</b> and pick the printer in Orca's dialog. The first request and the
        first file the plugin reads each show a one-time OrcaSlicer permission prompt; answer Yes.</li>
    </ol>
    <p class="muted">Bambu printers receive the <code>.gcode.3mf</code>; other printers receive the plate's G-code.</p>
  </details>
</section>

<section>
  <h2>OpenPrintHQ</h2>
  <div class="grid">
    <label for="ourl">Address</label><div><input id="ourl" type="text" autocomplete="off" spellcheck="false" placeholder="https://openprinthq.com"></div>
    <label for="akey">API key</label>
    <div style="display:flex;gap:6px"><input id="akey" type="password" autocomplete="off" spellcheck="false" placeholder="ophqk_..."><button id="akeyshow" class="link" type="button">Show</button><button id="akeyforget" class="link" type="button" style="display:none">Forget</button></div>
    <div class="help">An OpenPrintHQ key (<code>ophqk_...</code>), or an engine's own key (<code>bb_...</code>) for an engine reached without OpenPrintHQ in front. <span id="akeywhere"></span></div>
  </div>
</section>

<section id="conn"></section>

<section id="syncsec"></section>

<section>
  <h2>Checks and filament sync</h2>
  <div class="grid">
    <label for="fams">Sync filaments</label><div><input id="fams" type="checkbox"></div>
    <div class="help">Report each printer's AMS slots (H2D, H2C), or the spools assigned to it in OpenPrintHQ, so OrcaSlicer's sync-filaments button fills in colors and materials.</div>
    <label for="fnoz">Nozzle check</label><div><input id="fnoz" type="checkbox"></div>
    <div class="help">Before sending, compare the sliced nozzle size with the printer's nozzle in OpenPrintHQ.</div>
    <label for="ffil">Filament check</label><div><input id="ffil" type="checkbox"></div>
    <div class="help">Before sending, ask OpenPrintHQ whether the printer has the plate's materials loaded, and name printers of the same model that do. OpenPrintHQ holds a job only for a missing material type, not for a colour.</div>
    <label for="fmnt">Maintenance and stock</label><div><input id="fmnt" type="checkbox"></div>
    <div class="help">Stop when the printer is out of service, and warn when a spool of a material the job needs is low.</div>
  </div>
</section>

<section>
  <h2>OpenPrintHQ hub</h2>
  <p class="muted">Not required. The hub is the rest of OpenPrintHQ around the farm. Connected, the OpenPrintHQ tab gains plate checks with the
  camera photo, staggered start, a timeline with order due dates, maintenance, real filament use and the spool sync, and report
  totals. Leave it empty to use the plugin with the farm alone.</p>
  <div class="grid">
    <label for="hurl">Hub address</label><div><input id="hurl" type="text" autocomplete="off" spellcheck="false" placeholder="https://hub.example.com"></div>
    <label for="hkey">Hub API key</label>
    <div style="display:flex;gap:6px"><input id="hkey" type="password" autocomplete="off" spellcheck="false" placeholder="ahk_..."><button id="hkeyshow" class="link" type="button">Show</button><button id="hkeyforget" class="link" type="button" style="display:none">Forget</button></div>
    <div class="help">Create one in the hub under Settings, API keys (read and write). <span id="hkeywhere"></span></div>
    <div></div><div id="hubstat" class="note muted"></div>
  </div>
</section>

<section>
  <h2>Printer mapping</h2>
  <p class="muted">Which OpenPrintHQ printer each OrcaSlicer printer preset prints to.</p>
  <table><thead><tr><th style="width:46%">OrcaSlicer printer preset</th><th>OpenPrintHQ printer</th><th></th></tr></thead>
  <tbody id="rows"></tbody></table>
  <div class="addrow"><select id="addsel"></select><button id="addbtn" class="link">Add mapping</button></div>
</section>

<section>
  <h2>Queue priority</h2>
  <div class="grid">
    <label for="pnow">Print button</label><div><input id="pnow" type="number" min="0" max="100" step="1"></div>
    <div class="help">0 to 100. Higher runs sooner (25 low, 50 normal, 75 high, 100 urgent). The Print button also puts the job at the top of the printer's queue.</div>
    <label for="pq">Send to printer</label><div><input id="pq" type="number" min="0" max="100" step="1"></div>
    <div class="help">Used by Orca's <i>Send to printer</i> option; the job goes to the end of the printer's queue.</div>
  </div>
</section>

<section>
  <h2>After sending</h2>
  <div class="grid">
    <label for="copies">Copies per job</label><div><input id="copies" type="number" min="1" max="50" step="1"></div>
    <div class="help">How many copies each Print or Send queues. You can add more from the job window or the OpenPrintHQ tab.</div>
    <label for="jwin">Job window</label><div><input id="jwin" type="checkbox"></div>
    <div class="help">After each job is accepted, show a small window with its status, when it should start, and buttons to add copies or cancel.</div>
    <label for="notify">Finished alerts</label><div><input id="notify" type="checkbox"></div>
    <div class="help">Show an OrcaSlicer notification when a job you sent finishes, fails or is cancelled, including which printer it ran on.</div>
    <label for="dedupe">Skip re-uploads</label><div><input id="dedupe" type="checkbox"></div>
    <div class="help">A sliced file the OpenPrintHQ library already has (same SHA-256) is queued from the library instead of being uploaded again.</div>
  </div>
</section>

<section>
  <details>
    <summary>Advanced</summary>
    <div class="grid" style="margin-top:10px">
      <label for="poll">Status refresh (s)</label><div><input id="poll" type="number" min="2" max="300" step="1"></div>
      <div class="help">How often the Device tab refreshes from OpenPrintHQ. Each refresh is one API call.</div>
      <label for="tmo">Request timeout (s)</label><div><input id="tmo" type="number" min="5" max="300" step="1"></div>
      <div class="help">Large uploads over slow links may need more.</div>
      <label for="busy">Queue while busy</label><div><input id="busy" type="checkbox"></div>
      <div class="help">Orca's Print dialog refuses a printer that reports it is printing. When on, a busy printer is shown to Orca as idle so new jobs can queue behind the current one; the live state stays visible in OpenPrintHQ.</div>
    </div>
  </details>
</section>

<div class="bar">
  <span class="msg" id="msg"></span>
  <button id="restore">Restore defaults</button>
  <button id="save" class="primary">Save</button>
</div>

<script>
(function(){
var D = __WT_DATA__;
var OP = D.ophq || {printers: []};
var NAMES = (OP.printers || []).map(function(p){ return p.name; });
var cfg = {};
var dirty = false;
var ctx = (window.orca && orca.getContext) ? orca.getContext() : {};
function $(id){ return document.getElementById(id); }
function el(tag, attrs, text){ var e = document.createElement(tag);
  if (attrs) for (var k in attrs) e.setAttribute(k, attrs[k]);
  if (text !== undefined) e.textContent = text; return e; }
function esc(s){ return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }
function auto(preset){
  var info = presetInfo(preset);
  if (info && info.route) {
    for (var i = 0; i < NAMES.length; i++)
      if (NAMES[i].toLowerCase() === info.route.toLowerCase()) return {name: NAMES[i], route: true};
  }
  var hits = NAMES.filter(function(n){
    return new RegExp("(^|[^A-Za-z0-9])" + esc(n) + "($|[^A-Za-z0-9])", "i").test(preset); });
  return hits.length === 1 ? {name: hits[0]} : {none: true, many: hits};
}
function presetInfo(name){
  for (var i = 0; i < D.presets.length; i++) if (D.presets[i].name === name) return D.presets[i];
  return null;
}
function merged(c){
  var out = JSON.parse(JSON.stringify(D.defaults));
  for (var k in (c || {})) out[k] = c[k];
  if (!out.printers || typeof out.printers !== "object") out.printers = {};
  return out;
}
function setMsg(text, cls){ var m = $("msg"); m.textContent = text || ""; m.className = "msg " + (cls ? "note " + cls : "muted"); }
function markDirty(){ dirty = true; setMsg("Unsaved changes", "warn"); }

function renderSync(){
  var s = $("syncsec"), y = D.sync || {}; s.innerHTML = "";
  s.appendChild(el("h2", null, "Settings sync"));
  s.appendChild(el("div", {"class": "note muted"}, "These settings (not the API keys) are shared with your other computers through a hidden preset, “~ Norman Networks plugin settings (do not select)”, that Orca syncs with your account. Changes arrive when Orca starts on the other computer, or within about a minute when Orca Cloud Manager is signed in there."));
  var line = y.state === "error" ? ("Problem: " + (y.detail || "unknown")) : ("This computer (" + (y.machine || "?") + "): " + (y.detail || "not checked yet") + ".");
  s.appendChild(el("div", {"class": "note " + (y.state === "error" ? "bad" : "muted")}, line));
}

function renderConn(){
  var s = $("conn"); s.innerHTML = "";
  s.appendChild(el("h2", null, "Connection"));
  var row = el("div", {"class": "status"});
  var dot = el("span", {"class": "dot " + (OP.ok ? "ok" : (OP.base_url && OP.has_key ? "bad" : "warn"))});
  var body = el("div");
  if (OP.ok) {
    body.appendChild(el("div", null, "Connected to " + OP.base_url));
    body.appendChild(el("div", {"class": "note muted"}, (OP.user ? "Key of " + OP.user + (OP.scope ? ", scope " + OP.scope : "") + ". " : (OP.scope ? "Scope: " + OP.scope + ". " : ""))
      + NAMES.length + " printer" + (NAMES.length === 1 ? "" : "s") + "."));
    if (OP.scope && OP.scope !== "control" && OP.scope !== "engine key") body.appendChild(el("div", {"class": "note warn"}, "This key can print but not run the pre-send check, change the queue, set ready or change nozzles. Make a key with the control scope for those."));
  } else {
    body.appendChild(el("div", null, OP.base_url && OP.has_key ? ("Cannot use " + OP.base_url) : "Not set up yet"));
    body.appendChild(el("div", {"class": "note " + (OP.base_url && OP.has_key ? "bad" : "warn")}, OP.error || ""));
    if (!(OP.base_url && OP.has_key)) body.appendChild(el("div", {"class": "note muted"}, "Follow Setup directions, Save, then reopen this tab."));
  }
  row.appendChild(dot); row.appendChild(body); s.appendChild(row);
  if (OP.printers && OP.printers.length) {
    var chips = el("div", {"class": "chips"});
    OP.printers.forEach(function(p){
      chips.appendChild(el("span", {"class": "chip" + (p.online ? "" : " off"),
        title: (p.model || "") + (p.family ? " (" + p.family + ")" : "")}, p.name + " · " + (p.online ? (p.state || "Online") : "Offline")));
    });
    s.appendChild(chips);
  }
  s.appendChild(el("div", {"class": "note muted", style: "margin-top:8px"}, "Status is read when this tab opens. Reopen it to refresh."));
}

function rowNames(){
  var names = [];
  D.presets.forEach(function(p){ if (p.agent === D.agent_id) names.push(p.name); });
  Object.keys(cfg.printers).forEach(function(n){ if (names.indexOf(n) < 0) names.push(n); });
  return names;
}

function renderRows(){
  var tb = $("rows"); tb.innerHTML = "";
  var names = rowNames();
  if (!names.length) {
    var tr = el("tr"); var td = el("td", {colspan: "3", "class": "muted"},
      "No printer preset uses the OpenPrintHQ agent yet. Set one up (step 3), or add a mapping below.");
    tr.appendChild(td); tb.appendChild(tr);
  }
  names.forEach(function(name){
    var info = presetInfo(name);
    var tr = el("tr");
    var c1 = el("td"); c1.appendChild(el("div", {"class": "pname"}, name));
    if (!info) c1.appendChild(el("div", {"class": "note warn"}, "Preset not found in OrcaSlicer"));
    else if (info.agent !== D.agent_id) c1.appendChild(el("div", {"class": "note warn"},
        "Printer Agent is " + (info.agent || "the default") + ", not OpenPrintHQ (step 3)"));
    else if (!info.host) c1.appendChild(el("div", {"class": "note warn"}, "The preset has no print host, so OrcaSlicer will not use the agent (step 3)"));
    var c2 = el("td");
    var sel = el("select");
    var a = auto(name);
    sel.appendChild(el("option", {value: ""}, a.name ? ("Auto: " + a.name + (a.route ? " (from the preset's routing string)" : "")) : "Auto: no match"));
    NAMES.forEach(function(n){ sel.appendChild(el("option", {value: n}, n)); });
    var cur = cfg.printers[name] || "";
    if (cur && NAMES.indexOf(cur) < 0) sel.appendChild(el("option", {value: cur}, cur + (NAMES.length ? " (not in OpenPrintHQ)" : "")));
    sel.value = cur;
    sel.disabled = !!ctx.readOnly;
    sel.addEventListener("change", function(){
      if (sel.value) cfg.printers[name] = sel.value; else delete cfg.printers[name];
      markDirty(); renderRows();
    });
    c2.appendChild(sel);
    if (!cur) {
      if (a.many && a.many.length > 1) c2.appendChild(el("div", {"class": "note bad"}, "Matches " + a.many.join(", ") + "; pick one"));
      else if (!a.name && NAMES.length) c2.appendChild(el("div", {"class": "note bad"}, "No printer name in the preset name; pick one"));
    } else if (NAMES.length && NAMES.indexOf(cur) < 0) {
      c2.appendChild(el("div", {"class": "note bad"}, "OpenPrintHQ has no printer named " + cur));
    }
    var c3 = el("td", {style: "text-align:right"});
    if (cfg.printers[name] !== undefined && (!info || info.agent !== D.agent_id)) {
      var rm = el("button", {"class": "link", title: "Remove this mapping"}, "Remove");
      rm.disabled = !!ctx.readOnly;
      rm.addEventListener("click", function(){ delete cfg.printers[name]; markDirty(); renderRows(); });
      c3.appendChild(rm);
    }
    tr.appendChild(c1); tr.appendChild(c2); tr.appendChild(c3); tb.appendChild(tr);
  });
  var add = $("addsel"); add.innerHTML = "";
  add.appendChild(el("option", {value: ""}, "Map another printer preset…"));
  D.presets.forEach(function(p){
    if (names.indexOf(p.name) < 0) add.appendChild(el("option", {value: p.name}, p.name));
  });
  add.disabled = $("addbtn").disabled = !!ctx.readOnly || add.options.length < 2;
}

function fill(){
  $("pnow").value = cfg.print_now_priority; $("pq").value = cfg.queue_priority;
  $("ourl").value = cfg.ophq_url || ""; $("poll").value = cfg.status_poll_seconds; $("tmo").value = cfg.request_timeout_seconds;
  $("akey").value = cfg.ophq_key || ""; $("busy").checked = cfg.report_busy_as_idle !== false;
  renderSecrets();
  $("fams").checked = cfg.ams_sync !== false; $("fnoz").checked = cfg.nozzle_check !== false;
  $("ffil").checked = cfg.filament_check !== false; $("fmnt").checked = cfg.maintenance_check !== false;
  $("hurl").value = cfg.hub_url || ""; $("hkey").value = cfg.hub_key || "";
  ["hurl","hkey","fams","fnoz","ffil","fmnt"].forEach(function(id){ $(id).disabled = !!ctx.readOnly; });
  $("copies").value = cfg.default_copies || 1; $("jwin").checked = cfg.show_job_window !== false;
  $("notify").checked = cfg.notify_job_events !== false; $("dedupe").checked = cfg.skip_duplicate_uploads !== false;
  ["pnow","pq","ourl","poll","tmo","akey","busy","copies","jwin","notify","dedupe"].forEach(function(id){ $(id).disabled = !!ctx.readOnly; });
  $("save").disabled = $("restore").disabled = !!ctx.readOnly;
  renderRows();
}

function num(id, lo, hi, label){
  var v = Number($(id).value);
  if (!Number.isInteger(v) || v < lo || v > hi) throw new Error(label + " must be a whole number from " + lo + " to " + hi + ".");
  return v;
}

function collect(){
  var out = JSON.parse(JSON.stringify(cfg));
  out.print_now_priority = num("pnow", 0, 100, "Print button priority");
  out.queue_priority = num("pq", 0, 100, "Send button priority");
  out.status_poll_seconds = num("poll", 2, 300, "Status refresh");
  out.request_timeout_seconds = num("tmo", 5, 300, "Request timeout");
  var u = $("ourl").value.trim().replace(/\/+$/, "");
  if (u && !/^https?:\/\/[^\s/]+/i.test(u)) throw new Error("The OpenPrintHQ address must start with http:// or https://");
  out.ophq_url = u;
  var k = $("akey").value.trim();
  if (k && !/^(ophqk_|bb_)[A-Za-z0-9_-]{8,}$/.test(k)) throw new Error("The API key must start with ophqk_ (make one in OpenPrintHQ under Settings, API keys).");
  out.ophq_key = k;
  if (!u && !MANAGED.url) throw new Error("Enter the OpenPrintHQ address.");
  if (!k && !secretSaved("ophq_key") && !MANAGED.key) throw new Error("Enter an OpenPrintHQ API key.");
  out.report_busy_as_idle = $("busy").checked;
  out.default_copies = num("copies", 1, 50, "Copies per job");
  var hu = $("hurl").value.trim().replace(/\/+$/, "");
  if (hu && !/^https?:\/\/[^\s/]+/i.test(hu)) throw new Error("Hub address must start with http:// or https://");
  var hk = $("hkey").value.trim();
  if (hk && !/^ahk_[0-9a-f]{8}_[A-Za-z0-9_-]{20,}$/.test(hk)) throw new Error("Hub API key must start with ahk_ (create one in the hub under Settings, API keys).");
  if (hu && !hk && !secretSaved("hub_key")) throw new Error("Add the hub API key, or clear the hub address.");
  out.hub_url = hu; out.hub_key = hk; delete out.look; delete out.look_mode;
  if (forget.length) out.clear_secrets = forget.slice(); else delete out.clear_secrets;
  out.ams_sync = $("fams").checked; out.nozzle_check = $("fnoz").checked;
  out.filament_check = $("ffil").checked; out.maintenance_check = $("fmnt").checked;
  out.show_job_window = $("jwin").checked;
  out.notify_job_events = $("notify").checked;
  out.skip_duplicate_uploads = $("dedupe").checked;
  var printers = {};
  Object.keys(cfg.printers).forEach(function(k){ if (cfg.printers[k]) printers[k] = cfg.printers[k]; });
  out.printers = printers;
  return out;
}

["pnow","pq","ourl","poll","tmo","akey","copies"].forEach(function(id){ $(id).addEventListener("input", markDirty); });
["busy","jwin","notify","dedupe","fams","fnoz","ffil","fmnt"].forEach(function(id){ $(id).addEventListener("change", markDirty); });
["hurl","hkey"].forEach(function(id){ $(id).addEventListener("input", markDirty); });
function renderHubStatus(){
  var h = D.hub || {}, n = $("hubstat");
  if (!h.configured) { n.textContent = "Not connected."; n.className = "note muted"; return; }
  if (h.ok) { n.textContent = "Connected to " + h.url + (h.team ? " (team " + h.team + ")" : "") + (h.can_act === false ? ". This key can only look; plate checks and tasks need a read and write key." : "."); n.className = "note ok"; }
  else { n.textContent = "Cannot use the hub: " + (h.error || "no answer"); n.className = "note bad"; }
}
$("hkeyshow").addEventListener("click", function(){
  var f = $("hkey"); var show = f.type === "password"; f.type = show ? "text" : "password"; this.textContent = show ? "Hide" : "Show";
});
$("hkeyforget").addEventListener("click", function(){ forget.push("hub_key"); $("hkey").value = ""; markDirty(); renderSecrets(); setMsg("The saved hub key will be removed when you save.", ""); });
var SEC = D.secrets || {backend: "", status: {}};
var MANAGED = D.managed || {url: "", key: false};
if (MANAGED.url) $("ourl").placeholder = "Set for this OrcaSlicer: " + MANAGED.url;
var forget = [];
function secretSaved(k){ return (SEC.status || {})[k] === "store" && forget.indexOf(k) < 0; }
function renderSecrets(){
  var where = SEC.backend ? "Kept in " + SEC.backend + " on this computer, not in OrcaSlicer's settings file."
                          : "Stored in plain text in OrcaSlicer's plugin settings on this computer" + (SEC.error ? " (secure storage unavailable: " + SEC.error + ")." : ".");
  $("akeywhere").textContent = where; $("hkeywhere").textContent = where;
  [["akey","ophq_key","akeyforget"],["hkey","hub_key","hkeyforget"]].forEach(function(t){
    var saved = secretSaved(t[1]);
    $(t[0]).placeholder = saved ? "Saved; leave blank to keep"
      : (t[1] == "ophq_key" ? (MANAGED.key ? "Set for this OrcaSlicer; leave blank to keep" : "ophqk_...") : "ahk_...");
    $(t[2]).style.display = saved && !ctx.readOnly ? "" : "none";
  });
}
$("akeyforget").addEventListener("click", function(){ forget.push("ophq_key"); $("akey").value = ""; markDirty(); renderSecrets(); setMsg("The saved API key will be removed when you save.", ""); });
$("akeyshow").addEventListener("click", function(){
  var f = $("akey"); var show = f.type === "password"; f.type = show ? "text" : "password"; this.textContent = show ? "Hide" : "Show";
});
$("addbtn").addEventListener("click", function(){
  var v = $("addsel").value; if (!v) return;
  var a = auto(v); cfg.printers[v] = a.name || (NAMES[0] || ""); markDirty(); renderRows();
});
$("save").addEventListener("click", function(){
  try { var out = collect(); }
  catch (e) { setMsg(e.message, "bad"); return; }
  cfg = merged(out); orca.saveConfig(out); dirty = false;
  SEC.status = SEC.status || {};
  forget.forEach(function(k){ SEC.status[k] = ""; }); forget = [];
  if (SEC.backend) { if (out.ophq_key) SEC.status.ophq_key = "store"; if (out.hub_key) SEC.status.hub_key = "store"; }
  renderSecrets();
  setMsg("Saved. New settings apply the next time a printer connects." + (SEC.backend && out.ophq_key ? " New secrets move to " + SEC.backend + " within a few seconds." : ""), "ok");
});
$("restore").addEventListener("click", function(){ orca.restoreDefaults(); setMsg("Defaults restored.", "ok"); dirty = false; });

if (OP.legacy) $("legacy").style.display = "";
if (!OP.ok) $("howto").open = true;
renderConn();
renderSync();
renderHubStatus();
if (window.orca && orca.onConfig) {
  orca.onConfig(function(c){ if (dirty) return; cfg = merged(c); fill(); });
} else { cfg = merged({}); fill(); }
if (ctx.readOnly) setMsg("Read only", "warn");
})();
</script>
</body></html>
"""


# ---- openprinthq-ui: generated by tools/sync_ui.py, do not edit by hand ----
# openprinthq-ui 0.2.0 (MIT), https://github.com/norjms/artisanhub-ui: dist/theme.css and the bundled fonts.
# Fonts, SIL Open Font License 1.1 (https://openfontlicense.org), latin subsets from @fontsource:
#   Manrope: Copyright 2019 The Manrope Project Authors (https://github.com/sharanda/manrope)
#   Sora: Copyright 2019 The Sora Project Authors (https://github.com/sora-xor/sora-font)
#   JetBrains Mono: Copyright 2020 The JetBrains Mono Project Authors (https://github.com/JetBrains/JetBrainsMono)
AH_UI_VERSION = "0.2.0"
AH_THEME_CSS = r"""
:root,
[data-theme="glass-dark"] {
  --ah-page: #172028;
  --ah-page-mid: #273642;
  --ah-page-top: #3f566a;
  --ah-glass: rgba(255,255,255,0.08);
  --ah-glass-line: rgba(255,255,255,0.16);
  --ah-panel: rgba(12,20,26,0.24);
  --ah-panel-line: rgba(255,255,255,0.12);
  --ah-row-line: rgba(255,255,255,0.08);
  --ah-table-head: rgba(38,52,63,0.96);
  --ah-field: rgba(20,30,38,0.5);
  --ah-field-line: rgba(255,255,255,0.2);
  --ah-segment: rgba(255,255,255,0.06);
  --ah-selected: rgba(255,255,255,0.9);
  --ah-text-on-selected: #1d2a34;
  --ah-track: rgba(255,255,255,0.1);
  --ah-solid-panel: #26343f;
  --ah-text: #f1f6fa;
  --ah-text-2: rgba(236,243,248,0.85);
  --ah-text-3: rgba(236,243,248,0.85);
  --ah-text-3-large: rgba(236,243,248,0.75);
  --ah-group-label: rgba(236,243,248,0.85);
  --ah-link: #bfe6ff;
  --ah-accent: #8fd3ff;
  --ah-on-accent: #1d2a34;
  --ah-attention: #ffb38a;
  --ah-failure: #e66767;
  --ah-success: #9be28f;
  --ah-success-2: #a8e6b0;
  --ah-chip-underlay: rgba(12,20,26,0.3);
  --ah-chip-ok-bg: rgba(155,226,143,0.16);
  --ah-chip-ok-line: rgba(155,226,143,0.55);
  --ah-chip-ok-text: #d3f5cc;
  --ah-chip-warn-bg: rgba(255,179,138,0.16);
  --ah-chip-warn-line: rgba(255,179,138,0.55);
  --ah-chip-warn-text: #ffd9c4;
  --ah-chip-bad-bg: rgba(230,103,103,0.18);
  --ah-chip-bad-line: rgba(230,103,103,0.55);
  --ah-chip-bad-text: #ffd0d0;
  --ah-chip-live-bg: rgba(143,211,255,0.16);
  --ah-chip-live-line: rgba(143,211,255,0.55);
  --ah-chip-live-text: #d4efff;
  --ah-chip-neutral-bg: rgba(255,255,255,0.08);
  --ah-danger-bg: rgba(230,103,103,0.14);
  --ah-space-1: 4px;
  --ah-space-2: 8px;
  --ah-space-3: 12px;
  --ah-space-4: 16px;
  --ah-space-5: 20px;
  --ah-space-6: 24px;
  --ah-radius-sidebar: 28px;
  --ah-radius-card: 22px;
  --ah-radius-choice: 18px;
  --ah-radius-btn: 12px;
  --ah-radius-btn-sm: 10px;
  --ah-radius-pill: 999px;
  --ah-font-sans: Manrope, system-ui, sans-serif;
  --ah-font-display: Sora, Manrope, system-ui, sans-serif;
  --ah-font-mono: "JetBrains Mono", ui-monospace, Menlo, monospace;
  --ah-page-gradient: radial-gradient(80% 75% at 55% 35%, var(--ah-page-top) 0%, var(--ah-page-mid) 50%, var(--ah-page) 100%);
  --ah-blur: blur(24px);
  --ah-card-pad: clamp(12px, 1.6vh, 20px);
  --ah-focus: 0 0 0 2px var(--ah-page), 0 0 0 4px var(--ah-accent);
  color-scheme: dark;
}

@media (prefers-reduced-transparency: reduce) {
  :root:not([data-transparency="on"]) { --ah-glass: var(--ah-solid-panel); --ah-blur: none; }
}
[data-transparency="off"] { --ah-glass: var(--ah-solid-panel); --ah-blur: none; }
"""
AH_FONT_CSS = (
    '@font-face{font-family:"Manrope";font-style:normal;font-weight:200 500;font-display:swap;'
    'unicode-range:U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308, U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD;'
    'src:url(data:font/woff2;base64,'
    'd09GMgABAAAAADccAA8AAAAAjJwAADa7AAEAAAAAAAAAAAAAAAAAAAAAAAAAAAAAG7JoHJMKBmA/U1RBVC4AhGYRCAqBrWSBi0YLhDIAATYCJA'
    'OIYAQgBYRYB4pXDAcbQ3hFB2LYOABMwntUjEZEsHEE4P04cWSgTu5NiyT/H5MTOKzxWpsDVNgwTGrEI0yQWWwPtJ4YOz0zAynh61/Zc18UPIN+'
    '1tR4U1il6F26+oQ93PRU+rm9D3wqcrmxJNtFOCz2OfpCYRWCSkGlVEmHpLJJWATGLXzUnHoJorXI6tm5vWciYZBcXiYKyaKKQwlkAR2xjOTtI5'
    'qz2b3LRdCSA2IXQhCxQz11KGKp0jpPU3GkkqqnpqTiaIt4zaCi9sPDNvuHkXNGNdrDwGiQajEpFVEQIxEVbKyeNbOWdbEondvtYlEXuYr/eb7/'
    'e357Zua++yHwWh5B4mnmkaUxHv7pu6e8r7tqPtD6Gq1gLCfMAQ17ZvyH/flrdQtpyR1qFUrByfxgejj3non+8+v6s3Tbz5CkaCXeEZ4Gyu0T4e'
    '2+NK2aqAqNSWYCv+9e/zyw5dDzgKxABEbgWUDWAnI5xYZT6k1rVn/r82s/nKZ3suRxZ/9EISBhgW1x+Y89ZIvY7AdAHYuRxZtXM6YFhZ1GoasL'
    'A+DHa+7Q33eJpZwzzJZC4vAX3VQciclL1Jm6Xoa1nUwVuKr8/zb1a+dZmb/SR0/Q/hX6HC1CaX3/xSpQ7a+3xPLOfZp582YkazTS8QocR1HI8M'
    'FWSApq5KcofOyAj0JeQJI+JmxYBKqAimZLwKLeokxXLmBRdltU6Fd7nc3mzVH6gy6jqSvcfUpzOJfS7m+qbOOh6ygHCMUgHDgcXzJIlO4KHv7/'
    'UPmeAKVbPwYLViryT/bL7+B5/GNOKNY1gUq8NeCdQlUo9Js8mnu2BugfrGTiRCRvvvGigXS7+yihSBARkRBESt697au7bIWrqkcjWoQAI9k9/x'
    '+G5+wafbPOQbZpnJf1N8jWzFUbbxMBDOIsELHn5/ET9Hd5zED/ZKYCNBKwIta5/jsW4IHvzUj4pcQj/4EAHuaKBCM+jMu4cLbPMEY0RcbKaZMM'
    'yX31G5DGkZSdTXKLKXcdhqzyO5owe3Yy0GiEi7O9QWlLaIYPnqgsnsqAvNtMxornbEa7jwe3OzFWm2NUL3+nXvWxMSbBSeG5PMXNTfcMG5Zjhd'
    '3nWYzMptwZlM9w61ZsF7dbmJL66UvyIFL4ttIoixpbbKokASF3kDQDKHCADqipewHoQdMYB9mxbYl6jmLu9hsCTMEHDVVtuT8WjMNeM3L81fwk'
    'QNp0hKJvS90UU+g8AxsOE7zmQ7BgzfZpFGxdMH7ETttT9tX6zDxxCOiKx0kXD1gFsiC2RiLsjdYWAExXi6vy+5z5iS0jEV+0uGcuzIPEJhmxcc'
    'pc41rUbqpKsxY3myXJMyaSeLgx1DZHuXbxwkDp44adcJtbUoKSwVMgjSlpiefgl9L5PuAz0NsLci++lsOrJ6VB6s4Zo9s/bu6M3i2mtc94GEM8'
    'STSQsS0xo+CVgkYgdPWiHZNFS6iEr4P7XHkoN/duFGBXeo3sKDyO/An7w0vN3GBbGmvn6IurrW/psBxWMoptWxVniQu2i09VFV+oTL3yu3hmtv'
    'uEKUornNERyvuXtW4kPlwGxUcGj4G+tN0ed61lZS8bqHAnHFx5gbVcmtd+leAyk1jeNK1tGQqc1vbd8LYDrcWf4itahOdV4qVdxW0r/7arXmEM'
    'EQJjv4qQZQ9NRl4o+XYlJyIuBlHIkCVFDg2VSvRo53UY4y4ne6g1Xntnig8vV8NiIHwkg0U5Q8M2wMLJQnvWZmTWwVwh5k4hjyQTnnxx+fFnKm'
    'BpY4GC8AVrSyGiUKI1R4w4SDxtJGgkUQqBVEql0Vy6llAZhGcwHWVQJlPzDJGlj2zNkUMNGco+htHWcDoZQQsjaaY4iTHaeIQJJjIwaWlaiVKG'
    'JmsFjSmkplGqMolURc/BEUcp1S4qnafIBYo1UqSJYs0UaaFYK0XaKNZOkY5YHMA4q0Erz71g5xW085rW3hB7S6F3sTyeUZoSdcZD4EOSYNCYAU'
    'MmjFKw8Z4QGRikQB1HjFFIrm0pOcKcSHKpW0cY3KA999gyyMMEEPlKkYVIWVScm2PEMhAnplmBgkNUnMeoVVB/SRlGJ8PpbATNjaSFUTRVbIrw'
    'gGAIMxiJXZbYIZ5xDORgIAcDORjIwUAO2uhIfANYOpO/QA4jgVOXiQmvM6/119j+ex7t7t5YvLQnovP9qS1rK6DzN3sZS9qEqG/YTk+5MsgOT/'
    'eTT+ZEmTjHZ3GyhzG4QU7cfXCau8UaH6m7uLN3olpsOsrO7wlIPwhG/MB6Dh4aQ86ZK4+oS0DIb4VghQoTLmJnSnSdnyBRkmQpUqVJlxHeb+bI'
    'lSdfgUJF1IYaqcR/SmmUKVeh0nwLLKS9Rtmj2v/CeY2atWrX7ZKbbrntrgde+8BQTCg2FBeKDyWE0kI5odxQXig/VBAqDBWF1KHhoZGhqi6b/t'
    'TXIbRnlR2UDgaBxEHh4uE/Lou3L6keNkdDI1NMW1+ZTlW3jj4d6V44qrbj71KcNZ0QrFBhwkVEpERd6dQOirFuO7AnZxjC3CnZsGXHngNHTlyT'
    'R4Qf/xNOHprgwMiYcBsTLd5wYAitpywEEgeFi4dfy3KlMk4uzBoaKgILgcRB4eLh78q8ymdqQCCwiiBi0mS2IlUlqQ96QmQhPxJTB8gmIYbQyA'
    'IAAIBlGNqxnq7uXkCq67gsDd0mbleThIV5pmoW+tNqCqXCCCQOChcPv5YB99MZIV/LZkvdOXOpo/5fP6ZNg5RnnwACCtAAFegBLdAHBgADgfuB'
    'QVOOSrUSqlLp372cM8htLMKL5bjN+IG442JIuhsPYodn1VgdId30RuZnDbCiUi6CCdaSjP13lB7TtuoZlofijtntZL9iR1MIn5rVVIXm8rltZF'
    'pvUyEERNWh33eBvLRBIHFQuHj4B7IcCGtIlliEChMuQqSoFJNP8RIkBdoiJ0aapMuQKQsLO5VRmWJaVKpWdfX0pAsJmrRo0zFN6o64laqMB4W4'
    'tLmB2RUHKA0WAomDwsXD7wwUIRfBSqgw4SJEikoxORYrTkIkPpIkS4mcOnIlT74ChYqoy/AUhblUVnmdl2Z3lCNk2/+qZVnud2gYYdpNv9pLtX'
    '5dbaKivX9ugt3/yb6IGWfLsPH+YA6/iEn7FYL1mIdwZ2dnZ3tn5WRPgu6QK9WP+lNnKdqWkBwNO9ZNSI/v6qg40mQoapCPbGQNMG/GY1Deuwvc'
    'h9VIQPDgQkEAAWg6ECAhABfUjQGLl/zhLRT3x61+pp/+1//KwmkTsct3ryPTG1QefL6I92IifK3atOvQqcsjj911+f/zLmjUpFmLHr0eCk8qGN'
    'Wvbk//H0FDATFSqHdvD7/7EITSX7kSqlWnXoPifddcV32e0BEnnHJa/e4llw2E+swbTuPOb/sWLm3DV7kU+WZuzZEZHumwBznQkQ2P+DaP7q17'
    '6s7f3lt38y/zJt3gq7ym508/OdfO9hk+0pN3CAd2FMcQPWu19oSRCk/mjxeuOG2LhUoRUK8QwrY9MJSWNIny0kbb/DoNGTAEsh9dF+fHpb+PuD'
    'ES7uO+hbE6w0F7tEWCJBvDFomQoG20Douhelaqrp7AJByMwnMZmW2plRIaE91pe2Ckdupo20HrWSj5ihE2QcYMIlk2lyvDhpfORIRd9JMijshb'
    'Q4JhlokZb67spZ9TYKglNBiBEuRwNMIUA5eHxRAMsYcYWzJHm06zb8shBPCU2VK9h0EAG50iVsAud12FXyGXiOqKuiP2wFCBy/AzrBtdmB+G02'
    'J72wxUnNreMH3i+PJFjpziilEck1Fow3XCu9KlxcOYK01PlE5I4ETSGpirnIwbOJdsDxyrLKskcAWZ3nomh4dvn3eOqgE1mizZd9Dx/Ur0c0bV'
    '+9iboMYdSf2csnaSg4E86c69l291XGo+rzxK49ifogWeIH19WI9HfWVJ7VvSs27o3V2UCKc165x7RjRPS1th0xq1z12Z0tPUFm/qea2X74035I'
    'IFoiR2xRgu0esxELr6pJPQuidw0+wtumafbXbr5sBhszfpmD1JktxIggM9Z1zQRuQzaDTBKLGgvfA8Eg+Pa5+Nkq0fljPZUkuSYWykezKkV0g/'
    'NHgOM0R50v04fgr7TokxKiwRvrvoGUKMCxDSdFgxkowFUwLTYJmmwlQ0sFRTEFJlQiipip3X7bkfnmpblBJAZizfxlAWWxOpYLmuVSJYl3QYoV'
    'qlt5iDxPxyhzoc7aoyFbrahJjJWeTKPFpXUTjQS6JC1rQ6cUBvzOiB59rHLm/x7h+GS/eS+Z5rb02c/nq6Qz2jWV+8Af/3ggFnDu3bgZ4H1A2r'
    'A2YgIdMweJvsFPieP15aA0BBACUItAyCdBdonrxoq5Cln7VK8d1txoiHIDP9HzXyqv/55jw4P33TColCrlAq7BXBihi7hrXSern1SqWZ0vz/fw'
    'yl4CmYTib3u1ioEClklx70UfANcBqQUWLQHXkHde4flg7gh9+r4jpz5V2jLs+/v3MZd3743TcQsAI44hogHrPfB/FAbY71u+MeOOetf0HCw5cx'
    'PWd9su7pKtU/pkz+8E7kyNsPETBgxATNnAVLMgw5KwpKzly5cefB80dAxwrWrEaLP+6UnpBK4oI/+FlZZbAMmV/+2g413AgjFRttgokmKTFZY2'
    'lDk19+dcJr773xIQwRhUwQF2bo8dvN0kDmCeCnQ3aGvr/BgdNhYruZeh1y0GGnUDASDwcXnyEhU32YkRIRkzBmzZ4NW47sfOHKlxdvPoK4GCUc'
    'K1SkMBGixEr5Ida1D9JPfwPEy1ZQeRHk68DxxhhrHHwY734nanfcdcMtt92EoPw3HBATAGUFiA/B+H+AeZ8A8ScQXwEMhREPYXJDUBCstdH8dw'
    'uh1mMU23Q5z18dBURYGLCD0SGspJ1QrvmgG920PtNhIkIO4otZ49nKPa4qMlaJkQjSbF07I93b4HJstCnoqD0wLna1CcsRpNnBtlI+XOJgYWe6'
    'U4MINVENWJARTLX3TJhDRNIWJglm8Zl7GHEgDNPzEiWnQwwhrmotSEvxdJ7WCm/Ixp23423kKFOjxjbZHDWSyJIDlQKSNPc6C8z9659XqCMTYn'
    '6WgBCvBybJgU0BeAXIUDeO83oJWp7ME9IeBKeYLOdKVKi9RC1Q+uEECc02taqWGR5WkhGQxFCXomV+6pCcz98fwK2XXBgAdNoRJ3ggqPfggiLv'
    'xbLUI2/fmju7KncV251mYKABZS6kbYzERbm+Rd2xAP2SS7iCDdOqib+eVwjLERB4rfvmxvtGWF04GqwB1xO29WronPC+nPAdN0ITZgOAi7RHa2'
    'vNYO+kwr94e8fD4/gYg5uQPQmPKvjaSVHM9RjKnmEeou9vjg2HFLGuwzQYVGc9VcdUDTkWfOc+HHIe2h+6SqXCOMxRSJF7Oj2AqyOcZmiw0mQb'
    'E1qKKPAlZbuEiYVuALGwX16jW4dBGv6pK3zvy7FaxnJsAvDLm8aKCOwVaUjJc6E0gziJ4ZNAvSCr8UrVyMe/EDLL2/1TyD38xvcXJfx68YHInb'
    'w1OJXPZfM/gXpc4R/9vQ0cF/5R2/4BtONhE9ylXslHEG8lecA6XnjvLcoujU9cG9NwgIywPJpV2BTTvPskDbg2RSSWLG+qRfbcpjwkCnFiNgw+'
    'lSDe/sCGZZaDQ1IZ0iF0RwhF7Pj2XTcu/U9ZAu/tmsq4aY7C2tOEDtsxZNouKC535sMRnZ6cNPxCu9meHk13hHggxGzMoIps5Nw+iro+2+VnmO'
    'w10zxdlwWnyVHWSZOxvB3KXw7cVw9lPFV4cjKJ23Ibh0dvTX/F332D2O/AJ/KlGjrGDh5x5qGRzcKukXwq5bcFvKGrGWALK9Vg7W3GTGnFGU1P'
    'qnDEHXP8nldSZ5PpWXl6Z0Cv0SxLzyR0QRYFg5ZQdCQkigG7RNWPXxR+TelvfMyDH5LIQKdsdwTJ6L5BzSU0r5lJ6KSZR2yHZhGZd4zI/Sz90+'
    'RvE6BI4/kMT+d4MkWziaGil5iWIuq762M0KcyCDgTdfBXlFjlNVszHCEtfNj2Zs5Ks1ze1ntlP+PEOc+iCdijJiuBrKqnEiaXhSuxQpRXBF07v'
    'FaErlCoqQjOoW/MAnP1IENLHs1oaB893+IDTtELgnUc4ONTkpaZEIcst6hs6SPt4pbF7FmZhGcOfSNPcvuqCwJLsfuXRogYhz2KWVkK6JFHsBY'
    'qOMFZJxltjLPPGvUlxuJQmCu2lVkhH+FCqjDRViX48grnovIKKnzo3YLYklQmp+XGZJ/S48rYJ9pLJTj1LKcwA8BTdCNOwzVb5zJ0ddmOEvYJa'
    'E1dMNFBVocN8CUoOq+ZcmZmmbbMMoR7tyy4Tyov/9VqI4yiyCqeCXREsRriaCI3zG7rmPCl8aTO6hwsEj06Axo4zxGQFc4y3CjGBKI5QAGz/Nm'
    'bQNQIIQX/SjCgKpEksfGJ5WXyH+hIFwmKplG76NBateQlTE1GFOIeSAWIt2Xhrc4944oUnyxC6rIBrmvZkJ589Fi9VLD073lg++I22F5BALrsl'
    'WIz3u1ZTZQyWurSbcan2Ty+BXIRgwfpME8+N7AWwyUsYeS/JhkkNNv2jPHZOC58t6ZvJY0RaIrMqxbIy8mJawt3J8W9xKOYL4ie2E9/hThyKFW'
    'up2BqfORz3jAaOUQpOkS6eTegiFE3qBRyvSA4YgM4eyMaaZ/ac1sfkZdYzCVPDmtlDJRCET/Um7o4cqaDXtFze9ALiJRKCgWS+5goHd1OuTZeX'
    'Axk05pdhVaMfQ6nwkvc/YspaeXB63rBk8kYw3QMRtdvwOthfq8p80zWHUlSQryJrOYHQ4kW4b2WLKKXZSuscxFiwdqCyhuZY2WLBQAKLdYXBIv'
    'iURgNBqtyo4UCX6bFYsaoC4j1QyZAJYVNRXAgdPCouXLdubmVvMap4BI2LWEV6x+pY76a0vc5J41yuFjcxYRfS9Bp+cg58rqcmx2NkVkk6Jl5g'
    'yBoMEW9o1ruAZbbbZdfrRLZFtAlzytj+BpgLN/+sNO4fyMCW8bJtMG1vQm3gnCcBMiCIg3v96UbSKCkG+B8yoLdXrWenV5NTCmx9DGYIU7Acpm'
    'cth0/41EZsW4Pla2yJrWSoVi77cbMIeCuh/DxXLU9ng+FxLAcW+cX1DjlK0IdTZq38IxBM10DjhLlbtCrP0nVSLojWXXYMgKLT+/7NoXdf7K4b'
    'Y37aMkAFMpw1b9UZp+yUjNHk0yNSSMRoy07rjcPxe/8D8KB/l1mZa4R1IGzfLJjY+G6C2FhtrBOC8dOfDuBKmZLl2OSgrRy8gEnXZxV8L9V5u4'
    'j51DbhqF/CzjULGt/2sf/E9eEzp3L7gajdR9rPLE0tRRkH6OCcZTPEBrrYcFbKl0Qw5xYYQMY+Qu86bqoDqgj5bncsDHJ27+pYaMPFDXTSoYyI'
    'zItvDxzCAAoZtjZkQxjwK5+UTgLvugCR7DdqwJBLCx2DAWFC8r9HSIUYru1rqSZAcF3U7hj7dfOcuZzc0eeZfEzhzqI+o8zCvGH3y22bHueeVu'
    'd48nWfkpHEfqMYfn/dQF34CtU9Dx8N+ePjkOGtrU8QsM7U6HQIDlE5Bz5CZHtrC8mUz9pXzEOG6MqvOOgPyCZ+3brH6fgrPu44k1tqi/mqFTE+'
    'RLJDUbI7MAvfDiM9y/FBJnIfsNMQKReIbCKda2lAL+0tHwtINgBfaD+9d9r1hVrlwuKaghq1FjXtwiEbreHzIVoffMw5vhQwgwKxGDuI026UDL'
    '16ECwzO7IDExQk6+8NSmQH+hh2x0W72blt3ts9vJ8Ca4hUhUh+MaDZsq8M4kp88HKL0vVKyoy5+Uj6wx7QeDcup9l7mqVHhz9/KWi/KuGkzdC1'
    'Vifx5GXN8gUzmTNSQaXks0YYmcRuoOmJ6DVLr9qh3G0kLQP86lzvvCx70+07wVly+OEfzOGtx9a2BZv1m3Eafdn61d++IzGPgZiFcXBDuxUS7H'
    '5TPwZMXAMmq/etsPi4RJOG31MW36AlF1/6yrWFj3+EktdRdAykrWG3LchjN2rT10tbKcrc60Z0OnoRWatEgHxfo2QlIKOawzWbpCpUkymV1Q/F'
    'l3OLo4562p4sNWVNzkFywPbgHgy7VOwutcZX6G6G8BUJtpzbHF7HPzzhsA8rMkt2H/8f0eX8+4Vu1z/hV/PvzyfeN4WwwlhpJw6GY01FSBiyqT'
    'ABltBcFAOLaQyHhUMbHOCaSnxhvs2FXjCvpAfMw1ScYE5JO5hdd6EFzAK42apOXKTv+SHrYkyodTwmltnyawvwKkFKSoIkfjwpyGFdj9glFcXH'
    'FSVWwLBYWAgGmYrE+CLzwO6zdjjfdmM15DOdpuc6/T3L3cuWL5CAGWi/eOLg1nRgmwQxVD+HPWRpkGRKOKghi8kgTEiDOQSc0Q+M/XWvDRyOGI'
    'ezQsOy4OFhLN4vHJ4VGspKdmgW6obnQSOjvKH/h7pFRdLDj3Lg1jF56fzUJB5Ye6RXjL2NK2oq24SGq6J5jjY6Ln3zdFOx0kAXCT4qs4CjCMPB'
    'rJ1R5gWzLRwmqCtTvHpVoahQf6UAbcO/DfYq32Kx5rlFlds8B71W1/Fv26tzts9m1lxum6K1RuDEBByiRoLCn24diBdFxAiSuG01ggFwx/qe65'
    'kB/M8/D+Bdz9z7DAeh9/pcvBf/+vbCwsMhoQFkC2yN138yvHDhr++8F/pcQu/BAfh86PTfKWfOY1vXTmso3g2KuC7wb6XGEKLxBfyI8zRRgtqH'
    'TfMhLodMfrws8Dt2jICD/Cm9sUjJ2KrN2j1NxaTaeHg88d9lXYn/bTxYZWySoeJqDQw7JSUSzU58q02F/v+9KleUq/8X86C45X7c1aVz3xL9fu'
    'RznaZnOv3dyz3LBsFIiKTDC+RDs8Rt3AfK4U0a1jP/xrfbtxFcsx58nbN4kW609GQU53z8xgu7Pdh2jyO0fu/gxDx4OCg0AHqZrN7j4Xkw0laa'
    '/VBtECV54aKkfbHV32ydXk9Ps1VzN8/pbrFqAROjKqWBwZGFe/VctUlHxSIcpGek21Bp0ec6jBsioEuJIj7GtSqc/kEaXNi3RK2s8/q4o7zbQR'
    '9Tl0eHFQbpMNTYic97n4dDMgxoowGMVbpINzjfOXdq7MjdX89dvHlriAuXL35yjY7sk46BJ5uB6tY2qp0RLd1OhUabXaHXr3d0tPfPd8/BgyBo'
    'D+WJsUd6xdpbuzKnso1p2CqaJy4oDuB/BIVcwm0X3nc5MygLBvEuZ+61LTye7e3tBNyZB07HFJwG/gkqG/V9jWMUhlr0q3fUNROy6XQEuphi1N'
    'VedB3j7mpON/LKR0T4ob1dnJ12/MpQsOOK8rHvs1O//dEn2HCV0XeWHk06CeOYaSVvtAJ3XCTEH+djLNA7FGs85LjWZ/CajdBNo0MrY2OhVRzk'
    'YvYrsz43+Lm1fjMaYalHD3MdiRwjAkxQr0do0mEyq9lgvCmyIqxHwznLSNurYChb6BfSBLDjrWuqWazqajlbzO+sQ0v/jNt4/uJ41xLRFnSg5s'
    'CTxf45qmEG/mWPHjet6TNzbH05Npk7F5NeSYa5YqID0A7IvLxog4JQAsG7IKmiZuPndAXofMTpOWUqHbLcn8pvnkWyQOaaKGpBEgJHLfGrMSec'
    'LPESEhByHhtzsJ1/Bga04QjfK2oV7wFHCE4zM18aO97PDvQA+fjkqYrsLr/4hC6/8uzkKaG8d3C2492XJgDdc99D1eKNIQw1D85bh6pJyUZz/P'
    'ecs8Fmb991HePuCb6416oJHMxPJS3HsuijdPpjVU2C8xbWWsZGM0ZvpofA77HNDJu/neg1jjto7HQQGL26//ybzZbk09jCq/Hf/c0BZj8TPE2x'
    'CYhfrBuVji7aGManAOcGrexv/7fSs/+alG3lF/jm/4oPDr5vXmlowTnrimkqvotCS6iqwOKM8Ibf2JwxSdSKVBqvHZosGGTZ8NvdnTofjm/13s'
    'j3yI7QFoeP1+umB7e87OqU8IYQqVId3BakyrYKqblqOG8eq/8TGRqMDvWBUrNDgCv7iaQ0SGKwF8ynSt/oiUFPU8gM4Dbf01g7QZ2mkCD3Y9p2'
    '90nl+b3VludL7Ti9Es4KDc2Ch4c2bD6fiHP+8TlNVS7DltLlvf2s6wu7FDlc06rX6DRyRMVpahqvtIj/IyPqbUYedBX+v3ZADPmhY2js9jDHQH'
    'm7I1D5Uv+TpaK+/ISL0gsLv8/lzHCh2bWBrmmy+qW48PWaoq1sF5zEQKDEUHYO1Fwv1ha4JBGRqTVcnmTr29VuRlxxfpzUvkx7KkSTplYBm67F'
    'cXOn6Tb8Vg9zpqRuO14wf7XTQfqTKtUlotlT1F1/OLhRT6IjAUKm73p/jtLkdC642TPYu9MnhExOiSB3+6usgaquV6znTcgV9YmQlyorEBf7RU'
    'WiXgHiQk014lJfAUhmDj3uKgKQ3ZCFUvC4fXL61bhXS4e3c8Z0bm7pSF7yeTADnV/7fTV/mO2a0eMZ1vGyH1g8dHp8yplz29Z1olV9FAv5ludp'
    'vqug1gN3+0WQqUkhZKevd/BmTy6Y3pajdL1f3iTuK0Bcqq5BXOgViIoGREkXKyqRl/pEF3O99cTNS22QwAyDh7KKf2ErTLJP5weyIQgLchQRm6'
    'zbUHvtTnmTpmHFvPrnbmS9H+bAUeOPG4fKXz2oH25a2to7duLozixkEbK9d+zU4dv7Ln5mol1yUjGAxIy9aK35emCjGvKsdXTicbdYX+V0f6Dy'
    '7eW7s6vHHt++duzRYtFyrh+7KTCy7cNwiF5lSCXwiHvSB400N5hAtQHpyftIzwAyaftnT4rHiKsUU/jd0PdNHhp4S+RDZbQdSdO9pUpKIuBpg0'
    'ZPT0iKJQD2lTjh25jtTZQhvOhTGHJxJyV6AUgU+ur17lAVUrZBWcnIuWTFQzO+a8GgA8O+HyksgZMxJUE4tnsHk2cyK358VSzL7Axjc+B1lt0I'
    'wiRQ1cm9dcVAi6M60zOjGaQK64GBiR8xg229p/RczCYk5aKknlIhPZmOa5XlxZgGe+xNZ/iwHY6gIGkvu4GqYaNUJldPeD7TPQNawXiiV/hY/d'
    'JHyzDLPX11je6b1P4uRC2gRwDwWd3SzoeLDyAr2RQzJXHGymJG3qGL3y2BR/RzIFCmpQd9Bc7OaeoVCpsHa6g+2ZnTC2l5vAlqylwZ+EnSFx5M'
    'rqULBdDeZ837aKmSnlEdC4aZfNpVDplbrPz/s31s6llnGZCvRjl43jHetr3299lL6y+3ykfzUOckF7d+B47PlDpK2txgRHTg4s5opOIBepfLW+'
    '0OaaSeux/DCtOOlnIx2P+QtYjPYi0sjOTlFnd1srIOTSVkZPxhd5jIXroXWWiUdJ2VqIrm7q+g84ASybz1/ohBrl58NC64l/aUGjsmktnUqfe/'
    'n1vIP/S8ZUBNBNoCZSpdtiE4894CF5HaHy2rrbpr9KAi/booS2BjdebNIcuuPjPTlMU8SaWCS/e/0RzU5NTtOoONz0YxeS1HURUV5zDFCq3UPo'
    'v9aWP55ZBCpQ4m2jO8KUhi2+u8QEwUkYaerAKhLJChLe1SYHX8FKuD7Y23+xaVGuPUxCFx2diiVqkIUxNrkbyhSfT6/5CD/z7yeSssP7s/gFl6'
    'JIW3XMaljpPsP/Bg9HRYPem7l/+AWwyDdfWWWMfEqyoV2J+778UpMRwcY0bNxQZesSGSSxLgZhFFj9nrSrcfCLVzOlOu44WFli60BwbcjPL+iL'
    '0LwByqv5vwR02d/RW/w5PTTo6aqqrLMW6/HTUjp67Cs+66SAbrM3xdZrhTNnA/Aiikp0O6gZ5yop5hYgNl9BwTu/UxwaR6T5l6O0xgNGM/arwe'
    'Mx05vc5f01n7jaR15DpwfngrWGnOurd51Cz2apQJ62q/U4IZcEo3BzR//wGiw00u64E1KH+cxmLr2dMpby518KxXdDcD94ATEq9zT+tP1LIu3Z'
    '0Tif3zRU3KYKbHwKMxO5E9RgjNQOXEoLhEzwSXnzKsDOML8KkIvm/x93x7dGrlaykn5AA5FhXT36SrC0doCXT5H1fb2f0JBkvL28clNV2jJRUd'
    'Y9U1HWPlWZhkHpGE4qCxCC6RjOIAj8dDXsN37ntZHZ7PZRxt9Xj3mh0x1lFKiaxd3fpa7gNKZNKG5PPRpUHYn1hUD0w2FEo/RRezBhL9YnAgk5'
    'HvVlbuyn3llbu7lfJ8kBoyBb2LQu6iRdAvcLPo1FR6BO2THjT+2b99Zv9kH1DVARaA4sJkYnJwUzKd2KEg0ZgdVEIHIK2pr7y5U9JQf6ui7JaU'
    '4dHEKIqHR2vynRVgv4y/lVzbZp3Z6u6YOy3gQdIhLXhEV0bP4mabK9koBlHko1L5iyKq4+Vt8z6DpznS+90tJXdulgA1y6k2G8evM/Qp+HW6d/'
    'pg20FTEGgtPJ0in7TKbnM2QLOEDXBkxiCTPsRMTx0coqdwe2ieMfIDnn9CFOeS4lwkUfCENWym/K6k+p5cJn/wcz0oZBp/qqt7pHjdVfvkUV1H'
    '5R1+TIaQ6Z04QGZwDmbSx1JDERFZ18rLG9IZDfGwP/xUK29PG6YzhtLSGMNDDAYeeFNaKn3oAxT8YO+sP9IB67A31gcxTOMNftGF+obaW7Ig83'
    'w38XFRmWD5TGpV1dlUwXKZKP+oyAMhC5TdbqgvvnCT39jWtNDf3jnX29Iy09vZPtffeK+sr6K8vK/ssHvLcNTeCuDCOd30U13to7Y3nbVP63vB'
    'rbzY9ILbShgk07mjGQxfCPwIFfK9mqp7cnn9g5+lMsVPRBKApDQsDUmMVVJAPtOwM+Jw20rD8Eu0BgqhEFoDpYGSdrl8piKs0xhJKAGZkkRqyP'
    'zwc60MniEP3TNMGB+4wDK2n9qGx3ekQDPwXR2E1ORa+Lck5BEyNCA8BRaQeuDa63QJIpnUgEY1kMjE5lYcKRcvKiTgRCI8QSjC4YVicIqkN2Ew'
    'bcxwJqatCUunN2MxramKF916FEYOKU9Mj6LyxWSCQOT5214M7C01F7hVCxyhDLCkgLU028oe547l8jHBxABfop6YjK5rRFBJMhRaRgyzIYUn3U'
    '8JjftcDPIo9ojAKi3XBpsRtU4AGSMIRFY2wfRzThAlPTL+fq4xH2rrmKbZHoduoFFRMlkyUJLOjPPdxDVuYruvwq85/NQZ0MjNGau2zMq11Ivm'
    'oyMJBSJscCCNFhAYRvITZzlb7D3LWf3WqPl3dJ5vOFGGQclJRGJTGx6ch1DbCPj2jroclDT8u3tVUO0lCBN4oZBSIiGefMNMS42I5sN8fb5RgD'
    'kYR1RZfHIlkUKSk+yUE60R4iTxoKysDHCDmJ9sqUR2IMsoxK6Jy+MpudhMKoGcQcHhMilkQhYVcy6BFJ9Ae+PIUsKjFVjGbw7jc1RVo6MPU7PQ'
    'A9T91RLCag/EQeL5BZECfIhAxhDUVGsS/OkI/MSnrINMeuM2rWbGhsDjfmUuP23mVsxNPJ77RSqpZyhfTYD5A8dEQekljaTCoBqjlJ2lz0xmCv'
    'h3pmemm7c0tnVJBop9sQd/gMHMfMz+ks+3qMj0mJU885SAADPsqFDsGBmR6+Boa0P3H1TGhPdPzYz64rLWQsPiXrnWIYnwrjIjcgQmJmFchRwz'
    '3HRp5BIYfxijU6J9n4LMiFlJsWAG+JtjFTVFThGRfAdDO2s6GCQwYf2b0yN++Ky1sLDYp951CGJEN8+IEomOpqwpUaJHmi6NXgJPtLy/QKFfvD'
    '2vQaHXwHJtDD88MicmJjI3Nyw2ghcWkRMbk97AvHCgF6AtwB68+g7pLn1FVlVqUlX6j+wufYscuSrAdu2ADSDWDzi1DZKiBpxxc7ndPXndWFVL'
    '9VOiSD/xenfhOjoxBMd05qGBrXxiwEyVskDv7YRbhRfo6j314Hc1Mm3jYZcNK26YRzEE4BgcG5I/n2yMlU8Ut4z4omLBLEynJQz7F8rBBS6ffI'
    'UNtAyYhtkytEAbtKqPpyUz8scgmEeBsmPZcjmvzdaCVLLhtXFJWlm3qjWzSxUij6teMvSNYNSWuIFuUPp5iBTg+lYtX+h10qMBfeYAyiXOoGUu'
    '6yVUlKZ63VutrUgl573Ok0osbNt4cjkw3QIaO0ua/gbcVX83AeMt5ZEtARc8KjdS12vLG2JcW0Jvqb+EJnF5khIEKchPwalvSTMpazoevrYf9p'
    'oDg9df0bopKczP+3cYTh2BmSBIL5e+Sd+gxTnkOQjW4sCBjnmOAvBzzmAIMnjQE0qb1CZDkDlV9ge/TDBTqspOYPl3dwG3DJbdk/3gslgXmgZL'
    'LicmYtG7BzgQv/kESR/ksOCBlXgkdMtmI6NngJ5RUUI/U+ERkjnAyOy12vDZjEP0ryQESwaWV4xHgzvFqZjQ8siwaEltIU6UFthlMtIGDEKPdO'
    '37pp/3Fm2CjGe/aenp2HdMcrx5/7Uht6oDU2MJrrnQLd9/DEgv5J3Gd3TiTwvyyO7okHaeAHZnB4CDszs7XT/0ARZrdxeZrPK7d+MFu7vQp1sI'
    'P0w8VbDJz9nIFeRtbObkwsiRtiCXejf4P3ZnpfojlgXUG5/1PNMMTnze81xZPtHxjNN73ETSbmbX/GBi5OD5wSomXyB1iWTZM76WFm3eYrWC5l'
    'vc1pMm0m5LnRR280FUBmAujSQIEbjC/CpnA8JKwNBaT03l6UfZIPiWza77xWEC2FN5NIJzPLnDXHZHeGAqpTK7eDAPD4eFBltHhXoPB+aBApfG'
    'o8sRHisesDv7JM0SWFcseyeA2MXcUCIx1DM/a2ExPS+tk5A35iyUe7pJy6RMXlWdYDtAEbbZq3ijAOuk5MPM9zZhV5cfnTRg834vhGppkVi5zZ'
    'LmFlC6bEpM/i/dZMnQ6CbG0abRsmXMg6sDdIkwyZeZkedJKDENOW8mpo5Makp1mV8bmB2FTEomIRL6FbWxkJyTgOCuO2FCn+yINF+PAzaU/1YY'
    'kZyacnEEuMi+jcg0Hw8/Z6L6O4Tg1JJLBGGTDe9jJOeKgY7P7zt393ZTmBVVpdXAjMUivmVZWQaXU1hTGZlT7CzWoUOZGVN3RsYUmx1gz8+SwX'
    'lwODciAiqiEdwJJHjwad87jVH1ZJgFAIhPAXo0Un1SDFaDXz61sF9lU2IiBpvhDqxOQuJl4FhsJLmcoCt3d3eh4Z9WAFu/M6Gb//03Qp3xBYHJ'
    'd1cMBTOnLcqO3iBB1Jo1T42dbWc2kk3Qb5QJLZHJKU0YVCszA9vamkyLbglC8etzQVxSHBMXnBTmmqeEUB2gP50ZfJBc1f8ra2IN/wwiBbLcR3'
    'PTDxn15EXPGBdoAcX3rI/IAZNFT2OmpWIZaZk0emYaYDBl1+LiEPSAdiUsSHbr07yY5JOBRGNkmQXCoywvl1yDIPyIVwHqAObTYvd4ldQsU39y'
    'nN3psJxEuCsmlYo7/u1smJEFuNP6M1ab1Xl8MYM71qfFjjWgluqFKUJSdPqSfV/tLI0meY1dtEwNNLFjKr846AfWxHC6HuHNWL3RCvxxoRB3fL'
    'SyQO9NjP6fgDqrINLzgQgcA1lHIuuajPP6zAwS06hlbiGRAs9YUiQ8PkYB5VuJbZYS0nIoWYWStSCmhi/PooGDCsVHt3syTIus1xIyi9h5ud2H'
    'kVAPoxiWXxQtMjwu9mljnlXhOzw+LS8lW9S4HpvK20CL27TT+yx0UCSGyBUG57rCEQGhxJh2T5pJkc1afKaYxc2Sz/oDf9+gIZ3Cq1fFOsPDAA'
    '6yemhknx/gA7ArOqUDHhQbEO0ygAJoxz6doJiuyPTgdJM/viLon6Y6nTl+aSvOwDE0HWpuaS6Ewa1Ev33O8A7DFF8i3qy2p8USEde51sjN6nvM'
    '6Wnmg6pq5j0PSFVXPaCNew5sAMgxAmEYew0plyPXOGzkqly2lszmrCYDAAAbarVWkSwRd47NmuNxqWmOxeWBh6TKn+2ywxW3r0ZTKbrunsge6g'
    'YNOyU69Ge1zSl7x++tqwGse/NvlvkT8ycsyF+Fas87MM861KokasJaoYoEHHo7O7RvXyxRsFX9tOepZsBb/9CgePuBlcLSy8B+b7e6E1i9u9Ai'
    'DuyuMqkG+y3O0D4qgI2L8cmqRw54uqAuIXrBAl2qs/rtDJB/8qMfiNzVq4k8D1e/fyOw/uXL+sCNtXq/ly8k8Ixf/YsX8FvbqA988bLWRvt17p'
    'Cu7hD3+nWCiwIBJaiXciUKtzALlHmUQD9ozUDtZVKTsazZjWeOpLpW8yUMB2jlCDyp6dTO7vNpEaF1alrVTwlPcbq4F1Ap05ttla+sSsVuKJXL'
    'oGn+YsDOgr+Z0xaqrLdoAeA+bAP//yJ5u7xD3invknfLe+S9DfvqcFDFlw43ru3Xb9nTXwCh+M7iVNVfwLswKoHF5SsgO7tEBdN71OC0IUE5j1'
    'UaeXuare6vfZcT/vtCINO6VDyXU3pgShYAsXDRv0LPce9SwXYhervmnh/KQ5B9WHBz6OsB3tTNAW1w1RGSz+haiboNaow2NZLmUh2DcakgAt4l'
    'YAZ4DxjxBjzwB+L/gDsd9Xd7v0VknT0APALqiEeIzfHhPwHF75WyZ6hqbPwufbYg/oo+utTq3d/L3MWhTJtAbyLi2e0rvjpufHRuT+2yI4Py99'
    'FZDdVIhroyT9a6ihXERUtL83iRyitxT7vZGU4XITdsfu20edX/3H4aEE9dGTUob91Bqtx0D2V+hplYn0Y15bzfc5XIWa4R+b67hy4abyF6E9cY'
    'GB2oJxyQr8wm30TTnNA3LMu39NenI7N36vx7lSC1q5P6B86lcxhk+jh+SpK6nKuLZ99zZ+7EbSQuG1fplE7kbT5PeFdom786sLrMbGuSJ5lmYS'
    'dh1Jywufun3sJ36/11OX4KdsiuKaszfhZ1iuoygbfktzkzG99TfBgE1Pteb19zf9Ek/O9bjuBeAnz9V5cB4Nv3vuPdmg7zsp+rQAsDBLi7KDpw'
    '2kBasy9k/wqOCduqdV9zO7zX7fa2nbY3gOXhuTsAvSWxJHqbf6K9upmUuTh26MD3aUjbAp6zFdSXp/5fTbRdtBrMaw5L7BvZV529p1HqDDWTt7'
    'd5W/QuoM/0knjMepSgn856q9GbAY771kfUS6WKXqYumLCoEimQSaJooyvTl7FzVZ3TFo3IRvV0Ka7Ds77mzTzima1+ePqY1ziV6Vji950XRPRx'
    'U32btlp8KVcsauzRu5MG/OYDt/P/na0cvVasuU3X7Ya9ifj2pPSwxulAdylVbx6MDEdC33X3qEX1Em2zDzRdo3oJ0R2JvP7wH8T/rJLNNLl71t'
    '633ryRfVaTlWQFWUrKyU1TB+NoKmlLHc3YUY82qCYwcnt4SccvV8loGu2UWVy/Wnyv6aOid+QYcxymPpsvhjRN0dMsbmf/NVHEPpxfLaknUZdp'
    'jmU7bw72etGM+kFlWHReZkiZmp/Bk5iZGgn04NzpeqkkGvQuzPuYPdRxAxT11vUbHTbHSeUXaqsP3YXl9Xpm/nVXj6aW9B5qDLh3MntM7lKdoD'
    'j11UvsHPts1jnKwO4G0Uc/SqWYbxwpvrX28blZsucnHyyoFddXaS/3SirM1bjtvrbpnOwu8uFA21e5Z6po3+qpFnKeROw+om2/KhsZtn7bBuSi'
    'z4L717/N1ag6UuOYeTiXUicD9czdzpDwXTTgjwkIxASZjGSiVpDVUKlBZp7Wq3wAL0To1oLYlkULZhy8FkKC+haSL20Lh8TEFoq3CG48m2IYRa'
    'AwoEXoWoPvge+jz5Ot6AC/UCiUT48YS2HPhQ5ZgRLzvMgLGs6b1MdKgIKWKV8hhUIc8coUwTNbXTnpjUAPT7E8X74QBUeh4reqQD47gaCC+Aqs'
    '27aDS7ErVvhaHYYoCRpCmDM3Ub4bK5ZRXa7QH/dX0k9I4qSScEqqWbgzybVYonS04unjZLm4irgFERZvaZ4cVrJYljhrbAkCsrLdYuXssK8Pgd'
    'kCd0p/LKVNf1+U988c30fJH4VWsKzq5MwFm6vfOOL4TrsOTjx48nJNl24X76F7+f1w+bvlsuF2LLdmXYC/+N5Q99Ltum6EPUFChQn3h1BxXyaX'
    'K5/AhHgJCiT6XQLhJCKiP19wY/eUKFGahuOtdm9NvDIE5SpVqTCp2gaivw1ARtGMikZCqlbN7Xec+TOes1nJKRXEgXoNpQabbQn1TfgR4X17wb'
    'vq/tmWkpUBMkRGyBiZINP6ZJYwOvMsskyUOEnSZDHJs0qRdcpsss0u+xxyVO2Lt97RZ4IhdwBpB6XtLqhkhGImvJxyziVXBgzlyhcmorxzzyPP'
    'vPJmWj6OOiaqAvPLX6Mmx51w0in77FerDocgAgosqOBCEMEWWpgyNbgRrtyaiGLsvQ+OUbBmZaUCO0VWNF5EFVtc8Wyga7po0KpFm3rpfk1C7Q'
    'btYinc1+u2O+4lqX1mxHZu9/wtJk32+f7QcMhpnVxi3ZTSrZux1+lkwt85SLRn0KPs/l10/peNnNRpS2HDGSUtpNVFD6tms4M1llIxP48zNfFL'
    'ih0XKYrqFuWY8DPBvJfs7TjdMuSSW34y5ZEle8paynPJzs6dkvKeRhJRUqY8smQP2/gXb/it2rkTuzSZznHQUr/2GapaxocWmHDDJgJkyofGX9'
    'KFqGup5plYcU/QKKaBZfcuKFVENkwXhHbKh2fQIjXYunX+5cCrlGdXxhhwwwphD7wJ7zd3M58xdTOZaWShFqmheXZhknDaCC/gGu5kykZWmldN'
    'JgkYrtWsb6ce+76C4tckJMIWTSsWQWlUs/nCerS/5++B49RxOCwI62P20gYba+tx/6IiUz03djyYWk9hPFeN00sV102d7TTeN4+53P9SrsejAA'
    'A='
    ') format("woff2")}'
    '@font-face{font-family:"Manrope";font-style:normal;font-weight:600 800;font-display:swap;'
    'unicode-range:U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308, U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD;'
    'src:url(data:font/woff2;base64,'
    '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'
    ') format("woff2")}'
    '@font-face{font-family:"Sora";font-style:normal;font-weight:400 700;font-display:swap;'
    'unicode-range:U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308, U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD;'
    'src:url(data:font/woff2;base64,'
    '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'
    ') format("woff2")}'
    '@font-face{font-family:"JetBrains Mono";font-style:normal;font-weight:100 900;font-display:swap;'
    'unicode-range:U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308, U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD;'
    'src:url(data:font/woff2;base64,'
    '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'
    ') format("woff2")}'
)
# ---- end of openprinthq-ui ----


# 0.13.0: every page uses openprinthq-ui's glass-dark theme ("glass over a live feed"), as every OpenPrintHQ app does.
# The old colour variable names (--fg, --muted, --accent ...) are mapped onto openprinthq-ui's values so the pages'
# own rules keep working. Glass needs backdrop-filter; a web view without it (some WebKitGTK builds) gets solid
# panels, as does the system's "reduce transparency" setting.
SHARED_CSS = AH_THEME_CSS + r"""
:root{--fg:var(--ah-text);--muted:var(--ah-text-3);--line:var(--ah-row-line);--accent:var(--ah-accent);--accent-fg:var(--ah-on-accent);
--ok:var(--ah-success);--warn:var(--ah-attention);--bad:var(--ah-chip-bad-text);--info:var(--ah-accent);--soft:var(--ah-track);
--panel:var(--ah-glass);--bg:var(--ah-solid-panel)}
@supports not ((backdrop-filter:blur(1px)) or (-webkit-backdrop-filter:blur(1px))){:root:not([data-transparency="on"]){--ah-glass:var(--ah-solid-panel);--ah-blur:none}}
*{box-sizing:border-box}
html{background:var(--ah-page);color-scheme:dark}
body{margin:0;min-height:100vh;background:var(--ah-page-gradient);background-attachment:fixed;color:var(--ah-text);
font:14px/1.5 var(--ah-font-sans);-webkit-font-smoothing:antialiased}
:focus-visible{outline:none;box-shadow:var(--ah-focus)}
a{color:var(--ah-link)}
h1{font-family:var(--ah-font-display);font-weight:500;letter-spacing:-0.03em;font-size:30px;line-height:1.15;margin:0}
h2{font-size:17px;font-weight:600;margin:0 0 8px}
.muted{color:var(--ah-text-3)}
.card,section,.job{background:var(--ah-glass);border:1px solid var(--ah-glass-line);border-radius:var(--ah-radius-card);
backdrop-filter:var(--ah-blur);-webkit-backdrop-filter:var(--ah-blur)}
button{display:inline-flex;align-items:center;justify-content:center;gap:6px;height:32px;padding:0 12px;border-radius:var(--ah-radius-btn-sm);
font:600 13px var(--ah-font-sans);white-space:nowrap;cursor:pointer;border:1px solid var(--ah-field-line);background:var(--ah-glass);color:var(--ah-text)}
button:hover{border-color:rgba(255,255,255,0.35)}
button.primary{background:var(--ah-accent);border-color:var(--ah-accent);color:var(--ah-on-accent)}
button.ghost{background:transparent;border-color:rgba(255,255,255,0.25)}
button.danger{background:var(--ah-danger-bg);border-color:var(--ah-chip-bad-line);color:var(--ah-chip-bad-text)}
button.armed{background:var(--ah-attention);border-color:var(--ah-attention);color:var(--ah-on-accent)}
button:disabled{opacity:.55;cursor:not-allowed}
input,select{font:14px var(--ah-font-sans);color:var(--ah-text);background:var(--ah-field);border:1px solid var(--ah-field-line);
border-radius:var(--ah-radius-btn-sm);height:32px;padding:0 10px}
input[type=checkbox]{accent-color:var(--ah-accent);width:16px;height:16px;padding:0}
option{background:var(--ah-solid-panel);color:var(--ah-text)}
.pill{display:inline-flex;align-items:center;padding:2px 9px;border-radius:var(--ah-radius-pill);font-size:12px;line-height:18px;font-weight:500;
white-space:nowrap;border:1px solid var(--ah-field-line);background:linear-gradient(var(--ah-chip-neutral-bg),var(--ah-chip-neutral-bg)),var(--ah-chip-underlay);color:var(--ah-text)}
.s-queued{color:var(--ah-text-3)}.s-active{color:var(--ah-accent)}.s-done{color:var(--ah-success)}.s-failed{color:var(--ah-chip-bad-text)}
.s-cancelled,.s-wait{color:var(--ah-attention)}
.pill.s-queued{color:var(--ah-text)}
.pill.s-active{border-color:var(--ah-chip-live-line);background:linear-gradient(var(--ah-chip-live-bg),var(--ah-chip-live-bg)),var(--ah-chip-underlay);color:var(--ah-chip-live-text)}
.pill.s-done{border-color:var(--ah-chip-ok-line);background:linear-gradient(var(--ah-chip-ok-bg),var(--ah-chip-ok-bg)),var(--ah-chip-underlay);color:var(--ah-chip-ok-text)}
.pill.s-failed{border-color:var(--ah-chip-bad-line);background:linear-gradient(var(--ah-chip-bad-bg),var(--ah-chip-bad-bg)),var(--ah-chip-underlay);color:var(--ah-chip-bad-text)}
.pill.s-cancelled,.pill.s-wait{border-color:var(--ah-chip-warn-line);background:linear-gradient(var(--ah-chip-warn-bg),var(--ah-chip-warn-bg)),var(--ah-chip-underlay);color:var(--ah-chip-warn-text)}
.banner{display:flex;align-items:center;gap:10px;padding:10px 14px;border-radius:var(--ah-radius-choice);border:1px solid var(--ah-chip-live-line);
background:linear-gradient(var(--ah-chip-live-bg),var(--ah-chip-live-bg)),var(--ah-chip-underlay);color:var(--ah-chip-live-text)}
.bar{height:6px;border-radius:3px;background:var(--ah-track);overflow:hidden;margin-top:6px}
.bar>i{display:block;height:100%;border-radius:3px;background:var(--ah-accent)}
.msg{font-size:12px;min-height:16px;color:var(--ah-text-2)}
.msg.ok{color:var(--ah-chip-ok-text)}.msg.bad{color:var(--ah-chip-bad-text)}
.note{font-size:12px;color:var(--ah-text-3);margin-top:2px}
"""


def themed(html, look=None, hub_url=""):
    """A page in openprinthq-ui's glass-dark theme, with its fonts embedded. Nothing is fetched (OrcaSlicer may be
    offline): not the hub's fonts, not a stylesheet. look and hub_url are accepted for older callers and ignored."""
    html = html.replace("<html>", '<html lang="en" data-theme="%s">' % THEME, 1)
    return html.replace("<head>", "<head><style>" + AH_FONT_CSS + "</style>", 1)


JOB_WINDOW_HTML = r"""<!doctype html><html><head><meta charset="utf-8"><style>""" + SHARED_CSS + r"""
html,body{height:100%}
body{padding:18px 20px;display:flex;flex-direction:column;overflow:hidden}
.head{display:flex;align-items:center;gap:10px;margin-bottom:12px;flex:none}
h1{font-size:22px}
#jobs{flex:1;min-height:0;overflow:auto}
.job{padding:12px 14px;margin-bottom:8px}
#copyrow input{width:72px}
.row{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.row .name{font-weight:600;margin-right:auto;word-break:break-word}
.actions{display:flex;gap:6px;margin-top:8px;flex-wrap:wrap;align-items:center}
.foot{display:flex;gap:8px;align-items:center;margin-top:12px}
.foot .msg{margin-right:auto}
</style></head><body>
<div class="head"><h1 id="title"></h1></div>
<div id="jobs"></div>
<div class="actions" id="copyrow">
  <span>Add copies</span><input id="n" type="number" min="1" max="50" value="1" style="width:64px">
  <button id="add">Add</button>
</div>
<div class="foot"><span class="msg" id="msg"></span><button class="primary" id="close">Close</button></div>
<script>
(function(){
var INIT = __WT_INIT__;
function $(id){ return document.getElementById(id); }
function el(t, a, txt){ var e = document.createElement(t); for (var k in (a||{})) e.setAttribute(k, a[k]); if (txt != null) e.textContent = txt; return e; }
var LABEL = {queued:"Queued", active:"Printing", done:"Finished", failed:"Failed", cancelled:"Cancelled"};
var state = INIT.snapshot, armed = null;
$("title").textContent = INIT.title;
function msg(t, cls){ var m = $("msg"); m.textContent = t || ""; m.className = "msg " + (cls || ""); }
function send(o){ if (window.orca && orca.postMessage) orca.postMessage(o); }
function render(){
  var box = $("jobs"); box.innerHTML = "";
  (state.jobs || []).forEach(function(j){
    var d = el("div", {"class": "job"});
    var r = el("div", {"class": "row"});
    r.appendChild(el("span", {"class": "name"}, "#" + j.id + "  " + j.name));
    r.appendChild(el("span", {"class": "pill s-" + j.state}, (j.state === "active" && j.status === "paused") ? "Paused" : (LABEL[j.state] || j.status)));
    d.appendChild(r);
    var where = j.printer || j.requested || "";
    var line = where ? ("On " + where + (j.requested && j.printer && j.requested !== j.printer ? " (sent to " + j.requested + ")" : "")) : "";
    line += (line ? ", " : "") + "priority " + j.priority;
    d.appendChild(el("div", {"class": "note"}, line));
    if (j.note) d.appendChild(el("div", {"class": "note"}, j.note));
    if (j.error) d.appendChild(el("div", {"class": "note", style: "color:var(--bad)"}, j.error));
    if (j.state === "active") { var b = el("div", {"class": "bar"}); var i = el("i"); i.style.width = Math.max(0, Math.min(100, Number(j.progress) || 0)) + "%"; b.appendChild(i); d.appendChild(b); }
    if (j.state === "queued" || j.state === "active") {
      var a = el("div", {"class": "actions"});
      if (j.state === "active") {
        var pr = el("button", {}, j.status === "paused" ? "Resume" : "Pause");
        pr.onclick = function(){ send({cmd: j.status === "paused" ? "resume" : "pause", id: j.id}); msg("Sending...", ""); };
        a.appendChild(pr);
      }
      var c = el("button", {"class": armed === j.id ? "armed" : "danger"}, armed === j.id ? "Click again to cancel" : "Cancel job");
      c.onclick = function(){
        if (armed !== j.id) { armed = j.id; render(); setTimeout(function(){ if (armed === j.id) { armed = null; render(); } }, 4000); return; }
        armed = null; send({cmd: "cancel", id: j.id}); msg("Cancelling...", ""); render();
      };
      a.appendChild(c); d.appendChild(a);
    }
    box.appendChild(d);
  });
  if (state.error) msg(state.error, "bad");
}
$("add").onclick = function(){
  var n = Number($("n").value); var first = (state.jobs || [])[state.jobs.length - 1];
  if (!Number.isInteger(n) || n < 1 || n > 50) { msg("Copies must be a whole number from 1 to 50.", "bad"); return; }
  if (!first) return;
  send({cmd: "copies", id: first.id, n: n}); msg("Adding " + n + "...", "");
};
$("close").onclick = function(){ if (window.orca && orca.close) orca.close(); };
if (window.orca && orca.onMessage) orca.onMessage(function(m){
  if (!m) return;
  if (m.type === "state") { state = m; render(); }
  else if (m.type === "ack") { msg(m.text, m.ok ? "ok" : "bad"); }
});
render();
setInterval(function(){ send({cmd: "refresh"}); }, 6000);
})();
</script></body></html>"""

PREFLIGHT_HTML = r"""<!doctype html><html><head><meta charset="utf-8"><style>""" + SHARED_CSS + r"""
html,body{height:100%}
body{padding:20px 22px;display:flex;flex-direction:column;gap:12px;overflow:hidden}
h1{font-size:22px;flex:none}
.checks{flex:1;min-height:0;overflow:auto}
ul{margin:0;padding:0;list-style:none}
li{display:flex;align-items:flex-start;gap:10px;padding:8px 0;border-top:1px solid var(--ah-row-line)}
li .t{flex:1;min-width:0}
.alt{margin:0;flex:none}
.alt:empty{display:none}
.foot{display:flex;gap:8px;justify-content:flex-end;flex-wrap:wrap;flex:none}
</style></head><body>
<h1 id="title"></h1>
<div class="checks"><ul id="issues"></ul><ul id="warnings"></ul></div>
<p class="alt banner" id="alt"></p>
<div class="foot" id="foot"></div>
<script>
(function(){
var D = __WT_INIT__;
function $(id){ return document.getElementById(id); }
function el(t, a, txt){ var e = document.createElement(t); for (var k in (a||{})) e.setAttribute(k, a[k]); if (txt != null) e.textContent = txt; return e; }
$("title").textContent = D.title;
function row(list, cls, text, chip, chipCls){ var li = el("li", {"class": cls}); li.appendChild(el("span", {"class": "t"}, text));
  li.appendChild(el("span", {"class": "pill " + chipCls}, chip)); $(list).appendChild(li); }
D.issues.forEach(function(t){ row("issues", "issue", t, "Stop", "s-failed"); });
D.warnings.forEach(function(t){ row("warnings", "warn", t, "Warning", "s-wait"); });
if (D.alternatives.length) $("alt").textContent = "Printers of the same model with everything loaded: " + D.alternatives.join(", ") + ".";
var f = $("foot");
var cancel = el("button", {"class": "ghost"}, "Don't send"); cancel.onclick = function(){ orca.submit({action: "cancel"}); }; f.appendChild(cancel);
D.alternatives.slice(0, 3).forEach(function(n, i){ var b = el("button", {"class": i === 0 ? "primary" : ""}, "Send to " + n); b.onclick = function(){ orca.submit({action: "send", printer: n}); }; f.appendChild(b); });
var go = el("button", {"class": D.issues.length ? "danger" : (D.alternatives.length ? "" : "primary")}, "Send to " + D.printer + " anyway"); go.onclick = function(){ orca.submit({action: "send", printer: D.printer}); }; f.appendChild(go);
})();
</script></body></html>"""


TAB_HTML = r"""<!doctype html><html><head><meta charset="utf-8"><style>""" + SHARED_CSS + r"""
html,body{height:100%}
body{overflow:hidden}
.app{height:100vh;display:flex;flex-direction:column;gap:14px;padding:20px 22px}
.top{display:flex;align-items:center;gap:12px;flex:none}
.top .ttl{margin-right:auto;min-width:0}
.top .sub{font-size:12px;color:var(--ah-text-3)}
.top .msg{max-width:32%;text-align:right}
.alerts{display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end}
.alert{display:inline-flex;align-items:center;padding:2px 10px;border-radius:999px;font-size:12px;line-height:18px;border:1px solid var(--ah-chip-warn-line);
background:linear-gradient(var(--ah-chip-warn-bg),var(--ah-chip-warn-bg)),var(--ah-chip-underlay);color:var(--ah-chip-warn-text)}
.alert.bad{border-color:var(--ah-chip-bad-line);background:linear-gradient(var(--ah-chip-bad-bg),var(--ah-chip-bad-bg)),var(--ah-chip-underlay);color:var(--ah-chip-bad-text)}
.view .alert{margin:0 6px 6px 0}
.tabs{display:flex;align-items:center;gap:4px;flex-wrap:wrap;flex:none}
.tabs .tab{height:34px;padding:0 14px;border-radius:10px;border:1px solid transparent;background:transparent;color:var(--ah-text-2);font-weight:500;font-size:14px}
.tabs .tab:hover{background:var(--ah-segment)}
.tabs .tab.on{background:var(--ah-selected);color:var(--ah-text-on-selected);font-weight:600}
.badge{display:inline-block;min-width:18px;height:18px;line-height:16px;padding:0 6px;margin-left:6px;border-radius:999px;font-size:11px;font-weight:600;text-align:center;
border:1px solid var(--ah-chip-bad-line);background:linear-gradient(var(--ah-chip-bad-bg),var(--ah-chip-bad-bg)),var(--ah-chip-underlay);color:var(--ah-chip-bad-text)}
.badge.warn{border-color:var(--ah-chip-warn-line);background:linear-gradient(var(--ah-chip-warn-bg),var(--ah-chip-warn-bg)),var(--ah-chip-underlay);color:var(--ah-chip-warn-text)}
.tabs .tab.on .badge{background:var(--ah-on-accent);border-color:var(--ah-on-accent);color:var(--ah-text)}
.hubstate{margin-left:auto;font-size:12px;color:var(--ah-text-3);padding:0 4px}
.hubstate.ok::before{content:"";display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--ah-success);margin-right:6px}
.hubstate.bad{color:var(--ah-chip-bad-text)}
.view{flex:1;min-height:0;overflow:auto}
.view.farm{display:flex;flex-direction:column;gap:14px;overflow:hidden}
.view h2{margin:16px 0 8px}
.view>h2:first-child,.view.farm h2{margin-top:0}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:12px}
#printers{flex:0 1 auto;max-height:46%;overflow:auto;min-height:0}
.cols{flex:1;min-height:0;display:grid;grid-template-columns:minmax(0,1.7fr) minmax(0,1fr);gap:14px}
.cols>.card{display:flex;flex-direction:column;min-height:0;padding:var(--ah-card-pad)}
.chead{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;flex:none}
.chead h2{margin:0}
.scroll{flex:1;min-height:0;overflow:auto;margin-top:8px}
.card{padding:12px 14px}
.card .name{font-weight:700;font-size:14px}
.card .state{font-size:12px;margin-top:2px}
.card .file{font-size:12px;margin-top:6px;word-break:break-word}
.card .ready{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:8px}
.card .nozzles{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:6px;font-size:12px;color:var(--ah-text-3)}
.card .nozzles .dd{margin-left:6px}
.card .nozzles .dd-btn{height:28px;font-size:12px;padding:0 8px;background:var(--ah-field)}
.card .nozzles .lock{font-style:italic}
.card .nozzles button{height:28px;padding:0 10px;font-size:12px}
.card .ready button{height:28px;padding:0 10px;font-size:12px}
.card .temps{font-size:12px;color:var(--ah-text-3);margin-top:6px}
.dd{position:relative;display:inline-block}
.dd-list{position:fixed;z-index:50;max-height:260px;overflow:auto;background:var(--ah-solid-panel);border:1px solid var(--ah-field-line);border-radius:10px;
box-shadow:0 10px 30px rgba(0,0,0,.35);padding:4px 0;text-align:left}
.dd-item{padding:6px 12px;cursor:pointer;white-space:nowrap;color:var(--ah-text)}
.dd-item:hover{background:var(--ah-selected);color:var(--ah-text-on-selected)}
.dd-item.sel{font-weight:700}
.off{opacity:.6}
table{width:100%;border-collapse:separate;border-spacing:0}
th{position:sticky;top:0;z-index:1;background:var(--ah-table-head);text-align:left;font-weight:600;color:var(--ah-text-2);font-size:12px;padding:7px 8px;border-bottom:1px solid var(--ah-row-line)}
td{padding:8px;border-bottom:1px solid var(--ah-row-line);vertical-align:top;font-size:13px}
td.act{text-align:right}
td.act>*{margin:2px 0 2px 4px;vertical-align:middle}
.empty{color:var(--ah-text-3);padding:14px 0}
.locked{background:var(--ah-panel);border:1px solid var(--ah-panel-line);border-radius:var(--ah-radius-choice);padding:14px 16px;color:var(--ah-text-2)}
.stats{font-size:12px;color:var(--ah-text-3)}
.prio{width:64px;margin-right:4px}
.mine{font-size:11px;color:var(--ah-accent);margin-left:6px}
td.grip{width:26px;cursor:grab;color:var(--ah-text-3);font-size:16px;user-select:none;text-align:center;padding-left:2px;padding-right:2px}
tr.dragging{opacity:.4}
tr.drop-before td{box-shadow:inset 0 2px 0 var(--ah-accent)}
tr.drop-after td{box-shadow:inset 0 -2px 0 var(--ah-accent)}
button.mv{padding:0 8px}
.srow{padding:9px 0;border-top:1px solid var(--ah-row-line)}
.srow:first-child{border-top:0;padding-top:2px}
.srow .line{display:flex;align-items:center;gap:10px}
.srow .nm{flex:1;min-width:0;font-weight:600;font-size:13px;word-break:break-word}
.srow .acts{display:flex;gap:6px;flex-wrap:wrap;margin-top:6px}
.grid.checks{grid-template-columns:repeat(auto-fill,minmax(260px,1fr))}
.card.check .photo{height:150px;border-radius:12px;background:var(--ah-panel);display:flex;align-items:center;justify-content:center;overflow:hidden;margin:-2px 0 8px}
.card.check .photo img{width:100%;height:100%;object-fit:cover}
.chk{font-size:12px;display:inline-flex;align-items:center;gap:4px}
.kpis{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:12px;margin-bottom:16px}
.card.kpi .big{font-family:var(--ah-font-display);font-weight:500;letter-spacing:-0.03em;font-size:28px;line-height:1.1;margin-top:4px;font-variant-numeric:tabular-nums}
.tl{margin-bottom:20px}
.tl-row{display:flex;align-items:stretch;gap:10px;margin-bottom:6px}
.tl-name{width:110px;flex:none;font-weight:600;font-size:12px}
.tl-lane{position:relative;flex:1;min-height:30px;border-radius:8px;background:var(--ah-panel)}
.tl-lane.off{opacity:.55}
.tl-scale .tl-lane{background:none;min-height:16px}
.tl-tick{position:absolute;top:0;transform:translateX(-50%);font-size:11px;color:var(--ah-text-3);white-space:nowrap}
.tl-bar{position:absolute;top:3px;bottom:3px;border-radius:6px;background:var(--ah-chip-live-bg);border:1px solid var(--ah-chip-live-line);
font-size:11px;padding:2px 6px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;color:var(--ah-chip-live-text)}
.tl-bar.run{background:var(--ah-accent);color:var(--ah-on-accent)}
.tl-bar.order{border-color:var(--ah-attention);box-shadow:inset 3px 0 0 var(--ah-attention)}
.tl-tick:last-child{transform:translateX(-100%)}
#queue td:nth-child(5){white-space:nowrap}
@media (max-width:1099px){body{overflow:auto}.app{height:auto;min-height:100vh}.view.farm{overflow:visible}#printers{max-height:none}
.cols{grid-template-columns:minmax(0,1fr)}.scroll{max-height:60vh}}
</style></head><body>
<div class="app">
<div class="top"><div class="ttl"><h1>OpenPrintHQ</h1><div class="sub" id="ver"></div></div>
  <div class="alerts" id="alerts"></div><span class="msg" id="msg"></span>
  <button id="refresh">Refresh</button></div>
<div class="tabs" id="tabs"></div>
<div class="view farm" data-view="farm">
<div class="grid" id="printers"><div class="empty">Loading...</div></div>
<div class="cols">
<section class="card"><div class="chead"><h2 class="q">Farm queue</h2><div class="stats" id="stats"></div></div>
<div class="scroll">
<table id="qtable" style="display:none"><thead><tr><th></th><th>Job</th><th>Printer</th><th>Status</th><th>Priority</th><th></th></tr></thead><tbody id="queue"></tbody></table>
<div class="note" id="qhint" style="display:none">Drag rows by the handle, or use the arrows, to change the queue order. Jobs of the same priority run in this order; a higher priority still goes first, and priorities are not changed.</div>
<div class="locked" id="locked">Not set up yet. Enter the OpenPrintHQ address and an API key on the plugin's Config page (Plugins, OpenPrintHQ Print Queue, Config) to see the whole farm queue here, change
priorities and send a job to print now on a chosen printer. OpenPrintHQ runs higher priorities first.</div>
</div></section>
<section class="card"><div class="chead"><h2>Sent from this OrcaSlicer</h2></div>
<div class="scroll" id="jobs"></div></section>
</div>
</div>
<div class="view" data-view="checks" id="v-checks" style="display:none"></div>
<div class="view" data-view="timeline" id="v-timeline" style="display:none"></div>
<div class="view" data-view="maint" id="v-maint" style="display:none"></div>
<div class="view" data-view="filament" id="v-filament" style="display:none"></div>
<div class="view" data-view="reports" id="v-reports" style="display:none"></div>
</div>
<script>
(function(){
function $(id){ return document.getElementById(id); }
function el(t, a, txt){ var e = document.createElement(t); for (var k in (a||{})) e.setAttribute(k, a[k]); if (txt != null) e.textContent = txt; return e; }
var LABEL = {queued:"Queued", active:"Printing", done:"Finished", failed:"Failed", cancelled:"Cancelled"};
var state = null, armed = null;
function msg(t, cls){ var m = $("msg"); m.textContent = t || ""; m.className = "msg " + (cls || ""); }
function send(o){ if (window.orca && orca.postMessage) orca.postMessage(o); }
function temp(t){ if (!t || t.current == null) return ""; return Math.round(t.current) + (t.target ? " / " + Math.round(t.target) : "") + " °C"; }
function mins(m){ if (m == null) return ""; m = Math.round(m); return m < 60 ? m + " min left" : Math.floor(m/60) + " h " + ("0" + (m%60)).slice(-2) + " min left"; }
var qpick = {};
var NOZZLES = [0.2, 0.25, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0], nozzleDraft = {};
// 0.10.1: an HTML dropdown instead of a native <select>. Native select popups did not open in
// Orca's tab webview, and a refresh rebuilding the cards would close one anyway. While a list is
// open, render() leaves the printer cards and the queue alone.
var openDD = null;
// Closing does not re-render: a re-render on the mousedown that closed the list would replace
// the button being clicked and swallow the click. The next state push catches up.
function ddClose(){ if (!openDD) return; var o = openDD; openDD = null; if (o.parentNode) o.parentNode.removeChild(o); }
document.addEventListener("mousedown", function(e){ if (openDD && !openDD.contains(e.target) && !(openDD.__owner && openDD.__owner.contains(e.target))) ddClose(); }, true);
document.addEventListener("keydown", function(e){ if (e.key === "Escape") ddClose(); });
document.addEventListener("scroll", function(e){ if (openDD && !openDD.contains(e.target)) ddClose(); }, true);
window.addEventListener("resize", function(){ ddClose(); });
function placeList(list, b){
  var r = b.getBoundingClientRect();
  list.style.minWidth = r.width + "px";
  list.style.left = Math.max(4, Math.min(r.left, window.innerWidth - list.offsetWidth - 4)) + "px";
  var below = window.innerHeight - r.bottom;
  list.style.top = (below < list.offsetHeight + 8 && r.top > below ? Math.max(4, r.top - list.offsetHeight - 2) : r.bottom + 2) + "px";
}
function dropdown(opts, value, onPick, disabled, title){
  var w = el("span", {"class": "dd"});
  var cur = opts.filter(function(o){ return String(o.value) === String(value); })[0];
  var b = el("button", {"class": "dd-btn", type: "button", title: title || ""}, (cur ? cur.label : String(value)) + " \u25BE");
  b.disabled = !!disabled;
  b.onclick = function(e){
    e.stopPropagation();
    if (!b.isConnected) return;
    if (openDD && openDD.__owner === b) { ddClose(); return; }
    if (openDD) { var o = openDD; openDD = null; if (o.parentNode) o.parentNode.removeChild(o); }
    var list = el("div", {"class": "dd-list", role: "listbox"}); list.__owner = b;
    opts.forEach(function(o){
      var it = el("div", {"class": "dd-item" + (String(o.value) === String(value) ? " sel" : ""), role: "option"}, o.label);
      it.onclick = function(ev){ ev.stopPropagation(); openDD = null; if (list.parentNode) list.parentNode.removeChild(list); onPick(o.value); };
      list.appendChild(it);
    });
    document.body.appendChild(list); placeList(list, b); openDD = list;
  };
  w.appendChild(b);
  return w;
}
function nozzleRow(card, p){
  var have = state && state.nozzles && state.nozzles[p.name];
  if (!have || !have.length) return;
  var draft = nozzleDraft[p.name] || have.slice();
  var row = el("div", {"class": "nozzles"});
  draft.forEach(function(v, i){
    // A span, not a label: a label re-sends clicks inside it to the dropdown button.
    var lab = el("span", {}, have.length > 1 ? "Nozzle " + (i + 1) : "Nozzle");
    var opts = NOZZLES.slice(); if (opts.indexOf(Number(v)) < 0) { opts.push(Number(v)); opts.sort(function(a, b){ return a - b; }); }
    var dd = dropdown(opts.map(function(o){ return {value: o, label: o + " mm"}; }), Number(v), function(nv){
      var d = (nozzleDraft[p.name] || have.slice()).slice(); d[i] = Number(nv); nozzleDraft[p.name] = d; renderPrinters();
    }, !!p.busy, p.busy ? "Change the nozzle when the printer is idle" : "Nozzle size OpenPrintHQ uses for " + p.name + " (pre-send checks and job matching)");
    lab.appendChild(dd); row.appendChild(lab);
  });
  // 0.10.2: say why the dropdown is locked instead of leaving only a tooltip.
  if (p.busy) row.appendChild(el("span", {"class": "lock"}, "locked while " + String(p.state || "busy").toLowerCase()));
  var changed = draft.some(function(v, i){ return Number(v) !== Number(have[i]); });
  if (changed) {
    var ap = el("button", {"class": "armed", title: "Saves the new size in OpenPrintHQ's printer hardware settings"}, "Set in OpenPrintHQ");
    ap.onclick = function(){ send({cmd: "set_nozzle", printer: p.name, sizes: draft}); msg("Setting " + p.name + " nozzle...", ""); delete nozzleDraft[p.name]; };
    var cx = el("button", {}, "Undo");
    cx.onclick = function(){ delete nozzleDraft[p.name]; renderPrinters(); };
    row.appendChild(ap); row.appendChild(cx);
  }
  card.appendChild(row);
}
function renderPrinters(){
  var g = $("printers"); g.innerHTML = "";
  var list = (state && state.printers) || [];
  if (!list.length) { g.appendChild(el("div", {"class": "empty"}, state && state.error ? state.error : "No printers visible to this API key.")); return; }
  list.forEach(function(p){
    var c = el("div", {"class": "card" + (p.online ? "" : " off")});
    c.appendChild(el("div", {"class": "name"}, p.name));
    nozzleRow(c, p);
    var st = (p.state || (p.online ? "Online" : "Offline"));
    c.appendChild(el("div", {"class": "state " + (p.busy ? "s-active" : (p.online ? "s-done" : "s-queued"))}, st + (p.family ? "  ·  " + p.family : "")));
    if (p.file) c.appendChild(el("div", {"class": "file"}, p.file));
    if (p.online && !p.busy && p.ready != null) {
      var rd = el("div", {"class": "ready"});
      if (p.ready) rd.appendChild(el("span", {"class": "pill s-done", title: "OpenPrintHQ can start the next job on this printer"}, "Ready"));
      else {
        rd.appendChild(el("span", {"class": "pill s-wait", title: "OpenPrintHQ will not start a job here until the bed is marked clear"}, "Not ready: bed not cleared"));
        var rkey = "ready:" + p.name;
        var rb = el("button", {"class": armed === rkey ? "armed" : "", title: "Tell OpenPrintHQ the bed is clear. Its next job for this printer may start right away."}, armed === rkey ? "Bed is clear: set ready" : "Set ready");
        rb.onclick = function(){ if (armed !== rkey) { armed = rkey; renderPrinters(); setTimeout(function(){ if (armed === rkey) { armed = null; renderPrinters(); } }, 5000); return; }
          armed = null; send({cmd: "set_ready", printer: p.name}); msg("Marking " + p.name + " ready...", ""); renderPrinters(); };
        rd.appendChild(rb);
      }
      c.appendChild(rd);
    }
    if (p.busy && p.progress != null) {
      var b = el("div", {"class": "bar"}); var i = el("i"); i.style.width = Math.max(0, Math.min(100, p.progress)) + "%"; b.appendChild(i); c.appendChild(b);
      c.appendChild(el("div", {"class": "temps"}, Math.round(p.progress) + "%" + (p.eta_minutes != null ? ", " + mins(p.eta_minutes) : "") + (p.layers ? ", layer " + p.layers.current + " of " + p.layers.total : "")));
    }
    var t = p.temperatures || {};
    var tt = [temp(t.nozzle) && "Nozzle " + temp(t.nozzle), temp(t.bed) && "Bed " + temp(t.bed), temp(t.chamber) && "Chamber " + temp(t.chamber)].filter(Boolean).join("   ");
    if (tt) c.appendChild(el("div", {"class": "temps"}, tt));
    g.appendChild(c);
  });
}
function renderJobs(){
  var box = $("jobs"); box.innerHTML = "";
  var jobs = (state && state.jobs) || [];
  if (!jobs.length) { box.appendChild(el("div", {"class": "empty"}, "Nothing sent yet. Jobs you Print or Send through the OpenPrintHQ agent show up here.")); return; }
  jobs.forEach(function(j){
    var r = el("div", {"class": "srow"});
    var line = el("div", {"class": "line"});
    line.appendChild(el("span", {"class": "nm"}, "#" + j.id + "  " + j.name));
    line.appendChild(el("span", {"class": "pill s-" + j.state}, (j.status === "paused") ? "Paused" : (LABEL[j.state] || j.status)));
    r.appendChild(line);
    var where = (j.printer || j.requested || "") + (j.requested && j.printer && j.requested !== j.printer ? " (sent to " + j.requested + ")" : "");
    if (where) r.appendChild(el("div", {"class": "note"}, where));
    if (j.note) r.appendChild(el("div", {"class": "note"}, j.note));
    if (j.error) r.appendChild(el("div", {"class": "note", style: "color:var(--bad)"}, j.error));
    if (j.state === "active") { var b = el("div", {"class": "bar"}); var i = el("i"); i.style.width = Math.max(0, Math.min(100, Number(j.progress) || 0)) + "%"; b.appendChild(i); r.appendChild(b); }
    var c4 = el("div", {"class": "acts"});
    if (j.state === "active") { var pr = el("button", {}, j.status === "paused" ? "Resume" : "Pause"); pr.onclick = function(){ send({cmd: j.status === "paused" ? "resume" : "pause", id: j.id}); msg("Sending...", ""); }; c4.appendChild(pr); }
    if (j.state === "queued" || j.state === "active") {
      var cx = el("button", {"class": armed === j.id ? "armed" : "danger"}, armed === j.id ? "Confirm cancel" : "Cancel");
      cx.onclick = function(){ if (armed !== j.id) { armed = j.id; renderJobs(); setTimeout(function(){ if (armed === j.id) { armed = null; renderJobs(); } }, 4000); return; } armed = null; send({cmd: "cancel", id: j.id}); msg("Cancelling...", ""); };
      c4.appendChild(cx);
    }
    var cp = el("button", {}, "+1 copy"); cp.onclick = function(){ send({cmd: "copies", id: j.id, n: 1}); msg("Adding a copy...", ""); }; c4.appendChild(cp);
    r.appendChild(c4); box.appendChild(r);
  });
}
function renderAlerts(){
  var box = $("alerts"); box.innerHTML = "";
  var a = (state && state.alerts) || {};
  (a.down || []).forEach(function(t){ box.appendChild(el("div", {"class": "alert bad"}, "Out of service: " + t)); });
  if ((a.low || []).length) box.appendChild(el("div", {"class": "alert"}, "Low stock: " + a.low.join(", ")));
  var si = (state && state.signin) || {};
  if (si.configured && si.error) box.appendChild(el("div", {"class": "alert bad"}, si.error));
}
function renderQueue(){
  var si = (state && state.signin) || {};
  var rows = state && state.queue;
  $("locked").style.display = (si.configured && rows) ? "none" : "";
  if (si.configured && !rows) $("locked").textContent = si.error ? ("Farm queue unavailable: " + si.error) : "Loading the farm queue...";
  $("qtable").style.display = rows ? "" : "none";
  var st = (state && state.alerts && state.alerts.stats) || {};
  $("stats").textContent = rows ? ["printing", "assigned", "queued"].filter(function(k){ return st[k] != null; }).map(function(k){ return st[k] + " " + k; }).join(", ") + (si.user ? "  \u00b7  key of " + si.user : "") : "";
  var tb = $("queue"); tb.innerHTML = "";
  if (!rows) return;
  $("qhint").style.display = rows.length > 1 ? "" : "none";
  if (!rows.length) { var tr0 = el("tr"); tr0.appendChild(el("td", {colspan: "6", "class": "empty"}, "The queue is empty.")); tb.appendChild(tr0); return; }
  var mine = {}; ((state && state.jobs) || []).forEach(function(j){ mine[j.id] = true; });
  var names = ((state && state.printers) || []).map(function(p){ return p.name; });
  rows.forEach(function(j, idx){
    var tr = el("tr", {"data-id": String(j.id)});
    var grip = el("td", {"class": "grip", title: "Drag to reorder"}, "\u2261");
    if (rows.length > 1) { tr.setAttribute("draggable", "true"); } else { grip.textContent = ""; }
    tr.addEventListener("dragstart", function(e){ dragId = j.id; tr.classList.add("dragging"); try { e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", String(j.id)); } catch (x) {} });
    tr.addEventListener("dragend", function(){ dragId = null; tr.classList.remove("dragging"); clearMarks(); });
    tr.addEventListener("dragover", function(e){ if (dragId == null || dragId === j.id) return; e.preventDefault(); clearMarks(); var r = tr.getBoundingClientRect(); tr.classList.add(e.clientY < r.top + r.height / 2 ? "drop-before" : "drop-after"); });
    tr.addEventListener("dragleave", function(){ tr.classList.remove("drop-before", "drop-after"); });
    tr.addEventListener("drop", function(e){ e.preventDefault(); if (dragId == null || dragId === j.id) return; var r = tr.getBoundingClientRect(); moveJob(dragId, j.id, e.clientY >= r.top + r.height / 2); dragId = null; clearMarks(); });
    var c1 = el("td"); var t = el("div", {}, "#" + j.id + "  " + j.name); if (mine[j.id]) t.appendChild(el("span", {"class": "mine"}, "from this OrcaSlicer")); c1.appendChild(t);
    if (j.hours) c1.appendChild(el("div", {"class": "note"}, "about " + (j.hours < 1 ? Math.round(j.hours * 60) + " min" : (Math.round(j.hours * 10) / 10) + " h") + (j.user ? ", by " + j.user : "")));
    var c2 = el("td", {}, (j.printer || "any") + (j.soft ? " (suggested)" : ""));
    var waitF = j.status === "waiting_filament";
    var c3 = el("td"); c3.appendChild(el("span", {"class": "pill " + (waitF ? "s-cancelled" : "s-" + j.state)}, j.status === "paused" ? "Paused" : (j.state === "active" ? "Printing" : (waitF ? "Needs filament" : (j.status === "held" ? "Held" : (j.status === "assigned" ? "Assigned" : "Queued"))))));
    if (waitF && j.materials && j.materials.length) c3.appendChild(el("div", {"class": "note"}, j.materials.join(", ")));
    else if (j.why && j.state === "queued") c3.appendChild(el("div", {"class": "note"}, j.why));
    if (j.state === "active") { var b = el("div", {"class": "bar"}); var i = el("i"); i.style.width = Math.max(0, Math.min(100, Number(j.progress) || 0)) + "%"; b.appendChild(i); c3.appendChild(b); }
    var c4 = el("td"); var pin = el("input", {type: "number", min: "0", max: "100", "class": "prio"}); pin.value = j.priority;
    var setb = el("button", {}, "Set"); setb.onclick = function(){ var v = Number(pin.value); if (!Number.isInteger(v) || v < 0 || v > 100) { msg("Priority must be 0 to 100.", "bad"); return; } send({cmd: "priority", id: j.id, priority: v}); msg("Setting priority...", ""); };
    if (j.state !== "active") { c4.appendChild(pin); c4.appendChild(setb); } else c4.appendChild(el("span", {"class": "muted"}, String(j.priority)));
    var c5 = el("td", {"class": "act"});
    if (j.state !== "active") {
      if (!qpick[j.id] || names.indexOf(qpick[j.id]) < 0) qpick[j.id] = names.indexOf(j.printer) >= 0 ? j.printer : names[0];
      var sel = {value: qpick[j.id]};
      var seldd = dropdown(names.map(function(n){ return {value: n, label: n}; }), sel.value, function(nv){ qpick[j.id] = nv; renderQueue(); }, false, "Printer for Print now and Clear bed & print");
      var pn = el("button", {}, "Print now on"); pn.onclick = function(){ send({cmd: "print_now", id: j.id, printer: sel.value}); msg("Sending to " + sel.value + "...", ""); };
      c5.appendChild(pn); c5.appendChild(seldd);
      var ckey = "c" + j.id;
      var cb = el("button", {"class": armed === ckey ? "armed" : "", title: "Puts this job first on the printer, then marks its bed clear so it can start"}, armed === ckey ? "Bed is clear: print" : "Clear bed & print");
      cb.onclick = function(){ if (armed !== ckey) { armed = ckey; renderQueue(); setTimeout(function(){ if (armed === ckey) { armed = null; renderQueue(); } }, 5000); return; } armed = null; send({cmd: "clear_print", id: j.id, printer: sel.value}); msg("Clearing the bed on " + sel.value + " and printing...", ""); };
      c5.appendChild(cb);
    }
    if (j.state === "active") {
      var cx = el("button", {"class": armed === "q" + j.id ? "armed" : "danger"}, armed === "q" + j.id ? "Confirm cancel" : "Cancel");
      cx.onclick = function(){ var key = "q" + j.id; if (armed !== key) { armed = key; renderQueue(); setTimeout(function(){ if (armed === key) { armed = null; renderQueue(); } }, 4000); return; } armed = null; send({cmd: "cancel", id: j.id}); msg("Cancelling...", ""); };
      c5.appendChild(cx);
    } else {
      var rkey = "r" + j.id;
      var rm = el("button", {"class": armed === rkey ? "armed" : "danger", title: "Take this job out of the OpenPrintHQ queue"}, armed === rkey ? "Confirm remove" : "Remove");
      rm.onclick = function(){ if (armed !== rkey) { armed = rkey; renderQueue(); setTimeout(function(){ if (armed === rkey) { armed = null; renderQueue(); } }, 4000); return; } armed = null; send({cmd: "remove", id: j.id}); msg("Removing job #" + j.id + "...", ""); };
      c5.appendChild(rm);
    }
    if (rows.length > 1) {
      var up = el("button", {"class": "mv", title: "Move up", "aria-label": "Move job " + j.id + " up"}, "\u2191"); up.disabled = idx === 0;
      up.onclick = function(){ moveJob(j.id, rows[idx - 1].id, false); };
      var dn = el("button", {"class": "mv", title: "Move down", "aria-label": "Move job " + j.id + " down"}, "\u2193"); dn.disabled = idx === rows.length - 1;
      dn.onclick = function(){ moveJob(j.id, rows[idx + 1].id, true); };
      c5.appendChild(up); c5.appendChild(dn);
    }
    tr.appendChild(grip); tr.appendChild(c1); tr.appendChild(c2); tr.appendChild(c3); tr.appendChild(c4); tr.appendChild(c5); tb.appendChild(tr);
  });
}
var dragId = null;
function clearMarks(){ Array.prototype.forEach.call(document.querySelectorAll("#queue tr"), function(r){ r.classList.remove("drop-before", "drop-after"); }); }
function moveJob(id, targetId, after){
  var rows = (state && state.queue) || [];
  var moving = rows.filter(function(r){ return r.id === id; })[0];
  if (!moving) return;
  var rest = rows.filter(function(r){ return r.id !== id; });
  var at = rest.map(function(r){ return r.id; }).indexOf(targetId);
  if (at < 0) return;
  rest.splice(after ? at + 1 : at, 0, moving);
  state.queue = rest;               // show the new order right away
  renderQueue();
  send({cmd: "reorder", order: rest.map(function(r){ return r.id; })});
  msg("Saving order...", "");
}
function editing(){ var a = document.activeElement; return dragId != null || !!(a && a.closest && a.closest("#queue") && (a.tagName === "INPUT" || a.tagName === "SELECT")); }
function nozzleFocus(){ var a = document.activeElement; return !!(a && a.tagName === "SELECT" && a.closest && a.closest("#printers")); }
function render(){ if (!openDD) renderHub(); else renderTabs(); renderAlerts(); if (!openDD && !nozzleFocus()) renderPrinters(); renderJobs(); if (!openDD && !editing()) renderQueue(); if (state) { var np = (state.printers || []).length; $("ver").textContent = "plugin " + state.version + (np ? "  \u00b7  " + np + (np === 1 ? " printer" : " printers") : ""); } }
// ---- 0.11.0: the hub's views (plate checks, timeline, maintenance, filament, reports) and the look ----
var VIEWS = [["farm", "Farm"], ["checks", "Plate checks"], ["timeline", "Timeline"], ["maint", "Maintenance"],
             ["filament", "Filament"], ["reports", "Reports"]];
var view = "farm";
try { view = localStorage.getItem("wt.view") || "farm"; } catch (e) {}
var photos = {}, photoAsked = {}, failDraft = {}, hubBusy = false;
function hub(){ return (state && state.hub) || {configured: false}; }
function hd(){ return hub().data || null; }
function fmtTime(iso){ if (!iso) return ""; var d = new Date(iso); return isNaN(d) ? "" : d.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"}); }
function fmtDay(iso){ if (!iso) return ""; var d = new Date(iso); return isNaN(d) ? "" : d.toLocaleDateString([], {month: "short", day: "numeric"}) + " " + fmtTime(iso); }
function grams(g){ if (g == null) return ""; g = Number(g); return g >= 1000 ? (g / 1000).toFixed(2) + " kg" : Math.round(g) + " g"; }
function hubSend(o){ if (hubBusy && o.cmd !== "refresh") return; send(o); }
function counts(){
  var d = hd(); if (!d) return {};
  var m = d.maintenance || {};
  return {checks: (d.checks || []).length, maint: (m.open || []).length + (m.due || []).length,
          timeline: ((d.timeline || {}).orders || []).filter(function(o){ return o.state === "late" || o.state === "at_risk"; }).length};
}
function renderTabs(){
  var t = $("tabs"); if (!t) return; t.innerHTML = "";
  var c = counts();
  VIEWS.forEach(function(v){
    var b = el("button", {"class": "tab" + (view === v[0] ? " on" : ""), type: "button"}, v[1]);
    if (c[v[0]]) b.appendChild(el("span", {"class": "badge" + (v[0] === "maint" || v[0] === "timeline" ? " warn" : "")}, String(c[v[0]])));
    b.onclick = function(){ view = v[0]; try { localStorage.setItem("wt.view", view); } catch (e) {} renderTabs(); showView(); renderHub(); };
    t.appendChild(b);
  });
  var h = hub();
  var s = el("span", {"class": "hubstate " + (h.configured ? (h.ok ? "ok" : "bad") : "")},
             h.configured ? (h.ok ? "Hub" + (hd() && hd().team ? ": " + hd().team : "") : "Hub unreachable") : "");
  if (h.configured && !h.ok && h.error) s.title = h.error;
  t.appendChild(s);
}
function showView(){
  Array.prototype.forEach.call(document.querySelectorAll(".view"), function(v){ v.style.display = v.getAttribute("data-view") === view ? "" : "none"; });
}
function needHub(box){
  var h = hub();
  if (h.configured && hd()) return false;
  box.innerHTML = "";
  box.appendChild(el("div", {"class": "locked"}, h.configured
    ? "The hub did not answer" + (h.error ? ": " + h.error : ".") + " The view fills in when it does."
    : "This view comes from the OpenPrintHQ hub (plate checks, staggered start, timeline, maintenance, real filament use, reports). Add the hub's address and an API key on the plugin's Config page to see it here."));
  return true;
}
function section(box, title, note){
  box.appendChild(el("h2", {}, title));
  if (note) box.appendChild(el("div", {"class": "stats"}, note));
}
function btn(label, cls, fn, title){ var b = el("button", {"class": cls || "", type: "button", title: title || ""}, label); b.onclick = fn; b.disabled = hubBusy; return b; }
function armedBtn(key, label, confirmLabel, fn, cls){
  var b = btn(armed === key ? confirmLabel : label, armed === key ? "armed" : (cls || ""), function(){
    if (armed !== key) { armed = key; renderHub(); setTimeout(function(){ if (armed === key) { armed = null; renderHub(); } }, 5000); return; }
    armed = null; fn();
  });
  return b;
}
function act(o, text){ hubSend(o); msg(text, ""); }
function renderChecks(){
  var box = $("v-checks"); if (needHub(box)) return; box.innerHTML = "";
  var d = hd();
  if (d.plate_checks_on === false) box.appendChild(el("div", {"class": "alert"}, "Plate checks are turned off in the hub (Farm, Settings). Printers are set Ready by hand in OpenPrintHQ."));
  var st = d.stagger || {};
  section(box, "Waiting for a plate check", "Clear the plate, then record what came off it. Passing sets the printer Ready" +
          (st.minutes ? ", " + st.minutes + " minutes apart from the last start (staggered start)." : "."));
  var list = d.checks || [];
  if (!list.length) box.appendChild(el("div", {"class": "empty"}, "No printers are waiting. Checks open here when a print ends."));
  var grid = el("div", {"class": "grid checks"});
  list.forEach(function(c){
    var card = el("div", {"class": "card check"});
    var ph = el("div", {"class": "photo"});
    if (photos[c.id]) { var img = el("img", {src: photos[c.id], alt: "Plate on " + c.printer}); ph.appendChild(img); }
    else if (c.photo) { ph.appendChild(el("span", {"class": "muted"}, "Loading photo...")); if (!photoAsked[c.id]) { photoAsked[c.id] = 1; send({cmd: "hub_photo", id: c.id}); } }
    else ph.appendChild(el("span", {"class": "muted"}, "No camera photo"));
    card.appendChild(ph);
    card.appendChild(el("div", {"class": "name"}, c.printer));
    var oc = c.job_outcome || "completed";
    var line = el("div", {"class": "state"}); line.appendChild(el("span", {"class": "pill " + (oc === "completed" ? "s-done" : oc === "failed" ? "s-failed" : "s-cancelled")}, oc));
    card.appendChild(line);
    card.appendChild(el("div", {"class": "file"}, (c.job_name || "") + (c.model ? "  (" + (c.model.code || c.model.name) + ")" : "")));
    card.appendChild(el("div", {"class": "note"}, "Ended " + fmtDay(c.created_at)));
    var row = el("div", {"class": "ready"});
    if (d.can_act === false) { row.appendChild(el("span", {"class": "muted"}, "This hub key can only look.")); card.appendChild(row); grid.appendChild(card); return; }
    if (!failDraft[c.id]) {
      row.appendChild(btn("Passed, set Ready", "primary", function(){ act({cmd: "hub_pass", id: c.id, set_ready: true}, "Recording the pass..."); }, "The plate is clear and the print is good"));
      row.appendChild(btn("Passed", "", function(){ act({cmd: "hub_pass", id: c.id, set_ready: false}, "Recording the pass..."); }, "Record it without setting the printer Ready"));
      row.appendChild(btn("Failed...", "danger", function(){ failDraft[c.id] = {reason: "", reprint: true}; renderHub(); }));
      row.appendChild(armedBtn("skip" + c.id, "Skip", "Confirm skip", function(){ act({cmd: "hub_skip", id: c.id}, "Skipping..."); }));
    } else {
      var f = failDraft[c.id], reasons = d.reasons || {};
      var opts = Object.keys(reasons).map(function(k){ return {value: k, label: reasons[k]}; });
      opts.unshift({value: "", label: "Why did it fail?"});
      row.appendChild(dropdown(opts, f.reason, function(v){ f.reason = v; renderHub(); }, false, "Reason"));
      // a span, not a label (0.10.1: a label re-sends clicks to the dropdown next to it)
      var rp = el("span", {"class": "chk"}); var cb = el("input", {type: "checkbox", id: "rp" + c.id}); cb.checked = !!f.reprint; cb.onchange = function(){ f.reprint = cb.checked; };
      var rt = el("span", {style: "cursor:pointer"}, "Reprint"); rt.onclick = function(){ cb.checked = !cb.checked; f.reprint = cb.checked; };
      rp.appendChild(cb); rp.appendChild(rt); row.appendChild(rp);
      var go = btn("Record failure", "armed", function(){ if (!f.reason) { msg("Pick why it failed.", "bad"); return; } delete failDraft[c.id]; act({cmd: "hub_fail", id: c.id, reason: f.reason, reprint: f.reprint, set_ready: true}, "Recording the failure..."); });
      row.appendChild(go); row.appendChild(btn("Back", "", function(){ delete failDraft[c.id]; renderHub(); }));
    }
    card.appendChild(row); grid.appendChild(card);
  });
  box.appendChild(grid);
  if ((st.waiting || []).length) {
    section(box, "Staggered start", "Printers that passed their check and wait their turn to be set Ready.");
    var t = el("table"); var tb = el("tbody");
    st.waiting.forEach(function(w){ var tr = el("tr"); tr.appendChild(el("td", {}, w.printer)); tr.appendChild(el("td", {}, "Ready at " + fmtTime(w.due))); tb.appendChild(tr); });
    t.appendChild(tb); box.appendChild(t);
  }
  var rec = d.recent_checks || [];
  if (rec.length) {
    section(box, "Recent checks");
    var t2 = el("table"); var h2 = el("thead"); var hr = el("tr");
    ["Printer", "Job", "Result", "When", ""].forEach(function(x){ hr.appendChild(el("th", {}, x)); }); h2.appendChild(hr); t2.appendChild(h2);
    var b2 = el("tbody");
    rec.forEach(function(c){
      var tr = el("tr"); tr.appendChild(el("td", {}, c.printer)); tr.appendChild(el("td", {}, c.job_name || ""));
      var r = el("td"); r.appendChild(el("span", {"class": "pill " + (c.state === "passed" ? "s-done" : c.state === "failed" ? "s-failed" : "s-queued")}, c.state));
      if (c.reason_label) r.appendChild(el("div", {"class": "note"}, c.reason_label));
      if (c.ready_error) r.appendChild(el("div", {"class": "note", style: "color:var(--bad)"}, "Ready failed: " + c.ready_error));
      tr.appendChild(r); tr.appendChild(el("td", {}, fmtDay(c.checked_at)));
      var a = el("td", {"class": "act"});
      if (c.ready_error && d.can_act !== false) a.appendChild(btn("Retry Ready", "", function(){ act({cmd: "hub_retry_ready", id: c.id}, "Asking for Ready again..."); }));
      tr.appendChild(a); b2.appendChild(tr);
    });
    t2.appendChild(b2); box.appendChild(t2);
  }
}
function renderTimeline(){
  var box = $("v-timeline"); if (needHub(box)) return; box.innerHTML = "";
  var d = hd(), tl = d.timeline;
  if (!tl) { box.appendChild(el("div", {"class": "locked"}, "The timeline needs the farm: " + ((d.errors || {}).timeline || "not available"))); return; }
  var t0 = new Date(tl.now).getTime(), t1 = new Date(tl.until).getTime(), span = Math.max(1, t1 - t0);
  section(box, "Next " + Math.round(span / 3600000) + " hours", "Running and queued jobs per printer, highest priority first, with " + (tl.turnaround_minutes || 0) + " minutes to clear each plate. Estimates.");
  var wrap = el("div", {"class": "tl"});
  var scale = el("div", {"class": "tl-row tl-scale"}); scale.appendChild(el("div", {"class": "tl-name"}, ""));
  var sc = el("div", {"class": "tl-lane"});
  for (var h = 0; h <= 12; h += 2) { var tick = el("span", {"class": "tl-tick"}, fmtTime(new Date(t0 + h * span / 12).toISOString())); tick.style.left = (h / 12 * 100) + "%"; sc.appendChild(tick); }
  scale.appendChild(sc); wrap.appendChild(scale);
  (tl.lanes || []).forEach(function(l){
    var row = el("div", {"class": "tl-row"});
    var nm = el("div", {"class": "tl-name"}, l.printer); nm.appendChild(el("div", {"class": "note"}, l.down ? "Out of service" : (l.state || (l.online ? "Online" : "Offline"))));
    row.appendChild(nm);
    var lane = el("div", {"class": "tl-lane" + (l.online ? "" : " off")});
    (l.bars || []).forEach(function(b){
      var s = Math.max(t0, new Date(b.start).getTime()), e = Math.min(t1, new Date(b.end).getTime());
      if (e <= t0 || s >= t1) return;
      var bar = el("div", {"class": "tl-bar" + (b.status === "printing" ? " run" : "") + ((b.orders || []).length ? " order" : ""),
                           title: (b.name || "") + "\n" + fmtTime(b.start) + " to " + fmtTime(b.end) + ((b.orders || []).length ? "\nOrder " + b.orders.join(", ") : "")}, b.name || "");
      bar.style.left = ((s - t0) / span * 100) + "%"; bar.style.width = Math.max(1.5, (e - s) / span * 100) + "%";
      lane.appendChild(bar);
    });
    row.appendChild(lane); wrap.appendChild(row);
  });
  box.appendChild(wrap);
  var orders = tl.orders || [];
  section(box, "Orders", orders.length ? "When each order's last print should finish, against its due date." : "No store orders are printing.");
  if (orders.length) {
    var t = el("table"); var th = el("thead"); var hr = el("tr");
    ["Order", "Due", "Projected", "Jobs left", ""].forEach(function(x){ hr.appendChild(el("th", {}, x)); }); th.appendChild(hr); t.appendChild(th);
    var tb = el("tbody");
    orders.forEach(function(o){
      var tr = el("tr");
      var c1 = el("td", {}, "#" + o.number); if (o.customer) c1.appendChild(el("div", {"class": "note"}, o.customer)); tr.appendChild(c1);
      tr.appendChild(el("td", {}, fmtDay(o.due_at)));
      var c3 = el("td"); c3.appendChild(document.createTextNode(fmtDay(o.projected) + " "));
      var cls = {late: "s-failed", at_risk: "s-wait", on_time: "s-done"}[o.state] || "s-queued";
      c3.appendChild(el("span", {"class": "pill " + cls}, String(o.state || "").replace("_", " "))); tr.appendChild(c3);
      tr.appendChild(el("td", {}, o.jobs_left + " of " + o.jobs + (o.stuck ? " (" + o.stuck + " stuck)" : "")));
      var a = el("td", {"class": "act"});
      if (d.can_act !== false && (o.state === "late" || o.state === "at_risk")) a.appendChild(btn("Rush (priority 100)", "primary", function(){ act({cmd: "hub_prioritise", id: o.id, priority: 100}, "Raising priority..."); }));
      else if (d.can_act !== false && o.jobs_left) a.appendChild(btn("Priority 75", "", function(){ act({cmd: "hub_prioritise", id: o.id, priority: 75}, "Raising priority..."); }));
      tr.appendChild(a); tb.appendChild(tr);
    });
    t.appendChild(tb); box.appendChild(t);
  }
  if ((tl.unscheduled || []).length) {
    section(box, "Not scheduled");
    var ul = el("table"); var ub = el("tbody");
    tl.unscheduled.forEach(function(u){ var tr = el("tr"); tr.appendChild(el("td", {}, u.name || ("Job " + u.job))); tr.appendChild(el("td", {"class": "muted"}, u.why)); ub.appendChild(tr); });
    ul.appendChild(ub); box.appendChild(ul);
  }
}
function renderMaint(){
  var box = $("v-maint"); if (needHub(box)) return; box.innerHTML = "";
  var d = hd(), m = d.maintenance;
  if (!m) { box.appendChild(el("div", {"class": "locked"}, "Maintenance is not available: " + ((d.errors || {}).maintenance || ""))); return; }
  if ((m.down || []).length) m.down.forEach(function(x){ box.appendChild(el("div", {"class": "alert bad"}, (x.printer_name || x.name || x.printer || "A printer") + " is out of service" + (x.reason ? ": " + x.reason : ""))); });
  section(box, "Open tasks", (m.linked ? "Done here also completes the schedule in the farm. " : "") + "Parts listed on a task come out of InvenTree when it is done.");
  if (!(m.open || []).length) box.appendChild(el("div", {"class": "empty"}, "Nothing open."));
  else {
    var t = el("table"); var th = el("thead"); var hr = el("tr");
    ["Printer", "Task", "Due", ""].forEach(function(x){ hr.appendChild(el("th", {}, x)); }); th.appendChild(hr); t.appendChild(th);
    var tb = el("tbody");
    m.open.forEach(function(x){
      var tr = el("tr"); tr.appendChild(el("td", {}, x.printer));
      var c2 = el("td", {}, x.title); if (x.out_of_service) c2.appendChild(el("div", {"class": "note", style: "color:var(--bad)"}, "Printer held out of service until done"));
      if ((x.parts || []).length) c2.appendChild(el("div", {"class": "note"}, "Parts: " + x.parts.map(function(p){ return (p.qty || 1) + " x " + (p.name || p.part_id); }).join(", ")));
      tr.appendChild(c2); tr.appendChild(el("td", {}, fmtDay(x.due_at)));
      var a = el("td", {"class": "act"});
      if (d.can_act !== false) a.appendChild(armedBtn("m" + x.id, "Done", "Confirm done", function(){ act({cmd: "hub_maint_done", id: x.id}, "Recording..."); }, "primary"));
      tr.appendChild(a); tb.appendChild(tr);
    });
    t.appendChild(tb); box.appendChild(t);
  }
  if ((m.due || []).length) {
    section(box, "Due by print hours or days", "Plans past their interval. Start the task from the hub's Maintenance page.");
    var t2 = el("table"); var b2 = el("tbody");
    m.due.forEach(function(p){ var tr = el("tr"); tr.appendChild(el("td", {}, p.printer)); tr.appendChild(el("td", {}, p.title));
      tr.appendChild(el("td", {"class": "muted"}, (p.hours_since != null ? Math.round(p.hours_since) + " h" : "") + (p.every_hours ? " of " + p.every_hours + " h" : "") + (p.days_since != null && p.every_days ? ", " + Math.round(p.days_since) + " of " + p.every_days + " days" : "")));
      b2.appendChild(tr); });
    t2.appendChild(b2); box.appendChild(t2);
  }
}
function renderFilament(){
  var box = $("v-filament"); if (needHub(box)) return; box.innerHTML = "";
  var d = hd(), f = d.filament || {}, sp = d.spools || {};
  var top = el("div", {"class": "kpis"});
  [["Measured prints", f.measured_jobs != null ? f.measured_jobs : ""], ["Measured vs estimate", f.measured_vs_estimate != null ? Math.round(f.measured_vs_estimate * 100) + "%" : "n/a"],
   ["Printers that report use", (f.klipper_printers || []).join(", ") || "none"], ["Spools linked", (sp.linked || 0) + (sp.broken ? " (" + sp.broken + " broken)" : "")]].forEach(function(k){
    var c = el("div", {"class": "card kpi"}); c.appendChild(el("div", {"class": "note"}, k[0])); c.appendChild(el("div", {"class": "big"}, String(k[1]))); top.appendChild(c); });
  box.appendChild(top);
  var bar = el("div", {"class": "ready"});
  if (d.can_act !== false) {
    bar.appendChild(btn("Measure again", "", function(){ act({cmd: "hub_measure", days: 7}, "Measuring..."); }, "Re-read the printers' own history for the last 7 days"));
    bar.appendChild(btn("Sync spools now", "", function(){ act({cmd: "hub_spool_sync"}, "Syncing spools..."); }, "Spoolman and the farm, both ways"));
  }
  if (sp.last && sp.last.at) bar.appendChild(el("span", {"class": "note"}, "Last spool sync " + fmtDay(sp.last.at) + (sp.enabled === false ? " (turned off)" : "")));
  box.appendChild(bar);
  section(box, "Recent prints", "Filament each print really used where the printer reported it (Klipper) or a weigh-in corrected it; otherwise the slicer's estimate.");
  var rows = d.history || [];
  var t = el("table"); var th = el("thead"); var hr = el("tr");
  ["Job", "Printer", "Used", "Estimate", "Source"].forEach(function(x){ hr.appendChild(el("th", {}, x)); }); th.appendChild(hr); t.appendChild(th);
  var tb = el("tbody");
  var SRC = {printer: ["measured", "s-done"], weighed: ["weighed", "s-done"], progress: ["partial", "s-wait"], estimate: ["estimate", "s-queued"]};
  rows.forEach(function(j){
    var tr = el("tr"); tr.appendChild(el("td", {}, j.name || ("#" + j.external_id))); tr.appendChild(el("td", {}, j.printer || ""));
    tr.appendChild(el("td", {}, grams(j.used_g))); tr.appendChild(el("td", {"class": "muted"}, grams(j.filament_g)));
    var s = SRC[j.used_source] || ["", "s-queued"]; var c = el("td"); if (s[0]) c.appendChild(el("span", {"class": "pill " + s[1]}, s[0])); tr.appendChild(c);
    tb.appendChild(tr);
  });
  if (!rows.length) { var tr = el("tr"); tr.appendChild(el("td", {colspan: "5", "class": "empty"}, "No finished prints yet.")); tb.appendChild(tr); }
  t.appendChild(tb); box.appendChild(t);
  if ((f.weigh_ins || []).length) {
    section(box, "Recent weigh-ins", "A spool weighed in Spoolman spreads the difference over the prints on it since its last weigh-in.");
    var t2 = el("table"); var b2 = el("tbody");
    f.weigh_ins.forEach(function(w){ var tr = el("tr"); tr.appendChild(el("td", {}, "Spool #" + w.spool)); tr.appendChild(el("td", {}, fmtDay(w.at)));
      tr.appendChild(el("td", {}, w.factor != null ? "x" + Number(w.factor).toFixed(3) + " on " + w.jobs + " prints" : (w.note || ""))); b2.appendChild(tr); });
    t2.appendChild(b2); box.appendChild(t2);
  }
}
function renderReports(){
  var box = $("v-reports"); if (needHub(box)) return; box.innerHTML = "";
  var d = hd(), r = d.report;
  if (!r) { box.appendChild(el("div", {"class": "locked"}, "Reports are not available: " + ((d.errors || {}).report || ""))); return; }
  section(box, "Last 30 days", "From the hub's Reports page, which has the per-printer, per-model and profit detail.");
  var top = el("div", {"class": "kpis"});
  [["Prints", r.jobs], ["Success rate", r.success_rate != null ? r.success_rate + "%" : "n/a"], ["Print hours", r.print_hours], ["Filament", grams(r.filament_g)],
   ["Measured filament", r.measured_jobs ? grams(r.measured_g) + " on " + r.measured_jobs + " prints" : "none yet"], ["Failed", r.failed],
   ["Filament wasted", grams(r.waste_g)], ["Cost of failures", r.waste_cost != null ? Number(r.waste_cost).toFixed(2) : ""],
   ["Quotes waiting", d.quotes_waiting || 0]].forEach(function(k){
    var c = el("div", {"class": "card kpi"}); c.appendChild(el("div", {"class": "note"}, k[0])); c.appendChild(el("div", {"class": "big"}, String(k[1] == null ? "" : k[1]))); top.appendChild(c); });
  box.appendChild(top);
}
function renderHub(){
  renderTabs(); showView();
  if (view === "checks") renderChecks(); else if (view === "timeline") renderTimeline();
  else if (view === "maint") renderMaint(); else if (view === "filament") renderFilament(); else if (view === "reports") renderReports();
}
function hubEditing(){ return !!openDD || Object.keys(failDraft).length > 0 && view === "checks" && !!document.activeElement && document.activeElement.tagName === "INPUT"; }
$("refresh").onclick = function(){ send({cmd: "refresh"}); msg("Refreshing...", ""); };
if (window.orca && orca.onMessage) orca.onMessage(function(m){
  if (!m) return;
  if (m.type === "state") { state = m; render(); if ($("msg").textContent === "Refreshing...") msg(""); }
  else if (m.type === "ack") { msg(m.text, m.ok ? "ok" : "bad"); }
  else if (m.type === "photo") { if (m.data) photos[m.id] = m.data; else photoAsked[m.id] = 0; if (view === "checks" && !openDD) renderChecks(); }
});
send({cmd: "hello"});
setInterval(function(){ if (!document.hidden) send({cmd: "refresh"}); }, 5000);
})();
</script></body></html>"""


_PagesBase = getattr(getattr(orca, "pages", None), "PagesPluginCapabilityBase", None)

if _PagesBase is not None:
    class OpenPrintHQTab(_PagesBase):
        """An OpenPrintHQ tab next to Prepare, Preview and Device."""

        def get_name(self):
            return "OpenPrintHQ"

        def get_ui(self):
            HUB.page = self
            return themed(TAB_HTML, HUB.look(), hub_connection(HUB.config())[0])

        def on_message(self, msg):
            HUB.page = self
            if isinstance(msg, str):
                try:
                    msg = json.loads(msg)
                except ValueError:
                    return
            if not isinstance(msg, dict):
                return
            if msg.get("cmd") == "hub_photo":
                HUB.photo_async(msg.get("id") or 0, self.post_message)
                return
            if msg.get("cmd") in ("hello", "refresh"):
                HUB.act({"cmd": "refresh"}, lambda r: self.post_message(HUB.snapshot()))
            else:
                HUB.act(msg, self.post_message)
else:
    OpenPrintHQTab = None


@orca.plugin
class OpenPrintHQPrintQueuePlugin(orca.base):
    def register_capabilities(self):
        orca.register_capability(OpenPrintHQAgent)
        if OpenPrintHQTab is not None:
            orca.register_capability(OpenPrintHQTab)
