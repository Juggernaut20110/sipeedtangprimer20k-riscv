"""Exercise actual raw callbacks with lwIP types and deterministic hardware stubs."""
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

class EthernetDriverTests(unittest.TestCase):
    def test_callbacks_and_frame_bounds(self):
        with tempfile.TemporaryDirectory(prefix='ethernet-driver-') as temp:
            directory = Path(temp)
            (directory/'generated').mkdir()
            (directory/'libbase').mkdir()
            source = (ROOT/'firmware/peripherals/ethernet_lwip.c').read_text()
            calls = set(re.findall(r'\b((?:ethmac_|ethphy_|timer0_)\w+)\(', source))
            prototypes = ['#include <stdint.h>']
            for call in sorted(calls):
                prototypes.append(('uint32_t '+call+'(void);') if call.endswith('_read') else ('void '+call+'(uint32_t value);'))
            (directory/'generated/csr.h').write_text('\n'.join(prototypes))
            (directory/'generated/mem.h').write_text('#define ETHMAC_RX_BASE ((uintptr_t)mock_rx)\n#define ETHMAC_TX_BASE ((uintptr_t)mock_tx)\n')
            (directory/'generated/soc.h').write_text('#define CONFIG_CLOCK_FREQUENCY 48000000u\n#define ETHMAC_SLOT_SIZE 2048u\n#define ETHMAC_RX_SLOTS 2u\n#define ETHMAC_TX_SLOTS 2u\n')
            (directory/'profile.h').write_text('#define PROJECT_SDCARD_SPI 1\n')
            (directory/'libbase/timeout.h').write_text('struct timeout {int unused;};\nvoid timeout_start(struct timeout*,unsigned int);\nint timeout_expired(struct timeout*);\n')
            binary = directory/'test'
            command = ['/usr/bin/gcc','-std=c11','-O0','-g','-ffunction-sections','-fdata-sections',
                       '-I'+str(directory),'-I'+str(ROOT/'firmware/peripherals'),
                       '-I'+str(ROOT/'firmware/peripherals/lwip'),'-I'+str(ROOT/'.deps/lwip/src/include'),
                       str(ROOT/'tests/test_ethernet_driver.c'),'-Wl,--gc-sections','-o',str(binary)]
            env=os.environ.copy(); env["PATH"]="/usr/bin:/bin"
            result=subprocess.run(command,capture_output=True,text=True,env=env)
            self.assertEqual(result.returncode,0,result.stderr)
            result=subprocess.run([str(binary)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('Ethernet driver callbacks passed',result.stdout)
