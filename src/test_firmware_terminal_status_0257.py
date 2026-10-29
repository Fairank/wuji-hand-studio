"""Exact xterm status reports pass only to an existing synthetic owned PTY."""
from contextlib import contextmanager
import copy
import os
from pathlib import Path
import socket
import subprocess
import tempfile
from types import SimpleNamespace as N
import unittest
from unittest.mock import patch

import cli_coordination
from firmware_host import FirmwareHost

SN='WG_SYNTHETIC_VT_0257';TOKEN='a'*32;RUN='b'*32


def forbidden(*args,**kwargs):raise AssertionError('No network, CLI, SDK, or real device process')


class FirmwareTerminalStatusTests(unittest.TestCase):
    @contextmanager
    def environment(self):
        root=Path(os.environ.get('WUJI_CONTROL_QA_ROOT',str(Path(__file__).resolve().parents[2]/'update-flow-0257/calibration-audit/test-temp')))
        root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='firmware-vt-',dir=root) as temporary,patch.object(socket,'socket',forbidden),patch.object(socket,'create_connection',forbidden),patch.object(subprocess,'Popen',forbidden):
            folder=Path(temporary);host=FirmwareHost(N(),folder=folder)
            writes=[];sizes=[]
            host.process=N(write=lambda text:writes.append(text),setwinsize=lambda rows,cols:sizes.append((rows,cols)))
            host.session_token=TOKEN;host.session=dict(id=RUN,running=True,status='running',serial=SN)
            marker=dict(kind='firmware',token=TOKEN,serial=SN)
            cli_coordination.atomic(folder/'session.json',marker)
            yield N(host=host,folder=folder,writes=writes,sizes=sizes,marker=marker)

    def command(self,text,**changes):return dict(dict(name='firmware_terminal_input',run_id=RUN,type='write',text=text),**changes)

    def test_exact_bundled_xterm_cursor_device_and_focus_reports_pass_unchanged(self):
        replies=['\x1b[8;1R','\x1b[34;35R','\x1b[1;1R','\x1b[100;240R','\x1b[?1;2c','\x1b[I','\x1b[O']
        with self.environment() as env:
            for text in replies:self.assertEqual(env.host.terminal_input(self.command(text)),dict(submitted=True))
            self.assertEqual(env.writes,replies)
            self.assertEqual(cli_coordination.reservation(env.folder),env.marker)

    def test_actual_query_responses_and_user_confirmation_keep_exact_pty_order(self):
        replies=['\x1b[?1;2c','\x1b[O','\x1b[8;1R','\x1b[I','y','\r']
        with self.environment() as env:
            for text in replies:env.host.terminal_input(self.command(text))
            self.assertEqual(env.writes,replies)

    def test_zero_out_of_bounds_noncanonical_and_incomplete_cursor_reports_are_rejected(self):
        invalid=['\x1b[0;1R','\x1b[1;0R','\x1b[101;1R','\x1b[1;241R','\x1b[999;999R',
            '\x1b[01;1R','\x1b[1;001R','\x1b[+1;1R','\x1b[-1;1R','\x1b[1.0;1R',
            '\x1b[1;1r','\x1b[1;R','\x1b[;1R','\x1b[?1;1R','\x1b[1;1R\n','\x1b[1;1R\r',
            '\x1b[1;1;1R','\x1b[1;1R ', '\x1b[1;1']
        with self.environment() as env:
            for text in invalid:
                with self.subTest(text=repr(text)),self.assertRaises(ValueError):env.host.terminal_input(self.command(text))
            self.assertEqual(env.writes,[])

    def test_only_exact_primary_device_attributes_are_allowed(self):
        invalid=['\x1b[?1c','\x1b[?1;2;4c','\x1b[>0;276;0c','\x1b[?6c','\x1b[0n',
                 '\x1b[?1;2C','\x1b[?01;2c','\x1b[?1;2c\r']
        with self.environment() as env:
            for text in invalid:
                with self.subTest(text=repr(text)),self.assertRaises(ValueError):env.host.terminal_input(self.command(text))
            self.assertEqual(env.writes,[])

    def test_status_reports_cannot_carry_confirmation_interrupt_or_paste_controls(self):
        invalid=['\x03','\x04','\x1b','\x1b[200~y\x1b[201~','y\n','\x1b[1;1Ry','y\x1b[1;1R',
            '\x1b[1;1R\x03','\x1b[?1;2c\x04','\x1b[1;1R\x1b[?1;2c','\x1b[?1;2c\x1b[?1;2c',
            '\x1b[Iy','y\x1b[O','\x1b[O\x1b[I','\x1b[I\n','\x1b[1I','\x1b[i','\x1b[o']
        with self.environment() as env:
            for text in invalid:
                with self.subTest(text=repr(text)),self.assertRaises(ValueError):env.host.terminal_input(self.command(text))
            self.assertEqual(env.writes,[])

    def test_confirmation_arrow_and_64_character_limits_are_unchanged(self):
        accepted=['y','n','\r','\n','\x1b[A','\x1b[B','x'*64]
        with self.environment() as env:
            for text in accepted:env.host.terminal_input(self.command(text))
            for text in ('x'*65,'',None,False,'\x1b['+'1'*70+';1R'):
                with self.subTest(text=repr(text)),self.assertRaises(ValueError):env.host.terminal_input(self.command(text))
            self.assertEqual(env.writes,accepted)

    def test_each_status_reply_keeps_exact_run_serial_token_and_writer_state_gates(self):
        changes=['wrong_run','ended','unknown_status','no_process','no_token','marker_kind','marker_token','marker_serial']
        for reply in ('\x1b[1;1R','\x1b[?1;2c','\x1b[I','\x1b[O'):
            for change in changes:
                with self.subTest(reply=repr(reply),change=change),self.environment() as env:
                    command=self.command(reply);marker=copy.deepcopy(env.marker)
                    if change=='wrong_run':command['run_id']='c'*32
                    elif change=='ended':env.host.session['running']=False
                    elif change=='unknown_status':env.host.session['status']='unknown'
                    elif change=='no_process':env.host.process=None
                    elif change=='no_token':env.host.session_token=None
                    elif change=='marker_kind':marker['kind']='calibration'
                    elif change=='marker_token':marker['token']='c'*32
                    elif change=='marker_serial':marker['serial']='WG_OTHER'
                    cli_coordination.atomic(env.folder/'session.json',marker)
                    with self.assertRaisesRegex(ValueError,'ownership'):env.host.terminal_input(command)
                    self.assertEqual(env.writes,[])
                    self.assertEqual(cli_coordination.reservation(env.folder),marker)

    def test_resize_and_request_shape_are_not_loosened_by_protocol_responses(self):
        with self.environment() as env:
            env.host.terminal_input(dict(name='firmware_terminal_input',run_id=RUN,type='resize',rows=100,cols=240))
            self.assertEqual(env.sizes,[(100,240)])
            for command in (dict(self.command('\x1b[1;1R'),serial=SN),dict(self.command('\x1b[?1;2c'),token=TOKEN),
                dict(name='firmware_terminal_input',run_id=RUN,type='resize',rows=101,cols=240),
                dict(name='firmware_terminal_input',run_id=RUN,type='resize',rows=100,cols=241)):
                with self.subTest(command=command),self.assertRaises(ValueError):env.host.terminal_input(command)
            self.assertEqual(env.writes,[])


if __name__=='__main__':unittest.main()
