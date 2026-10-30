"""Official optional-parameter semantics; no actual SDK or device execution."""
from contextlib import contextmanager
import hashlib
from pathlib import Path
import sys,tempfile
from types import SimpleNamespace as N
import unittest
from unittest.mock import patch

from calibration_portability import verify_loaded_model


UNSET='Parameter not set: calibration.hand_model_path'
MODEL=b'<robot name="verified-personal-hand"/>'


class WujiException(Exception):pass


class Resource:
    """Stored SDK param, not the model artifact itself; initially absent."""
    def __init__(self,value=None):self.value=value;self.calls=[];self.get_error=None;self.on_set=None
    def get(self):
        self.calls.append(('get',))
        if self.get_error is not None:raise self.get_error
        if self.value is None:raise WujiException(UNSET)
        return self.value
    def set(self,value):
        self.calls.append(('set',value))
        if self.on_set is not None:return self.on_set(value)
        if not Path(value).is_file():raise WujiException('hand_model_path is not readable')
        self.value=value


class RuntimeModelTests(unittest.TestCase):
    @contextmanager
    def environment(self,value=None):
        with tempfile.TemporaryDirectory() as folder,patch.dict(sys.modules,wuji_sdk=N(WujiException=WujiException)):
            root=Path(folder);model=root/'sdk/users/owner-A/models/right.urdf';model.parent.mkdir(parents=True);model.write_bytes(MODEL)
            resource=Resource(value)
            receipt=dict(model_path=str(model),model_sha256=hashlib.sha256(MODEL).hexdigest(),model_bytes=len(MODEL))
            yield N(root=root,model=model,resource=resource,glove=N(hand_model_path=lambda:resource),receipt=receipt)

    def test_strict_sdk_unset_loads_verified_model_and_reads_it_back(self):
        with self.environment() as e:
            self.assertTrue(verify_loaded_model(e.glove,e.receipt,apply_missing=True))
            self.assertEqual(e.resource.calls,[('get',),('set',str(e.model)),('get',)])
            self.assertEqual(e.model.read_bytes(),MODEL)

    def test_empty_stored_override_explicitly_loads_but_nonstring_does_not(self):
        with self.environment('') as e:
            self.assertTrue(verify_loaded_model(e.glove,e.receipt,apply_missing=True))
            self.assertEqual(e.resource.value,str(e.model))
        for value in (False,0,[],{}):
            with self.subTest(value=value),self.environment(value) as e:
                with self.assertRaises(ValueError):verify_loaded_model(e.glove,e.receipt,apply_missing=True)
                self.assertEqual(e.resource.calls,[('get',)])

    def test_existing_matching_override_is_verified_without_writing(self):
        with self.environment() as e:
            e.resource.value=str(e.model)
            self.assertTrue(verify_loaded_model(e.glove,e.receipt,apply_missing=True))
            self.assertEqual(e.resource.calls,[('get',)])

    def test_foreign_override_never_becomes_authorization_to_overwrite(self):
        with self.environment() as e:
            other=e.root/'other.urdf';other.write_bytes(b'<robot name="another-person"/>');e.resource.value=str(other)
            with self.assertRaisesRegex(ValueError,'custom'):verify_loaded_model(e.glove,e.receipt,apply_missing=True)
            self.assertEqual(e.resource.calls,[('get',)]);self.assertEqual(other.read_bytes(),b'<robot name="another-person"/>')

    def test_only_exact_sdk_parameter_error_allows_application(self):
        for error in (WujiException('Device disconnected'),WujiException('Parameter not set: another.path'),
                      WujiException(UNSET+'.other'),RuntimeError(UNSET),ValueError(UNSET)):
            with self.subTest(error=error),self.environment() as e:
                e.resource.get_error=error
                with self.assertRaises(type(error)):verify_loaded_model(e.glove,e.receipt,apply_missing=True)
                self.assertEqual(e.resource.calls,[('get',)])

    def test_set_failure_is_not_success_or_an_implicit_retry(self):
        with self.environment() as e:
            def fail(value):raise WujiException('Synthetic model setter failure')
            e.resource.on_set=fail
            with self.assertRaisesRegex(WujiException,'setter failure'):verify_loaded_model(e.glove,e.receipt,apply_missing=True)
            self.assertEqual(e.resource.calls,[('get',),('set',str(e.model))])

    def test_readback_must_confirm_a_nonempty_string_with_exact_model_bytes(self):
        for value in ('',False,'foreign','unset','disconnected'):
            with self.subTest(value=value),self.environment() as e:
                other=e.root/'other.urdf';other.write_bytes(b'<robot name="wrong"/>')
                def setter(path):
                    if value=='foreign':e.resource.value=str(other)
                    elif value=='unset':e.resource.value=None
                    elif value=='disconnected':e.resource.get_error=WujiException('Device disconnected')
                    else:e.resource.value=value
                e.resource.on_set=setter
                with self.assertRaises(Exception):verify_loaded_model(e.glove,e.receipt,apply_missing=True)
                self.assertEqual(sum(call[0]=='set' for call in e.resource.calls),1)

    def test_mutation_during_application_is_detected_before_success(self):
        with self.environment() as e:
            def setter(path):e.resource.value=path;e.model.write_bytes(b'changed during SDK set')
            e.resource.on_set=setter
            with self.assertRaises(ValueError):verify_loaded_model(e.glove,e.receipt,apply_missing=True)
            self.assertEqual(sum(call[0]=='set' for call in e.resource.calls),1)

    def test_already_changed_receipt_file_is_rejected_before_any_sdk_parameter_read(self):
        with self.environment() as e:
            e.model.write_bytes(b'changed before call')
            with self.assertRaises(ValueError):verify_loaded_model(e.glove,e.receipt,apply_missing=True)
            self.assertEqual(e.resource.calls,[])

    def test_read_only_default_never_sets_or_swallows_unset(self):
        with self.environment() as e:
            with self.assertRaises(WujiException):verify_loaded_model(e.glove,e.receipt)
            self.assertEqual(e.resource.calls,[('get',)])
        with self.environment('') as e:
            self.assertTrue(verify_loaded_model(e.glove,e.receipt));self.assertEqual(e.resource.calls,[('get',)])

    def test_reentry_with_persisted_runtime_model_does_not_repeat_the_set(self):
        with self.environment() as e:
            self.assertTrue(verify_loaded_model(e.glove,e.receipt,apply_missing=True))
            self.assertTrue(verify_loaded_model(e.glove,e.receipt,apply_missing=True))
            self.assertEqual(sum(call[0]=='set' for call in e.resource.calls),1)


if __name__=='__main__':unittest.main()
