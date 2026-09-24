"""Workspace-level binding and mapping selection. No device or motor access."""
import json
from pathlib import Path
from mapping_library import MappingLibrary
from retarget_settings import load as legacy_load


class RetargetWorkspace:
    def __init__(self, folder):
        self.folder=Path(folder)
        self.library=MappingLibrary(self.folder/'mapping_library.json')
        self.path=self.folder/'retarget_binding.json'

    def context(self, profile):
        binding=dict(generation=profile['generation'],side=profile['side'],glove_serial='',sdk_user='',hand_serial='')
        if self.path.exists():
            saved=json.loads(self.path.read_text(encoding='utf-8'))
            self.library.snapshot(saved) # reject malformed persisted bindings
            if all(saved[k]==binding[k] for k in ('generation','side')):binding=saved
        result=self.library.snapshot(binding)
        # Keep old mapping only for its original unbound workspace context.
        legacy=self.folder/'retargeting.json'
        if not result['saved'] and not any(binding[k] for k in ('glove_serial','sdk_user','hand_serial')) and legacy.exists():
            result['settings']=legacy_load(legacy);result['legacy']=True
        return dict(**result,presets=self.library.list_presets())

    def select(self, binding, profile):
        if not isinstance(binding,dict):raise ValueError('Expected mapping binding')
        if any(binding.get(k)!=profile[k] for k in ('generation','side')):
            raise ValueError('Mapping side/model must match this workspace / 映射代际与左右手必须匹配工作区')
        self.library.snapshot(binding)
        self.folder.mkdir(parents=True,exist_ok=True)
        stage=self.path.with_suffix('.pending')
        stage.write_text(json.dumps(binding,ensure_ascii=False),encoding='utf-8');stage.replace(self.path)
        return self.context(profile)

    def open_glove(self, command, profile, user):
        old=self.context(profile)['binding']
        selected=dict(old,glove_serial=command['serial'],sdk_user=command.get('user_id') or user.get('user_id') or '')
        return self.select(selected,profile)
