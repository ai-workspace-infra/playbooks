import importlib.util
import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('volume', ROOT / 'scripts/data_operations/selfhost/mount_uat_retained_volume.py')
subject = importlib.util.module_from_spec(spec)
spec.loader.exec_module(subject)


class RetainedVolumeContract(unittest.TestCase):
    def host(self):
        name = subject.NAME
        return {'name': 'web-saas-uat', 'provider':'gcp-cloud', 'zone':'asia-east1-a',
                'persistent_data_disks':[{'name':name,'id':'projects/open-platform-uat/zones/asia-east1-a/disks/'+name,
                                         'zone':'asia-east1-a','device_name':name,'mount_path':'/data',
                                         'management':'google_compute_attached_disk'}]}

    def test_exact_iac_disk_identity(self):
        self.assertEqual(subject.disk_contract(self.host())['mount_path'], '/data')
        for key,value in [('mount_path','/'),('id','projects/production/zones/asia-east1-a/disks/other'),('device_name','sda')]:
            record=self.host()
            record['persistent_data_disks'][0][key]=value
            with self.assertRaises(ValueError): subject.disk_contract(record)

    def test_missing_or_multiple_volumes_rejected(self):
        for disks in [[], self.host()['persistent_data_disks']*2]:
            record=self.host();record['persistent_data_disks']=disks
            with self.assertRaises(ValueError): subject.disk_contract(record)

    def test_mount_program_syntax_and_no_force_format(self):
        result=subprocess.run(['bash','-n'],input=subject.REMOTE,text=True,capture_output=True)
        self.assertEqual(result.returncode,0)
        self.assertNotIn('mkfs.ext4 -F',subject.REMOTE)
        self.assertIn('wipefs --no-act',subject.REMOTE)
        self.assertIn('test -z "$(find /data',subject.REMOTE)
        self.assertIn('blockdev --getsize64',subject.REMOTE)
        self.assertNotIn('docker',subject.REMOTE)


if __name__=='__main__': unittest.main()
