"""Native windows for registered tasks; window lifetime never controls a device."""
import json
import re
import threading
from types import SimpleNamespace
from urllib.parse import urlencode


class TaskWindowAPI:
    """A child view may close only itself; it cannot close the workbench."""
    def __init__(self, manager, task_id):
        self._manager = manager
        self._task_id = task_id

    def info(self):
        return self._manager.host.info()

    def close_task_window(self, task_id):
        if task_id != self._task_id:
            raise ValueError('This window can only close its own task view')
        return self._manager.close(task_id)

    def task_window_state(self):
        return self._manager.snapshot()

    def rebind_task_window(self, source_task_id, target_task_id, receipt):
        if source_task_id != self._task_id:
            raise ValueError('Task window owner changed')
        return self._manager.rebind(source_task_id, target_task_id, receipt)


class TaskWindows:
    def __init__(self, host, create_window):
        self.host = host
        self.create_window = create_window
        self.windows = {}
        self.bindings = {}
        self.lock = threading.RLock()
        self.closed = False

    @staticmethod
    def validate(task_id):
        if not isinstance(task_id, str) or not re.fullmatch(r'[0-9a-f]{24}', task_id):
            raise ValueError('Invalid device task')

    def _publish(self, task_id, opened):
        try:
            payload = json.dumps(dict(task_id=task_id, open=opened))
            event = 'task-window-opened' if opened else 'task-window-closed'
            self.host.window.evaluate_js('window.dispatchEvent(new CustomEvent(' + json.dumps(event) + ',{detail:' + payload + '}))')
        except Exception:
            # Closing a view must still finish if the main page is reloading.
            pass

    def open(self, task_id):
        self.validate(task_id)
        # The host URL is constructed here, never accepted from the browser.
        tasks = self.host.server.tasks.snapshot().get('tasks', [])
        matches = [row for row in tasks if row.get('task_id') == task_id
                   and row.get('status') != 'disconnected']
        if len(matches) != 1:
            raise ValueError('设备任务已断开，请打开当前设备 / Open the current connected device task')
        with self.lock:
            if self.closed or self.host.closing or self.host.allow_close:
                raise ValueError('Workbench is closing')
            window = self.windows.get(task_id)
            if window is not None:
                window.restore()
                window.show()
                return dict(ok=True, task_id=task_id, reused=True)
            if len(self.windows) >= 8:
                raise ValueError('最多同时打开 8 个设备窗口 / Up to eight task windows can be open')
            label = matches[0].get('label')
            label = label[:100] if isinstance(label, str) and label.strip() else '设备任务'
            url = self.host.origin + '/?' + urlencode(dict(desktop=1, task_window=task_id)) + '#tasks'
            api = TaskWindowAPI(self, task_id)
            window = self.create_window(label + ' · Hand Workbench', url,
                width=1060, height=780, min_size=(640, 480), background_color='#f5f5f7',
                text_select=True, zoomable=False, js_api=api)
            self.windows[task_id] = window
            self.bindings[task_id] = dict(endpoint=matches[0].get('endpoint_id'), api=api)

            def cleared():
                with self.lock:
                    current_id = api._task_id
                    if self.windows.get(current_id) is window:
                        self.windows.pop(current_id, None)
                        self.bindings.pop(current_id, None)
                        self._publish(current_id, False)

            window.events.closed += cleared
            window.events.loaded += lambda: self.host.secure_window(window)
            self._publish(task_id, True)
            return dict(ok=True, task_id=task_id, reused=False)

    def rebind(self, source_task_id, target_task_id, receipt):
        self.validate(source_task_id)
        self.validate(target_task_id)
        if (not isinstance(receipt, dict) or type(receipt.get('version')) is not int or receipt.get('version') != 1
                or receipt.get('source_task_id') != source_task_id
                or receipt.get('target_task_id') != target_task_id):
            raise ValueError('Task switch receipt does not match this window')
        with self.lock:
            window = self.windows.get(source_task_id)
            binding = self.bindings.get(source_task_id)
            if self.closed or self.host.closing or self.host.allow_close or window is None or binding is None:
                raise ValueError('Task window is no longer available')
            rows = self.host.server.tasks.snapshot().get('tasks', [])
            matches = [row for row in rows if row.get('task_id') == target_task_id
                       and row.get('status') != 'disconnected' and row.get('endpoint_id') == binding['endpoint']]
            if len(matches) != 1 or target_task_id in self.windows and target_task_id != source_task_id:
                raise ValueError('The destination task is unavailable or already has a window')
            state = self.host.server.tasks.devices.read(binding['endpoint'])
            from task_view import scope
            proof = scope(SimpleNamespace(snapshot=lambda: state), target_task_id)
            def canonical(value):
                try:
                    encoded = json.dumps(value, sort_keys=True, allow_nan=False, separators=(',', ':'))
                    if len(encoded) > 8192: raise ValueError('Task receipt exceeds its size limit')
                    return encoded
                except (TypeError, ValueError) as error:
                    raise ValueError('Invalid task switch receipt') from error
            if (proof.get('current') is not True or canonical(proof.get('last_transition_receipt')) != canonical(receipt)
                    or proof.get('owner_context') != receipt.get('after_context')):
                raise ValueError('Task switch is not confirmed by its current owner')
            self.windows.pop(source_task_id)
            self.bindings.pop(source_task_id)
            self.windows[target_task_id] = window
            self.bindings[target_task_id] = binding
            binding['api']._task_id = target_task_id
            label = matches[0].get('label')
            try:
                window.set_title((label[:100] if isinstance(label, str) and label.strip() else '设备任务') + ' · Hand Workbench')
            except Exception:
                pass
            if source_task_id != target_task_id:
                self._publish(source_task_id, False)
                self._publish(target_task_id, True)
            return dict(ok=True, task_id=target_task_id)

    def close(self, task_id):
        self.validate(task_id)
        with self.lock:
            window = self.windows.get(task_id)
        if window is not None:
            window.destroy()
        return dict(ok=True, task_id=task_id)

    def snapshot(self):
        with self.lock:
            return dict(ok=True, task_ids=list(self.windows))

    def shutdown(self):
        with self.lock:
            self.closed = True
            windows = list(self.windows.values())
        for window in windows:
            try:
                window.destroy()
            except Exception:
                pass
