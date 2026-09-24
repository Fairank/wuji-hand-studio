import copy
import tempfile
import unittest
from pathlib import Path
import yaml
from retarget_tuning import TuningStore,defaults,source,validate,FINGERS


class TuningTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=TuningStore(Path(self.tmp.name));self.binding=dict(generation='hand2',side='left',glove_serial='G1',sdk_user='demo',hand_serial='H1')

    def test_all_four_templates_export_without_semantic_changes(self):
        for gen in ('hand1','hand2'):
            for side in ('left','right'):
                b=dict(self.binding,generation=gen,side=side)
                original,meta=source(b);out=self.store.export(b,defaults(b))
                self.assertEqual(yaml.safe_load(out['yaml']),original)
                self.assertEqual(out['filename'],meta['filename']);self.assertFalse(out['runtime_applied'])

    def test_edit_only_changes_requested_parameters(self):
        before,_=source(self.binding);v=defaults(self.binding);v['segment_scaling']['index'][2]=1.27
        actual=yaml.safe_load(self.store.export(self.binding,v)['yaml'])
        before['retarget']['segment_scaling']['index'][2]=1.27
        self.assertEqual(before,actual)

    def test_bound_revision_prevents_overwrite_and_other_hand_isolation(self):
        ctx=self.store.context(self.binding);v=ctx['values'];v['lp_alpha']=.35
        saved=self.store.save(self.binding,v,ctx['revision']);self.assertEqual(saved['revision'],1)
        with self.assertRaises(ValueError):self.store.save(self.binding,defaults(self.binding),0)
        self.assertEqual(self.store.context(self.binding)['values']['lp_alpha'],.35)
        for key,value in [('side','right'),('generation','hand1'),('hand_serial','H2'),('glove_serial','G2'),('sdk_user','other')]:
            self.assertEqual(self.store.context(dict(self.binding,**{key:value}))['revision'],0)

    def test_rejects_nonfinite_empty_missing_and_bad_pinch(self):
        for value in (True,None,'',float('nan'),float('inf'),0,-1):
            v=defaults(self.binding);v['segment_scaling']['thumb'][0]=value
            with self.assertRaises(ValueError):validate(v)
        v=defaults(self.binding);v['pinch_thresholds']['index']={'d1':4,'d2':2}
        with self.assertRaises(ValueError):validate(v)
        v=defaults(self.binding);v['lp_alpha']=1.1
        with self.assertRaises(ValueError):validate(v)

    def test_future_or_corrupt_saved_file_not_silently_reset(self):
        self.store.save(self.binding,defaults(self.binding),0)
        file=next(Path(self.tmp.name).glob('*.json'));file.write_text('{bad',encoding='utf-8')
        with self.assertRaises(ValueError):self.store.context(self.binding)
        with self.assertRaises(ValueError):self.store.save(self.binding,defaults(self.binding),1)
        self.assertEqual(file.read_text(),'{bad')

    def test_four_column_form_preserves_all_columns(self):
        v=defaults(self.binding)
        for finger in FINGERS:v['segment_scaling'][finger]=[1.,1.1,1.2,1.3]
        self.assertEqual(validate(v),v)
        v['segment_scaling']['thumb']=[1,1,1]
        with self.assertRaises(ValueError):validate(v)


if __name__=='__main__':unittest.main()
