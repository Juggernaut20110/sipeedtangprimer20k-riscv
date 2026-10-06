import os
import subprocess
import tempfile
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

class ExfatFilesystemTests(unittest.TestCase):
    def test_production_fatfs_exfat_roundtrips_and_remount(self):
        with tempfile.TemporaryDirectory(prefix='fatfs-exfat-') as temp:
            directory=Path(temp);image=directory/'disposable-test.img';binary=directory/'test'
            with image.open('wb') as output:output.truncate(64*1024*1024)
            env=os.environ.copy();env['PATH']='/usr/bin:/bin:/usr/sbin:/sbin'
            subprocess.run(['/usr/sbin/mkfs.exfat',str(image)],check=True,capture_output=True,env=env)
            folder=ROOT/'firmware/peripherals/fatfs'
            result=subprocess.run(['/usr/bin/gcc','-std=c11','-O2','-Wall','-I'+str(folder),
                str(folder/'ff.c'),str(folder/'ffunicode.c'),str(ROOT/'tests/test_fatfs_exfat.c'),'-o',str(binary)],capture_output=True,text=True,env=env)
            self.assertEqual(result.returncode,0,result.stderr)
            result=subprocess.run([str(binary),str(image)],capture_output=True,text=True,env=env)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('preservation passed',result.stdout)
            subprocess.run(['/usr/sbin/fsck.exfat','-n',str(image)],check=True,capture_output=True,env=env)
