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
        // Throw rather than return 0 when the monitor is unavailable. 0 is a
        // real answer -- it is what get_idletime() reports the instant after a
        // keypress -- so returning it for "I cannot tell" makes the two
        // indistinguishable to the caller. The collector read that 0 as "no
        // information", fell through every remaining probe, and ended up
        // defaulting to active, so a shell without a core idle monitor reported
        // a machine as busy all night. A thrown error becomes a D-Bus error
        // reply, gdbus exits non-zero, and the collector can tell the
        // difference.
        const backend = global.backend;
        if (!backend) {
            throw new Error('no backend: idle time is unavailable');
        }

        const monitor = backend.get_core_idle_monitor();
        if (!monitor) {
            throw new Error('no core idle monitor: idle time is unavailable');
        }

        // Already in milliseconds.
        return monitor.get_idletime();
    }
}
