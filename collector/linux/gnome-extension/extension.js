import Gio from 'gi://Gio';
import Shell from 'gi://Shell';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const INTERFACE = `
<node>
  <interface name="org.activitycollector.Telemetry">
    <method name="GetFocusedApp">
      <arg type="s" direction="out" name="app"/>
    </method>
    <method name="GetIdletime">
      <arg type="t" direction="out" name="idletime"/>
    </method>
  </interface>
</node>`;

export default class ActivityCollectorExtension extends Extension {
    enable() {
        this._dbus = Gio.DBusExportedObject.wrapJSObject(INTERFACE, this);
        this._dbus.export(Gio.DBus.session, '/org/activitycollector/Telemetry');

        // g_bus_own_name_on_connection is a free function, not a method on
        // GDBusConnection, so introspection exposes it as Gio.bus_own_name_on_connection
        // rather than Gio.DBus.session.own_name. Calling the latter throws a
        // TypeError out of enable(), and the shell then refuses to load the
        // extension at all, which looks identical to the extension not being
        // installed. The well-known name is what makes --dest= resolvable, so
        // without it the collector could never reach the object either.
        this._ownerId = Gio.bus_own_name_on_connection(
            Gio.DBus.session,
            'org.activitycollector.Telemetry',
            Gio.BusNameOwnerFlags.NONE,
            null,
            null
        );
    }

    disable() {
        // disable() runs on lock, not just on uninstall, so every field has to
        // be dropped or the extension leaks its bus name across lock/unlock.
        if (this._ownerId) {
            Gio.bus_unown_name(this._ownerId);
            this._ownerId = null;
        }
        if (this._dbus) {
            this._dbus.unexport();
            this._dbus = null;
        }
    }

    GetFocusedApp() {
        try {
            const tracker = Shell.WindowTracker.get_default();
            const focusedApp = tracker.focus_app;

            if (!focusedApp) {
                return 'unknown';
            }

            // get_id() returns the app id, or null if unavailable
            let appName = focusedApp.get_id();
            if (!appName) {
                // fall back to get_name() if get_id() returns null
                appName = focusedApp.get_name();
            }

            if (!appName) {
                return 'unknown';
            }

            // cap at 128 chars matching the adapter's truncation
            return appName.substring(0, 128);
        } catch (e) {
            return 'unknown';
        }
    }

    GetIdletime() {
        try {
            const backend = global.backend;
            if (!backend) {
                return 0;
            }

            const monitor = backend.get_core_idle_monitor();
            if (!monitor) {
                return 0;
            }

            const idletime = monitor.get_idletime();
            // idletime is already in milliseconds, return as uint64
            return idletime;
        } catch (e) {
            // return 0 when idle information is unavailable
            return 0;
        }
    }
}
