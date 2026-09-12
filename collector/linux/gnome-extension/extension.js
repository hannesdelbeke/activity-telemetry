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

        this._ownerId = Gio.DBus.session.own_name(
            'org.activitycollector.Telemetry',
            Gio.BusNameOwnerFlags.NONE,
            null,
            null
        );
    }

    disable() {
        if (this._dbus) {
            this._dbus.unexport();
            this._dbus = null;
        }
        if (this._ownerId) {
            Gio.DBus.session.unown_name(this._ownerId);
            this._ownerId = null;
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
