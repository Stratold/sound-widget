#!/usr/bin/env python

import dbus, dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib


class TMeta(type):
    def __new__(cls, name, bases, namespace, **kwds):
        namespace.pop('__module__')
        namespace.pop('__qualname__')
        return namespace


class AwesomeTalker(dbus.service.Object):
    def __init__(self, conn, object_path='/'):
        super().__init__(conn, object_path)

        aw = conn.get_object('org.awesomewm.awful', '/')
        self.awful = dbus.Interface(aw, 'org.awesomewm.awful.sototools.Tools')
        self._conn = conn
        self._sig_recvs = []
        class sig_funcs(metaclass=TMeta):
            def pawMouseEnter(*args, **kwgs):
                print(f"sig_funcs(pawMouseEnter): {args} {kwgs}")
            def pawMouseLeave(*args, **kwgs):
                print(f"sig_funcs(pawMouseLeave): {args} {kwgs}")
            def pawMouseWheelUp(*args, **kwgs):
                print(f"sig_funcs(pawMouseWheelUp): {args} {kwgs}")
                self._paw.sig_functions['set_fsink_vol_up'](*args, **kwgs)
            def pawMouseWheelDown(*args, **kwgs):
                print(f"sig_funcs(pawMouseWheelDown): {args} {kwgs}")
                self._paw.sig_functions['set_fsink_vol_down'](*args, **kwgs)
        self.sig_funcs = sig_funcs

        print("initialized")

    def add_sig_recv(self, handler, sig_name, dbus_iface='org.awesomewm.awful.sototools.Tools1'):
        match = self._conn.add_signal_receiver(handler, sig_name, dbus_iface)
        self._sig_recvs.append(match)


    def init_paw(self, paw_obj):
        self._paw = paw_obj
        for k, f in self.sig_funcs.items():
            self.add_sig_recv(f, k)


    def set_default_vol(self, volume):
        print('Making call')
        self.awful.set_default_vol(volume)

    def test_receiver(self, *args, **kwargs):
        print(f'Recieved following:\n{args}\n{kwargs}')
        #self.test_signal('Wow')


class PulseTalker(dbus.service.Object):
    MATRIX = []
    def __init__(self, conn, object_path='/', awesome_obj=None):
        self._awesome = awesome_obj
        pulse = conn.get_object('org.PulseAudio1', '/org/pulseaudio/server_lookup1')
        address = pulse.Get('org.PulseAudio.ServerLookup1', 'Address')
        print(f"Address: {address}")
        pbus = dbus.connection.Connection(address)
        super().__init__(pbus, object_path)
        self._sig_handlers = {}
        self._pa_state = {
                'pa_signals': {},
                'fb_sink_vol': None,
                }
        self._conn = pbus
        self._pa_core = pbus.get_object('org.PulseAudio.Core1', '/org/pulseaudio/core1')
        self._fallback_sink =  self._pa_core.Get('org.PulseAudio.Core1', 'FallbackSink')

        class sig_functions(metaclass=TMeta):
            def error_handler(*args, **kwgs):
                print(f'Error handler invoked: {args} {kwgs}')
            def fsink_vol_update(*args):
                self._awesome.set_default_vol(max(args[0]))
                self._pa_state['fb_sink_vol'] = args[0]
            def get_fsink_vol(*args):
                self._conn.call_async('org.PulseAudio.Core1',
                        self._fallback_sink, None,
                        'Get', None,
                        ('org.PulseAudio.Core1.Device', 'Volume'),
                        reply_handler=sig_functions['fsink_vol_update'],
                        error_handler=sig_functions['error_handler'])
            def set_fsink_vol_up(*args):
                v = max(self._pa_state['fb_sink_vol']) + 2000
                v = v if v <= 65536 else 65536
                self._conn.call_async('org.PulseAudio.Core1',
                        self._fallback_sink, None,
                        'Set', None,
                        ('org.PulseAudio.Core1.Device', 'Volume',
                            dbus.Array([dbus.UInt32(v)],variant_level=1)),
                        reply_handler=None,
                        error_handler=sig_functions['error_handler'])
            def set_fsink_vol_down(*args):
                v = max(self._pa_state['fb_sink_vol']) - 2000
                v = v if v > 0 else 0
                self._conn.call_async('org.PulseAudio.Core1',
                        self._fallback_sink, None,
                        'Set', None,
                        ('org.PulseAudio.Core1.Device', 'Volume',
                            dbus.Array([dbus.UInt32(v)],variant_level=1)),
                        reply_handler=None,
                        error_handler=sig_functions['error_handler'])
            def change_fsink(*args):
                fsink = args[0]
                self._disconnect_signal('org.PulseAudio.Core1.Device.VolumeUpdated',
                        self._fallback_sink)
                self._connect_signal(SLfac, 'org.PulseAudio.Core1.Device.VolumeUpdated',
                        fsink, ['fsink_vol_update'])
                self._fallback_sink = args[0]
            def get_fsink(*args):
                self._pa_core.Get('org.PulseAudio.Core1', 'FallbackSink',
                        reply_handler=SLfac('', None, ['change_fsink','get_fsink_vol']),
                        error_handler=sig_functions['error_handler'])
        self.sig_functions = sig_functions

        def SLfac(signal_name, obj_path, funcs=[]):
            this = self
            def handler(*args, **kwargs):
                print(f'Signal handler {(signal_name,obj_path)} executed\n{args} {kwargs}')
                for func in funcs:
                    print(f'Executing signal handler function {func}')
                    sig_functions[func](*args, **kwargs)
            return handler

        self._matrix = [
                ('init', None, ['get_fsink']),
                ('org.PulseAudio.Core1.FallbackSinkUpdated', None, ['change_fsink','get_fsink_vol']),
                ('org.PulseAudio.Core1.FallbackSinkUnset', None, []),
                ('org.PulseAudio.Core1.Device.VolumeUpdated', self._fallback_sink, ['fsink_vol_update']),
                ]

        for m in self._matrix:
            name, path, funcs = m
            if name == 'init':
                SLfac('Initialization', None, funcs)()
            else:
                self._connect_signal(SLfac, name, path, funcs)

        print("PA initialized")
        print(f'Fallback: {self._fallback_sink}')
        self._awesome.init_paw(self)

    def _pa_signal_on(self, name, path=None):
        if self._pa_state['pa_signals'].get((name,path), None):
            return True
        self._pa_core.ListenForSignal(name, dbus.Array(path if path else [], signature='o'),
                ignore_reply=True)
        self._pa_state['pa_signals'][(name,path)] = True

    def _connect_signal(self, factory, name, path=None, funcs=[]):
        self._pa_signal_on(name)
        interface, signal_name = name.rsplit('.', 1)
        match = self._conn.add_signal_receiver(factory(name, path, funcs),
                signal_name=signal_name,
                dbus_interface=interface,
                path=path)
        self._sig_handlers[(name, path)] = match
        print(f"_connect_signal: signal {(name, path)} connected\nsig_handlers: {self._sig_handlers}")
    
    def _disconnect_signal(self, name, path=None):
        self._conn.remove_signal_receiver(self._sig_handlers[(name,path)])
        print(f'_disconnect_signal: {(name,path)} sig handler removed')

    def _fallback_change(self, *args, **kwargs):
        print(f'Recieved following:\n{args}\n{kwargs}')
        self._fallback_sink

    def _fallback_volume_changed(self, *args, **kwargs):
        print(f'Received following:\n{args}\n{kwargs}')


# Main
DBusGMainLoop(set_as_default=True)

awesome_talker = AwesomeTalker(dbus.SessionBus())
pulse_talker = PulseTalker(dbus.SessionBus(), awesome_obj=awesome_talker)
loop = GLib.MainLoop()
print("starting")
loop.run()

