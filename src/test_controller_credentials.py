import unittest
from unittest.mock import patch,MagicMock
from bridge_config import DEFAULT,validate,ssh_client
import controller_credentials as creds

class CredentialTests(unittest.TestCase):
    def setUp(self):self.c=dict(DEFAULT,mode='ssh',host='linux.test',username='operator',agent_directory='/opt/hand')
    def test_password_never_part_of_connection_config(self):
        with self.assertRaises(ValueError):validate(dict(self.c,password='do-not-store'))
    def test_host_port_and_user_scope(self):
        targets={creds.target(self.c)}
        for key,value in [('host','other.test'),('port',2222),('username','Operator')]:targets.add(creds.target(dict(self.c,**{key:value})))
        self.assertEqual(len(targets),4)
        with self.assertRaises(ValueError):creds.target(dict(self.c,host=''))
    def test_saved_password_and_key_are_distinct_paths(self):
        with patch('paramiko.SSHClient') as factory,patch('controller_credentials.read',return_value='test-only') as reader:
            ssh_client(self.c);kw=factory.return_value.connect.call_args.kwargs
            self.assertEqual(kw['password'],'test-only');self.assertFalse(kw['look_for_keys']);self.assertFalse(kw['allow_agent'])
            reader.reset_mock();ssh_client(dict(self.c,key_filename='key.pem'));reader.assert_not_called()
            self.assertIsNone(factory.return_value.connect.call_args.kwargs['password'])

if __name__=='__main__':unittest.main()
