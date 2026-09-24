"""Linux wrapper: official calibration stdout, cancellation and ownership.

The Windows host sends one `cancel` line; EOF also cancels. Signals are sent to
the actual official CLI process, not merely the Windows WSL launcher.
"""
import json
import signal
import subprocess
import sys
import threading


def run(argv):
    from sdk_session import ProfileLease
    from device_ownership import DeviceOwnership
    if len(argv) < 2 or argv[0] not in ('profile', 'calibrate'):
        raise ValueError('Unsupported official CLI wrapper operation')
    mode, command = argv[0], argv[1:]
    if mode == 'profile':
        if len(command) < 5 or command[1:3] != ['--json', 'user'] or command[3] not in ('create', 'switch'):
            raise ValueError('Expected official user create/switch')
        with ProfileLease(exclusive=True):
            return subprocess.call(command)
    if len(command)<3 or command[1] not in ('keep','replace'):
        raise ValueError('Expected calibration user and overwrite choice')
    expected_user,overwrite,command=command[0],command[1]=='replace',command[2:]
    if not expected_user or expected_user.lower()=='default':raise ValueError('Named calibration user required')
    if len(command) != 10 or command[1:4] != ['--jsonl', 'calib', 'hand-model'] or command[4] != '--sn' or command[6] != '--handedness' or command[8] != '--timeout-s':
        raise ValueError('Expected official hand-model calibration arguments')
    with ProfileLease(exclusive=True), DeviceOwnership(command[5]):
        # Preflight on the host can race another workspace changing SDK user.
        # Recheck under the exclusive Linux lease before any capture starts.
        check=subprocess.run([command[0],'--json','user','show'],capture_output=True,text=True,timeout=20)
        if check.returncode:raise ValueError('Cannot verify calibration user under the session lock')
        current=json.loads(check.stdout)
        if not isinstance(current,dict) or current.get('name')!=expected_user or current.get('is_default'):
            raise ValueError('SDK user changed before capture; refresh and select again / 标定用户已变更，请刷新后重新选择')
        model=current.get(command[7]+'_hand')
        if not isinstance(model,dict) or type(model.get('calibrated')) is not bool:
            raise ValueError('Official calibration state is unknown; refresh first')
        if model['calibrated'] and not overwrite:
            raise ValueError('Calibration now exists; explicit replacement consent required / 已有标定，请重新确认是否覆盖')
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL)
        cancelled = threading.Event()
        def cancel(*_):
            if not cancelled.is_set() and process.poll() is None:
                cancelled.set()
                try: process.send_signal(signal.SIGINT)
                except ProcessLookupError: pass
        def input_loop():
            for line in sys.stdin:
                if line.strip() == 'cancel':
                    cancel(); return
            cancel()
        signal.signal(signal.SIGTERM, cancel)
        signal.signal(signal.SIGINT, cancel)
        threading.Thread(target=input_loop, daemon=True).start()
        return process.wait()


if __name__ == '__main__':
    try:
        code = run(sys.argv[1:])
    except Exception as error:
        print(json.dumps(dict(schema_version=2, calibration='hand_model', event='error',
                              error=dict(code=1, message=str(error)))), flush=True)
        code = 1
    sys.exit(code)
