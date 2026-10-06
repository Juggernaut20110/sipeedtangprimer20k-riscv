import os
import subprocess
import tempfile
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class SDDriverTests(unittest.TestCase):
    def test_status_probes_real_driver_with_bounded_failures(self):
        with tempfile.TemporaryDirectory(prefix='sd-driver-') as temp:
            directory=Path(temp);(directory/'generated').mkdir();(directory/'libbase').mkdir()
            prototypes=['#include <stdint.h>']
            for name in ['mosi','control','cs','clk_divider']:prototypes.append(f'void spisdcard_{name}_write(uint32_t);')
            for name in ['status','miso']:prototypes.append(f'uint32_t spisdcard_{name}_read(void);')
            prototypes.append('uint32_t spisdcard_clk_divider_read(void);')
            (directory/'generated/csr.h').write_text('\n'.join(prototypes))
            (directory/'generated/soc.h').write_text('#define CONFIG_CLOCK_FREQUENCY 48000000u\n')
            (directory/'libbase/timeout.h').write_text('struct timeout {int unused;};\nvoid timeout_start(struct timeout*,unsigned int);\nint timeout_expired(struct timeout*);\n')
            env=os.environ.copy();env['PATH']='/usr/bin:/bin';binary=directory/'test'
            result=subprocess.run(['/usr/bin/gcc','-std=c11','-O0','-ffunction-sections','-fdata-sections',
               '-I'+str(directory),'-I'+str(ROOT/'firmware/peripherals'),str(ROOT/'tests/test_sd_driver.c'),
               str(ROOT/'firmware/peripherals/sd_protocol.c'),'-Wl,--gc-sections','-o',str(binary)],capture_output=True,text=True,env=env)
            self.assertEqual(result.returncode,0,result.stderr)
            result=subprocess.run([str(binary)],capture_output=True,text=True,env=env,timeout=5)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('checks passed',result.stdout)
